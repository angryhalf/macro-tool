"""Screen-condition evaluation: image matching and change detection.

This module powers the macro engine's automatic screen monitoring
(stop-trigger/start-trigger triggers and wait-for conditions).  Two kinds of checks are
supported:

* ``image_found`` / ``image_missing`` -- OpenCV template matching against a
  screenshot region, reacting to an image appearing or disappearing.
* ``color_present`` / ``color_absent`` -- HSV-range pixel counting that reacts
  to a specific colour showing up (or vanishing) inside the watched region(s).
* ``region_changed`` -- mean per-pixel difference between consecutive frames,
  which reacts to any visual change inside the watched rectangle.

The pure functions (:func:`match_template`, :func:`color_pixel_ratio`,
:func:`region_change_score`) are side-effect free so they can be unit-tested
without a display;
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


def parse_hex_color(text: str) -> tuple[int, int, int] | None:
    """Parse a ``#RRGGBB`` / ``#RGB`` / ``RRGGBB`` string into an RGB triplet.

    Returns ``None`` when *text* is not a valid hex colour so callers can fail
    softly instead of raising on every poll.
    """
    if not text:
        return None
    cleaned = text.strip().lstrip("#")
    if len(cleaned) == 3:  # shorthand: #f0a -> ff00aa
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6:
        return None
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def hsv_range_for_color(rgb: tuple[int, int, int], tolerance: int) -> tuple[np.ndarray, np.ndarray]:
    """Inclusive lower/upper HSV bounds around an RGB colour.

    The single colour is converted to HSV and expanded by *tolerance* on each
    axis.  Because OpenCV stores hue in 0-179 (half of the usual 0-360 scale),
    the hue window uses half the tolerance; saturation and value use the full
    tolerance, clamped to their valid ranges.
    """
    pixel = np.uint8([[[rgb[2], rgb[1], rgb[0]]]])  # RGB -> BGR for cv2
    h, s, v = cv2.cvtColor(pixel, cv2.COLOR_RGB2HSV)[0][0]  # type: ignore[misc]
    tol_h = min(90, max(0, int(tolerance) // 2))
    tol_sv = min(255, max(0, int(tolerance)))
    lower = np.array(
        [max(0, int(h) - tol_h), max(0, int(s) - tol_sv), max(0, int(v) - tol_sv)], dtype=np.uint8
    )
    upper = np.array(
        [min(179, int(h) + tol_h), min(255, int(s) + tol_sv), min(255, int(v) + tol_sv)], dtype=np.uint8
    )
    return lower, upper


def color_pixel_ratio(
    frame: np.ndarray,
    rgb: tuple[int, int, int],
    tolerance: int,
    range_override: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
) -> float:
    """Fraction of pixels in *frame* (BGR) that match the given colour.

    Matching happens in HSV space with per-axis tolerances around the target
    colour (or an explicit ``((h,s,v), (h,s,v))`` lower/upper override).
    Pure functions -- no screen access -- so they unit-test without a display.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    if range_override is not None:
        lower = np.array(range_override[0], dtype=np.uint8)
        upper = np.array(range_override[1], dtype=np.uint8)
    else:
        lower, upper = hsv_range_for_color(rgb, tolerance)
    mask = cv2.inRange(hsv, lower, upper)
    total = frame.shape[0] * frame.shape[1]
    if total == 0:
        return 0.0
    return float(cv2.countNonZero(mask)) / float(total)


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
