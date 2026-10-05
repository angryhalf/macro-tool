"""Core macro execution engine.

The :class:`MacroEngine` runs action sequences on a worker thread, supports
looping/repeat, hotkey-triggered starts, per-macro pause/unpause toggling and
an emergency stop that works from any thread (GUI button or global hotkey).

The macro itself is switched on/off by the user (run button / hotkey / stop).
Pausing lives *inside the action list*:

* **``wait_for``** -- block until its screen condition holds on screen (an
  optional timeout on the condition gives up and finishes the macro).
* **``pause`` / ``unpause``** -- mark a stretch of actions as *conditional*:
  while the rule carried by the pair holds on screen, every action written
  between them is held back; they resume as soon as it clears.  The pair also
  acts as a checkpoint: reaching a ``pause`` action engages the gate, reaching
  an ``unpause`` action disengages it.
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
        # Rule currently gating execution (set by the engine's monitor).
        self.condition: ScreenCondition | None = None

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
        """Engage/release the ``pause``/``unpause`` gate around a stretch."""
        if active:
            self._gate_open.clear()
        else:
            self._gate_open.set()

    @property
    def gated(self) -> bool:
        return not self._gate_open.is_set()

    # -- combined --------------------------------------------------------
    def pause(self) -> None:
        """Request that the sequence hold before its next action."""
        self._released.clear()

    def unpause(self) -> None:
        """Let a paused sequence continue (manual layer only)."""
        self._released.set()

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
            self.set_macro_paused(name, not token.manually_paused)

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

        ``pause``/``unpause`` steps form a bracket around a stretch of the
        sequence:

        * reaching a **pause** step opens the bracket and arms its screen
          rule; from here on the gate follows the screen -- while the rule
          holds, every action inside the stretch is held back and they resume
          as soon as it clears (the engine's monitor flips the gate).  A pause
          step without a rule simply waits for the user to press Unpause.
        * reaching an **unpause** step closes the bracket; if that step carries
          its own rule, execution stays held until the rule holds on screen,
          then runs freely until the next ``pause``.

        Normal actions call :meth:`ExecutionToken.wait_if_paused` first, so
        anything written between the pair pauses/unpauses with the rule.
        Outside the bracket the armed rule no longer applies.
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
                # Open a gated stretch: while the pause rule holds on screen,
                # every action inside the stretch is held back (the monitor
                # flips the gate as the rule comes and goes).  A pause step
                # without a rule simply waits for the user to press Unpause.
                usable = action.condition is not None and action.condition.configured
                token.condition = action.condition if usable else None
                if usable:
                    holding = self._evaluate_once(action.condition)
                    token.set_gate(holding)
                    if holding:
                        logger.info(
                            "Pause engaged at step %d/%d: %s", index + 1, total, action.condition.description
                        )
                else:
                    # No rule: hold the *gate* until the user presses Unpause
                    # (the manual layer is left alone so a later manual Pause
                    # toggle still reads correctly).
                    token.set_gate(True)
                    logger.info("Paused at step %d/%d: waiting for user unpause", index + 1, total)
            elif kind == "unpause":
                # Close the gated stretch.  With a rule attached, execution
                # stays held until that rule appears on screen; otherwise the
                # stretch simply ends here.
                if action.condition is not None and action.condition.configured:
                    if not self._await_condition(action.condition, token):
                        return False
                if token.gated:
                    logger.info("Pause released at step %d/%d", index + 1, total)
                token.condition = None
                token.set_gate(False)
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
        """Re-evaluate one token's armed rule; returns True when the gate flipped.

        While a ``pause`` bracket is open the rule *pauses* the stretch: the
        actions inside it are held back whenever the rule holds on screen and
        released as soon as it clears.  Outside a bracket (gate open) the same
        rule *arms* the pause: execution stops at the next action boundary as
        soon as the rule appears, so the macro reacts without waiting for the
        sequence to reach the ``pause`` step again.
        """
        condition = token.condition
        if condition is None or not condition.configured:
            return False
        runtime = ConditionRuntime.create(condition)
        try:
            holding = runtime.evaluate()
        except Exception:  # pragma: no cover - transient capture failures
            logger.exception("Screen-condition check failed (%s)", condition.description)
            return False
        if token.gated:
            if not holding:
                logger.info("Unpausing actions: %s cleared", condition.description)
                token.set_gate(False)
                return True
        elif holding:
            logger.info("Pausing actions: %s appeared", condition.description)
            token.set_gate(True)
            return True
        return False
