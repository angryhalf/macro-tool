"""Settings tab: global options (stop hotkey, start delay)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.settings import AppSettings
from ui.widgets import HotkeyButton


class SettingsTab(QWidget):
    """Global application settings bound directly to the settings document."""

    def __init__(self, on_changed: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_changed = on_changed

        self.stop_hotkey = HotkeyButton("f8")
        self.stop_hotkey.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_hotkey.setToolTip(
            "Click, then press the key combination that stops every running macro."
        )
        self.stop_hotkey.hotkey_changed.connect(self._emit_changed)

        self.start_delay = QSpinBox(minimum=0, maximum=10_000, singleStep=100, value=500)
        self.start_delay.setSuffix(" ms")
        self.start_delay.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
        self.start_delay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.start_delay.valueChanged.connect(self._emit_changed)

        group = QGroupBox("General")
        form = QFormLayout(group)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        # Keep the compact controls left-aligned instead of stretching full width.
        for widget in (self.stop_hotkey, self.start_delay):
            widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        form.addRow("Emergency stop hotkey:", self.stop_hotkey)
        form.addRow("Macro start delay:", self.start_delay)
        hint = QLabel(
            "The delay gives you time to focus the target window before a macro runs.  "
            "Changes here are saved automatically."
        )
        hint.setProperty("hint", "true")
        hint.setWordWrap(True)
        form.addRow("", hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(group)
        layout.addStretch()

    # ------------------------------------------------------------------
    def load(self, settings: AppSettings) -> None:
        self.stop_hotkey.hotkey = settings.stop_hotkey
        self.start_delay.setValue(settings.execution_delay_ms)

    def apply_to(self, settings: AppSettings) -> AppSettings:
        """Return a copy of *settings* with this tab's values applied."""
        return replace(
            settings,
            stop_hotkey=self.stop_hotkey.hotkey or "f8",
            execution_delay_ms=self.start_delay.value(),
        )

    def _emit_changed(self) -> None:
        self._on_changed()
