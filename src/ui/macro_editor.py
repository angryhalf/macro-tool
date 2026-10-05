"""Dialog for editing a single macro: metadata, hotkey and action list."""

from __future__ import annotations

import mouse as ms
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.settings import ActionConfig, MacroConfig
from src.ui.widgets import HotkeyButton

ACTION_KINDS = {
    "Press key": "key",
    "Hold key": "hold_key",
    "Move mouse": "move",
    "Click": "click",
    "Double click": "double_click",
    "Scroll": "scroll",
    "Wait": "wait",
}
BUTTONS = ["left", "right", "middle"]


class ActionEditorDialog(QDialog):
    """Modal editor for one :class:`ActionConfig`."""

    def __init__(self, action: ActionConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macro action")
        action = action or ActionConfig()

        self._kind = QComboBox()
        for label, kind in ACTION_KINDS.items():
            self._kind.addItem(label, kind)
        self._kind.setCurrentIndex(max(0, list(ACTION_KINDS.values()).index(action.kind)))
        self._kind.currentIndexChanged.connect(self._update_visibility)

        self._key = QLineEdit(action.key)
        self._key.setPlaceholderText("e.g. a, space, f1")
        self._button = QComboBox()
        self._button.addItems(BUTTONS)
        self._button.setCurrentText(action.button)
        self._x = QSpinBox(minimum=-32000, maximum=32000, value=action.x or 0)
        self._y = QSpinBox(minimum=-32000, maximum=32000, value=action.y or 0)
        self._amount = QSpinBox(minimum=-100, maximum=100, value=action.amount)
        self._duration = QSpinBox(minimum=0, maximum=600_000, value=action.duration_ms)

        self._record_button = QPushButton("Record current mouse position")
        self._record_button.clicked.connect(self._record_position)
        self._form = QFormLayout()
        self._form.addRow("Type:", self._kind)
        self._form.addRow("Key:", self._key)
        self._form.addRow("Mouse button:", self._button)
        self._form.addRow("Position X:", self._x)
        self._form.addRow("Position Y:", self._y)
        self._form.addRow("Scroll amount:", self._amount)
        self._form.addRow("Duration / wait (ms):", self._duration)
        self._form.addRow("", self._record_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(self._form)
        layout.addWidget(buttons)
        self._update_visibility()

    # ------------------------------------------------------------------
    def _visible_fields(self) -> dict[str, list[QWidget]]:
        return {
            "key": [self._key],
            "hold_key": [self._key, self._duration],
            "move": [self._x, self._y, self._record_button],
            "click": [self._button, self._record_button],
            "double_click": [self._button, self._record_button],
            "scroll": [self._amount],
            "wait": [self._duration],
        }

    def _row_widget(self, widget: QWidget) -> QWidget:
        """Return the form row container holding *widget* (label + field)."""
        item = self._form.itemAtPosition(self._form.getWidgetPosition(widget)[0], 0)
        return item.widget() if item and item.widget() else widget

    def _update_visibility(self) -> None:
        visible = set(self._visible_fields().get(self._kind.currentData(), []))
        for widgets in self._visible_fields().values():
            for widget in widgets:
                row = self._row_widget(widget)
                row.setVisible(widget in visible)

    def _record_position(self) -> None:
        x, y = ms.position()
        self._x.setValue(int(x))
        self._y.setValue(int(y))

    def action(self) -> ActionConfig:
        kind = self._kind.currentData()
        uses_position = kind in ("move", "click", "double_click")
        return ActionConfig(
            kind=kind,
            key=self._key.text().strip().lower(),
            button=self._button.currentText(),
            x=self._x.value() if uses_position else None,
            y=self._y.value() if uses_position else None,
            amount=self._amount.value(),
            duration_ms=self._duration.value(),
        )




class MacroEditorDialog(QDialog):
    """Edit one macro: name, hotkey, loop options and its action sequence."""

    def __init__(self, macro: MacroConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit macro — {macro.name}")
        self.setMinimumSize(560, 420)

        self._name = QLineEdit(macro.name)
        self._hotkey = HotkeyButton(macro.start_hotkey)
        self._repeat = QCheckBox("Repeat until stopped")
        self._repeat.setChecked(macro.repeat)
        self._loops = QSpinBox(minimum=1, maximum=9999, value=max(1, macro.loops))
        self._loops.setEnabled(not macro.repeat)
        self._repeat.toggled.connect(self._loops.setDisabled)
        self._interval = QSpinBox(minimum=0, maximum=600_000, value=macro.interval_ms)

        meta = QFormLayout()
        meta.addRow("Name:", self._name)
        meta.addRow("Start hotkey:", self._hotkey)
        meta.addRow("Repeat:", self._repeat)
        meta.addRow("Loops:", self._loops)
        meta.addRow("Loop interval (ms):", self._interval)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Action", "Details"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        toolbar = QHBoxLayout()
        for text, slot in (("Add", self._add_action), ("Edit", self._edit_action), ("Remove", self._remove_action)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(meta)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)
        layout.addWidget(buttons)

        self._actions: list[ActionConfig] = list(macro.actions)
        self._reload_table()

    # ------------------------------------------------------------------
    # Action table
    # ------------------------------------------------------------------
    @staticmethod
    def describe(action: ActionConfig) -> tuple[str, str]:
        labels = {
            "key": ("Press key", action.key),
            "hold_key": ("Hold key", f"{action.key} for {action.duration_ms} ms"),
            "move": ("Move mouse", f"({action.x}, {action.y})"),
            "click": ("Click", action.button),
            "double_click": ("Double click", action.button),
            "scroll": ("Scroll", str(action.amount)),
            "wait": ("Wait", f"{action.duration_ms} ms"),
        }
        return labels.get(action.kind, (action.kind, ""))

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
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._actions.append(dialog.action())
            self._reload_table()

    def _edit_action(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        dialog = ActionEditorDialog(self._actions[row], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._actions[row] = dialog.action()
            self._reload_table()

    def _remove_action(self) -> None:
        row = self._selected_row()
        if row >= 0:
            del self._actions[row]
            self._reload_table()

    # ------------------------------------------------------------------
    def macro(self) -> MacroConfig:
        return MacroConfig(
            name=self._name.text().strip() or "Unnamed macro",
            start_hotkey=self._hotkey.hotkey,
            repeat=self._repeat.isChecked(),
            loops=self._loops.value(),
            interval_ms=self._interval.value(),
            actions=tuple(self._actions),
        )
