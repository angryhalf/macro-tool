"""Background screen-watcher engine.

Runs each :class:`~app.settings.WatcherConfig` in its own polling thread.  A
watcher wraps a reusable :class:`~app.conditions.ScreenCondition` (image
appearing/disappearing or a region changing); when it fires, it starts its
target macro and/or runs its own action list through the
:class:`~core.macro_engine.MacroEngine`.

The detection primitives themselves live in
:mod:`core.screen_watch` so macros' start/stop conditions can share them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app.conditions import ConditionRuntime
from app.settings import WatcherConfig
from core.macro_engine import MacroEngine

logger = logging.getLogger(__name__)


class _WatcherRuntime:
    """Bookkeeping for one running watcher thread."""

    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_fired_mono: float = 0.0
        self.trigger_count = 0


class ScreenWatcherEngine:
    """Owns all watcher threads and dispatches their triggers."""

    def __init__(
        self,
        macro_engine: MacroEngine,
        macros_provider: Callable[[], dict[str, object]],
    ) -> None:
        self._macro_engine = macro_engine
        self._macros_provider = macros_provider
        self._runtimes: dict[str, _WatcherRuntime] = {}
        self._lock = threading.Lock()
        self.on_state_changed: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self, config: WatcherConfig) -> bool:
        """Start (or restart) a watcher. Returns False if misconfigured."""
        if not config.condition.configured:
            logger.warning("Watcher '%s': condition is incomplete", config.name)
            return False

        with self._lock:
            self._stop_runtime_locked(config.name)
            runtime = _WatcherRuntime(config)
            runtime.thread = threading.Thread(
                target=self._poll_loop, args=(runtime,), name=f"watcher-{config.name}", daemon=True
            )
            self._runtimes[config.name] = runtime
            runtime.thread.start()
        logger.info("Watcher '%s' started (%s)", config.name, config.condition.description)
        self._notify()
        return True

    def stop(self, name: str) -> None:
        with self._lock:
            self._stop_runtime_locked(name)
        self._notify()

    def stop_all(self) -> None:
        with self._lock:
            for name in list(self._runtimes):
                self._stop_runtime_locked(name)
        self._notify()

    def is_running(self, name: str) -> bool:
        with self._lock:
            runtime = self._runtimes.get(name)
        return bool(runtime and runtime.thread and runtime.thread.is_alive())

    def status_text(self) -> str:
        """One-line summary of active watchers for the status bar."""
        with self._lock:
            names = [n for n, r in self._runtimes.items() if r.thread and r.thread.is_alive()]
        if not names:
            return "Watchers: none active"
        return "Watchers: " + ", ".join(names)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _stop_runtime_locked(self, name: str) -> None:
        runtime = self._runtimes.pop(name, None)
        if runtime is None:
            return
        runtime.stop_event.set()
        if runtime.thread and runtime.thread.is_alive():
            runtime.thread.join(timeout=2.0)

    def _poll_loop(self, runtime: _WatcherRuntime) -> None:
        config = runtime.config
        poll = ConditionRuntime.create(config.condition)
        interval_s = max(config.condition.poll_interval_ms, 10) / 1000.0
        cooldown_s = max(config.cooldown_ms, 0) / 1000.0

        while not runtime.stop_event.wait(interval_s):
            try:
                fired = poll.evaluate()
            except Exception:  # pragma: no cover - screen capture can fail transiently
                logger.exception("Watcher '%s': capture failed", config.name)
                continue
            if not fired:
                continue

            now = time.monotonic()
            if now - runtime.last_fired_mono < cooldown_s:
                continue
            runtime.last_fired_mono = now
            runtime.trigger_count += 1
            logger.info("Watcher '%s' fired (#%d)", config.name, runtime.trigger_count)
            self._dispatch(config)

    def _dispatch(self, config: WatcherConfig) -> None:
        initial_delay = self._macro_engine.settings.execution_delay_ms / 1000.0

        macro = self._macros_provider().get(config.target_macro)
        if macro is not None:
            self._macro_engine.start_macro(macro, initial_delay_s=initial_delay)
        if config.actions:
            self._macro_engine.run_actions(config.actions, name=f"watcher-{config.name}")

    def _notify(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()
