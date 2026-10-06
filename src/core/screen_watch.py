"""Screen-condition evaluation: image matching and change detection.

This module powers the macro engine's automatic screen monitoring
(stop-trigger/start-trigger triggers and wait-for conditions).  Two kinds of checks are
supported:

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
import os
import threading

import cv2
import mss
import numpy as np

logger = logging.getLogger(__name__)

# One mss context per thread, created on first use.  Opening/closing an mss
# instance on every poll burns CPU and (on Windows) GDI handles; the contexts
# are not documented as thread-safe, so each polling thread keeps its own and
# they live for the lifetime of the process (daemon threads exit with it).
_tls = threading.local()


def _get_mss():
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = mss.mss()
        _tls.sct = sct
    return sct


def grab_region(region: tuple[int, int, int, int]) -> np.ndarray:
    """Capture a screen rectangle (left, top, width, height) as a BGR array."""
    left, top, width, height = region
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid screen region: {region!r}")
    sct = _get_mss()
    shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    frame = np.array(shot, dtype=np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def load_template(path: str) -> np.ndarray | None:
    """Read a template image from disk, or ``None`` when unreadable.

    Results are cached by ``(path, mtime)`` because conditions re-evaluate
    their template on every poll -- without the cache each sample performs a
    fresh PNG decode.  A saved/edited template changes mtime and is reloaded
    transparently.
    """
    if not path:
        return None
    cache: dict[tuple[str, float], np.ndarray | None] | None = getattr(
        _tls, "template_cache", None
    )
    if cache is None:
        cache = {}
        _tls.template_cache = cache
    try:
        key = (path, os.stat(path).st_mtime)
    except OSError:
        logger.warning("Template image could not be read: %s", path)
        return None
    if key in cache:
        return cache[key]
    template = cv2.imread(path, cv2.IMREAD_COLOR)
    if template is None:
        logger.warning("Template image could not be read: %s", path)
    # Only negative results expire quickly; keep at most a handful of decoded
    # templates per thread so long-lived monitors do not grow unbounded.
    if len(cache) > 32:
        cache.clear()
    cache[key] = template
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
    """Mean absolute grayscale pixel difference between two same-size frames.

    Grayscale ``cv2.absdiff`` + ``cv2.mean`` replaces the original full-color
    float32 version, which allocated ~4x the frame size per poll for
    identical threshold behaviour (change detection cares *that* pixels
    moved, not chroma precision).  No downscaling: quarter-scaling a large
    region turned out to resize *up* and benchmark slower than plain
    grayscale absdiff, and INTER_AREA averaging also dampens small-but-real
    changes near the threshold.
    """
    if previous.shape != current.shape:
        return float("inf")
    prev_gray = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(prev_gray, cur_gray)
    return float(cv2.mean(diff)[0])


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
