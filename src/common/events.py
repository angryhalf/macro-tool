"""Canonical key/button naming helpers shared across features.

These functions translate between the simple string names used throughout
the app (``"ctrl"``, ``"f5"``, ``"left"``) and human-readable captions.
They are pure string utilities -- no pynput, no Qt -- so every layer can
use them; the pynput-specific half of the naming logic lives with the
input feature in :mod:`components.input.naming`.
"""

from __future__ import annotations

#: Names treated as modifiers when ordering hotkey combos (ctrl+alt+shift+key).
MODIFIER_NAMES: frozenset[str] = frozenset(
    {
        "ctrl", "ctrl_l", "ctrl_r",
        "alt", "alt_l", "alt_r",
        "shift", "shift_l", "shift_r",
        "cmd", "cmd_r",
    }
)


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
