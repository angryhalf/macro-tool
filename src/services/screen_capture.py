"""Screen-capture helpers shared by the UI (thumbnails, region picker)."""

from __future__ import annotations

import mss
import numpy as np
import cv2
from PySide6.QtGui import QImage


def grab_full_screen() -> np.ndarray:
    """Capture the primary monitor as a BGR numpy array."""
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        frame = np.array(shot, dtype=np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    """Convert an OpenCV BGR array into a QImage that copies its data."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width, _ = rgb.shape
    return QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


def save_template(frame: np.ndarray, path: str) -> bool:
    """Persist a cropped screenshot as a template image file."""
    return bool(cv2.imwrite(path, frame))
