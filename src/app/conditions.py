"""Screen-condition dataclass used by macro flow-control actions.

A :class:`ScreenCondition` describes one rule the app reacts to on screen:
an image appearing or disappearing (template matching) or a region changing
(pixel-difference detection).  ``wait_for`` / ``stop_trigger`` / ``start_trigger`` macro
actions each embed one of these; while a macro runs, the engine's background
monitor evaluates the armed rules automatically -- no separate watcher setup
is needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScreenCondition:
    """One screen-reaction rule.

    Attributes:
        mode: ``image_found`` | ``image_missing`` | ``region_changed``.
        template_path: PNG used by the image modes (empty = not configured).
        confidence: Minimum normalized match score in [0, 1] for image modes.
        change_threshold: Mean per-pixel difference that counts as "changed".
        poll_interval_ms: How often the screen is sampled.
        timeout_ms: Give up waiting after this long (initial wait_for only;
            0 disables the timeout).
        region: Screen rectangle (left, top, width, height) to watch.
    """

    mode: str = "image_found"
    template_path: str = ""
    confidence: float = 0.85
    change_threshold: float = 5.0
    poll_interval_ms: int = 200
    timeout_ms: int = 0
    region: tuple[int, int, int, int] = (0, 0, 1920, 1080)
    #: When True the rule matches only if the template/region appears inside
    #: *every* listed rectangle (logical screen coords).  Empty means the
    #: single :attr:`region` is used -- old documents load unchanged.
    all_regions: tuple[tuple[int, int, int, int], ...] = ()

    @property
    def watch_regions(self) -> list[tuple[int, int, int, int]]:
        """Rectangles this rule samples, primary ``region`` first."""
        return [self.region, *self.all_regions] if self.all_regions else [self.region]

    @property
    def configured(self) -> bool:
        """True when the condition has everything needed to be evaluated."""
        if self.mode == "region_changed":
            return True
        return bool(self.template_path)

    @property
    def description(self) -> str:
        """Short human-readable summary for tables and labels."""
        labels = {
            "image_found": "image appears",
            "image_missing": "image disappears",
            "region_changed": "region changes",
        }
        base = labels.get(self.mode, self.mode)
        extra = f", {len(self.all_regions) + 1} regions" if self.all_regions else ""
        return f"{base} ({self.poll_interval_ms} ms poll{extra})"


@dataclass
class ConditionRuntime:
    """Mutable bookkeeping for one condition while it is being polled."""

    condition: ScreenCondition
    detectors: list[object | None] = field(default_factory=list)  # core.screen_watch.ChangeDetector, one per watched region
    deadline_mono: float = field(default=0.0)

    @classmethod
    def create(cls, condition: ScreenCondition, timeout_s: float = 0.0) -> ConditionRuntime:
        """Wrap *condition*, arming an optional wall-clock deadline."""
        from core.screen_watch import ChangeDetector  # local import: avoid cycles

        deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0
        detectors: list[object | None] = []
        if condition.mode == "region_changed":
            # One independent baseline per watched rectangle.
            detectors = [ChangeDetector(condition.change_threshold) for _ in condition.watch_regions]
        return cls(condition=condition, detectors=detectors, deadline_mono=deadline)

    def expired(self) -> bool:
        """True once the optional timeout has elapsed."""
        return self.deadline_mono > 0 and time.monotonic() >= self.deadline_mono

    def evaluate(self) -> bool:
        """Capture each watched region once; report whether the rule holds.

        With several rectangles the modes compose as expected:
        ``image_found``/``region_changed`` need *all* regions to hold,
        ``image_missing`` needs the template absent from *all* of them.
        """
        from core.screen_watch import grab_region, load_template, match_template

        config = self.condition
        template = None
        if config.mode != "region_changed":
            template = load_template(config.template_path)
            if template is None:
                return False
        results: list[bool] = []
        for index, rect in enumerate(config.watch_regions):
            frame = grab_region(rect)
            if config.mode == "region_changed":
                detector = self.detectors[index]
                results.append(bool(detector.poll(frame)))  # type: ignore[union-attr]
            else:
                assert template is not None
                found = match_template(frame, template, config.confidence)
                results.append(found if config.mode == "image_found" else not found)
        return all(results)
