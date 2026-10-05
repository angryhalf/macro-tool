"""Fullscreen overlay used to pick a screen region or crop a template image.

Usage::

    rect = RegionPickerDialog.pick(parent)   # returns (left, top, w, h) or None

The picker shows a frozen screenshot of the entire virtual desktop (all
monitors) in a frameless window and lets the user drag a rubber-band
selection.  Coordinates returned are global screen coordinates, which is
what :mod:`services.screen_capture` and the condition engine expect.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

_BORDER_PX = 2
_MIN_SELECTION_PX = 5


class _RubberBandCanvas(QWidget):
    """Widget that paints the screenshot and the selection rubber band."""

    selection_done = Signal(QRect)
    selection_cancelled = Signal()

    def __init__(self, image: QImage, offset: QPoint, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = image
        self._offset = offset  # widget (0,0) in global screen coordinates
        self._origin: QPoint | None = None
        self._current = QRect()
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, image: QImage, offset: QPoint) -> None:
        """Replace the frozen background screenshot."""
        self._image = image
        self._offset = offset
        self.update()

    # -- painting -----------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if not self._image.isNull():
            painter.drawImage(0, 0, self._image)

        if not self._current.isNull():
            painter.fillRect(self.rect(), QColor(0, 0, 0, 130))
            painter.drawImage(self._current.topLeft(), self._image, self._current)
            painter.setPen(QPen(QColor(30, 144, 255), _BORDER_PX))
            painter.drawRect(self._current)
            size_text = f"{self._current.width()} × {self._current.height()}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(self._current.adjusted(6, 20, 0, 0), Qt.AlignmentFlag.AlignLeft, size_text)
        else:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                self.rect().adjusted(20, 20, -20, -20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                "Drag to select a region — Esc to cancel",
            )

    # -- mouse --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = QRect(self._origin, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._origin = None
        if (
            self._current.width() >= _MIN_SELECTION_PX
            and self._current.height() >= _MIN_SELECTION_PX
        ):
            self.selection_done.emit(self._current.translated(self._offset))
        else:
            self._current = QRect()
            self.update()

    # -- keyboard -----------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.selection_cancelled.emit()


class RegionPickerDialog(QDialog):
    """Frameless fullscreen dialog returning a screen rectangle.

    The dialog covers the whole virtual desktop so multi-monitor setups
    work; the frozen background comes from a single full-desktop capture.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select screen region")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._selected: QRect | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Canvas is created lazily in showEvent with a *live* screenshot; a
        # blank placeholder keeps the layout valid until then.
        self._canvas = _RubberBandCanvas(QImage(), QPoint(0, 0), self)
        self._canvas.selection_done.connect(self._on_selection)
        self._canvas.selection_cancelled.connect(self.reject)
        layout.addWidget(self._canvas)

        self.setGeometry(QApplication.primaryScreen().virtualGeometry())
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    # -- live background ------------------------------------------------
    def showEvent(self, event) -> None:
        """Grab the desktop *after* the window exists, then hand focus to the canvas."""
        super().showEvent(event)
        image, origin = self._grab_virtual_desktop()
        self._canvas.set_image(image, origin)
        self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        self._canvas.update()

    # ------------------------------------------------------------------
    @staticmethod
    def _grab_virtual_desktop() -> tuple[QImage, QPoint]:
        """Capture the desktop behind us; returns (image, top-left in global coords).

        Uses Qt's own per-screen grab so multi-monitor setups are composited
        completely (every monitor is captured and placed at its position in
        the virtual desktop) and so the frozen background and the returned
        coordinates share the same DPI-independent coordinate space as the
        overlay window itself.
        """
        app = QApplication.instance()
        screens = app.screens() if isinstance(app, QApplication) else []
        if not screens:
            screens = [QApplication.primaryScreen()]

        geometry = QRect(screens[0].virtualGeometry()) if hasattr(screens[0], "virtualGeometry") else None
        if geometry is None or geometry.isNull():
            geometry = screens[0].geometry()
        for screen in screens:
            geometry = geometry.united(screen.geometry())

        canvas = QImage(geometry.size(), QImage.Format.Format_RGB888)
        canvas.fill(QColor(20, 20, 20))
        painter = QPainter(canvas)
        try:
            for screen in screens:
                try:
                    shot = screen.grabWindow(0).toImage()
                except Exception:  # pragma: no cover - transient capture failures
                    logger.exception(
                        "Screen capture failed for monitor %s, leaving that area blank",
                        screen.name(),
                    )
                    continue
                if shot.isNull():
                    continue
                # Place this monitor's shot at its position within the virtual desktop.
                painter.drawImage(screen.geometry().topLeft() - geometry.topLeft(), shot)
        finally:
            painter.end()
        return canvas, geometry.topLeft()

    # ------------------------------------------------------------------
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
