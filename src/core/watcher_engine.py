"""Screen watcher engine.

Runs each :class:`WatcherConfig` in its own polling thread. Two detection
modes are supported:

* ``image_found`` / ``image_missing`` -- OpenCV template matching against a
  screenshot region.
* ``region_changed`` -- mean per-pixel difference between consecutive frames,
  which reacts to any visual change inside the watched rectangle.

When a watcher fires it starts its target macro and/or runs its own action
list through the :class:`~core.macro_engine.MacroEngine`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2
import mss
import numpy as np

from app.settings import WatcherConfig
from core.macro_engine import MacroEngine

logger = logging.getLogger(__name__)


def grab_region(region: tuple[int, int, int, int]) -> np.ndarray:
    """Capture a screen rectangle (left, top, width, height) as a BGR array."""
    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        frame = np.array(shot, dtype=np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def match_template(frame: np.ndarray, template: np.ndarray, confidence: float) -> bool:
    """Return True when *template* appears in *frame* at >= *confidence*."""
    if template.shape[0] > frame.shape[0] or template.shape[1] > frame.shape[1]:
        logger.warning("Template larger than the search region")
        return False
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(gray_frame, gray_template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, _ = cv2.minMaxLoc(result)
    return max_value >= confidence


def region_change_score(previous: np.ndarray, current: np.ndarray) -> float:
    """Mean absolute pixel difference between two same-size frames."""
    if previous.shape != current.shape:
        return float("inf")
    diff = cv2.absdiff(previous, current).astype(np.float32)
    return float(diff.mean())


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
        if config.mode in ("image_found", "image_missing") and not self._load_template(config):
            logger.warning("Watcher '%s': template not found (%s)", config.name, config.template_path)
            return False

        with self._lock:
            self._stop_runtime_locked(config.name)
            runtime = _WatcherRuntime(config)
            runtime.thread = threading.Thread(
                target=self._poll_loop, args=(runtime,), name=f"watcher-{config.name}", daemon=True
            )
            self._runtimes[config.name] = runtime
            runtime.thread.start()
        logger.info("Watcher '%s' started (%s)", config.name, config.mode)
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

    @staticmethod
    def _load_template(config: WatcherConfig) -> np.ndarray | None:
        template = cv2.imread(config.template_path, cv2.IMREAD_COLOR)
        return None if template is None else template

    def _poll_loop(self, runtime: _WatcherRuntime) -> None:
        config = runtime.config
        template = self._load_template(config) if config.mode != "region_changed" else None
        interval_s = max(config.poll_interval_ms, 10) / 1000.0
        cooldown_s = max(config.cooldown_ms, 0) / 1000.0
        previous_frame: np.ndarray | None = None

        while not runtime.stop_event.wait(interval_s):
            try:
                frame = grab_region(config.region)
            except Exception:  # pragma: no cover - screen capture can fail transiently
                logger.exception("Watcher '%s': capture failed", config.name)
                continue

            fired = self._evaluate(config, frame, template, previous_frame)
            previous_frame = frame
            if not fired:
                continue

            now = time.monotonic()
            if now - runtime.last_fired_mono < cooldown_s:
                continue
            runtime.last_fired_mono = now
            runtime.trigger_count += 1
            logger.info("Watcher '%s' fired (#%d)", config.name, runtime.trigger_count)
            self._dispatch(config)

    @staticmethod
    def _evaluate(
        config: WatcherConfig,
        frame: np.ndarray,
        template: np.ndarray | None,
        previous_frame: np.ndarray | None,
    ) -> bool:
        if config.mode == "image_found":
            return template is not None and match_template(frame, template, config.confidence)
        if config.mode == "image_missing":
            return template is not None and not match_template(frame, template, config.confidence)
        if config.mode == "region_changed":
            if previous_frame is None:
                return False
            return region_change_score(previous_frame, frame) > config.change_threshold
        logger.warning("Watcher '%s': unknown mode %r", config.name, config.mode)
        return False

    def _dispatch(self, config: WatcherConfig) -> None:
        settings = self._macro_engine.settings
        initial_delay = settings.execution_delay_ms / 1000.0

        macro = self._macros_provider().get(config.target_macro)
        if macro is not None:
            self._macro_engine.start_macro(macro, initial_delay_s=initial_delay)
        if config.actions:
            self._macro_engine.run_actions(config.actions, name=f"watcher-{config.name}")

    def _notify(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()
