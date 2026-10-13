"""Core macro execution engine.

:class:`MacroEngine` runs action sequences on worker threads with hotkey
starts and an emergency stop callable from any thread.  A macro is only ever
*running* or *stopped* (no paused state); both triggering and looping are
expressed by ordinary actions:

* ``wait_for`` -- block until its condition holds (optional timeout).
* ``stop_trigger <rule>`` -- hold later actions while the rule is true.
* ``start_trigger <rule>`` -- keep later actions held until the rule becomes
  true.  A bare ``start_trigger`` closes the stretch: it drops every armed
  rule so later actions run freely again.  Together the two steps work like
  an if/else over the stretch of actions written between them.
* ``loop_start`` / ``loop_end`` -- jump back/forward pair that repeats the
  steps between them (iteration count on the start step, delay on the end
  step) instead of a general per-macro loop option.

Rules watch three kinds of sources: the screen (polled by the background
monitor), physical user input (keyboard/mouse events recorded live) and
other actions executing inside the same run.  Event-based rules baseline
their counter when armed, so only events happening *after* the trigger step
count.  Flow steps never halt themselves -- normal actions call
:meth:`ExecutionToken.wait_while_blocked` before firing.

The heavy lifting lives in focused collaborators; this module owns the
lifecycle they share:

* :mod:`engine.token` -- :class:`ExecutionToken` / :class:`MacroStats`.
* :mod:`engine.macro_flow` -- :class:`MacroRunner`, the worker-thread pass.
* :mod:`engine.condition_monitor` -- background rule sampling helpers.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from automation.hotkeys import HotkeyManager
from automation.recorder import InputRecorder
from automation.synthesis import perform_action
from engine import condition_monitor
from engine.condition_runtime import ConditionRuntime
from engine.macro_flow import MacroRunner
from engine.token import ExecutionToken, MacroStats
from models.condition import ScreenCondition
from models.macro import ActionConfig, AppSettings, MacroConfig

logger = logging.getLogger(__name__)


def _resolve_perform(engine: "MacroEngine", action: ActionConfig, cancel: Callable[[], bool] | None) -> None:
    """Dispatch one action through the engine's current ``perform_action`` hook.

    Resolved lazily on every call so tests (and embedders) can simply assign
    ``engine.perform_action = fake`` -- including after the runner was built.
    ``getattr`` with a default falls back to the module-level
    :func:`perform_action` (real pynput synthesis) when the class-level hook
    is left as ``None``; the lookup happens at call time so monkeypatching
    this module's ``perform_action`` attribute also takes effect.
    """
    perform = getattr(engine, "perform_action", None) or perform_action
    perform(action, cancel=cancel)


class MacroEngine:
    """Runs macros and ad-hoc action lists in background threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Instance-level test/embedder hook (see class docstring above):
        # ``None`` = real pynput synthesis.
        self.perform_action: Callable[..., None] | None = None
        self._tokens: set[ExecutionToken] = set()
        self._macro_tokens: dict[str, ExecutionToken] = {}
        # Per-macro session counters (loops completed, last block reason);
        # keyed by uid so renames while running do not orphan the stats.
        self._stats: dict[str, MacroStats] = {}
        self._hotkeys = HotkeyManager()
        self._settings = AppSettings()
        self._monitor_started = False
        # Guards one-time creation of the monitor thread (start_macro can be
        # called from any thread -- GUI, hotkey listener, tests).
        self._monitor_lock = threading.Lock()
        # Woken whenever a macro starts/stops so the monitor can park in the
        # idle case instead of busy-looping at 50 ms forever.
        self._work_available = threading.Event()
        # One shared global input listener, started on demand while any
        # running token watches user-input conditions (see sync_input_watch).
        self._input_recorder: InputRecorder | None = None
        self.on_state_changed: Callable[[], None] | None = None  # called after start/stop events
        # The worker pass itself is delegated to MacroRunner; the perform hook
        # goes through _resolve_perform so late monkeypatching keeps working.
        self._runner = MacroRunner(self, lambda action, cancel=None: _resolve_perform(self, action, cancel))

    # ------------------------------------------------------------------
    # Collaborator hooks (read by MacroRunner via duck typing)
    # ------------------------------------------------------------------
    @property
    def lock(self) -> threading.Lock:
        """Engine lock guarding the token set and stats (runner writes loops)."""
        return self._lock

    @property
    def stats(self) -> dict[str, MacroStats]:
        """uid -> session counters map (runner writes loop counts here)."""
        return self._stats

    # ------------------------------------------------------------------
    # Settings & hotkeys
    # ------------------------------------------------------------------
    @property
    def settings(self) -> AppSettings:
        """The most recently applied settings snapshot."""
        with self._lock:
            return self._settings

    def apply_settings(self, settings: AppSettings) -> None:
        """Store the latest settings and re-register macro hotkeys."""
        with self._lock:
            self._settings = settings
            self._sync_hotkeys(settings)

    def _sync_hotkeys(self, settings: AppSettings) -> None:
        """Re-register all global hotkeys (stop key + one per *enabled* macro)."""
        bindings: list[tuple[str, object]] = [(settings.stop_hotkey, self.stop_all)]

        seen: set[str] = {settings.stop_hotkey.strip().lower()}
        for macro in settings.macros:
            if not macro.enabled:
                continue  # disabled macros never claim their hotkey
            key = macro.start_hotkey.strip().lower()
            if not key:
                continue
            if key in seen:
                logger.warning("Duplicate hotkey '%s' ignored for macro '%s'", key, macro.name)
                continue
            seen.add(key)
            bindings.append((key, self._make_macro_launcher(macro.name)))

        self._hotkeys.set_hotkeys(bindings)  # type: ignore[arg-type]

    def _make_macro_launcher(self, macro_name: str):
        """Return a callback that starts the named macro with the start delay."""

        def launch() -> None:
            macro = next((m for m in self._settings.macros if m.name == macro_name), None)
            if macro is None or not macro.enabled:
                return
            delay_s = self._settings.execution_delay_ms / 1000.0
            self.start_macro(macro, initial_delay_s=delay_s)

        return launch

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def start_macro(
        self,
        macro: MacroConfig,
        initial_delay_s: float = 0.0,
    ) -> ExecutionToken | None:
        """Start a macro in a worker thread. Returns the token or None if invalid.

        The macro runs immediately; any holding is done by the ``stop_trigger`` /
        ``start_trigger`` steps inside its action list (see :class:`MacroEngine`).
        """
        if not macro.actions:
            logger.info("Macro '%s' has no actions", macro.name)
            return None
        if not macro.enabled:
            logger.info("Macro '%s' is disabled; ignoring start request", macro.name)
            return None
        for action in macro.actions:
            if action.kind in ("wait_for", "stop_trigger", "start_trigger") and (
                action.condition is None or not action.condition.configured
            ):
                logger.warning("Macro '%s': %s action has no usable condition", macro.name, action.kind)
        # The background monitor is what makes stop_trigger/start_trigger rules fire at
        # all.  Start it lazily here so *every* entry point (GUI button, global
        # hotkey, programmatic use) gets screen watching -- previously only the
        # main-window constructor started it, leaving hotkey-triggered macros in
        # headless/embedded setups blocked forever.
        self.start_monitor()
        token = ExecutionToken(macro)
        thread = threading.Thread(
            target=self._run_macro,
            args=(macro, token, initial_delay_s),
            name=f"macro-{macro.name}",
            daemon=True,
        )
        with self._lock:
            existing = self._macro_tokens.get(macro.name)
            if existing is not None and existing in self._tokens and not existing.cancelled:
                logger.info("Macro '%s' is already running; ignoring duplicate start", macro.name)
                return existing
            self._tokens.add(token)
            self._macro_tokens[macro.name] = token
            stats = MacroStats()
            stats.last_event = "started"
            self._stats[macro.uid or macro.name] = stats
        # Wake the (possibly parked) monitor thread so it starts watching this
        # macro's rules without waiting for its bounded idle timeout.
        self._work_available.set()
        thread.start()
        self._notify()
        return token

    def stop_all(self) -> None:
        """Cancel every running sequence (safe to call from any thread)."""
        with self._lock:
            tokens = list(self._tokens)
        for token in tokens:
            token.cancel()
        logger.info("Stop requested: %d sequence(s) cancelled", len(tokens))
        self._notify()

    def stop_macro(self, name: str) -> bool:
        """Cancel one named macro's running sequence.

        Returns True when a running instance was found and stopped, False
        when that macro is not currently running.  This stops the *macro*,
        which is the only state distinction the engine makes -- individual
        actions are started/stopped by the ``stop_trigger``/``start_trigger``
        steps inside the action list, never by a user-facing pause switch.
        """
        with self._lock:
            token = self._macro_tokens.get(name)
            if token is None or token not in self._tokens:
                return False
        token.cancel()
        logger.info("Macro '%s' stopped", name)
        self._notify()
        return True

    def shutdown(self) -> None:
        """Stop everything and unregister global hotkeys (on app exit)."""
        self.stop_all()
        # Wake the monitor thread so it observes cancellation and exits
        # instead of lingering in a parked wait until process teardown.
        self._work_available.set()
        if self._input_recorder is not None:
            self._input_recorder.stop()
            self._input_recorder = None
        self._hotkeys.stop()

    # ------------------------------------------------------------------
    # Running macros (introspection for the UI)
    # ------------------------------------------------------------------
    def running_macros(self) -> dict[str, ExecutionToken]:
        """Map of macro name -> live token for every running macro."""
        with self._lock:
            return {
                name: token
                for name, token in self._macro_tokens.items()
                if token in self._tokens and not token.cancelled
            }

    def is_macro_running(self, name: str) -> bool:
        return name in self.running_macros()

    def macro_stats(self, macro: MacroConfig) -> dict[str, object]:
        """Session counters for *macro*'s current-or-last run (UI/debug aid).

        Returns ``{"running": bool, "loops": int, "blocked_by": str|None,
        "last_event": str}`` -- or all-zero defaults if the macro never ran
        since launch.  ``blocked_by`` names the trigger rule that currently
        holds the macro's actions back; it is the answer to "why did my
        macro silently stop firing?".
        """
        key = macro.uid or macro.name
        with self._lock:
            stats = self._stats.get(key)
            if stats is None:
                return {"running": False, "loops": 0, "blocked_by": None, "last_event": ""}
            running = any(
                token.macro is not None
                and (token.macro.uid or token.macro.name) == key
                and token in self._tokens
                and not token.cancelled
                for token in self._tokens
            )
            return {
                "running": running,
                "loops": stats.loops,
                "blocked_by": stats.blocked_by,
                "last_event": stats.last_event,
            }

    def record_block_reason(self, token: ExecutionToken, reason: str | None) -> None:
        """Store which rule currently blocks *token* (engine-lock protected).

        Public because the worker (MacroRunner) reports through it; formerly
        the private ``_record_block_reason``.
        """
        macro = token.macro
        if macro is None:
            return
        with self._lock:
            stats = self._stats.get(macro.uid or macro.name)
            if stats is not None:
                stats.blocked_by = reason

    # Backwards-compatible alias for the former private name.
    _record_block_reason = record_block_reason

    def _finish(self, token: ExecutionToken, macro_name: str | None = None) -> None:
        with self._lock:
            self._tokens.discard(token)
            if macro_name is not None and self._macro_tokens.get(macro_name) is token:
                del self._macro_tokens[macro_name]
        # The last run watching user input may have ended -- drop the global
        # listener so we never keep recording keys nobody waits for.
        self.sync_input_watch()
        self._notify()

    # ------------------------------------------------------------------
    # User-input watch (physical key/mouse events feed input-source rules)
    # ------------------------------------------------------------------
    def _token_needs_input_watch(self, token: ExecutionToken) -> bool:
        """True when *token* has armed rules that watch physical user input."""
        with token.rules_lock:
            for entry in token.blocked_rules:
                condition = entry.get("condition")
                if condition is not None and condition.source == "input":
                    return True
        return False

    def _any_token_needs_input_watch(self) -> bool:
        with self._lock:
            tokens = list(self._tokens)
        return any(
            not t.cancelled and self._token_needs_input_watch(t) for t in tokens
        )

    def sync_input_watch(self) -> None:
        """Start/stop the single shared InputRecorder to match demand.

        Called whenever trigger rules are armed/disarmed and when runs end;
        keeps exactly one global keyboard/mouse listener alive while at least
        one running macro waits on a user-input event.
        """
        needed = self._any_token_needs_input_watch()
        recorder = self._input_recorder
        if needed and (recorder is None or not recorder.running):
            if recorder is None:
                recorder = InputRecorder(
                    on_press=self._on_user_key,
                    on_button_press=self._on_user_button,
                    on_scroll=self._on_user_scroll,
                )
                self._input_recorder = recorder
            try:
                recorder.start()
            except Exception:  # pragma: no cover - platform without listeners
                logger.exception("Could not start the user-input watcher")
        elif not needed and recorder is not None and recorder.running:
            recorder.stop()

    # Alias kept for the previous private name.
    _sync_input_watch = sync_input_watch

    def _bump_matching_tokens(self, event: str) -> None:
        """Record *event* (``key:<name>`` / ``mouse:<button>`` / ``wheel``).

        Bumps the per-run counter only on tokens that currently have an armed
        input rule waiting for exactly this event, so unrelated keystrokes
        never influence any macro.
        """
        with self._lock:
            tokens = list(self._tokens)
        touched = False
        for token in tokens:
            if token.cancelled:
                continue
            with token.rules_lock:
                watched = any(
                    entry.get("condition") is not None
                    and entry["condition"].source == "input"
                    and entry["condition"].event == event
                    for entry in token.blocked_rules
                )
            if watched:
                token.bump_event(event)
                touched = True
        if touched:
            self._work_available.set()  # let the monitor react immediately

    def _on_user_key(self, name: str) -> None:
        self._bump_matching_tokens(f"key:{name}")

    def _on_user_button(self, button: str) -> None:
        self._bump_matching_tokens(f"mouse:{button}")

    def _on_user_scroll(self, amount: int) -> None:
        del amount  # any scroll counts as the "wheel" event
        self._bump_matching_tokens("wheel")

    def _notify(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()

    def _has_work(self) -> bool:
        """Cheap idle check used by the monitor loop (no dict copy)."""
        with self._lock:
            return bool(self._tokens)

    # ------------------------------------------------------------------
    # Worker bodies
    # ------------------------------------------------------------------
    def _run_macro(self, macro: MacroConfig, token: ExecutionToken, initial_delay_s: float) -> None:
        try:
            if initial_delay_s > 0:
                logger.info("Macro '%s' starts in %.1fs", macro.name, initial_delay_s)
                token.wait(initial_delay_s)
            # Looping is an *action*, not a general macro option: exactly one
            # pass over the action list runs, and any ``loop_start`` /
            # ``loop_end`` pair inside it repeats the steps between them.
            # (Legacy repeat/loops/interval_ms values were folded into a
            # matching loop pair at load time -- see persistence.settings_store.)
            self._runner.run_actions(macro.actions, token)
            if not token.cancelled:
                logger.info("Macro '%s' finished", macro.name)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Macro '%s' crashed", macro.name)
        finally:
            token.cancel()
            self._finish(token, macro.name)

    # ------------------------------------------------------------------
    # Screen-rule monitor (automatically watches for stop-trigger/start-trigger triggers)
    # ------------------------------------------------------------------
    def start_monitor(self) -> None:
        """Launch the background thread that polls trigger rules (once).

        Whenever a running macro's action list contains ``stop_trigger``/``start_trigger``
        steps, their screen rules are watched here automatically -- no
        separate watcher configuration is needed.  The monitor samples each
        armed rule at its own ``poll_interval_ms`` and flips the matching
        actions between triggered on and off as the rule comes and goes.
        """
        if self._monitor_started:
            return
        with self._monitor_lock:
            if self._monitor_started:
                return
            self._monitor_started = True
            threading.Thread(
                target=self._monitor_loop, name="macro-condition-monitor", daemon=True
            ).start()

    def _monitor_loop(self) -> None:
        """Watch every running macro's armed trigger rules.

        Screen rules are sampled live; input/action rules are checked against
        the per-run event counters (bumped by the input listener and by the
        worker when actions execute).  Parks on ``_work_available`` while no
        macro is running instead of busy-sleeping at 50 ms forever; start/stop
        events wake it promptly, and the bounded wait covers any path that
        forgets to signal.
        """
        last_notify = 0.0
        while True:
            if not self._has_work():
                self._work_available.wait(1.0)
                self._work_available.clear()
                continue
            changed = False
            for token in self.running_macros().values():
                if token.cancelled:
                    continue
                if condition_monitor.monitor_token(token, self._record_token_block):
                    changed = True
            # Keep the shared input listener in step with demand: rules may
            # have been disarmed by the pass above without going through
            # the runner's trigger sync.
            self.sync_input_watch()
            if changed and time.monotonic() - last_notify > 0.4:
                last_notify = time.monotonic()
                self._notify()  # let the UI refresh its status line
            self._work_available.wait(0.05)
            self._work_available.clear()

    def _record_token_block(self, token: ExecutionToken) -> None:
        """Publish the rule that currently blocks *token* into its stats.

        ``record_block_reason`` re-acquires the engine lock, so this must run
        *outside* ``token.rules_lock`` (lock ordering: the engine lock is
        never held while taking a token's rules lock).
        """
        if token.macro is None:
            return
        reason = MacroRunner._first_blocking_reason(token)
        self.record_block_reason(token, reason)

    @staticmethod
    def _evaluate_once(condition: ScreenCondition) -> bool:
        """One synchronous screen check; False on any failure."""
        return condition_monitor.evaluate_once(condition)


__all__ = [
    "ConditionRuntime",
    "ExecutionToken",
    "MacroEngine",
    "MacroStats",
]
