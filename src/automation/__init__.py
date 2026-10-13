"""Keyboard and mouse automation built on pynput.

This package is the single place that talks to the operating system for
input synthesis (macro actions), global hotkey listening and live input
recording.  Keeping it isolated behind small, typed helpers makes the rest
of the code base independent from the pynput API:

* :func:`perform_action` -- execute one :class:`~models.macro.ActionConfig`.
* :class:`HotkeyManager` -- register/replace global hotkeys from any thread.
* :class:`InputRecorder` -- record live keyboard/mouse events.
* Naming helpers (:func:`describe_hotkey`, :func:`sort_combo`, ...) keep
  key/button names canonical across the app.

The implementation lives in focused submodules --
:mod:`automation.naming` (canonical names), :mod:`automation.synthesis`
(playing actions), :mod:`automation.hotkeys` (global hotkey listener) and
:mod:`automation.recorder` (live input capture).  They are re-exported here
so callers can simply write ``from automation import InputRecorder``.
"""

from __future__ import annotations

from automation.hotkeys import HotkeyManager
from automation.naming import (
    MODIFIER_NAMES,
    HotkeyKey,
    describe_hotkey,
    is_modifier_name,
    sort_combo,
    to_pynput_key,
)
from automation.recorder import InputRecorder
from automation.synthesis import current_mouse_position, perform_action

__all__ = [
    "HotkeyKey",
    "HotkeyManager",
    "InputRecorder",
    "MODIFIER_NAMES",
    "current_mouse_position",
    "describe_hotkey",
    "is_modifier_name",
    "perform_action",
    "sort_combo",
    "to_pynput_key",
]
