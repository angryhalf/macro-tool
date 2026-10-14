"""Record live keyboard and mouse input through pynput listeners."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Self

from pynput import keyboard as kb
from pynput import mouse as ms

from components.input.naming import _BUTTON_NAMES, to_pynput_key

logger = logging.getLogger(__name__)


class InputRecorder:
    """Record live keyboard and mouse input through pynput listeners.

    Callbacks all receive simple, canonical values so callers never touch
    pynput types:

    * ``on_press(name)`` / ``on_release(name)`` -- key names like ``"a"``,
      ``"f8"``, ``"space"`` (modifiers included, e.g. ``"ctrl_l"``).
    * ``on_move(x, y)`` -- pointer position; move events are throttled to
      one per :attr:`MOVE_THROTTLE_S` seconds.
    * ``on_scroll(amount)`` -- positive scrolls up.
    * ``on_button_press(name)`` / ``on_button_release(name)`` -- button is
      ``"left" | "right" | "middle"``.

    Usage::

        recorder = InputRecorder(on_press=print)
        recorder.start()
        ...
        recorder.stop()   # idempotent, safe from any thread/callback
    """

    #: Minimum interval between two ``on_move`` callbacks (seconds).
    MOVE_THROTTLE_S = 0.05

    def __init__(
        self,
        on_press: Callable[[str], None] | None = None,
        on_release: Callable[[str], None] | None = None,
        on_move: Callable[[int, int], None] | None = None,
        on_scroll: Callable[[int], None] | None = None,
        on_button_press: Callable[[str], None] | None = None,
        on_button_release: Callable[[str], None] | None = None,
    ) -> None:
        self._callbacks = {
            "press": on_press,
            "release": on_release,
            "move": on_move,
            "scroll": on_scroll,
            "button_press": on_button_press,
            "button_release": on_button_release,
        }
        self._listeners: list[kb.Listener | ms.Listener] = []
        self._lock = threading.Lock()
        self._last_move = 0.0
        self._stopping = False

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._listeners)

    def start(self) -> Self:
        """Start both listeners (no-op if already running)."""
        with self._lock:
            if self._listeners:
                return self

            def on_press(key: kb.Key | kb.KeyCode | None) -> bool | None:
                if key is not None:
                    self._on_key(key, pressed=True)
                return None

            def on_release(key: kb.Key | kb.KeyCode | None) -> bool | None:
                if key is not None:
                    self._on_key(key, pressed=False)
                return None

            keyboard = kb.Listener(on_press=on_press, on_release=on_release)
            mouse = ms.Listener(
                on_move=self._on_move,
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            for listener in (keyboard, mouse):
                listener.daemon = True
                listener.start()
            self._listeners = [keyboard, mouse]
            self._stopping = False
        logger.info("Input recorder started")
        return self

    def stop(self) -> None:
        """Stop both listeners; safe to call multiple times or from a callback."""
        with self._lock:
            listeners, self._listeners = self._listeners, []
            self._stopping = True
        for listener in listeners:
            try:
                listener.stop()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Listener already stopped", exc_info=True)
        if listeners:
            logger.info("Input recorder stopped")

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # pynput event adapters (each drops events already queued during stop)
    # ------------------------------------------------------------------
    def _stopped(self) -> bool:
        with self._lock:
            return self._stopping

    def _emit(self, name: str, *args: object) -> None:
        callback = self._callbacks.get(name)
        if callback is not None:
            callback(*args)  # type: ignore[arg-type]

    def _on_key(self, key: kb.Key | kb.KeyCode, *, pressed: bool) -> None:
        if self._stopped():
            return
        self._emit("press" if pressed else "release", to_pynput_key(key))

    def _on_move(self, x: float, y: float) -> None:
        if self._stopped():
            return
        now = time.monotonic()
        if now - self._last_move < self.MOVE_THROTTLE_S:
            return
        self._last_move = now
        self._emit("move", int(x), int(y))

    def _on_click(self, x: float, y: float, button: ms.Button, pressed: bool) -> None:
        if self._stopped():
            return
        del x, y  # positions come from current_mouse_position() when needed
        name = _BUTTON_NAMES.get(button, "left")
        self._emit("button_press" if pressed else "button_release", name)

    def _on_scroll(self, x: float, y: float, dx: float, dy: float) -> None:
        if self._stopped():
            return
        del x, y
        self._emit("scroll", int(dy))
