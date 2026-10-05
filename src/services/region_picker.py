"""Fullscreen overlay used to pick a screen region or crop a template image.

Usage::

    rect = RegionPickerDialog.pick(parent)   # returns (left, top, w, h) or None
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QWidget

from services.screen_capture import bgr_to_qimage, grab_full_screen

_BORDER_PX = 2


class _RubberBandCanvas(QWidget):
    """Widget that paints the screenshot and the selection rubber band."""

    selection_done = Signal(QRect)

    def __init__(self, pixmap_image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = pixmap_image
        self._origin: QPoint | None = None
        self._current = QRect()

    # -- painting -----------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.drawImage(0, 0, self._image)

        if not self._current.isNull():
            hole = QColor(0, 0, 0, 130)
            painter.fillRect(self.rect(), hole)
            painter.drawImage(self._current.topLeft(), self._image, self._current)
            pen = QPen(QColor(30, 144, 255), _BORDER_PX)
            painter.setPen(pen)
            painter.drawRect(self._current)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(20, 30, "Drag to select a region — Esc to cancel")

    # -- mouse --------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is not None:
            self._current = QRect(self._origin, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            self._origin = None
            if self._current.width() > 4 and self._current.height() > 4:
                self.selection_done.emit(self._current)

    # -- keyboard -----------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and isinstance(parent := self.parent(), QDialog):
            parent.reject()


class RegionPickerDialog(QDialog):
    """Frameless fullscreen dialog returning a screen rectangle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select screen region")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._selected: QRect | None = None
        screen_image = bgr_to_qimage(grab_full_screen())

        from PySide6.QtWidgets import QVBoxLayout

        self._canvas = _RubberBandCanvas(screen_image, self)
        self._canvas.selection_done.connect(self._on_selection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)
        self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._canvas.setFocus()

    def _on_selection(self, rect: QRect) -> None:
        self._selected = rect
        self.accept()

    @property
    def selected_rect(self) -> QRect | None:
        return self._selected

    @staticmethod
    def pick(parent: QWidget | None = None) -> tuple[int, int, int, int] | None:
        """Show the picker modally; returns (left, top, width, height) or None."""
        dialog = RegionPickerDialog(parent)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return None
        rect = dialog.selected_rect
        return rect.x(), rect.y(), rect.width(), rect.height()
