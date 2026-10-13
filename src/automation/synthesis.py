"""Synthesize keyboard and mouse input with pynput (macro action playback).

Every function here performs *real* input synthesis against the operating
system; long actions accept a ``cancel`` predicate so an emergency stop
takes effect promptly **while releasing every key/button already pressed**.
Flow-control kinds are handled by :class:`~engine.macro_flow.MacroRunner`
and never reach this module (they are accepted as no-ops so ad-hoc action
lists can be played through :func:`perform_action` without special-casing).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from pynput import keyboard as kb
from pynput import mouse as ms

from automation.naming import _to_kb_key, _to_ms_button
from models.macro import ActionConfig

logger = logging.getLogger(__name__)


def current_mouse_position() -> tuple[int, int]:
    """Absolute screen coordinates of the mouse pointer."""
    x, y = ms.Controller().position
    return int(x), int(y)


def _interruptible_sleep(seconds: float, cancel: Callable[[], bool] | None = None) -> bool:
    """Sleep up to *seconds*, waking early when *cancel* reports True.

    Returns True when the full duration elapsed, False when cancelled.
    Sleeping in short slices keeps long actions (a 600 s key hold, a slow
    typing run) interruptible so an emergency stop takes effect promptly.
    """
    deadline = time.monotonic() + seconds
    while True:
        if cancel is not None and cancel():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.05))


def perform_action(action: ActionConfig, cancel: Callable[[], bool] | None = None) -> None:
    """Execute a single macro action via pynput (blocking).

    *cancel* is an optional predicate checked between sleeps and between
    individual input steps; when it returns True the action aborts as soon
    as possible **while releasing every key/button it already pressed**, so
    an emergency stop never leaves modifiers or mouse buttons stuck down.

    Flow-control kinds (``wait_for``, ``stop_trigger``, ``start_trigger``,
    ``loop_start``, ``loop_end``) are handled by
    :class:`~engine.macro_engine.MacroEngine` itself and never reach real input
    synthesis; they are accepted here as no-ops so ad-hoc action lists can be
    played through this function without special-casing.
    """
    kind = action.kind
    if kind in ("wait_for", "stop_trigger", "start_trigger", "loop_start", "loop_end"):
        return
    if kind in ("key", "hold_key"):
        _perform_key(kind, action.key, action.duration_ms, cancel)
    elif kind == "combo":
        _perform_combo(action.combo, cancel)
    elif kind == "type":
        _perform_type(action.text, max(action.amount, 0), cancel)
    elif kind == "move":
        if action.x is not None and action.y is not None:
            ms.Controller().position = (action.x, action.y)
    elif kind in ("click", "double_click"):
        _perform_click(action.button, double=kind == "double_click", x=action.x, y=action.y, cancel=cancel)
    elif kind == "mouse_down":
        ms.Controller().press(_to_ms_button(action.button))
    elif kind == "mouse_up":
        ms.Controller().release(_to_ms_button(action.button))
    elif kind == "drag":
        _perform_drag(action, cancel)
    elif kind == "scroll":
        if action.amount:
            ms.Controller().scroll(0, action.amount)
    elif kind == "wait":
        _interruptible_sleep(max(action.duration_ms, 0) / 1000.0, cancel)
    else:
        logger.warning("Unknown action kind: %r", kind)


def _perform_key(
    kind: str, name: str, duration_ms: int, cancel: Callable[[], bool] | None = None
) -> None:
    key = _to_kb_key(name)
    if key is None:
        logger.warning("Key action skipped: no key specified")
        return
    ctrl = kb.Controller()
    ctrl.press(key)
    try:
        if kind == "hold_key" and not _interruptible_sleep(max(duration_ms, 1) / 1000.0, cancel):
            logger.info("Hold of %r cancelled; releasing key", name)
    finally:
        # Always release, even on cancellation -- otherwise the key stays
        # physically "down" for the rest of the session.
        ctrl.release(key)


def _perform_combo(combo: str, cancel: Callable[[], bool] | None = None) -> None:
    """Press all keys of ``"ctrl+shift+d"``, release them in reverse order."""
    names = [part.strip().lower() for part in combo.split("+") if part.strip()]
    keys = [_to_kb_key(name) for name in names]
    if not keys or any(key is None for key in keys):
        logger.warning("Invalid combo: %r", combo)
        return
    ctrl = kb.Controller()
    # ``keys`` was validated above, so every entry is a real pynput key.
    pressed = [key for key in keys if key is not None]
    held: list = []
    try:
        for key in pressed:
            if cancel is not None and cancel():
                logger.info("Combo %r cancelled after %d key(s)", combo, len(held))
                break
            ctrl.press(key)  # pyright: ignore[reportArgumentType]
            held.append(key)
    finally:
        # Release exactly what was pressed, in reverse -- never leave a
        # modifier stuck down after a cancelled combo.
        for key in reversed(held):
            ctrl.release(key)  # pyright: ignore[reportArgumentType]


def _perform_type(text: str, delay_ms: int, cancel: Callable[[], bool] | None = None) -> None:
    """Type *text* one character at a time using a shared controller."""
    if not text:
        return
    ctrl = kb.Controller()
    for index, char in enumerate(text):
        if cancel is not None and cancel():
            logger.info("Typing cancelled after %d char(s)", index)
            return
        try:
            ctrl.type(char)
        except Exception as exc:  # noqa: BLE001 - controller errors are not typed
            logger.warning("Cannot type character %r: %s", char, exc)
        if delay_ms > 0 and not _interruptible_sleep(delay_ms / 1000.0, cancel):
            return


def _perform_click(
    button_name: str,
    *,
    double: bool,
    x: int | None,
    y: int | None,
    cancel: Callable[[], bool] | None = None,
) -> None:
    button = _to_ms_button(button_name)
    ctrl = ms.Controller()
    if x is not None and y is not None:
        ctrl.position = (x, y)
    clicks = 2 if double else 1
    for i in range(clicks):
        if cancel is not None and cancel():
            return
        ctrl.click(button)
        if double and i == 0:
            _interruptible_sleep(0.03, cancel)


def _perform_drag(action: ActionConfig, cancel: Callable[[], bool] | None = None) -> None:
    """Press at (x, y), move to (amount, duration_ms fields), release.

    The end position uses ``None``-checks rather than truthiness so that a
    legitimate drag ending at coordinate 0 is honored.
    """
    if action.x is None or action.y is None:
        logger.warning("Drag action missing start position")
        return
    button = _to_ms_button(action.button)
    ctrl = ms.Controller()
    steps = 15
    start_x, start_y = action.x, action.y
    end_x = action.amount if action.amount is not None else start_x
    end_y = action.duration_ms if action.duration_ms is not None else start_y
    ctrl.position = (start_x, start_y)
    ctrl.press(button)
    try:
        for step in range(1, steps + 1):
            if cancel is not None and cancel():
                logger.info("Drag cancelled at step %d/%d; releasing button", step, steps)
                return
            frac = step / steps
            ctrl.position = (
                int(start_x + (end_x - start_x) * frac),
                int(start_y + (end_y - start_y) * frac),
            )
            _interruptible_sleep(0.01, cancel)
    finally:
        # Never leave the mouse button physically pressed.
        ctrl.release(button)
