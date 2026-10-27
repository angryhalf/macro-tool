"""Runtime bookkeeping and evaluation for one armed condition.

:class:`ConditionRuntime` pairs a frozen :class:`~components.macro.condition.ScreenCondition`
with the mutable state needed while it is being polled (per-region change
detectors, an optional wall-clock deadline).  Screen rules are evaluated by
capturing each watched rectangle through :mod:`components.screen_watch.vision` and applying
the configured mode (template match / colour ratio / change detection).

The capture backend is injectable via the *capture* argument so tests can run
headless; ``None`` uses the process-wide mss context.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from components.macro.condition import ScreenCondition
from components.screen_watch import vision


@dataclass
class ConditionRuntime:
    """Mutable bookkeeping for one condition while it is being polled."""

    condition: ScreenCondition
    detectors: list[object | None] = field(default_factory=list)  # components.screen_watch.vision.ChangeDetector, one per watched region
    deadline_mono: float = field(default=0.0)

    @classmethod
    def create(cls, condition: ScreenCondition, timeout_s: float = 0.0) -> ConditionRuntime:
        """Wrap *condition*, arming an optional wall-clock deadline."""
        deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0
        detectors: list[object | None] = []
        if condition.source == "screen" and condition.mode == "region_changed":
            # One independent baseline per watched rectangle.
            detectors = [vision.ChangeDetector(condition.change_threshold) for _ in condition.watch_regions]
        return cls(condition=condition, detectors=detectors, deadline_mono=deadline)

    def expired(self) -> bool:
        """True once the optional timeout has elapsed."""
        return self.deadline_mono > 0 and time.monotonic() >= self.deadline_mono

    def evaluate(self, capture: object | None = None) -> bool:
        """Report whether the rule currently holds.

        Non-screen rules (user input / executed action) are sampled by the
        engine itself -- they never hold here; treat them as not holding.
        For screen rules each watched region is captured once; with several
        rectangles the modes compose as expected: ``image_found``/
        ``region_changed``/``color_present`` need *all* regions to hold,
        ``image_missing``/``color_absent`` need the pattern/colour absent from
        *all* of them.
        """
        config = self.condition
        if config.source != "screen":
            return False
        template = None
        target_rgb = None
        if config.mode in ("image_found", "image_missing"):
            template = vision.load_template(config.template_path)
            if template is None:
                return False
        elif config.mode in ("color_present", "color_absent"):
            target_rgb = vision.parse_hex_color(config.color_hex)
            if target_rgb is None:
                return False
        results: list[bool] = []
        for index, rect in enumerate(config.watch_regions):
            frame = vision.grab_region(rect, capture=capture)
            if config.mode == "region_changed":
                detector = self.detectors[index]
                results.append(bool(detector.poll(frame)))  # type: ignore[union-attr]
            elif target_rgb is not None:
                ratio = vision.color_pixel_ratio(frame, target_rgb, config.color_tolerance)
                present = ratio >= config.color_ratio
                results.append(present if config.mode == "color_present" else not present)
            else:
                assert template is not None
                found = vision.match_template(frame, template, config.confidence)
                results.append(found if config.mode == "image_found" else not found)
        return all(results)
