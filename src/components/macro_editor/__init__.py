"""Macro editor dialogs (split from the former single-file ``macro_editor.py``).

The pieces live in focused modules and are re-exported here so the public
import path ``from components.macro_editor import MacroEditorDialog``
(macro_list_tab's entry point) is unchanged from the single-file era:

* :mod:`components.macro_editor.action_editor` -- :class:`ActionEditorDialog`
  (one step of the action list, per-kind pages, single-field recording).
* :mod:`components.macro_editor.recorder_dialog` -- :class:`MacroRecorderDialog`
  (whole-session input recording).
* :mod:`components.macro_editor.macro_editor` -- :class:`MacroEditorDialog`
  (macro metadata + ordered action table).

The events -> actions conversion those dialogs rely on is pure domain
logic and lives in :mod:`components.macro.recording`, importable (and
testable) from every layer.  See ``docs/architecture-review-2026-11-11.md``
for the review that motivated the split.
"""

from __future__ import annotations

from components.macro_editor.action_editor import ActionEditorDialog
from components.macro_editor.macro_editor import MacroEditorDialog
from components.macro_editor.recorder_dialog import MacroRecorderDialog

__all__ = [
    "ActionEditorDialog",
    "MacroEditorDialog",
    "MacroRecorderDialog",
]
