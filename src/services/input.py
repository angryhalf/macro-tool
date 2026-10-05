"""Keyboard and mouse input service built on pynput.

This module is the single place that talks to the operating system for input
synthesis (macro actions) and global hotkey listening.  Keeping it isolated
behind small, typed helpers makes the rest of the code base independent from
the pynput API:

* :func:`perform_action` -- execute one :class:`~src.app.settings.ActionConfig`.
* :class:`HotkeyManager` -- register/replace global hotkeys from any thread.
* :func:`current_mouse_position` -- read the live pointer position.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from pynput import keyboard as kb
from pynput import mouse as ms

from src.app.settings import ActionConfig

logger = logging.getLogger(__name__)

# Keys that must be released when the listener stops (pynput keeps their
# state pressed until explicitly released).
_MODIFIER_KEYS: tuple[kb.Key, ...] = (
    kb.Key.ctrl_l,
    kb.Key.ctrl_r,
    kb.Key.alt_l,
    kb.Key.alt_r,
    kb.Key.shift_l,
    kb.Key.shift_r,
    kb.Key.cmd,
    kb.Key.cmd_r,
)

_BUTTONS: dict[str, ms.Button] = {
    "left": ms.Button.left,
    "right": ms.Button.right,
    "middle": ms.Button.middle,
}


def _to_kb_key(name: str) -> kb.Key | str | None:
    """Resolve an action key name ("a", "space", "f5") into a pynput key."""
    if not name:
        return None
    lowered = name.strip().lower()
    member = getattr(kb.Key, lowered, None)
    if type(member).__name__ == "Key":  # real enum member, not the class itself
        return member
    if len(lowered) == 1:
        return lowered
    try:
        return kb.KeyCode.from_char(lowered)
    except ValueError:
        logger.warning("Unknown key name: %r", name)
        return None


def _to_ms_button(name: str) -> ms.Button:
    return _BUTTONS.get(name, ms.Button.left)


def current_mouse_position() -> tuple[int, int]:
    """Absolute screen coordinates of the mouse pointer."""
    x, y = ms.Controller().position
    return int(x), int(y)


def perform_action(action: ActionConfig) -> None:
    """Execute a single macro action via pynput (blocking)."""
    kind = action.kind
    if kind in ("key", "hold_key"):
        _perform_key(kind, action.key, action.duration_ms)
    elif kind == "move":
        if action.x is not None and action.y is not None:
            ms.Controller().position = (action.x, action.y)
    elif kind in ("click", "double_click"):
        _perform_click(action.button, double=kind == "double_click")
    elif kind == "scroll":
        if action.amount:
            ms.Controller().scroll(0, action.amount)
    elif kind == "wait":
        time.sleep(max(action.duration_ms, 0) / 1000.0)
    else:
        logger.warning("Unknown action kind: %r", kind)


def _perform_key(kind: str, name: str, duration_ms: int) -> None:
    key = _to_kb_key(name)
    if key is None:
        logger.warning("Key action skipped: no key specified")
        return
    ctrl = kb.Controller()
    ctrl.press(key)
    if kind == "hold_key":
        time.sleep(max(duration_ms, 1) / 1000.0)
    ctrl.release(key)


def _perform_click(button_name: str, *, double: bool) -> None:
    button = _to_ms_button(button_name)
    ctrl = ms.Controller()
    clicks = 2 if double else 1
    for _ in range(clicks):
        ctrl.click(button)
        if double:
            time.sleep(0.03)


class HotkeyManager:
    """Global hotkey registry backed by a pynput keyboard listener.

    The underlying listener always runs with *all currently registered*
    hotkeys; :meth:`set_hotkeys` swaps the whole set atomically, which is
    safe to call from the GUI thread while the listener thread is active.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hotkeys: dict[frozenset[kb.Key | str], Callable[[], None]] = {}
        self._listener: kb.Listener | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_hotkeys(self, bindings: Iterable[tuple[str, Callable[[], None]]]) -> None:
        """Replace all registrations with *bindings* ((combo string, callback))."""
        rebuilt: dict[frozenset[kb.Key | str], Callable[[], None]] = {}
        for combo, callback in bindings:
            parsed = self.parse_combo(combo)
            if parsed is None:
                continue
            if parsed in rebuilt:
                logger.warning("Duplicate hotkey '%s' ignored", combo)
                continue
            rebuilt[parsed] = callback

        with self._lock:
            self._hotkeys = rebuilt
            self._sync_locked()

    def stop(self) -> None:
        """Stop the listener and release any held modifier keys."""
        with self._lock:
            self._hotkeys.clear()
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
        self._release_modifiers()

    @staticmethod
    def parse_combo(combo: str) -> frozenset[kb.Key | str] | None:
        """Parse ``"ctrl+shift+d"`` into a frozenset usable as a map key."""
        names = [part.strip().lower() for part in combo.split("+") if part.strip()]
        if not names:
            return None
        keys = [_to_kb_key(name) for name in names]
        if any(key is None for key in keys):
            logger.warning("Invalid hotkey '%s'", combo)
            return None
        return frozenset(keys)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _sync_locked(self) -> None:
        if self._hotkeys and self._listener is None:
            self._listener = kb.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.daemon = True
            self._listener.start()
            logger.info("Hotkey listener started (%d binding(s))", len(self._hotkeys))
        elif not self._hotkeys and self._listener is not None:
            listener, self._listener = self._listener, None
            listener.stop()
            logger.info("Hotkey listener stopped")

    def _on_press(self, key: kb.Key | kb.KeyCode) -> None:
        with self._lock:
            callback = self._hotkeys.get(frozenset({key}))
        if callback is None:
            return
        # Run callbacks off the listener thread so a macro can start here
        # without blocking further key events.
        threading.Thread(target=self._fire, args=(callback,), daemon=True).start()

    def _on_release(self, key: kb.Key | kb.KeyCode) -> None:
        del key  # combos fire on press; nothing to track on release.

    @staticmethod
    def _fire(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Hotkey callback failed")

    @staticmethod
    def _release_modifiers() -> None:
        ctrl = kb.Controller()
        for key in _MODIFIER_KEYS:
            try:
                ctrl.release(key)
            except Exception:
                pass  # key was not held down


def describe_hotkey(combo: str) -> str:
    """Human-readable form for UI captions: 'ctrl+shift+d' -> 'Ctrl+Shift+D'."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    aliases = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd"}
    return "+".join(aliases.get(p, p.upper()) for p in parts)


__all__ = [
    "HotkeyManager",
    "current_mouse_position",
    "describe_hotkey",
    "perform_action",
]
