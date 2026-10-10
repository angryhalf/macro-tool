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
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from app.conditions import ConditionRuntime, ScreenCondition
from app.settings import ActionConfig, AppSettings, MacroConfig
from services.input import HotkeyManager, InputRecorder, perform_action

logger = logging.getLogger(__name__)


class ExecutionToken:
    """Per-run bookkeeping for one running sequence.

    Holds cooperative **cancellation** (user/emergency stop), the armed
    trigger rules from the ``stop_trigger``/``start_trigger`` steps reached
    so far, and the event counters backing non-screen conditions.  Each entry
    in :attr:`blocked_rules` has a ``mode``:

    * ``"while"`` -- block later actions while the rule holds.
    * ``"until"`` -- block until the rule holds once, then self-disarm.
    * ``"hold"``  -- bare marker: block until a bare ``start_trigger`` removes it.

    When any armed rule currently blocks, :attr:`gated` is True: normal
    actions call :meth:`wait_while_blocked` before firing, but the flow steps
    themselves never halt, so a later ``start_trigger`` can re-arm the tail.
    """

    def __init__(self, macro: MacroConfig | None = None) -> None:
        self.macro = macro  # identity (name/uid) for stats reporting; may be None
        self._cancelled = threading.Event()
        self._blocked = threading.Event()  # set => later actions are triggered off
        # Guards ``blocked_rules``: the worker thread and the background
        # monitor both mutate the list, so every read-modify-write of it
        # happens while holding this lock (prevents lost updates).
        self.rules_lock = threading.Lock()
        # Rules armed by the ``stop_trigger``/``start_trigger`` steps reached so far.
        # Each entry: {"mode": "while"|"until"|"hold", "condition": ...}
        self.blocked_rules: list[dict] = []
        #: Monotonic per-run counters keyed by ``action:<kind>`` /
        #: ``key:<name>`` / ``mouse:<button>`` / ``wheel``.  Incremented by
        #: the worker when an action executes and by the input watcher when
        #: the user physically performs an input; the monitor compares each
        #: non-screen rule's stored baseline against the current value to
        #: decide whether the event happened since the rule was armed.
        self.event_counts: dict[str, int] = {}
        self._events_lock = threading.Lock()
        #: Set while at least one running token watches user-input events so
        #: the engine keeps exactly one global listener alive.
        self.needs_input_watch = False

    def bump_event(self, key: str) -> None:
        """Record that the event *key* (e.g. ``"key:a"``) just happened."""
        with self._events_lock:
            self.event_counts[key] = self.event_counts.get(key, 0) + 1

    def event_count(self, key: str) -> int:
        with self._events_lock:
            return self.event_counts.get(key, 0)

    # -- cancellation --------------------------------------------------
    def cancel(self) -> None:
        self._cancelled.set()
        # Never leave a blocked sequence hanging after cancellation.
        self._blocked.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- trigger state (set by the stop-trigger/start-trigger actions) ---------------
    def refresh_block(self) -> None:
        """Recompute whether later actions are triggered off from the rules.

        Called by the execution thread whenever the trigger set changes and
        periodically by the background monitor as screen rules come and go.
        """
        blocking = any(entry.get("_blocking") for entry in self.blocked_rules)
        if blocking:
            self._blocked.set()
        else:
            self._blocked.clear()

    @property
    def gated(self) -> bool:
        """True while a rule holds later actions back (worker + monitor read this)."""
        return self._blocked.is_set()

    def wait_while_blocked(self) -> None:
        """Block while later actions are triggered off; returns when they
        are triggered on again (or the sequence is cancelled)."""
        while self._blocked.is_set() and not self._cancelled.is_set():
            self._cancelled.wait(0.05)

    def wait(self, seconds: float) -> None:
        """Interruptible wait used for delays and polling cadence."""
        self._cancelled.wait(seconds)


class MacroStats:
    """Mutable per-macro session counters for debuggability.

    The engine's failure mode is a macro that *silently stops firing* (a
    trigger rule holding everything back), so each run records which rule
    last blocked it and how many loops completed.  Read via
    :meth:`MacroEngine.macro_stats`; guarded by the engine lock.
    """

    __slots__ = ("loops", "blocked_by", "last_event")

    def __init__(self) -> None:
        self.loops = 0
        self.blocked_by: str | None = None
        self.last_event: str = ""


class MacroEngine:
    """Runs macros and ad-hoc action lists in background threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
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
        # running token watches user-input conditions (see _sync_input_watch).
        self._input_recorder: InputRecorder | None = None
        self.on_state_changed: Callable[[], None] | None = None  # called after start/stop events

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

    def _record_block_reason(self, token: ExecutionToken, reason: str | None) -> None:
        """Store which rule currently blocks *token* (engine-lock protected)."""
        macro = token.macro
        if macro is None:
            return
        with self._lock:
            stats = self._stats.get(macro.uid or macro.name)
            if stats is not None:
                stats.blocked_by = reason

    def _finish(self, token: ExecutionToken, macro_name: str | None = None) -> None:
        with self._lock:
            self._tokens.discard(token)
            if macro_name is not None and self._macro_tokens.get(macro_name) is token:
                del self._macro_tokens[macro_name]
        # The last run watching user input may have ended -- drop the global
        # listener so we never keep recording keys nobody waits for.
        self._sync_input_watch()
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

    def _sync_input_watch(self) -> None:
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
            # matching loop pair at load time -- see app.settings.)
            self._run_actions(macro.actions, token)
            if not token.cancelled:
                logger.info("Macro '%s' finished", macro.name)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Macro '%s' crashed", macro.name)
        finally:
            token.cancel()
            self._finish(token, macro.name)

    def _run_actions(self, actions: tuple[ActionConfig, ...], token: ExecutionToken) -> bool:
        """Execute one pass of *actions*; False when the pass ended early.

        A ``stop_trigger`` with a rule arms it (later actions held while the
        rule holds); a ``start_trigger`` with a rule holds later actions
        until the rule fires once -- together they gate the stretch between
        them like an if/else.  Rules can watch the screen, physical user
        input or other actions executing.  Bare markers hold/release the
        stretch between them.  ``loop_start``/``loop_end`` repeat the steps
        between them.  Flow steps never block themselves -- normal actions
        call :meth:`ExecutionToken.wait_while_blocked` before firing.
        """
        loop_frames = self._match_loop_markers(actions)
        index = 0
        total = len(actions)
        while index < total:
            if token.cancelled:
                return False
            action = actions[index]
            kind = action.kind
            condition = action.condition
            has_rule = condition is not None and condition.configured
            if kind not in ("loop_start", "loop_end", "wait_for", "stop_trigger", "start_trigger"):
                # Normal action: fires only while no trigger rule holds it back.
                # Flow markers (wait_for / stop_trigger / start_trigger) are
                # never held back by the very rules they arm or close -- a
                # marker blocked by its own rule would deadlock the run.
                token.wait_while_blocked()
                if token.cancelled:
                    return False
            if kind == "wait_for":
                if not self._await_condition(condition, token):
                    return False
            elif kind == "loop_end":
                # Reached from inside the loop body (or a stray marker).
                entry = self._top_loop_frame(loop_frames, index)
                if entry is None:
                    logger.warning("Unmatched loop_end at step %d/%d; ignoring", index + 1, total)
                    index += 1
                    continue
                entry["completed"] = entry.get("completed", 0) + 1
                iterations = entry["iterations"]
                if iterations and entry["completed"] >= iterations:
                    logger.info(
                        "Loop %d/%d completed at step %d/%d, falling through",
                        entry["completed"], iterations, index + 1, total,
                    )
                    if token.macro is not None:
                        with self._lock:
                            stats = self._stats.get(token.macro.uid or token.macro.name)
                            if stats is not None:
                                stats.loops = entry["completed"]
                    index += 1  # exhausted: leave the loop, run on past it
                else:
                    if action.duration_ms > 0:
                        token.wait(action.duration_ms / 1000.0)
                    if token.cancelled:
                        return False
                    # Restarting this frame re-enters its whole body, so any
                    # inner loop nested inside it starts over as well -- a
                    # stale "completed" count would make the inner loop fall
                    # through on later outer passes instead of repeating.
                    for inner in loop_frames:
                        if entry["start"] < inner["start"] < index and inner is not entry:
                            inner.pop("completed", None)
                    index = entry["start"] + 1  # jump back into the loop body
                continue
            elif kind == "loop_start":
                # Marker only: the frame was pre-paired in ``stack``; the
                # body between this step and its loop_end just runs on.
                pass
            elif kind == "stop_trigger":
                # Trigger this step's rule(s) off for everything after it.
                with token.rules_lock:
                    if has_rule:
                        assert condition is not None
                        entry = {
                            "mode": "while", "condition": condition, "_runtime": None,
                            "_blocking": False, "_token": token,
                        }
                        if condition.source in ("input", "action"):
                            # Baseline the counter at arming so only events
                            # happening *from now on* gate later actions --
                            # a click that ran before this marker must not
                            # instantly satisfy an ``action:click`` rule.
                            entry["_baseline"] = token.event_count(condition.event)
                        token.blocked_rules.append(entry)
                        message = (
                            "Stop trigger armed at step %d/%d: actions after this point are "
                            "triggered off while '%s' holds"
                        )
                        args: tuple = (index + 1, total, condition.description)
                    else:
                        # Bare marker: hold everything after it until the next
                        # bare start_trigger releases the hold.
                        token.blocked_rules.append({"mode": "hold", "condition": None, "_blocking": True})
                        message = (
                            "Trigger stretch opened at step %d/%d: actions hold until the "
                            "next start-trigger step"
                        )
                        args = (index + 1, total)
                logger.info(message, *args)
                self._sync_triggers(token)
            elif kind == "start_trigger":
                if has_rule:
                    # Trigger on only after this rule fires once.
                    assert condition is not None
                    entry = {
                        "mode": "until", "condition": condition, "_runtime": None,
                        "_blocking": True, "_token": token,
                    }
                    if condition.source in ("input", "action"):
                        # Only events happening *after* arming release the tail.
                        entry["_baseline"] = token.event_count(condition.event)
                    with token.rules_lock:
                        token.blocked_rules.append(entry)
                    logger.info(
                        "Start trigger armed at step %d/%d: actions after this point "
                        "stay triggered off until '%s' fires",
                        index + 1, total, condition.description,
                    )
                else:
                    # Close the current stretch: drop every hold/until entry
                    # *and* any stop-trigger rule that gated the stretch, so
                    # the actions written after this marker run freely again.
                    with token.rules_lock:
                        token.blocked_rules = []
                    token.refresh_block()
                    logger.info("Trigger stretch closed at step %d/%d", index + 1, total)
                self._sync_triggers(token)
            else:
                token.wait_while_blocked()  # don't fire while triggered off
                if token.cancelled:
                    return False
                # Pass the cancellation predicate so long actions (key holds,
                # typing runs, waits) abort promptly instead of blocking the
                # emergency stop for their full duration.
                perform_action(action, cancel=lambda: token.cancelled or token.gated)
                # Record the execution so ``action:<kind>`` rules armed by
                # stop/start triggers can react to it.
                token.bump_event(f"action:{kind}")
            index += 1
        return True

    @staticmethod
    def _match_loop_markers(actions: tuple[ActionConfig, ...]) -> list[dict]:
        """Pre-pair every ``loop_start`` with its matching ``loop_end``.

        Returns a stack of loop frames (outermost first) plus a sentinel
        frame covering the whole list; :meth:`_top_loop_frame` peels the
        innermost frame whose body contains the current index.  Unpaired
        markers degrade gracefully (a stray ``loop_end`` is ignored, a stray
        ``loop_start`` simply runs as a no-op marker).
        """
        pending: list[int] = []
        pairs: dict[int, int] = {}
        for i, action in enumerate(actions):
            if action.kind == "loop_start":
                pending.append(i)
            elif action.kind == "loop_end" and pending:
                pairs[pending.pop()] = i
        sentinel = {"start": -1, "end": len(actions), "iterations": 0}
        stack = [sentinel]
        # Push outermost-first so popping at runtime yields innermost first.
        for start in sorted(pairs):
            iterations = max(0, actions[start].amount)
            stack.append({"start": start, "end": pairs[start], "iterations": iterations})
        return stack

    @staticmethod
    def _top_loop_frame(stack: list[dict], index: int) -> dict | None:
        """Return the innermost *still-open* loop frame containing *index*.

        Frames stay on the stack for their whole life (iteration state lives
        on the dict itself), so a finished inner frame sits above the outer
        one that is still looping.  Scan from the top down and skip frames
        whose iteration budget is exhausted or whose body ended before this
        ``loop_end``; only such frames can nest *above* an open one, so the
        first match is the correct innermost target.  Returns None when only
        the sentinel covers the index (a stray ``loop_end``).
        """
        for entry in reversed(stack[1:]):
            if entry["start"] < index <= entry["end"]:
                iterations = entry["iterations"]
                completed = entry.get("completed", 0)
                if iterations and completed >= iterations:
                    continue  # exhausted earlier -- look one level out
                return entry
        return None

    def _sync_triggers(self, token: ExecutionToken) -> None:
        """Reflect the *current* state of the armed rules right away.

        Also (re)starts or stops the shared user-input listener so physical
        key/mouse events reach exactly the macros waiting on them.
        """
        self._apply_rule_states(token)
        token.refresh_block()
        self._record_token_block(token)
        self._sync_input_watch()

    def _apply_rule_states(self, token: ExecutionToken) -> None:
        """Re-evaluate every armed rule and store its blocking state."""
        with token.rules_lock:
            for entry in token.blocked_rules:
                entry["_blocking"] = self._rule_blocks_now(entry)

    @staticmethod
    def _first_blocking_reason(token: ExecutionToken) -> str | None:
        """Human-readable name of the rule currently holding actions back."""
        with token.rules_lock:
            for entry in token.blocked_rules:
                if not entry.get("_blocking"):
                    continue
                condition = entry.get("condition")
                mode = entry.get("mode", "?")
                return condition.description if condition is not None else f"{mode} hold"
        return None

    def _record_token_block(self, token: ExecutionToken) -> None:
        """Publish the rule that currently blocks *token* into its stats.

        ``_record_block_reason`` re-acquires the engine lock, so this must run
        *outside* ``token.rules_lock`` (lock ordering: the engine lock is
        never held while taking a token's rules lock).
        """
        if token.macro is None:
            return
        reason = self._first_blocking_reason(token)
        self._record_block_reason(token, reason)

    @staticmethod
    def _evaluate_once(condition: ScreenCondition) -> bool:
        """One synchronous screen check; False on any failure."""
        runtime = ConditionRuntime.create(condition)
        try:
            return bool(runtime.evaluate())
        except Exception:  # pragma: no cover - transient capture failures
            logger.exception("Screen-condition check failed (%s)", condition.description)
            return False

    # ------------------------------------------------------------------
    # Condition helpers
    # ------------------------------------------------------------------
    def _await_condition(self, condition: ScreenCondition | None, token: ExecutionToken) -> bool:
        """Block until *condition* holds; False when cancelled or timed out."""
        if condition is None or not condition.configured:
            logger.warning("wait_for action without a usable condition; skipping wait")
            return True
        runtime = ConditionRuntime.create(condition, timeout_s=condition.timeout_ms / 1000.0)
        interval_s = max(condition.poll_interval_ms, 10) / 1000.0
        logger.info("Waiting for condition: %s", condition.description)
        while not token.cancelled:
            try:
                if runtime.evaluate():
                    logger.info("Condition met, continuing: %s", condition.description)
                    return True
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Condition check failed (%s)", condition.description)
            if runtime.expired():
                logger.info("Condition timeout reached, giving up: %s", condition.description)
                return False
            token.wait(interval_s)
        return False

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
                if self._monitor_token(token):
                    changed = True
            # Keep the shared input listener in step with demand: rules may
            # have been disarmed by the pass above without going through
            # _sync_triggers.
            self._sync_input_watch()
            if changed and time.monotonic() - last_notify > 0.4:
                last_notify = time.monotonic()
                self._notify()  # let the UI refresh its status line
            self._work_available.wait(0.05)
            self._work_available.clear()

    @staticmethod
    def _rule_blocks_now(entry: dict) -> bool:
        """Whether one armed trigger rule currently blocks later actions.

        ``while`` blocks while the condition holds; ``until`` blocks until it
        holds once (then self-disarms); ``hold`` always blocks until a bare
        start-trigger step drops it.  Screen rules are sampled live; input
        rules hold until the user physically performs the watched event and
        action rules until another action of the watched kind executes --
        both are detected by comparing the per-run event counter against the
        baseline stored when the rule was armed.
        """
        mode = entry.get("mode")
        if mode == "hold":
            return True
        condition = entry.get("condition")
        if condition is None or not condition.configured:
            return False
        source = condition.source
        if source in ("input", "action"):
            holding = MacroEngine._event_rule_fired(entry, condition)
        else:
            runtime = entry.get("_runtime")
            if runtime is None:
                runtime = ConditionRuntime.create(condition)
                entry["_runtime"] = runtime
            try:
                holding = bool(runtime.evaluate())
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Screen-condition check failed (%s)", condition.description)
                return bool(entry.get("_blocking"))
        if mode == "while":
            return holding
        # mode == "until": met once => trigger on and disarm this rule.
        if holding:
            logger.info("Start-trigger condition met, triggering actions on: %s", condition.description)
            entry["disarm"] = True
            return False
        return True

    @staticmethod
    def _event_rule_fired(entry: dict, condition: ScreenCondition) -> bool:
        """True when the watched input/action event happened since arming.

        Arming stores the current counter value as a baseline; the rule
        "holds" as soon as the counter grows past it.  ``while`` rules clear
        again immediately after being consumed so they only gate while the
        most recent event matches; ``until`` rules latch via the caller's
        disarm logic.
        """
        token = entry.get("_token")
        if token is None:
            return False
        key = condition.event
        count = token.event_count(key)
        baseline = entry.get("_baseline")
        if baseline is None:
            entry["_baseline"] = count
            return False
        if count > baseline:
            entry["_baseline"] = count  # consume the event
            return True
        return False

    def _monitor_token(self, token: ExecutionToken) -> bool:
        """Re-evaluate one macro's armed rules; True when triggers flipped.

        This is the automatic screen watching: every rule a running macro's
        ``stop_trigger``/``start_trigger`` steps left armed gets sampled here, and the
        blocked/on state of the actions after them follows the screen.  When
        an ``until`` rule has been met it is removed from the set entirely.
        """
        with token.rules_lock:
            if not token.blocked_rules:
                # Nothing to watch -- make sure nothing stale is left blocking.
                was_blocked = token.gated
                token.refresh_block()
                return was_blocked
            for entry in token.blocked_rules:
                entry["_blocking"] = self._rule_blocks_now(entry)
            token.blocked_rules = [e for e in token.blocked_rules if not e.get("disarm")]
        before = token.gated
        token.refresh_block()
        now = token.gated
        # Keep the UI's "blocked_by" stat current even when the overall
        # gated flag did not flip (e.g. rule A released, rule B took over).
        self._record_token_block(token)
        if before != now:
            if now:
                logger.info("Actions triggered off by screen rule")
            else:
                logger.info("Actions triggered on again")
            return True
        return False
