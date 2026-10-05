"""Core macro execution engine.

The :class:`MacroEngine` runs action sequences on a worker thread, supports
looping/repeat, hotkey-triggered starts, and an emergency stop that works
from any thread (GUI button or global hotkey).

The macro itself is switched on/off by the user (run button / hotkey / stop).
Screen conditions only *pause* and *unpause* its actions while it runs:

* **pause condition** (``pause_condition``) -- while this holds on
  screen, execution is paused; when it stops holding, the macro resumes.  An
  optional timeout bounds how long the initial wait may last before the macro
  gives up and finishes.
* **unpause condition** (``unpause_condition``) -- once the macro has
  been paused, execution stays paused until this condition holds (or forever,
  if no resume condition is configured).
"""

from __future__ import annotations

import logging
import threading
import time

from app.conditions import ConditionRuntime
from app.settings import ActionConfig, AppSettings, MacroConfig
from services.input import HotkeyManager, perform_action

logger = logging.getLogger(__name__)


class ExecutionToken:
    """Cooperative cancellation *and* pause handle shared by one running sequence.

    Cancellation is final (user stop / emergency stop).  Pausing is driven by
    screen conditions: while paused, :meth:`sleep` waits until the sequence is
    unpaused or cancelled, and :meth:`wait_if_paused` blocks at action
    boundaries.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._paused = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        # Never leave a paused sequence hanging after cancellation.
        self._paused.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def pause(self) -> None:
        """Request that the sequence hold before its next action."""
        self._paused.set()

    def unpause(self) -> None:
        """Let a paused sequence continue."""
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def wait_if_paused(self) -> None:
        """Block while paused; returns when unpaused or cancelled."""
        while self._paused.is_set() and not self._cancelled.is_set():
            self._cancelled.wait(0.1)

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
        self._hotkeys = HotkeyManager()
        self._settings = AppSettings()
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
    def start_macro(self, macro: MacroConfig, initial_delay_s: float = 0.0) -> ExecutionToken | None:
        """Start a macro in a worker thread. Returns the token or None if invalid."""
        if not macro.actions:
            logger.info("Macro '%s' has no actions", macro.name)
            return None
        if macro.pause_condition is not None and not macro.pause_condition.configured:
            logger.warning("Macro '%s': pause condition is incomplete", macro.name)
        if macro.unpause_condition is not None and not macro.unpause_condition.configured:
            logger.warning("Macro '%s': unpause condition is incomplete", macro.name)
        token = ExecutionToken()
        thread = threading.Thread(
            target=self._run_macro,
            args=(macro, token, initial_delay_s),
            name=f"macro-{macro.name}",
            daemon=True,
        )
        with self._lock:
            self._tokens.add(token)
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

    def _finish(self, token: ExecutionToken) -> None:
        with self._lock:
            self._tokens.discard(token)
        self._notify()

    def _notify(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()

    # ------------------------------------------------------------------
    # Worker bodies
    # ------------------------------------------------------------------
    def _run_macro(self, macro: MacroConfig, token: ExecutionToken, initial_delay_s: float) -> None:
        monitor = self._pause_monitor(macro, token)
        try:
            if initial_delay_s > 0:
                logger.info("Macro '%s' starts in %.1fs", macro.name, initial_delay_s)
                token.sleep(initial_delay_s)
            if not self._await_unpaused(macro, token):
                return
            loops = 0
            while not token.cancelled:
                self._run_actions(macro.actions, token)
                loops += 1
                if not macro.repeat and loops >= max(1, macro.loops):
                    break
                if macro.interval_ms > 0:
                    token.sleep(macro.interval_ms / 1000.0)
                if token.cancelled:
                    break
            if not token.cancelled:
                logger.info("Macro '%s' finished (%d loop)", macro.name, loops)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Macro '%s' crashed", macro.name)
        finally:
            token.cancel()
            if monitor is not None:
                monitor.join(timeout=2.0)
            self._finish(token)

    def _await_unpaused(self, macro: MacroConfig, token: ExecutionToken) -> bool:
        """Hold execution until the macro's start condition stops holding.

        The user turns the macro on; the pause condition only holds its
        actions back while it is true on screen.  An optional timeout bounds
        this initial wait -- when it elapses while the condition still holds,
        the macro finishes without acting.  Returns True when execution may
        proceed, False when cancelled or timed out.  (The background monitor
        keeps pausing/unpausing the sequence afterwards.)
        """
        condition = macro.pause_condition
        if condition is None or not condition.configured or token.cancelled:
            return not token.cancelled
        runtime = ConditionRuntime.create(condition, timeout_s=condition.timeout_ms / 1000.0)
        interval_s = max(condition.poll_interval_ms, 10) / 1000.0
        logger.info("Macro '%s': waiting for pause condition to clear (%s)", macro.name, condition.description)
        while not token.cancelled:
            try:
                if not runtime.evaluate():
                    logger.info("Macro '%s': pause condition clear, running", macro.name)
                    return True
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Macro '%s': pause-condition check failed", macro.name)
            if runtime.expired():
                logger.info("Macro '%s': pause-condition timeout reached, giving up", macro.name)
                return False
            token.sleep(interval_s)
        return False

    def _pause_monitor(self, macro: MacroConfig, token: ExecutionToken) -> threading.Thread | None:
        """Poll the macro's conditions while it runs, pausing/unpausing *token*.

        While the start (pause) condition holds, actions are held back; they
        resume as soon as it clears.  If a stop (unpause-requirement) condition
        is configured, execution stays paused after a pause until that
        condition holds on screen.
        """
        pause_condition = macro.pause_condition
        resume_condition = macro.unpause_condition
        usable_pause = pause_condition is not None and pause_condition.configured
        usable_resume = resume_condition is not None and resume_condition.configured
        if not usable_pause:
            return None
        thread = threading.Thread(
            target=self._monitor_pause_conditions,
            args=(macro.name, pause_condition, resume_condition if usable_resume else None, token),
            name=f"pause-monitor-{macro.name}",
            daemon=True,
        )
        thread.start()
        return thread

    @staticmethod
    def _monitor_pause_conditions(
        macro_name: str,
        pause_condition,
        resume_condition,
        token: ExecutionToken,
    ) -> None:
        """Flip the token's pause flag as the screen conditions come and go."""
        pause_runtime = ConditionRuntime.create(pause_condition)
        resume_runtime = ConditionRuntime.create(resume_condition) if resume_condition is not None else None
        was_paused = False
        while not token.cancelled:
            interval_s = max(pause_condition.poll_interval_ms, 10) / 1000.0
            token.sleep(interval_s)
            if token.cancelled:
                return
            try:
                holding = pause_runtime.evaluate()
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Macro '%s': pause-condition check failed", macro_name)
                holding = False
            if holding:
                if not was_paused:
                    logger.info("Macro '%s': paused (%s)", macro_name, pause_condition.description)
                    token.pause()
                    was_paused = True
                continue
            if was_paused:
                if resume_runtime is not None:
                    resume_interval = max(resume_condition.poll_interval_ms, 10) / 1000.0
                    try:
                        if not resume_runtime.evaluate():
                            # Re-poll the resume rule on its own cadence while
                            # the sequence stays paused (token.sleep honours
                            # the pause flag, so this loop would otherwise be
                            # stuck).
                            token.sleep(resume_interval)
                            continue
                    except Exception:  # pragma: no cover - transient capture failures
                        logger.exception("Macro '%s': resume-condition check failed", macro_name)
                        token.sleep(resume_interval)
                        continue
                logger.info("Macro '%s': unpaused", macro_name)
                token.unpause()
                was_paused = False

    def _run_actions(self, actions: tuple[ActionConfig, ...], token: ExecutionToken) -> None:
        for action in actions:
            if token.cancelled:
                return
            token.wait_if_paused()  # never fire actions while a pause condition holds
            if token.cancelled:
                return
            perform_action(action)
