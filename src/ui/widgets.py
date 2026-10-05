"""Reusable widgets shared across the tabs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from src.services.input import describe_hotkey
from src.services.screen_capture import bgr_to_qimage, grab_full_screen

# Qt keys that have no printable text mapped to pynput key names.
_QT_KEY_NAMES: dict[Qt.Key, str] = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backtab: "tab",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page_up",
    Qt.Key.Key_PageDown: "page_down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Print: "print_screen",
}

_MODIFIER_NAMES: dict[Qt.Key, str] = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Meta: "cmd",
}


class HotkeyButton(QPushButton):
    """Button that captures a global hotkey combo from the next key press.

    Click the button, then press the desired combination (e.g. ``F8`` or
    ``Ctrl+Shift+D``); modifiers alone are shown as a hint while capturing.
    Press Esc to cancel.  The normalized combo string (e.g.
    ``"ctrl+shift+d"``) is emitted via :attr:`hotkey_changed`.
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
        return f"[{describe_hotkey(hotkey)}]"

    def _toggle_capture(self) -> None:
        self._capturing = self.isChecked()
        if self._capturing:
            self.setText("Press keys…")
        else:
            self.setText(self._caption(self._hotkey))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._finish(False)
            return
        combo = self._build_combo(event)
        if combo:
            self._hotkey = combo
            self.hotkey_changed.emit(combo)
            self._finish(True)
        elif self._is_modifier_only(event):
            self.setText("Keep holding…")

    def _finish(self, checked: bool) -> None:
        del checked
        self.setChecked(False)
        self._capturing = False
        self.setText(self._caption(self._hotkey))

    @staticmethod
    def _is_modifier_only(event: QKeyEvent) -> bool:
        return event.key() in _MODIFIER_NAMES and not event.text().strip()

    @staticmethod
    def _build_combo(event: QKeyEvent) -> str | None:
        """Turn a Qt key event into a normalized 'ctrl+shift+a' style combo."""
        parts: list[str] = []
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")

        key = event.key()
        if key in _MODIFIER_NAMES:  # pure modifier press, no main key yet
            return None

        name = _QT_KEY_NAMES.get(Qt.Key(key))
        if name is None and 0 <= key - Qt.Key.Key_F1 <= 23:
            name = f"f{key - Qt.Key.Key_F1 + 1}"
        if name is None:
            text = event.text()
            name = text.lower() if text.isprintable() and text.strip() else None
        if name is None:
            return None

        parts.append(name)
        return "+".join(parts)


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
