"""Advanced watchers tab: standalone background screen triggers.

The primary way to react to the screen is via a macro's own start/stop
conditions (see :mod:`ui.macro_editor`).  This tab offers an additional,
always-on watcher that can *launch* a macro or run inline actions when a
:class:`~app.conditions.ScreenCondition` fires -- useful for one-off
reactions that shouldn't be tied to a specific macro's lifecycle.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.settings import WatcherConfig
from ui.condition_editor import ConditionEditor
from ui.macro_editor import ActionEditorDialog


class _WatcherEditor(QWidget):
    """Inline editor bound to one watcher config."""

    def __init__(self, macros_provider: Callable[[], list[str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._macros_provider = macros_provider
        self._actions: list = []

        self.name_edit = QLineEdit()
        self.condition = ConditionEditor(show_timeout=False)
        self.cooldown = QSpinBox(minimum=0, maximum=600_000, value=1000)
        self.cooldown.setSuffix(" ms")
        self.auto_start = QCheckBox("Auto-start when the app launches")
        self.target_macro = QComboBox()
        self.actions_summary = QLabel("No direct actions")
        add_action = QPushButton("Add action…")
        add_action.clicked.connect(self._add_action)
        clear_actions = QPushButton("Clear actions")
        clear_actions.clicked.connect(self._clear_actions)

        self.refresh_macros()

        form = QFormLayout(self)
        form.addRow("Name:", self.name_edit)
        form.addRow("Cooldown between triggers:", self.cooldown)
        form.addRow("Then run macro:", self.target_macro)
        actions_row = QHBoxLayout()
        actions_row.addWidget(self.actions_summary)
        actions_row.addWidget(add_action)
        actions_row.addWidget(clear_actions)
        form.addRow("Extra actions:", actions_row)
        form.addRow(self.auto_start)

    # ------------------------------------------------------------------
    def load_config(self, config: WatcherConfig) -> None:
        self.name_edit.setText(config.name)
        self.condition.load(config.condition)
        self.cooldown.setValue(config.cooldown_ms)
        self.auto_start.setChecked(config.auto_start)
        self.refresh_macros()
        self.target_macro.setCurrentText(config.target_macro)
        self._actions = list(config.actions)
        self._update_actions_summary()

    def blank(self) -> WatcherConfig:
        draft = WatcherConfig(name=f"Watcher {time.strftime('%H:%M:%S')}")
        self.load_config(draft)
        return draft

    def refresh_macros(self) -> None:
        current = self.target_macro.currentText()
        self.target_macro.blockSignals(True)
        self.target_macro.clear()
        self.target_macro.addItem("— none —")
        self.target_macro.addItems(self._macros_provider())
        index = self.target_macro.findText(current)
        self.target_macro.setCurrentIndex(index if index >= 0 else 0)
        self.target_macro.blockSignals(False)

    def build_config(self) -> WatcherConfig:
        return WatcherConfig(
            name=self.name_edit.text().strip() or "Unnamed watcher",
            condition=self.condition.build(),
            cooldown_ms=self.cooldown.value(),
            auto_start=self.auto_start.isChecked(),
            target_macro="" if self.target_macro.currentIndex() == 0 else self.target_macro.currentText(),
            actions=tuple(self._actions),
        )

    def _add_action(self) -> None:
        dialog = ActionEditorDialog(parent=self.window())
        if dialog.exec():
            self._actions.append(dialog.action())
            self._update_actions_summary()

    def _clear_actions(self) -> None:
        self._actions = []
        self._update_actions_summary()

    def _update_actions_summary(self) -> None:
        count = len(self._actions)
        self.actions_summary.setText("No direct actions" if count == 0 else f"{count} action(s)")


class WatchersTab(QWidget):
    """Left list of watchers + right inline editor + start/stop controls."""

    def __init__(
        self,
        on_changed: Callable[[list[WatcherConfig]], None],
        on_start: Callable[[WatcherConfig], None],
        on_stop: Callable[[str], None],
        macros_provider: Callable[[], list[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_changed = on_changed
        self._on_start = on_start
        self._on_stop = on_stop
        self._watchers: list[WatcherConfig] = []
        self._editing_name: str | None = None

        self._hint = QLabel(
            "<i>Tip: most reactions belong inside a macro's Start / Stop "
            "condition. Use this tab only for always-on triggers that should "
            "launch other macros.</i>"
        )
        self._hint.setWordWrap(True)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)

        list_buttons = QHBoxLayout()
        for text, slot in (("New", self._new_watcher), ("Delete", self._delete_watcher)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            list_buttons.addWidget(button)

        left = QVBoxLayout()
        left.addWidget(self._list)
        left.addLayout(list_buttons)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(240)

        self._editor = _WatcherEditor(macros_provider)

        self._start_button = QPushButton("▶ Start watcher")
        self._start_button.clicked.connect(self._start_selected)
        self._stop_button = QPushButton("■ Stop watcher")
        self._stop_button.clicked.connect(self._stop_selected)
        control_row = QHBoxLayout()
        control_row.addWidget(self._start_button)
        control_row.addWidget(self._stop_button)
        control_row.addStretch()

        save_row = QDialogButtonBox(QDialogButtonBox.StandardButton.Save)
        save_row.accepted.connect(self._save_clicked)

        right = QVBoxLayout()
        right.addWidget(self._editor)
        right.addLayout(control_row)
        right.addWidget(save_row)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter_layout = QVBoxLayout(self)
        splitter_layout.addWidget(self._hint)
        body = QHBoxLayout()
        body.addWidget(left_widget)
        body.addWidget(right_widget, 3)
        splitter_layout.addLayout(body)

    # ------------------------------------------------------------------
    def set_watchers(self, watchers: list[WatcherConfig]) -> None:
        """Replace the model (e.g. after loading settings)."""
        selected = self._editing_name
        self._watchers = list(watchers)
        self._list.clear()
        for watcher in self._watchers:
            self._list.addItem(watcher.name)
        if selected:
            row = next((i for i, w in enumerate(self._watchers) if w.name == selected), -1)
            if row >= 0:
                self._list.setCurrentRow(row)

    def set_running_state(self, running_names: set[str]) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            base = item.text().split(" ●")[0]
            item.setText(f"{base} ●" if base in running_names else base)

    def refresh_macro_choices(self) -> None:
        self._editor.refresh_macros()

    # ------------------------------------------------------------------
    def _on_selection(self, current, previous) -> None:
        del previous
        if current is None:
            self._editing_name = None
            return
        name = current.text().split(" ●")[0]
        watcher = next((w for w in self._watchers if w.name == name), None)
        if watcher is not None:
            self._editing_name = watcher.name
            self._editor.load_config(watcher)

    def _new_watcher(self) -> None:
        self._editor.blank()
        self._editing_name = None

    def _delete_watcher(self) -> None:
        name = self._current_name()
        if name is None:
            return
        self._on_stop(name)
        self._watchers = [w for w in self._watchers if w.name != name]
        self.set_watchers(self._watchers)
        self._on_changed(self._watchers)

    def _save_clicked(self) -> None:
        config = self._editor.build_config()
        if self._editing_name is None:
            self._watchers.append(config)
        else:
            self._watchers = [config if w.name == self._editing_name else w for w in self._watchers]
        self._editing_name = config.name
        self.set_watchers(self._watchers)
        self._select_by_name(config.name)
        self._on_changed(self._watchers)

    def _start_selected(self) -> None:
        watcher = self._current_watcher()
        if watcher is not None:
            self._sync_editor_before_action()
            self._on_start(self._current_watcher() or watcher)

    def _stop_selected(self) -> None:
        name = self._current_name()
        if name:
            self._on_stop(name)

    # ------------------------------------------------------------------
    def _current_name(self) -> str | None:
        item = self._list.currentItem()
        return item.text().split(" ●")[0] if item else None

    def _current_watcher(self) -> WatcherConfig | None:
        name = self._current_name()
        return next((w for w in self._watchers if w.name == name), None) if name else None

    def _sync_editor_before_action(self) -> None:
        """Persist any pending edits made in the right-hand panel."""
        if self._editing_name is not None:
            updated = self._editor.build_config()
            self._watchers = [updated if w.name == self._editing_name else w for w in self._watchers]
            self.set_watchers(self._watchers)
            self._select_by_name(updated.name)
            self._on_changed(self._watchers)

    def _select_by_name(self, name: str) -> None:
        row = next((i for i, w in enumerate(self._watchers) if w.name == name), -1)
        if row >= 0:
            self._list.setCurrentRow(row)

