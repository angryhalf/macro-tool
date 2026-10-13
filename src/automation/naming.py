"""Canonical key/button naming helpers shared by the automation layer.

These functions translate between the simple string names used throughout
the app (``"ctrl"``, ``"f5"``, ``"left"``) and pynput's key/button objects.
Keeping them in one module means the rest of the code base never has to
touch pynput types just to normalize a name or order a hotkey combo.
"""

from __future__ import annotations

import logging

from pynput import keyboard as kb
from pynput import mouse as ms

logger = logging.getLogger(__name__)

_BUTTONS: dict[str, ms.Button] = {
    "left": ms.Button.left,
    "right": ms.Button.right,
    "middle": ms.Button.middle,
}

_BUTTON_NAMES: dict[ms.Button, str] = {button: name for name, button in _BUTTONS.items()}

#: Names treated as modifiers when ordering hotkey combos (ctrl+alt+shift+key).
MODIFIER_NAMES: frozenset[str] = frozenset(
    {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "shift", "shift_l", "shift_r", "cmd", "cmd_r"}
)

#: Side-specific modifier keys mapped onto their generic alias so that a
#: parsed combo like ``ctrl+shift+d`` matches whichever physical keys the
#: user actually presses (e.g. ``ctrl_l``).
_MODIFIER_ALIASES: dict[kb.Key, kb.Key] = {
    kb.Key.ctrl_l: kb.Key.ctrl,
    kb.Key.ctrl_r: kb.Key.ctrl,
    kb.Key.alt_l: kb.Key.alt,
    kb.Key.alt_r: kb.Key.alt,
    kb.Key.shift_l: kb.Key.shift,
    kb.Key.shift_r: kb.Key.shift,
    kb.Key.cmd: kb.Key.cmd,
    kb.Key.cmd_r: kb.Key.cmd,
}


#: Everything that can appear in a parsed hotkey combination or in the set of
#: currently pressed keys: named keys, raw character codes, plain characters.
HotkeyKey = kb.Key | kb.KeyCode | str


def _canonical_hotkey_key(key: HotkeyKey) -> HotkeyKey:
    """Normalize side-specific modifiers and character case."""
    if isinstance(key, kb.KeyCode):
        if key.char:
            return key.char.lower()
        return key
    if isinstance(key, kb.Key):
        return _MODIFIER_ALIASES.get(key, key)
    return key


def _to_kb_key(name: str) -> HotkeyKey | None:
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


def to_pynput_key(key: kb.Key | kb.KeyCode) -> str:
    """Inverse of :func:`_to_kb_key`: a pynput key event -> canonical name."""
    if isinstance(key, kb.KeyCode):
        if key.char:
            return key.char
        if key.vk is not None:
            return f"<{hex(key.vk)}>"
        return "<unknown>"
    return key.name


def _to_ms_button(name: str) -> ms.Button:
    return _BUTTONS.get(name, ms.Button.left)


def is_modifier_name(name: str) -> bool:
    """True when a pynput-style key name denotes a modifier (``ctrl``, ``shift_l``, ...)."""
    return name in MODIFIER_NAMES or name.startswith(("ctrl", "alt", "shift", "cmd"))


def sort_combo(combo: str) -> str:
    """Normalize a ``"a+ctrl"`` string into modifier-first order ``"ctrl+a"``."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    modifiers = sorted(p for p in parts if is_modifier_name(p))
    others = [p for p in parts if not is_modifier_name(p)]
    return "+".join(modifiers + others)


def describe_hotkey(combo: str) -> str:
    """Human-readable form for UI captions: 'ctrl+shift+d' -> 'Ctrl+Shift+D'."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    aliases = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd"}
    return "+".join(aliases.get(p, p.upper()) for p in parts)
