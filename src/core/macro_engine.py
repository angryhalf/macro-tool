"""Core macro execution engine.

The :class:`MacroEngine` runs action sequences on a worker thread, supports
looping/repeat, hotkey-triggered starts, and an emergency stop that works
from any thread (GUI button or global hotkey).

Macros can additionally be gated by screen conditions:

* **start condition** -- the macro waits (polling the screen) until the
  condition holds before running its first action; an optional timeout gives
  up waiting.
* **stop condition** -- while the macro runs, a monitor thread polls the
  screen and cancels the macro as soon as the condition holds.
"""

from __future__ import annotations

import logging
import threading

from app.conditions import ConditionRuntime
from app.settings import ActionConfig, AppSettings, MacroConfig
from services.input import HotkeyManager, perform_action

logger = logging.getLogger(__name__)


class ExecutionToken:
    """Cooperative cancellation handle shared by one running sequence."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep: returns early if the token is cancelled."""
        self._cancelled.wait(seconds)


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
        if macro.start_condition is not None and not macro.start_condition.configured:
            logger.warning("Macro '%s': start condition is incomplete", macro.name)
        if macro.stop_condition is not None and not macro.stop_condition.configured:
            logger.warning("Macro '%s': stop condition is incomplete", macro.name)
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
        monitor = self._start_stop_monitor(macro, token)
        try:
            if initial_delay_s > 0:
                logger.info("Macro '%s' starts in %.1fs", macro.name, initial_delay_s)
                token.sleep(initial_delay_s)
            if not self._await_start_condition(macro, token):
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
            if monitor is not None:
                monitor.join(timeout=2.0)
            self._finish(token)

    def _await_start_condition(self, macro: MacroConfig, token: ExecutionToken) -> bool:
        """Block until the macro's start condition holds.

        Returns True when execution may proceed, False when the macro was
        cancelled or the wait timed out.
        """
        condition = macro.start_condition
        if condition is None or not condition.configured or token.cancelled:
            return not token.cancelled
        runtime = ConditionRuntime.create(condition, timeout_s=condition.timeout_ms / 1000.0)
        interval_s = max(condition.poll_interval_ms, 10) / 1000.0
        logger.info("Macro '%s' waiting for start condition: %s", macro.name, condition.description)
        while not token.cancelled:
            if runtime.expired():
                logger.info("Macro '%s': start-condition timeout reached, skipping", macro.name)
                return False
            token.sleep(interval_s)
            if token.cancelled:
                return False
            try:
                if runtime.evaluate():
                    logger.info("Macro '%s': start condition met", macro.name)
                    return True
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Macro '%s': start-condition check failed", macro.name)
        return False

    def _start_stop_monitor(self, macro: MacroConfig, token: ExecutionToken) -> threading.Thread | None:
        """Poll the stop condition in the background; cancel *token* when it holds."""
        condition = macro.stop_condition
        if condition is None or not condition.configured:
            return None
        thread = threading.Thread(
            target=self._monitor_stop_condition,
            args=(macro.name, condition, token),
            name=f"stop-monitor-{macro.name}",
            daemon=True,
        )
        thread.start()
        return thread

    @staticmethod
    def _monitor_stop_condition(macro_name: str, condition, token: ExecutionToken) -> None:
        runtime = ConditionRuntime.create(condition)
        interval_s = max(condition.poll_interval_ms, 10) / 1000.0
        while not token.cancelled:
            token.sleep(interval_s)
            if token.cancelled:
                return
            try:
                if runtime.evaluate():
                    logger.info("Macro '%s': stop condition met (%s)", macro_name, condition.description)
                    token.cancel()
                    return
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Macro '%s': stop-condition check failed", macro_name)

    def _run_actions(self, actions: tuple[ActionConfig, ...], token: ExecutionToken) -> None:
        for action in actions:
            if token.cancelled:
                return
            perform_action(action)
