"""Pynput-specific key/button naming helpers for the input component.

These functions translate between the simple string names used throughout
the app (``"ctrl"``, ``"f5"``, ``"left"``) and pynput's key/button objects.
Keeping them in one module means the rest of the code base never has to
touch pynput types just to resolve a name.  The pure-string half of the
naming logic (modifier sets, combo ordering, human captions) is shared
app-wide and lives in :mod:`common.events`.
"""

from __future__ import annotations

import logging

from pynput import keyboard as kb
from pynput import mouse as ms

# Re-exported for backwards compatibility; common.events is the single
# source of truth for the pure-string naming helpers.
__all__ = [
    "HotkeyKey",
    "MODIFIER_NAMES",
    "to_pynput_key",
]

from common.events import MODIFIER_NAMES

logger = logging.getLogger(__name__)

_BUTTONS: dict[str, ms.Button] = {
    "left": ms.Button.left,
    "right": ms.Button.right,
    "middle": ms.Button.middle,
}

_BUTTON_NAMES: dict[ms.Button, str] = {button: name for name, button in _BUTTONS.items()}

# MODIFIER_NAMES is imported from common.events above (single source of truth).

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


# is_modifier_name / sort_combo / describe_hotkey are pure string helpers and
# live in common.events (shared with the UI layer); import them from there.
