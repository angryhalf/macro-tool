"""Core macro execution engine.

The :class:`MacroEngine` runs action sequences on a worker thread, supports
looping/repeat, hotkey-triggered starts, per-macro pause/unpause toggling and
an emergency stop that works from any thread (GUI button or global hotkey).

The macro itself is switched on/off by the user (run button / hotkey / stop).
Pausing lives *inside the action list*:

* **``wait_for``** -- block until its screen condition holds on screen (an
  optional timeout on the condition gives up and finishes the macro).
* **``pause`` / ``unpause``** -- mark a stretch of actions as *conditional*.
  Reaching the ``pause`` step *starts* watching its screen rule (the macro
  keeps running normally at this point); once the rule is met on screen, every
  action written between the pair stops firing.  Reaching the ``unpause`` step
  *stops* watching that rule; once its own rule is met, the actions in the
  stretch resume.  In other words: pause-condition met => the stretch pauses,
  unpause-condition met => the stretch unpauses.
* **Manual state** -- macros start *paused* unless configured otherwise (or
  started with ``start_paused=False``); the user can toggle pause/unpause at
  any time while a macro runs.
"""

from __future__ import annotations

import logging
import threading
import time
from app.conditions import ConditionRuntime, ScreenCondition
from app.settings import CONDITION_ACTION_KINDS, ActionConfig, AppSettings, MacroConfig
from services.input import HotkeyManager, perform_action

logger = logging.getLogger(__name__)


class ExecutionToken:
    """Cooperative cancellation *and* pause handle shared by one running sequence.

    Cancellation is final (user stop / emergency stop).  Pausing has two
    layers: a *manual* flag toggled by the user, and a *conditional* gate
    driven by the screen rule attached to the current ``pause``/``unpause``
    action.  The sequence holds whenever either layer is engaged; while held,
    :meth:`sleep` waits until it releases and :meth:`wait_if_paused` blocks at
    action boundaries.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._released = threading.Event()  # set => not manually paused
        self._released.set()
        self._gate_open = threading.Event()  # set => not gated by a pause rule
        self._gate_open.set()
        # Rule armed by the most recent ``pause`` step whose ``unpause`` has
        # not been reached yet; the engine's monitor pauses/unpauses the gate
        # whenever this rule is met / clears on screen.  ``None`` = no rule
        # armed (outside a pause/unpause stretch).
        self.pause_rule: ScreenCondition | None = None
        # When a ``pause`` step had no rule, the gate stays shut until the
        # user presses Unpause; tracked here so reaching the ``unpause``
        # step can open it again.
        self._ruleless_hold = False

    # -- cancellation --------------------------------------------------
    def cancel(self) -> None:
        self._cancelled.set()
        # Never leave a paused sequence hanging after cancellation.
        self._released.set()
        self._gate_open.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- manual (user) pause --------------------------------------------
    def set_manual_pause(self, paused: bool) -> None:
        if paused:
            self._released.clear()
        else:
            self._released.set()

    @property
    def manually_paused(self) -> bool:
        return not self._released.is_set()

    # -- conditional (screen-rule) gate ---------------------------------
    def set_gate(self, active: bool) -> None:
        """Engage/release the ``pause``/``unpause`` gate around a stretch.

        Manual unpausing by the user also lifts a rule-driven gate; the
        monitor re-engages it the next time the rule is met on screen.
        """
        if active:
            self._gate_open.clear()
        else:
            self._ruleless_hold = False
            self._gate_open.set()

    @property
    def gated(self) -> bool:
        return not self._gate_open.is_set()

    # -- combined --------------------------------------------------------
    def pause(self) -> None:
        """Request that the sequence hold before its next action."""
        self._released.clear()

    def unpause(self) -> None:
        """Let a paused sequence continue.

        Clears the manual layer *and* any rule-driven gate -- including the
        user-waiting hold set by a rule-less ``pause`` step: the user pressing
        Unpause always resumes the actions; the monitor re-engages the gate
        the next time an armed pause rule is met on screen.
        """
        self._released.set()
        if self.gated:
            self._ruleless_hold = False
            self._gate_open.set()

    @property
    def paused(self) -> bool:
        return not self._released.is_set() or not self._gate_open.is_set()

    def wait_if_paused(self) -> None:
        """Block while held (manually or by the gate); returns when released."""
        while self.paused and not self._cancelled.is_set():
            self._cancelled.wait(0.1)

    def wait(self, seconds: float) -> None:
        """Plain interruptible wait used for polling cadence.

        Unlike :meth:`sleep` this ignores the pause layers -- it is used by
        loops that must keep evaluating screen conditions *while* the
        sequence is held back.
        """
        self._cancelled.wait(seconds)

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep: honours cancellation and pauses."""
        deadline = time.monotonic() + seconds
        while True:
            self.wait_if_paused()
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
        self.on_state_changed: callable | None = None  # called after start/stop events

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
        start_paused: bool | None = None,
    ) -> ExecutionToken | None:
        """Start a macro in a worker thread. Returns the token or None if invalid.

        *start_paused* overrides the macro's own default; when omitted the
        macro starts paused unless ``macro.start_paused`` is False.
        """
        if not macro.actions:
            logger.info("Macro '%s' has no actions", macro.name)
            return None
        for action in macro.actions:
            if action.kind in ("wait_for", "pause") and (
                action.condition is None or not action.condition.configured
            ):
                logger.warning("Macro '%s': %s action has no usable condition", macro.name, action.kind)
        token = ExecutionToken()
        if start_paused if start_paused is not None else macro.start_paused:
            token.pause()  # manual layer: user must press Unpause to begin
        thread = threading.Thread(
            target=self._run_macro,
            args=(macro, token, initial_delay_s),
            name=f"macro-{macro.name}",
            daemon=True,
        )
        with self._lock:
            self._tokens.add(token)
            self._macro_tokens[macro.name] = token
        thread.start()
        self._notify()
        return token

    def run_actions(self, actions: tuple[ActionConfig, ...], name: str = "watcher") -> ExecutionToken:
        """Run a one-shot action list (used by watchers)."""
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

    def shutdown(self) -> None:
        """Stop everything and unregister global hotkeys (on app exit)."""
        self.stop_all()
        self._hotkeys.stop()

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._tokens)

    # ------------------------------------------------------------------
    # Per-macro pause control (user-driven)
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

    def is_macro_paused(self, name: str) -> bool:
        tokens = self.running_macros()
        token = tokens.get(name)
        return bool(token and token.paused)

    def set_macro_paused(self, name: str, paused: bool) -> None:
        """Toggle the *manual* pause layer of a running macro."""
        token = self.running_macros().get(name)
        if token is None:
            return
        token.set_manual_pause(paused)
        logger.info("Macro '%s': user %s", name, "paused" if paused else "unpaused")
        self._notify()

    def toggle_macro_pause(self, name: str) -> None:
        token = self.running_macros().get(name)
        if token is not None:
            if token.manually_paused:
                # Resume: drop the manual layer (and any rule gate) so the
                # user's Unpause always takes effect immediately.
                self.set_macro_paused(name, False)
                token.unpause()
            else:
                self.set_macro_paused(name, True)

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

        ``pause``/``unpause`` steps bracket a stretch of the sequence:

        * reaching a **pause** step arms its screen rule and starts watching
          it (execution is *not* blocked here -- the macro keeps running).
          Whenever the rule is met on screen, the gate shuts and every action
          inside the stretch stops firing; when the rule clears, they resume.
          A pause step without a rule simply holds until the user unpauses.
        * reaching an **unpause** step stops watching that rule.  If the step
          carries its own rule, execution stays held until that rule is met
          (then runs freely until the next ``pause``); otherwise the stretch
          just ends here.

        Normal actions call :meth:`ExecutionToken.wait_if_paused` first, so
        anything written between the pair pauses/unpauses with the rule.
        """
        index = 0
        total = len(actions)
        while index < total:
            if token.cancelled:
                return False
            action = actions[index]
            kind = action.kind
            if kind == "wait_for":
                if not self._await_condition(action.condition, token):
                    return False
            elif kind == "pause":
                # Arm the stretch's rule; keep executing normally from here.
                usable = action.condition is not None and action.condition.configured
                if usable:
                    token.pause_rule = action.condition
                    logger.info(
                        "Pause armed at step %d/%d: actions will hold while '%s' is on screen",
                        index + 1, total, action.condition.description,
                    )
                else:
                    # No rule: hold the gate until the user presses Unpause
                    # (the manual layer is left alone so a later manual Pause
                    # toggle still reads correctly).
                    token._ruleless_hold = True
                    token.set_gate(True)
                    logger.info("Paused at step %d/%d: waiting for user unpause", index + 1, total)
            elif kind == "unpause":
                # End the stretch: disarm the pause rule first so the monitor
                # can no longer gate these actions.
                token.pause_rule = None
                if action.condition is not None and action.condition.configured:
                    # Wait until the unpause condition is met before resuming.
                    if not self._await_condition(action.condition, token):
                        return False
                else:
                    # No rule attached: release any hold left by the pause
                    # step (rule-driven *or* user-waiting) so the stretch can
                    # finish -- the user pressing Unpause also does this.
                    if token.gated:
                        logger.info("Pause released at step %d/%d", index + 1, total)
                    token.set_gate(False)
                token.wait_if_paused()  # honour a manual Pause while we wait here
            else:
                token.wait_if_paused()  # never fire actions while paused
                if token.cancelled:
                    return False
                perform_action(action)
            index += 1
        return True

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
    # Screen-rule monitor (drives pause/unpause checkpoints)
    # ------------------------------------------------------------------
    def start_monitor(self) -> None:
        """Launch the background thread that polls gating rules (once)."""
        if self._monitor_started:
            return
        self._monitor_started = True
        threading.Thread(target=self._monitor_loop, name="macro-condition-monitor", daemon=True).start()

    def _monitor_loop(self) -> None:
        """Flip each macro's conditional gate as its screen rule comes and goes."""
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
                self._notify()  # keep the UI's pause buttons truthful
            time.sleep(0.05)

    @staticmethod
    def _monitor_token(token: ExecutionToken) -> bool:
        """Re-evaluate one token's armed pause rule; True when the gate flipped.

        While a ``pause``/``unpause`` stretch is active (rule armed), the rule
        *pauses* the actions inside it: whenever the rule is met on screen the
        gate shuts and they stop firing; as soon as it clears they resume.
        When no rule is armed (outside the stretch, or after the ``unpause``
        step was reached) the monitor does nothing.
        """
        condition = token.pause_rule
        if condition is None or not condition.configured:
            # Nothing armed -- make sure a stale gate isn't left shut by an
            # earlier evaluation failure.
            if token.gated and token.pause_rule is None and not token._ruleless_hold:
                token.set_gate(False)
                return True
            return False
        runtime = ConditionRuntime.create(condition)
        try:
            holding = runtime.evaluate()
        except Exception:  # pragma: no cover - transient capture failures
            logger.exception("Screen-condition check failed (%s)", condition.description)
            return False
        if holding and not token.gated:
            logger.info("Pausing actions: %s", condition.description)
            token.set_gate(True)
            return True
        if not holding and token.gated:
            logger.info("Unpausing actions: %s cleared", condition.description)
            # Re-shut immediately if a rule-less pause is also holding.
            token._gate_open.set()
            if token._ruleless_hold:
                token._gate_open.clear()
            return True
        return False
