"""Headless tests for screen-condition evaluation helpers (pure numpy/cv2).

No display is needed: frames are synthetic arrays.  Only the functions that
touch the *screen* (mss capture) are skipped here -- everything downstream of
a frame works offline.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.conditions import ConditionRuntime, ScreenCondition
from core.screen_watch import ChangeDetector, match_template, region_change_score


def gradient_frame(width: int = 64, height: int = 48) -> np.ndarray:
    """Deterministic BGR frame with a clear pattern to template against."""
    yy, xx = np.mgrid[0:height, 0:width]
    channel = ((xx * 7 + yy * 11) % 256).astype(np.uint8)
    blue = channel
    green = np.roll(channel, 13, axis=1)          # same shape, shifted pattern
    red = ((xx * 3 + yy * 5) % 256).astype(np.uint8)
    return np.dstack([blue, green, red]).copy()


# ------------------------------------------------------------------ templates
def test_match_template_finds_embedded_patch():
    frame = gradient_frame()
    template = frame[10:20, 20:35].copy()
    assert match_template(frame, template, confidence=0.9) is True


def test_match_template_rejects_absent_pattern():
    frame = gradient_frame()
    # A genuinely foreign pattern (random noise, unrelated to the frame's
    # linear ramps) must not pass at high confidence.  Note: patches taken
    # from a *similar* synthetic image can legitimately score ~1.0 because
    # the gradients are periodic -- that is expected matching behaviour,
    # not a bug.
    rng = np.random.default_rng(7)
    other = rng.integers(0, 256, size=(10, 15, 3), dtype=np.uint8)
    assert match_template(frame, other, confidence=0.99) is False


def test_match_template_confidence_boundary_is_inclusive():
    frame = gradient_frame()
    template = frame[10:20, 20:35].copy()
    assert match_template(frame, template, confidence=1.0) is True  # exact copy scores 1.0


def test_match_template_oversized_template_returns_false(caplog):
    frame = gradient_frame(width=20, height=10)
    template = np.zeros((12, 12, 3), dtype=np.uint8)
    assert match_template(frame, template, confidence=0.5) is False


# ------------------------------------------------------------- change scoring
def test_region_change_score_zero_for_identical_frames():
    frame = gradient_frame()
    assert region_change_score(frame, frame.copy()) == pytest.approx(0.0)


def test_region_change_score_detects_small_edit():
    frame = gradient_frame()
    edited = frame.copy()
    edited[5:15, 5:15] = 255  # brighten one block
    score = region_change_score(frame, edited)
    assert 0 < score < 255


def test_region_change_score_shape_mismatch_is_inf():
    a = gradient_frame(32, 32)
    b = gradient_frame(40, 32)
    assert region_change_score(a, b) == float("inf")


def test_change_detector_first_poll_never_reports_change():
    detector = ChangeDetector(threshold=5.0)
    frame = gradient_frame()
    assert detector.poll(frame) is False  # nothing to compare against yet
    edited = frame.copy()
    # Paint a large block pure white: mean grayscale diff must clear the
    # threshold (the frame's own gray values average ~127, so even a black
    # block would only score ~6 -- use white for a robust signal).
    edited[0:24, 0:32] = 255
    assert detector.poll(edited) is True


def test_change_detector_compares_to_immediate_predecessor():
    """poll() diffs each frame against the *previous* one, not the baseline."""
    detector = ChangeDetector(threshold=5.0)
    base = np.full((32, 32, 3), 100, dtype=np.uint8)
    detector.poll(base)
    flipped = np.full((32, 32, 3), 200, dtype=np.uint8)
    assert detector.poll(flipped) is True   # +100 change
    assert detector.poll(flipped) is False  # identical repeat: no change


def test_change_detector_ignores_noise_below_threshold():
    detector = ChangeDetector(threshold=50.0)
    base = np.full((32, 32, 3), 100, dtype=np.uint8)
    detector.poll(base)
    noisy = base.copy()
    noisy += 2  # tiny global shift
    assert detector.poll(noisy) is False


# ------------------------------------------------------------ condition model
def test_watch_regions_single_by_default():
    condition = ScreenCondition(region=(1, 2, 3, 4))
    assert condition.watch_regions == [(1, 2, 3, 4)]


def test_watch_regions_multi_lists_primary_first():
    condition = ScreenCondition(region=(0, 0, 10, 10), all_regions=((5, 5, 2, 2), (9, 9, 1, 1)))
    assert condition.watch_regions == [(0, 0, 10, 10), (5, 5, 2, 2), (9, 9, 1, 1)]
    assert "3 regions" in condition.description


def test_configured_flags_missing_template():
    assert ScreenCondition(mode="image_found", template_path="").configured is False
    assert ScreenCondition(mode="image_found", template_path="x.png").configured is True
    assert ScreenCondition(mode="region_changed").configured is True


def test_runtime_expired_with_timeout():
    runtime = ConditionRuntime.create(ScreenCondition(timeout_ms=1), timeout_s=0.05)
    import time

    time.sleep(0.08)
    assert runtime.expired() is True


def test_runtime_no_timeout_never_expires():
    runtime = ConditionRuntime.create(ScreenCondition(timeout_ms=0))
    assert runtime.expired() is False
