"""Settings page: global options (stop hotkey, start delay) and appearance.

The appearance group holds the dark/light mode toggle plus an accent-colour
picker; both feed straight back into the settings document via ``apply_to``
so they persist like every other global option.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.settings import AppSettings, DEFAULT_ACCENT_COLOR
from app.theme import THEMES
from ui.widgets import HotkeyButton


class _SwatchButton(QPushButton):
    """Small button that paints its own background as the current accent.

    Clicking it opens the standard colour picker; the resulting hex colour
    is emitted through :attr:`color_changed`.
    """

    color_changed = Signal(str)

    def __init__(self, color: str) -> None:
        super().__init__()
        self._color = color
        self.setFixedSize(44, 22)
        self.setToolTip("Click to choose the accent colour")
        self.clicked.connect(self._pick)
        self._repaint()

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, value: str) -> None:
        self._color = value
        self._repaint()

    def _repaint(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._color};"
            " border: 1px solid rgba(0, 0, 0, 0.35); border-radius: 4px;"
        )

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Accent colour")
        if chosen.isValid():
            self.color = chosen.name().lower()
            self.color_changed.emit(self._color)


class SettingsTab(QWidget):
    """Global application settings bound directly to the settings document."""

    def __init__(self, on_changed: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_changed = on_changed

        # -- general -----------------------------------------------------
        self.stop_hotkey = HotkeyButton("f8")
        self.stop_hotkey.hotkey_changed.connect(self._emit_changed)

        self.start_delay = QSpinBox(minimum=0, maximum=10_000, singleStep=100, value=500)
        self.start_delay.setSuffix(" ms")
        self.start_delay.valueChanged.connect(self._emit_changed)

        general = QGroupBox("General")
        form = QFormLayout(general)
        form.addRow("Emergency stop hotkey:", self.stop_hotkey)
        form.addRow("Macro start delay:", self.start_delay)
        form.addRow(QLabel("The delay gives you time to focus the target window before a macro runs."))

        # -- appearance --------------------------------------------------
        self.theme_select = QComboBox()
        self.theme_select.addItem("Light", "light")
        self.theme_select.addItem("Dark", "dark")
        self.theme_select.currentIndexChanged.connect(self._emit_changed)

        self.accent_swatch = _SwatchButton(DEFAULT_ACCENT_COLOR)
        self.accent_swatch.color_changed.connect(self._emit_changed)
        self.accent_reset = QPushButton("Reset to default")
        self.accent_reset.clicked.connect(self._reset_accent)
        accent_row = QHBoxLayout()
        accent_row.addWidget(self.accent_swatch)
        accent_row.addWidget(self.accent_reset)
        accent_row.addStretch()

        appearance = QGroupBox("Appearance")
        aform = QFormLayout(appearance)
        aform.addRow("Colour scheme:", self.theme_select)
        aform.addRow("Accent colour:", accent_row)
        hint = QLabel("The scheme applies instantly and is saved with your settings.")
        hint.setWordWrap(True)
        aform.addRow(hint)

        layout = QVBoxLayout(self)
        layout.addWidget(general)
        layout.addWidget(appearance)
        layout.addStretch()

    # ------------------------------------------------------------------
    def load(self, settings: AppSettings) -> None:
        self.stop_hotkey.hotkey = settings.stop_hotkey
        self.start_delay.setValue(settings.execution_delay_ms)
        index = self.theme_select.findData(
            settings.theme if settings.theme in THEMES else "light"
        )
        self.theme_select.blockSignals(True)
        self.theme_select.setCurrentIndex(max(index, 0))
        self.theme_select.blockSignals(False)
        self.accent_swatch.color = settings.accent_color or DEFAULT_ACCENT_COLOR

    def apply_to(self, settings: AppSettings) -> AppSettings:
        """Return a copy of *settings* with this page's values applied."""
        return replace(
            settings,
            stop_hotkey=self.stop_hotkey.hotkey or "f8",
            execution_delay_ms=self.start_delay.value(),
            theme=self.theme_select.currentData() or "light",
            accent_color=self.accent_swatch.color or DEFAULT_ACCENT_COLOR,
        )

    def current_theme_name(self) -> str:
        """Theme id currently selected in the combo (for live preview)."""
        return self.theme_select.currentData() or "light"

    def current_accent(self) -> str:
        return self.accent_swatch.color

    # ------------------------------------------------------------------
    def _reset_accent(self) -> None:
        self.accent_swatch.color = DEFAULT_ACCENT_COLOR
        self._emit_changed()

    def _emit_changed(self) -> None:
        self._on_changed()
