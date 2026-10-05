"""Screen-condition evaluation: image matching and change detection.

This module is shared by the macro engine (start/stop conditions) and the
background watcher engine.  Two kinds of checks are supported:

* ``image_found`` / ``image_missing`` -- OpenCV template matching against a
  screenshot region, reacting to an image appearing or disappearing.
* ``region_changed`` -- mean per-pixel difference between consecutive frames,
  which reacts to any visual change inside the watched rectangle.

The pure functions (:func:`match_template`, :func:`region_change_score`) are
side-effect free so they can be unit-tested without a display;
:class:`ChangeDetector` keeps state across polls for the change mode.
"""

from __future__ import annotations

import logging

import cv2
import mss
import numpy as np

logger = logging.getLogger(__name__)


def grab_region(region: tuple[int, int, int, int]) -> np.ndarray:
    """Capture a screen rectangle (left, top, width, height) as a BGR array."""
    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        frame = np.array(shot, dtype=np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def load_template(path: str) -> np.ndarray | None:
    """Read a template image from disk, or ``None`` when unreadable."""
    if not path:
        return None
    template = cv2.imread(path, cv2.IMREAD_COLOR)
    if template is None:
        logger.warning("Template image could not be read: %s", path)
    return template


def match_template(frame: np.ndarray, template: np.ndarray, confidence: float) -> bool:
    """Return True when *template* appears in *frame* at >= *confidence*."""
    if template.shape[0] > frame.shape[0] or template.shape[1] > frame.shape[1]:
        logger.warning("Template larger than the search region")
        return False
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(gray_frame, gray_template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, _ = cv2.minMaxLoc(result)
    return max_value >= confidence


def region_change_score(previous: np.ndarray, current: np.ndarray) -> float:
    """Mean absolute pixel difference between two same-size frames."""
    if previous.shape != current.shape:
        return float("inf")
    diff = cv2.absdiff(previous, current).astype(np.float32)
    return float(diff.mean())


class ChangeDetector:
    """Decide whether a region changed between successive captures.

    The first frame only establishes a baseline; afterwards every capture is
    compared with its predecessor and reported through :meth:`poll`.
    """

    def __init__(self, threshold: float) -> None:
        self._threshold = threshold
        self._previous: np.ndarray | None = None

    def poll(self, frame: np.ndarray) -> bool:
        """Feed a new frame; return True when it differs enough from the last."""
        previous, self._previous = self._previous, frame
        if previous is None:
            return False
        return region_change_score(previous, frame) > self._threshold


def evaluate_screen_condition(
    *,
    mode: str,
    region: tuple[int, int, int, int],
    template_path: str = "",
    confidence: float = 0.85,
    change_threshold: float = 5.0,
    detector: ChangeDetector | None = None,
) -> bool:
    """Take one screenshot and evaluate a single screen condition.

    ``image_found`` / ``image_missing`` use template matching; ``region_changed``
    needs a persistent *detector* so consecutive polls can be compared.
    Returns True when the condition currently holds.
    """
    frame = grab_region(region)
    if mode == "region_changed":
        if detector is None:
            detector = ChangeDetector(change_threshold)
        return detector.poll(frame)
    template = load_template(template_path)
    if template is None:
        logger.warning("Screen condition '%s' needs a valid template image", mode)
        return False
    found = match_template(frame, template, confidence)
    return found if mode == "image_found" else not found
