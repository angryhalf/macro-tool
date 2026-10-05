"""Watchers tab: define screen-reaction rules (image found / region changed)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.settings import WatcherConfig
from src.ui.macro_editor import ActionEditorDialog
from src.ui.widgets import RegionPreviewWidget, crop_and_save_template

MODE_LABELS = {
    "Image appears": "image_found",
    "Image disappears": "image_missing",
    "Region changes": "region_changed",
}
TEMPLATES_DIR = Path("templates")


class WatcherEditorDialog(QWidget):
    """Inline editor bound to one watcher config."""

    def __init__(self, macros_provider: Callable[[], list[str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._macros_provider = macros_provider
        self._config = WatcherConfig()
        self._template_path = ""

        self.name_edit = QLineEdit()
        self.mode_combo = QComboBox()
        for label, mode in MODE_LABELS.items():
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_visibility)

        self.template_label = QLabel("No template selected")
        self.template_label.setStyleSheet("color: gray;")
        self.preview = RegionPreviewWidget()
        self.capture_button = QPushButton("Capture template from screen…")
        self.capture_button.clicked.connect(self._capture_template)
        self.load_button = QPushButton("Load file…")
        self.load_button.clicked.connect(self._load_file)

        self.confidence_slider = QSlider(Qt.Orientation.Horizontal)
        self.confidence_slider.setRange(50, 100)
        self.confidence_slider.setValue(85)
        self.confidence_value = QLabel("0.85")
        self.confidence_slider.valueChanged.connect(
            lambda v: self.confidence_value.setText(f"{v / 100:.2f}")
        )

        self.poll_interval = QSpinBox(minimum=50, maximum=10_000, singleStep=50, value=200)
        self.cooldown = QSpinBox(minimum=0, maximum=600_000, value=1000)
        self.change_threshold = QDoubleSpinBox(decimals=1, minimum=0.1, maximum=255.0, value=5.0)

        self.region_label = QLabel("Full screen")
        self.pick_region_button = QPushButton("Pick region…")
        self.pick_region_button.clicked.connect(self._pick_region)
        self._region: tuple[int, int, int, int] | None = None

        self.auto_start = QCheckBox("Auto-start when the app launches")
        self.target_macro = QComboBox()
        self.refresh_macros()

        self.actions_summary = QLabel("No direct actions")
        add_action = QPushButton("Add action…")
        add_action.clicked.connect(self._add_action)
        clear_action = QPushButton("Clear actions")
        clear_action.clicked.connect(self._clear_actions)
        self._actions: list = []

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Trigger when:", self.mode_combo)
        form.addRow("Template:", self.template_label)
        template_row = QHBoxLayout()
        template_row.addWidget(self.capture_button)
        template_row.addWidget(self.load_button)
        form.addRow("", template_row)
        form.addRow(self.preview)
        form.addRow("Match confidence:", self._row(self.confidence_slider, self.confidence_value))
        form.addRow("Poll interval (ms):", self.poll_interval)
        form.addRow("Cooldown (ms):", self.cooldown)
        form.addRow("Change sensitivity:", self.change_threshold)
        form.addRow("Watched region:", self._row(self.region_label, self.pick_region_button))
        form.addRow("Then run macro:", self.target_macro)
        actions_row = QHBoxLayout()
        actions_row.addWidget(self.actions_summary)
        actions_row.addWidget(add_action)
        actions_row.addWidget(clear_action)
        form.addRow("Extra actions:", actions_row)
        form.addRow(self.auto_start)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_clicked)
        buttons.rejected.connect(self._cancel_clicked)
        self._save_cb: Callable[[WatcherConfig], None] | None = None
        self._cancel_cb: Callable[[], None] | None = None

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._update_mode_visibility()

    # ------------------------------------------------------------------
    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row.addWidget(widget, 1 if len(widgets) == 2 else 0)
        if len(widgets) == 2:
            row.setStretchFactor(widgets[0], 3)
            row.setStretchFactor(widgets[1], 1)
        return container

    # ------------------------------------------------------------------
    # Editor population
    # ------------------------------------------------------------------
    def load_config(self, config: WatcherConfig) -> None:
        self._config = config
        self.name_edit.setText(config.name)
        index = self.mode_combo.findData(config.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self._template_path = config.template_path
        self.template_label.setText(Path(config.template_path).name or "No template selected")
        self.confidence_slider.setValue(int(config.confidence * 100))
        self.poll_interval.setValue(config.poll_interval_ms)
        self.cooldown.setValue(config.cooldown_ms)
        self.change_threshold.setValue(config.change_threshold)
        self._region = config.region
        self.region_label.setText(str(config.region))
        self.auto_start.setChecked(config.auto_start)
        self.refresh_macros()
        self.target_macro.setCurrentText(config.target_macro)
        self._actions = list(config.actions)
        self._update_actions_summary()
        self._update_mode_visibility()

    def blank(self) -> None:
        self.load_config(WatcherConfig(name=f"Watcher {time.strftime('%H:%M:%S')}"))

    def refresh_macros(self) -> None:
        current = self.target_macro.currentText()
        self.target_macro.blockSignals(True)
        self.target_macro.clear()
        self.target_macro.addItem("— none —")
        self.target_macro.addItems(self._macros_provider())
        index = self.target_macro.findText(current)
        self.target_macro.setCurrentIndex(index if index >= 0 else 0)
        self.target_macro.blockSignals(False)

    # ------------------------------------------------------------------
    # Callbacks wired by the owner tab
    # ------------------------------------------------------------------
    def set_callbacks(self, on_save: Callable[[WatcherConfig], None], on_cancel: Callable[[], None]) -> None:
        self._save_cb = on_save
        self._cancel_cb = on_cancel

    def _save_clicked(self) -> None:
        if self._save_cb:
            self._save_cb(self.build_config())

    def _cancel_clicked(self) -> None:
        if self._cancel_cb:
            self._cancel_cb()

    def build_config(self) -> WatcherConfig:
        mode = self.mode_combo.currentData()
        return WatcherConfig(
            name=self.name_edit.text().strip() or "Unnamed watcher",
            mode=mode,
            template_path=self._template_path,
            confidence=self.confidence_slider.value() / 100.0,
            poll_interval_ms=self.poll_interval.value(),
            cooldown_ms=self.cooldown.value(),
            change_threshold=self.change_threshold.value(),
            auto_start=self.auto_start.isChecked(),
            target_macro="" if self.target_macro.currentIndex() == 0 else self.target_macro.currentText(),
            actions=tuple(self._actions),
            region=self._region or (0, 0, 1920, 1080),
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _update_mode_visibility(self) -> None:
        uses_template = self.mode_combo.currentData() != "region_changed"
        self.template_label.setVisible(uses_template)
        self.capture_button.setVisible(uses_template)
        self.load_button.setVisible(uses_template)
        self.confidence_slider.setVisible(uses_template)
        self.confidence_value.setVisible(uses_template)
        self.change_threshold.setVisible(not uses_template)

    def _capture_template(self) -> None:
        TEMPLATES_DIR.mkdir(exist_ok=True)
        path = str(TEMPLATES_DIR / f"template_{int(time.time())}.png")
        if crop_and_save_template(self, path):
            self._template_path = path
            self.template_label.setText(Path(path).name)

    def _load_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Load template", str(TEMPLATES_DIR), "Images (*.png *.jpg *.bmp)")
        if path:
            self._template_path = path
            self.template_label.setText(Path(path).name)

    def _pick_region(self) -> None:
        from src.services.region_picker import RegionPickerDialog

        rect = RegionPickerDialog.pick(self)
        if rect is not None:
            self._region = rect
            self.region_label.setText(str(rect))

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
    """Left list of watchers + right inline editor."""

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

        self._editor = WatcherEditorDialog(macros_provider)
        self._editor.set_callbacks(self._save_editor, lambda: None)

        self._start_button = QPushButton("▶ Start watcher")
        self._start_button.clicked.connect(self._start_selected)
        self._stop_button = QPushButton("■ Stop watcher")
        self._stop_button.clicked.connect(self._stop_selected)
        control_row = QHBoxLayout()
        control_row.addWidget(self._start_button)
        control_row.addWidget(self._stop_button)
        control_row.addStretch()

        right = QVBoxLayout()
        right.addWidget(self._editor)
        right.addLayout(control_row)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter_layout = QHBoxLayout(self)
        splitter_layout.addWidget(left_widget)
        splitter_layout.addWidget(right_widget, 3)

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
        self._editing_name = None  # a save with no prior name creates a new entry

    def _delete_watcher(self) -> None:
        name = self._current_name()
        if name is None:
            return
        self._on_stop(name)
        self._watchers = [w for w in self._watchers if w.name != name]
        self.set_watchers(self._watchers)
        self._on_changed(self._watchers)

    def _save_editor(self, config: WatcherConfig) -> None:
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
