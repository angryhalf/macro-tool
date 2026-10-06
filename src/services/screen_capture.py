"""Screen-capture helpers shared by the UI (thumbnails, region picker)."""

from __future__ import annotations

import logging

import cv2
import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def _qimage_to_bgr(image: QImage) -> np.ndarray:
    """Convert a ``QImage`` into a contiguous BGR uint8 numpy array."""
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    height, width = image.height(), image.width()
    if height <= 0 or width <= 0:
        raise ValueError(f"Invalid image size {width}x{height}")
    bytes_per_line = image.bytesPerLine()
    buffer = image.constBits()
    array = np.frombuffer(buffer, dtype=np.uint8, count=height * bytes_per_line)
    array = array.reshape((height, bytes_per_line // 4, 4))[:, :width, :3]
    bgr = np.ascontiguousarray(array[..., ::-1], dtype=np.uint8)  # RGB -> BGR
    return bgr


def grab_primary_screen_via_qt() -> np.ndarray:
    """Capture the primary monitor through Qt's own screen-grab API.

    Works on Windows/macOS/Linux without extra native dependencies and uses
    Qt's logical (DPI-independent) coordinates, matching the region picker.
    """
    screen = QApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("No primary screen available")
    return _qimage_to_bgr(screen.grabWindow(0).toImage())


def save_template(frame: np.ndarray, path: str) -> bool:
    """Persist a cropped screenshot as a template image file."""
    return bool(cv2.imwrite(path, frame))
