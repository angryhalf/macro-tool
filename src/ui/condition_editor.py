"""Shared editor widget for one :class:`ScreenCondition`.

Used on the macro flow-control actions (``wait_for`` / ``stop_trigger`` / ``start_trigger`` steps embed one of these so the
screen rule lives on the action itself).  It exposes only the fields relevant
to the selected mode (template picker for image modes, sensitivity for change
detection) plus polling options and an optional timeout (shown for *wait_for*
steps, which can give up waiting after a while).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from models.condition import ScreenCondition
from persistence.paths import TEMPLATES_DIR
from ui.widgets import crop_and_save_template

logger = logging.getLogger(__name__)

MODE_LABELS: dict[str, str] = {
    "Image appears": "image_found",
    "Image disappears": "image_missing",
    "Colour appears": "color_present",
    "Colour disappears": "color_absent",
    "Region changes": "region_changed",
}

#: Modes whose rule is driven by a target colour instead of a template image.
COLOR_MODES = ("color_present", "color_absent")



def _normalize_hex(text: str) -> str:
    """Return ``#RRGGBB`` for any accepted hex spelling, else ``''``."""
    cleaned = (text or "").strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6:
        return ""
    try:
        value = int(cleaned, 16)
    except ValueError:
        return ""
    return f"#{value:06X}"


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

        # --- colour-detection widgets (color_present / color_absent modes) ---
        self.color_hex_edit = QLineEdit("#FF0000")
        self.color_hex_edit.setMaxLength(7)
        self.color_hex_edit.editingFinished.connect(self._sync_color_swatch)
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(24, 24)
        self.color_swatch.setToolTip("Current target colour")
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_hex_edit, 1)
        color_row.addWidget(self.color_swatch)
        self.pick_color_button = QPushButton("Pick colour…")
        self.pick_color_button.clicked.connect(self._pick_color_dialog)
        self.pick_screen_color_button = QPushButton("Pick from screen…")
        self.pick_screen_color_button.setToolTip(
            "Drag a tiny rectangle on screen; its dominant colour becomes the target"
        )
        self.pick_screen_color_button.clicked.connect(self._pick_color_from_screen)
        color_btn_row = QHBoxLayout()
        color_btn_row.addWidget(self.pick_color_button)
        color_btn_row.addWidget(self.pick_screen_color_button)
        color_btn_row.addStretch()
        self.color_tolerance_spin = QSpinBox(minimum=0, maximum=255, singleStep=5, value=40)
        self.color_tolerance_spin.setToolTip(
            "How far shades may deviate from the target colour (per HSV channel)"
        )
        self.color_ratio_spin = QDoubleSpinBox(
            decimals=2, minimum=0.0, maximum=1.0, singleStep=0.01, value=0.01
        )
        self.color_ratio_spin.setToolTip(
            "Fraction of the watched region that must show the colour "
            "(appears: at least this much; disappears: less than this)"
        )

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
        self._form = form
        form.addRow("When:", self.mode_combo)
        form.addRow("Template:", self.template_label)
        form.addRow("", template_row)
        form.addRow("Match confidence:", self.confidence_spin)
        form.addRow("Target colour:", color_row)
        form.addRow("", color_btn_row)
        form.addRow("Colour tolerance:", self.color_tolerance_spin)
        form.addRow("Required area:", self.color_ratio_spin)
        form.addRow("Change sensitivity:", self.change_threshold)
        form.addRow("Poll interval:", self.poll_interval)
        if show_timeout:
            form.addRow(self.timeout_label, self.timeout_spin)
        self.timeout_label.setVisible(show_timeout)
        self.timeout_spin.setVisible(show_timeout)
        form.addRow("Watched region:", region_row)
        # Rows that only make sense for the colour modes (target colour,
        # picker buttons, tolerance, required area): hide them wholesale --
        # label included -- whenever another mode is selected.
        self._color_only_rows = [4, 5, 6, 7]
        self._sync_color_swatch()
        self._update_visibility()

    # ------------------------------------------------------------------
    # Model <-> widgets
    # ------------------------------------------------------------------
    def set_timeout_visible(self, visible: bool) -> None:
        """Show/hide the give-up timeout row after construction."""
        self.timeout_label.setVisible(visible)
        self.timeout_spin.setVisible(visible)

    def load(self, condition: ScreenCondition | None) -> None:
        """Populate the form from *condition* (``None`` resets to defaults)."""
        condition = condition or ScreenCondition()
        index = self.mode_combo.findData(condition.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self._template_path = condition.template_path
        self.template_label.setText(Path(condition.template_path).name or "No template selected")
        self.confidence_spin.setValue(condition.confidence)
        self.change_threshold.setValue(condition.change_threshold)
        self.color_hex_edit.setText(condition.color_hex or "#FF0000")
        self.color_tolerance_spin.setValue(condition.color_tolerance)
        self.color_ratio_spin.setValue(condition.color_ratio)
        self.poll_interval.setValue(condition.poll_interval_ms)
        self.timeout_spin.setValue(condition.timeout_ms)
        self._region = condition.region
        self._update_region_label()
        self._sync_color_swatch()
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
            color_hex=_normalize_hex(self.color_hex_edit.text()),
            color_tolerance=self.color_tolerance_spin.value(),
            color_ratio=self.color_ratio_spin.value(),
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
        mode = self.mode_combo.currentData()
        is_color = mode in COLOR_MODES
        uses_template = mode not in COLOR_MODES and mode != "region_changed"
        for widget in (self.template_label, self.confidence_spin, self.capture_button, self.load_button):
            widget.setVisible(uses_template)
        self.change_threshold.setVisible(mode == "region_changed")
        # Hide whole colour rows (labels included), not just the fields.
        for row in self._color_only_rows:
            self._set_row_visible(row, is_color)

    def _set_row_visible(self, row: int, visible: bool) -> None:
        """Toggle every item of a :class:`QFormLayout` row, label included.

        ``QFormLayout`` exposes no ``columnCount()``; its rows are addressed
        through the role-based API (``QFormLayout.LabelRole`` /
        ``QFormLayout.FieldRole`` -- enum members live on the class, not on
        layout instances).
        """
        for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
            item = self._form.itemAt(row, role)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setVisible(visible)
            elif item.layout() is not None:
                for i in range(item.layout().count()):
                    sub = item.layout().itemAt(i)
                    sub_widget = sub.widget() if sub is not None else None
                    if sub_widget is not None:
                        sub_widget.setVisible(visible)

    def _sync_color_swatch(self) -> None:
        """Repaint the swatch next to the hex field from its current text."""
        normalized = _normalize_hex(self.color_hex_edit.text())
        if not normalized:
            self.color_swatch.setStyleSheet("background: transparent; border: 1px solid gray;")
            return
        self.color_swatch.setStyleSheet(
            f"background: {normalized}; border: 1px solid palette(mid);"
        )

    def _apply_color(self, qcolor) -> None:
        """Push a picked QColor into the hex field (uppercase ``#RRGGBB``)."""
        if not qcolor.isValid():
            return
        self.color_hex_edit.setText(f"#{qcolor.red():02X}{qcolor.green():02X}{qcolor.blue():02X}")
        self._sync_color_swatch()

    def _pick_color_dialog(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        initial = QColor(_normalize_hex(self.color_hex_edit.text()) or "#FF0000")
        chosen = QColorDialog.getColor(initial, self, "Pick trigger colour")
        if chosen.isValid():
            self._apply_color(chosen)

    def _pick_color_from_screen(self) -> None:
        """Grab the dominant colour of a user-dragged rectangle on screen."""
        from PySide6.QtWidgets import QMessageBox

        from services.region_picker import RegionPickerDialog
        from ui.widgets import grab_screen_for_crop

        try:
            rect = RegionPickerDialog.pick(self)
        except Exception:  # pragma: no cover - never leave the button dead
            logger.exception("Colour region picker failed")
            QMessageBox.warning(
                self,
                "Colour picker",
                "The screen region picker could not be opened.",
            )
            return
        if rect is None:
            return
        frame = grab_screen_for_crop()
        if frame is None:
            QMessageBox.warning(
                self,
                "Colour picker",
                "The screen could not be captured, so no colour was sampled.",
            )
            return
        left, top, width, height = rect
        origin_x, origin_y = 0, 0
        try:  # Qt grabs start at the primary monitor's origin
            from PySide6.QtWidgets import QApplication

            geometry = QApplication.primaryScreen().geometry()
            origin_x, origin_y = geometry.left(), geometry.top()
        except Exception:  # pragma: no cover - headless edge cases
            pass
        x0 = max(left - origin_x, 0)
        y0 = max(top - origin_y, 0)
        crop = frame[y0 : y0 + max(height, 1), x0 : x0 + max(width, 1)]
        if crop.size == 0:
            QMessageBox.warning(
                self,
                "Colour picker",
                "The selected region fell outside the captured screen.",
            )
            return
        bgr = np.median(crop.reshape(-1, 3), axis=0).astype(int)  # dominant/median colour
        self._apply_color(QColor(int(bgr[2]), int(bgr[1]), int(bgr[0])))

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
