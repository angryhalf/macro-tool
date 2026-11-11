"""Edit one macro: metadata plus its ordered action table.

:class:`MacroEditorDialog` edits a :class:`~components.macro.macro.MacroConfig`
top-to-bottom:

* **General** -- name, start hotkey and the enabled flag.  Looping is no
  longer a general setting: add "Loop start" / "Loop end" steps in the
  Actions table instead (the legacy repeat/loops fields are migrated
  automatically when an old settings file is loaded).
  The user turns the macro on and off; there is no separate pause state.
* **Actions** -- an ordered table of steps with add/edit/remove/reorder
  controls plus a whole-session recorder.  The triggers are *actions*:
  insert a "Stop trigger" step to open a gated stretch and a "Start
  trigger" step to close it -- everything written between them is held back
  while the stop-trigger step's screen rule is present on screen.

This module was split out of the former 1232-line ``macro_editor.py``
(see docs/architecture-review-2026-11-11.md, candidate 2).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from components.macro.macro import ActionConfig, MacroConfig, new_macro_uid
from components.macro_editor.action_editor import ActionEditorDialog
from components.macro_editor.recorder_dialog import MacroRecorderDialog
from components.ui.widgets import HotkeyButton


class MacroEditorDialog(QDialog):
    """Edit one macro.

    Layout top-to-bottom:

    * **General** -- name, start hotkey and the enabled flag.  Looping is no
      longer a general setting: add "Loop start" / "Loop end" steps in the
      Actions table instead (the legacy repeat/loops fields are migrated
      automatically when an old settings file is loaded).
      The user turns the macro on and off; there is no separate pause state.
    * **Actions** -- an ordered table of steps with add/edit/remove/reorder
      controls plus a whole-session recorder.  The triggers are *actions*:
      insert a "Stop trigger" step to open a gated stretch and a "Start
      trigger" step to close it -- everything written between them is held
      back while the stop-trigger step's screen rule is present on screen.
    """

    def __init__(
        self,
        macro: MacroConfig,
        parent: QWidget | None = None,
        stop_hotkey: str = "f8",
    ) -> None:
        super().__init__(parent)
        self._stop_hotkey = stop_hotkey
        # Preserve identity across edits; stamp a fresh uid for brand-new macros.
        self._uid = macro.uid or new_macro_uid()
        self.setWindowTitle(f"Edit macro — {macro.name}")
        self.setMinimumSize(680, 620)

        # -- general ---------------------------------------------------
        self._name = QLineEdit(macro.name)
        self._hotkey = HotkeyButton(macro.start_hotkey)
        self._enabled = QCheckBox("Enabled (unchecked macros ignore their hotkey and Run clicks)")
        self._enabled.setChecked(macro.enabled)

        meta_form = QFormLayout()
        meta_form.addRow("Name:", self._name)
        meta_form.addRow("Start hotkey:", self._hotkey)
        meta_form.addRow("", self._enabled)
        meta_box = QGroupBox("General")
        meta_box.setLayout(meta_form)

        # -- actions ---------------------------------------------------
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Action", "Details"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._edit_action)

        toolbar = QHBoxLayout()
        for text, slot in (
            ("Add", self._add_action),
            ("Edit", self._edit_action),
            ("Remove", self._remove_action),
            ("Up", lambda: self._move(-1)),
            ("Down", lambda: self._move(+1)),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()
        record_button = QPushButton("🎥 Record inputs…")
        record_button.clicked.connect(self._record_inputs)
        toolbar.addWidget(record_button)

        actions_box = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.addLayout(toolbar)
        actions_layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(meta_box)
        layout.addWidget(actions_box, 1)
        layout.addWidget(buttons)

        self._actions: list[ActionConfig] = list(macro.actions)
        self._reload_table()

    # ------------------------------------------------------------------
    # Action table
    # ------------------------------------------------------------------
    @staticmethod
    def describe(action: ActionConfig) -> tuple[str, str]:
        """Human-readable (type, details) pair for the action table."""
        where = "" if action.x is None else f" at ({action.x}, {action.y})"
        cond = MacroEditorDialog._describe_condition(action)
        labels = {
            "key": ("Press key", action.key),
            "hold_key": ("Hold key", f"{action.key} for {action.duration_ms} ms"),
            "combo": ("Hotkey combo", action.combo),
            "type": ("Type text", f'"{action.text}"'),
            "move": ("Move mouse", f"({action.x}, {action.y})"),
            "click": ("Click", action.button + where),
            "double_click": ("Double click", action.button + where),
            "mouse_down": ("Mouse down", action.button),
            "mouse_up": ("Mouse up", action.button),
            "drag": (
                "Drag",
                f"{action.button}: ({action.x}, {action.y}) → ({action.amount}, {action.duration_ms})",
            ),
            "scroll": ("Scroll", str(action.amount)),
            "wait": ("Wait", f"{action.duration_ms} ms"),
            "wait_for": ("Wait until", cond),
            "stop_trigger": ("Stop trigger", cond),
            "start_trigger": ("Start trigger", cond),
            "loop_start": (
                "Loop start",
                f"forever, {action.duration_ms} ms delay"
                if action.duration_ms
                else "forever",
            ),
            "loop_end": (
                "Loop end",
                f"{action.duration_ms} ms delay" if action.duration_ms else "",
            ),
        }
        return labels.get(action.kind, (action.kind, ""))

    @staticmethod
    def _describe_condition(action: ActionConfig) -> str:
        """Short summary of a flow action's screen rule for the table."""
        condition = action.condition
        if condition is None or not condition.configured:
            return "no rule configured"
        parts = [condition.description]
        # The give-up timeout only means something for wait_for; drag steps
        # reuse duration_ms as the end Y coordinate and must not be mislabeled.
        if action.duration_ms and action.kind == "wait_for":
            parts.append(f"gives up after {action.duration_ms} ms")
        return ", ".join(part for part in parts if part) or "rule configured"

    def _reload_table(self) -> None:
        self._table.setRowCount(len(self._actions))
        for row, action in enumerate(self._actions):
            name, details = self.describe(action)
            self._table.setItem(row, 0, QTableWidgetItem(name))
            self._table.setItem(row, 1, QTableWidgetItem(details))

    def _selected_row(self) -> int:
        return self._table.currentRow()

    def _add_action(self) -> None:
        dialog = ActionEditorDialog(parent=self)
        if dialog.exec():
            self._actions.append(dialog.action())
            self._reload_table()

    def _edit_action(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        dialog = ActionEditorDialog(self._actions[row], parent=self)
        if dialog.exec():
            self._actions[row] = dialog.action()
            self._reload_table()

    def _remove_action(self) -> None:
        row = self._selected_row()
        if row >= 0:
            del self._actions[row]
            self._reload_table()

    def _move(self, delta: int) -> None:
        row = self._selected_row()
        target = row + delta
        if 0 <= row < len(self._actions) and 0 <= target < len(self._actions):
            self._actions[row], self._actions[target] = self._actions[target], self._actions[row]
            self._reload_table()
            self._table.selectRow(target)

    def _record_inputs(self) -> None:
        dialog = MacroRecorderDialog(stop_hotkey=self._stop_hotkey, parent=self)
        if dialog.exec():
            recorded = dialog.actions()
            if recorded:
                self._actions.extend(recorded)
                self._reload_table()

    # ------------------------------------------------------------------
    def macro(self) -> MacroConfig:
        """Build the :class:`MacroConfig` described by this dialog.

        Looping lives entirely in the action list now (``loop_start`` /
        ``loop_end`` steps), so the legacy repeat/loops/interval fields are
        left at their neutral defaults.
        """
        return MacroConfig(
            name=self._name.text().strip() or "Unnamed macro",
            start_hotkey=self._hotkey.hotkey,
            actions=tuple(self._actions),
            uid=self._uid,
            enabled=self._enabled.isChecked(),
        )
