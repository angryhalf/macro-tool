"""Keyboard and mouse automation built on pynput (the *input* component).

This package is the single place that talks to the operating system for
input synthesis (macro actions), global hotkey listening and live input
recording.  Keeping it isolated behind small, typed helpers makes the rest
of the code base independent from the pynput API:

* :func:`perform_action` -- execute one :class:`~components.macro.macro.ActionConfig`.
* :class:`HotkeyManager` -- register/replace global hotkeys from any thread.
* :class:`InputRecorder` -- record live keyboard/mouse events.
* Naming helpers (:func:`to_pynput_key`, ...) keep key/button names
  canonical across the app; the pure-string half of that logic
  (:func:`describe_hotkey`, :func:`sort_combo`, ...) lives in
  :mod:`common.events` because the UI layer needs it too.

The implementation lives in focused submodules --
:mod:`components.input.naming` (pynput name resolution),
:mod:`components.input.synthesis` (playing actions),
:mod:`components.input.hotkeys` (global hotkey listener) and
:mod:`components.input.recorder` (live input capture).  They are re-exported
here so callers can simply write ``from components.input import InputRecorder``.
"""

from __future__ import annotations

from common.events import MODIFIER_NAMES, describe_hotkey, is_modifier_name, sort_combo
from components.input.hotkeys import HotkeyManager
from components.input.naming import HotkeyKey, to_pynput_key
from components.input.recorder import InputRecorder
from components.input.synthesis import current_mouse_position, perform_action

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
