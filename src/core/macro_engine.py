"""Core macro execution engine.

The :class:`MacroEngine` runs action sequences on a worker thread, supports
looping/repeat, hotkey-triggered starts and an emergency stop that works from
any thread (GUI button or global hotkey).

There is *no* macro-level paused state anywhere in this engine -- a macro is
only ever *running* or *stopped*, and the UI exposes just Run/Stop.
``stop_trigger`` and ``start_trigger`` are ordinary **actions** in the
sequence that trigger or untrigger other actions:

* **``wait_for``** -- block until its screen condition holds on screen (an
  optional timeout on the condition gives up and finishes the macro).
* **``stop_trigger <rule>``** -- *triggers off* every action written after it while
  the rule holds on screen.  The screen is watched automatically by the
  background monitor; nothing blocks the sequence itself.
* **``start_trigger <rule>``** -- *triggers on* every action written after it as
  soon as the rule holds on screen.
* Bare ``stop_trigger`` / ``start_trigger`` markers (no rule) open/close a gated stretch:
  actions between them run only while the earlier ``stop_trigger`` rule is absent,
  and a bare ``start_trigger`` simply releases the hold opened by the last
  ``stop_trigger``.

These triggers are per-action flags, not a macro state: the sequence always
keeps advancing through its steps, and each normal action checks its own
trigger flag right before firing.  When several ``stop_trigger`` rules are armed at
once, any one of them holding keeps the later actions triggered off.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from app.conditions import ConditionRuntime, ScreenCondition
from app.settings import ActionConfig, AppSettings, MacroConfig
from services.input import HotkeyManager, perform_action

logger = logging.getLogger(__name__)


class ExecutionToken:
    """Per-run bookkeeping for one running sequence.

    A macro with a token like this is simply *running*; there is no paused
    flag or any other macro-level state a user could toggle.  The token holds
    two things:

    * Cooperative **cancellation** (user stop / emergency stop) -- final.
    * The current **trigger setting** for the actions that follow in the
      sequence, i.e. what the most recent ``stop_trigger``/``start_trigger`` steps did:

      - ``blocked_rules``: the ``stop_trigger`` rules currently armed.  Each entry
        has a ``mode`` (``"while"`` -- block later actions while the rule
        holds on screen; ``"until"`` -- keep later actions blocked until the
        rule holds once, then disarm; ``"hold"`` -- a bare ``stop_trigger`` marker,
        blocked until a bare ``start_trigger`` removes it).  The engine's
        background monitor watches the screen for these rules automatically.
      - ``stretch_open``: True between a bare ``stop_trigger`` and its ``start_trigger``,
        meaning the stretch's actions are tied to the enclosing ``stop_trigger``
        rule(s) instead of running freely.

    When any armed rule currently blocks, :attr:`triggered` is False: normal
    actions call :meth:`wait_while_blocked` before firing and the whole
    sequence halts there -- but the flow steps themselves never halt, so a
    later ``start_trigger`` can always re-trigger the actions after it.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._blocked = threading.Event()  # set => later actions are triggered off
        # Guards ``blocked_rules``: the worker thread and the background
        # monitor both mutate the list, so every read-modify-write of it
        # happens while holding this lock (prevents lost updates).
        self.rules_lock = threading.Lock()
        # Rules armed by the ``stop_trigger``/``start_trigger`` steps reached so far.
        # Each entry: {"mode": "while"|"until"|"hold", "condition": ...}
        self.blocked_rules: list[dict] = []
        # True while inside a bare stop-trigger/start-trigger stretch (see docstring).
        self.stretch_open = False
        # The ``while`` rule of the currently open stop_trigger(rule) stretch,
        # if any; its rule is disarmed when the matching start_trigger closes it.
        self.stretch_rule: ScreenCondition | None = None

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
    def triggered(self) -> bool:
        """True when the actions following the last trigger step may fire."""
        return not self._blocked.is_set()

    @property
    def gated(self) -> bool:
        """Alias used by the UI: actions currently held back by a rule."""
        return self._blocked.is_set()

    def remove_rule(self, condition: "ScreenCondition") -> None:
        """Drop an armed rule (used when a stop-trigger stretch is closed)."""
        with self.rules_lock:
            self.blocked_rules = [e for e in self.blocked_rules if e.get("condition") is not condition]
        self.refresh_block()

    def wait_while_blocked(self) -> None:
        """Block while later actions are triggered off; returns when they
        are triggered on again (or the sequence is cancelled)."""
        while self._blocked.is_set() and not self._cancelled.is_set():
            self._cancelled.wait(0.05)

    def wait(self, seconds: float) -> None:
        """Plain interruptible wait used for polling cadence.

        Unlike :meth:`sleep` this ignores the trigger state -- it is used by
        loops that must keep evaluating screen conditions *while* the later
        actions are triggered off.
        """
        self._cancelled.wait(seconds)

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep: honours cancellation and the trigger state."""
        deadline = time.monotonic() + seconds
        while True:
            self.wait_while_blocked()
            if self._cancelled.is_set():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._cancelled.wait(min(remaining, 0.1))


class MacroEngine:
    """Runs macros and ad-hoc action lists in background threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: set[ExecutionToken] = set()
        self._macro_tokens: dict[str, ExecutionToken] = {}
        self._hotkeys = HotkeyManager()
        self._settings = AppSettings()
        self._monitor_started = False
        self.on_state_changed: Callable[[], None] | None = None  # called after start/stop events

    # ------------------------------------------------------------------
    # Settings & hotkeys
    # ------------------------------------------------------------------
    @property
    def settings(self) -> AppSettings:
        """The most recently applied settings snapshot."""
        return self._settings

    def apply_settings(self, settings: AppSettings) -> None:
        """Store the latest settings and re-register macro hotkeys."""
        with self._lock:
            self._settings = settings
            self._sync_hotkeys(settings)

    def _sync_hotkeys(self, settings: AppSettings) -> None:
        """Re-register all global hotkeys (stop key + one per macro)."""
        bindings: list[tuple[str, object]] = [(settings.stop_hotkey, self.stop_all)]

        seen: set[str] = {settings.stop_hotkey.strip().lower()}
        for macro in settings.macros:
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
            if macro is None:
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
        for action in macro.actions:
            if action.kind in ("wait_for", "stop_trigger", "start_trigger") and (
                action.condition is None or not action.condition.configured
            ):
                logger.warning("Macro '%s': %s action has no usable condition", macro.name, action.kind)
        token = ExecutionToken()
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
        thread.start()
        self._notify()
        return token

    def run_actions(self, actions: tuple[ActionConfig, ...], name: str = "actions") -> ExecutionToken:
        """Run a one-shot action list in the background."""
        token = ExecutionToken()
        thread = threading.Thread(target=self._run_actions, args=(actions, token), name=name, daemon=True)
        with self._lock:
            self._tokens.add(token)
        thread.start()
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
        self._hotkeys.stop()

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._tokens)

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

    def _finish(self, token: ExecutionToken, macro_name: str | None = None) -> None:
        with self._lock:
            self._tokens.discard(token)
            if macro_name is not None and self._macro_tokens.get(macro_name) is token:
                del self._macro_tokens[macro_name]
        self._notify()

    def _notify(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()

    # ------------------------------------------------------------------
    # Worker bodies
    # ------------------------------------------------------------------
    def _run_macro(self, macro: MacroConfig, token: ExecutionToken, initial_delay_s: float) -> None:
        try:
            if initial_delay_s > 0:
                logger.info("Macro '%s' starts in %.1fs", macro.name, initial_delay_s)
                token.sleep(initial_delay_s)
            loops = 0
            while not token.cancelled:
                if not self._run_actions(macro.actions, token):
                    break
                loops += 1
                # Loop boundary: whatever trigger rules are left armed from
                # the last pass no longer apply to the next one -- start it
                # with every action triggered on.
                with token.rules_lock:
                    token.blocked_rules = []
                token.stretch_open = False
                token.refresh_block()
                if not macro.repeat and loops >= max(1, macro.loops):
                    break
                if token.cancelled:
                    break
                if macro.interval_ms > 0:
                    token.sleep(macro.interval_ms / 1000.0)
            if not token.cancelled:
                logger.info("Macro '%s' finished (%d loop)", macro.name, loops)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Macro '%s' crashed", macro.name)
        finally:
            token.cancel()
            self._finish(token, macro.name)

    def _run_actions(self, actions: tuple[ActionConfig, ...], token: ExecutionToken) -> bool:
        """Execute one pass of *actions*; False when the pass ended early.

        ``stop_trigger``/``start_trigger`` are ordinary actions that trigger or untrigger
        the actions written after them; the background monitor watches the
        screen for their rules automatically:

        * A **stop_trigger** step with a rule arms it: every later action is
          triggered off while the rule holds on screen and re-triggered as
          soon as it clears.  A bare stop-trigger step (no rule) opens a stretch --
          later actions stay triggered off until the matching ``start_trigger``.
        * An **start_trigger** step with a rule keeps later actions triggered off
          until that rule holds on screen once, then triggers them on (and
          disarms its own rule).  A bare start-trigger step closes the current
          stretch: inside one, it hands the stretch's actions back to the
          enclosing ``stop_trigger`` rule(s); otherwise it simply releases any hold.

        The flow steps themselves are never blocked -- the sequence always
        advances through them, so a later ``start_trigger`` can re-trigger what an
        earlier ``stop_trigger`` held back.  Normal actions call
        :meth:`ExecutionToken.wait_while_blocked` first, which is where the
        triggering-off actually takes effect.
        """
        index = 0
        total = len(actions)
        while index < total:
            if token.cancelled:
                return False
            action = actions[index]
            kind = action.kind
            condition = action.condition
            has_rule = condition is not None and condition.configured
            if kind == "wait_for":
                if not self._await_condition(condition, token):
                    return False
            elif kind == "stop_trigger":
                # Trigger this step's rule(s) off for everything after it.
                if has_rule:
                    assert condition is not None
                    entry = {"mode": "while", "condition": condition, "_runtime": None, "_blocking": False}
                    with token.rules_lock:
                        token.blocked_rules.append(entry)
                    token.stretch_open = True
                    token.stretch_rule = condition
                    logger.info(
                        "Stop trigger armed at step %d/%d: actions after this point are "
                        "triggered off while '%s' is on screen",
                        index + 1, total, condition.description,
                    )
                else:
                    # Bare marker: hold everything after it until the next
                    # start_trigger closes the stretch.  The hold is scoped to
                    # this stretch only -- rules armed by earlier stop-trigger
                    # steps keep gating their own stretches, not this one.
                    with token.rules_lock:
                        token.blocked_rules.append({"mode": "hold", "condition": None, "_blocking": True})
                    token.stretch_open = True
                    token.stretch_rule = None
                    logger.info(
                        "Trigger stretch opened at step %d/%d: actions hold until the "
                        "next start-trigger step",
                        index + 1, total,
                    )
                self._sync_triggers(token)
            elif kind == "start_trigger":
                if has_rule:
                    # Trigger on only after this rule appears once on screen.
                    assert condition is not None
                    if self._evaluate_once(condition):
                        logger.info(
                            "Start-trigger condition already met at step %d/%d: actions "
                            "after this point run freely",
                            index + 1, total,
                        )
                    else:
                        with token.rules_lock:
                            token.blocked_rules.append(
                                {"mode": "until", "condition": condition, "_runtime": None, "_blocking": True}
                            )
                        logger.info(
                            "Start trigger armed at step %d/%d: actions after this point "
                            "stay triggered off until '%s' is on screen",
                            index + 1, total, condition.description,
                        )
                else:
                    # Close the current stretch: release any bare hold *and*
                    # disarm the stop-trigger rule that gated the stretch, so
                    # the actions written after this marker run freely again.
                    if token.stretch_open:
                        if token.stretch_rule is not None:
                            token.remove_rule(token.stretch_rule)
                            token.stretch_rule = None
                        token.stretch_open = False
                        logger.info("Trigger stretch closed at step %d/%d", index + 1, total)
                    with token.rules_lock:
                        # Any wait-for-start-trigger rule before this point is
                        # done: from here the following actions run freely again.
                        token.blocked_rules = [
                            e for e in token.blocked_rules if e["mode"] not in ("hold", "until")
                        ]
                    token.refresh_block()
                self._sync_triggers(token)
            else:
                token.wait_while_blocked()  # don't fire while triggered off
                if token.cancelled:
                    return False
                perform_action(action)
            index += 1
        return True

    @staticmethod
    def _sync_triggers(token: ExecutionToken) -> None:
        """Reflect the *current* screen state of the armed rules right away."""
        with token.rules_lock:
            for entry in token.blocked_rules:
                entry["_blocking"] = MacroEngine._rule_blocks_now(entry)
        token.refresh_block()

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
        self._monitor_started = True
        threading.Thread(target=self._monitor_loop, name="macro-condition-monitor", daemon=True).start()

    def _monitor_loop(self) -> None:
        """Watch the screen for every running macro's armed trigger rules."""
        last_notify = 0.0
        while True:
            changed = False
            for token in self.running_macros().values():
                if token.cancelled:
                    continue
                if self._monitor_token(token):
                    changed = True
            if changed and time.monotonic() - last_notify > 0.4:
                last_notify = time.monotonic()
                self._notify()  # let the UI refresh its status line
            time.sleep(0.05)

    @staticmethod
    def _rule_blocks_now(entry: dict) -> bool:
        """Whether one armed trigger rule currently blocks later actions.

        Takes one screenshot per rule (through the rule's own detector where
        needed) and applies the rule's mode:

        * ``while``  -- blocks while the condition holds on screen.
        * ``until``  -- blocks until the condition holds once; when it does,
          the rule disarms itself so later actions run freely.
        * ``hold``   -- a bare stop-trigger marker: always blocks until the sequence
          reaches the matching start-trigger step.
        """
        mode = entry.get("mode")
        if mode == "hold":
            return True
        condition = entry.get("condition")
        if condition is None or not condition.configured:
            return False
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
    def _monitor_token(token: ExecutionToken) -> bool:
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
                entry["_blocking"] = MacroEngine._rule_blocks_now(entry)
            token.blocked_rules = [e for e in token.blocked_rules if not e.get("disarm")]
        before = token.gated
        token.refresh_block()
        now = token.gated
        if before != now:
            if now:
                logger.info("Actions triggered off by screen rule")
            else:
                logger.info("Actions triggered on again")
            return True
        return False
