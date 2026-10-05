"""Reusable widgets shared across the tabs."""

from __future__ import annotations

import keyboard as kb
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from src.services.screen_capture import bgr_to_qimage, grab_full_screen


class HotkeyButton(QPushButton):
    """Button that captures the next pressed key and emits it as a hotkey name.

    Click the button, press any key (or Esc to cancel). The resolved key name
    (e.g. "f8", "a", "ctrl+shift+d" is not supported — single keys only) is
    emitted via :attr:`hotkey_changed`.
    """

    hotkey_changed = Signal(str)

    def __init__(self, hotkey: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hotkey = hotkey
        self._capturing = False
        self.setText(self._caption(hotkey))
        self.setCheckable(True)
        self.clicked.connect(self._toggle_capture)

    @property
    def hotkey(self) -> str:
        return self._hotkey

    @hotkey.setter
    def hotkey(self, value: str) -> None:
        self._hotkey = value
        self.setText(self._caption(value))

    @staticmethod
    def _caption(hotkey: str) -> str:
        if not hotkey:
            return "Set hotkey"
        return f"[{hotkey.upper()}]"

    def _toggle_capture(self) -> None:
        self._capturing = self.isChecked()
        if self._capturing:
            self.setText("Press a key…")
        else:
            self.setText(self._caption(self._hotkey))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._finish(False)
            return
        name = self._resolve_key(event)
        if name:
            self._hotkey = name
            self.hotkey_changed.emit(name)
        self._finish(True)

    def _finish(self, checked: bool) -> None:
        self.setChecked(False)
        self._capturing = False
        self.setText(self._caption(self._hotkey))

    @staticmethod
    def _resolve_key(event: QKeyEvent) -> str | None:
        scan = event.nativeScanCode()
        try:
            name = kb.scanmap_to_name(scan).lower()
            if name != "unknown":
                return name
        except Exception:
            pass
        # Fallback: map common Qt keys to keyboard-lib names.
        qt_map = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_Enter: "enter",
            Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Escape: "esc",
        }
        if event.key() in qt_map:
            return qt_map[event.key()]
        text = event.text()
        return text.lower() if text.isprintable() and text.strip() else None


class RegionPreviewWidget(QWidget):
    """Live thumbnail of a screen region with pick/crop buttons.

    Signals:
        region_changed(tuple): (left, top, width, height) after picking.
        template_cropped(str): file path after saving a cropped template.
    """

    region_changed = Signal(tuple)
    template_cropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._pixmap: QPixmap | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)

    # -- public --------------------------------------------------------
    def refresh(self) -> None:
        frame = grab_full_screen()
        self._pixmap = QPixmap.fromImage(bgr_to_qimage(frame)).scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.update()

    # -- painting ------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        if self._pixmap:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor(30, 30, 30))
        pen = QPen(QColor(255, 200, 0), 2)
        painter.setPen(pen)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


def crop_and_save_template(parent: QWidget | None, save_path: str) -> bool:
    """Interactively select a screen area and save it as a template image.

    Returns True when a crop was captured and written to *save_path*.
    """
    from src.services.region_picker import RegionPickerDialog
    from src.services.screen_capture import save_template

    rect = RegionPickerDialog.pick(parent)
    if rect is None:
        return False
    left, top, width, height = rect
    frame = grab_full_screen()
    crop = frame[top : top + height, left : left + width]
    return save_template(crop, save_path)
