"""Shared editor widget for one :class:`ScreenCondition`.

Used inside the standalone watcher editor and on the macro flow-control
actions (``wait_for`` / ``pause`` / ``unpause`` steps embed one of these so the
screen rule lives on the action itself).  It exposes only the fields relevant
to the selected mode (template picker for image modes, sensitivity for change
detection) plus polling options and an optional timeout (shown for *wait_for*
steps, which can give up waiting after a while).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from app.conditions import ScreenCondition
from ui.widgets import crop_and_save_template

logger = logging.getLogger(__name__)

MODE_LABELS: dict[str, str] = {
    "Image appears": "image_found",
    "Image disappears": "image_missing",
    "Region changes": "region_changed",
}

TEMPLATES_DIR = Path("templates")


def _screen_size() -> tuple[int, int, int, int]:
    """Fallback watch region: the primary screen, or a sane default."""
    try:
        from PySide6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is not None:
            rect = screen.geometry()
            return (rect.left(), rect.top(), rect.width(), rect.height())
    except Exception:  # pragma: no cover - headless edge cases
        pass
    return (0, 0, 1920, 1080)


class ConditionEditor(QWidget):
    """Inline form that builds a :class:`ScreenCondition`."""

    def __init__(self, parent: QWidget | None = None, show_timeout: bool = False) -> None:
        super().__init__(parent)
        self._template_path = ""
        self._region: tuple[int, int, int, int] | None = None

        self.mode_combo = QComboBox()
        for label, mode in MODE_LABELS.items():
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._update_visibility)

        self.template_label = QLabel("No template selected")
        self.template_label.setStyleSheet("color: gray;")
        self.capture_button = QPushButton("Capture region from screen…")
        self.capture_button.clicked.connect(self._capture_template)
        self.load_button = QPushButton("Load file…")
        self.load_button.clicked.connect(self._load_file)
        template_row = QHBoxLayout()
        template_row.addWidget(self.capture_button)
        template_row.addWidget(self.load_button)
        template_row.addStretch()

        self.confidence_spin = QDoubleSpinBox(
            decimals=2, minimum=0.30, maximum=1.00, singleStep=0.01, value=0.85
        )
        self.change_threshold = QDoubleSpinBox(decimals=1, minimum=0.1, maximum=255.0, value=5.0)
        self.poll_interval = QSpinBox(minimum=50, maximum=10_000, singleStep=50, value=200)
        self.poll_interval.setSuffix(" ms")

        self.timeout_spin = QSpinBox(minimum=0, maximum=600_000, value=0)
        self.timeout_spin.setSuffix(" ms")
        self.timeout_label = QLabel("Give-up timeout:")

        self.region_label = QLabel("Full screen")
        self.pick_region_button = QPushButton("Pick region…")
        self.pick_region_button.clicked.connect(self._pick_region)
        region_row = QHBoxLayout()
        region_row.addWidget(self.region_label, 3)
        region_row.addWidget(self.pick_region_button, 1)

        form = QFormLayout(self)
        form.addRow("When:", self.mode_combo)
        form.addRow("Template:", self.template_label)
        form.addRow("", template_row)
        form.addRow("Match confidence:", self.confidence_spin)
        form.addRow("Change sensitivity:", self.change_threshold)
        form.addRow("Poll interval:", self.poll_interval)
        if show_timeout:
            form.addRow(self.timeout_label, self.timeout_spin)
        self.timeout_label.setVisible(show_timeout)
        self.timeout_spin.setVisible(show_timeout)
        form.addRow("Watched region:", region_row)
        self._update_visibility()

    # ------------------------------------------------------------------
    # Model <-> widgets
    # ------------------------------------------------------------------
    def load(self, condition: ScreenCondition | None) -> None:
        """Populate the form from *condition* (``None`` resets to defaults)."""
        condition = condition or ScreenCondition()
        index = self.mode_combo.findData(condition.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self._template_path = condition.template_path
        self.template_label.setText(Path(condition.template_path).name or "No template selected")
        self.confidence_spin.setValue(condition.confidence)
        self.change_threshold.setValue(condition.change_threshold)
        self.poll_interval.setValue(condition.poll_interval_ms)
        self.timeout_spin.setValue(condition.timeout_ms)
        self._region = condition.region
        self._update_region_label()
        self._update_visibility()

    def build(self) -> ScreenCondition:
        """Return the condition currently described by the form."""
        return ScreenCondition(
            mode=self.mode_combo.currentData(),
            template_path=self._template_path,
            confidence=self.confidence_spin.value(),
            change_threshold=self.change_threshold.value(),
            poll_interval_ms=self.poll_interval.value(),
            timeout_ms=self.timeout_spin.value(),
            region=self._region or _screen_size(),
        )

    def clear(self) -> None:
        """Reset to a blank, unconfigured condition."""
        self.load(ScreenCondition(template_path=""))
        self._template_path = ""
        self.template_label.setText("No template selected")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _update_visibility(self) -> None:
        uses_template = self.mode_combo.currentData() != "region_changed"
        for widget in (self.template_label, self.confidence_spin, self.capture_button, self.load_button):
            widget.setVisible(uses_template)
        self.change_threshold.setVisible(not uses_template)

    def _update_region_label(self) -> None:
        if self._region is None:
            self.region_label.setText("Full screen")
        else:
            left, top, width, height = self._region
            self.region_label.setText(f"{left},{top} — {width}×{height}")

    def _capture_template(self) -> None:
        TEMPLATES_DIR.mkdir(exist_ok=True)
        path = str(TEMPLATES_DIR / f"template_{int(time.time())}.png")
        if crop_and_save_template(self, path):
            self._template_path = path
            self.template_label.setText(Path(path).name)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load template", str(TEMPLATES_DIR), "Images (*.png *.jpg *.bmp)"
        )
        if path:
            self._template_path = path
            self.template_label.setText(Path(path).name)

    def _pick_region(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from services.region_picker import RegionPickerDialog

        try:
            rect = RegionPickerDialog.pick(self)
        except Exception:  # pragma: no cover - never leave the button dead
            logger.exception("Region picker failed")
            QMessageBox.warning(
                self,
                "Region picker",
                "The screen region picker could not be opened.",
            )
            return
        if rect is not None:
            self._region = rect
            self._update_region_label()
