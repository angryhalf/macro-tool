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
        return f"{base} ({self.poll_interval_ms} ms poll)"


@dataclass
class ConditionRuntime:
    """Mutable bookkeeping for one condition while it is being polled."""

    condition: ScreenCondition
    detector: object | None = None  # core.screen_watch.ChangeDetector
    deadline_mono: float = field(default=0.0)

    @classmethod
    def create(cls, condition: ScreenCondition, timeout_s: float = 0.0) -> "ConditionRuntime":
        """Wrap *condition*, arming an optional wall-clock deadline."""
        from core.screen_watch import ChangeDetector  # local import: avoid cycles

        deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0
        detector = ChangeDetector(condition.change_threshold) if condition.mode == "region_changed" else None
        return cls(condition=condition, detector=detector, deadline_mono=deadline)

    def expired(self) -> bool:
        """True once the optional timeout has elapsed."""
        return self.deadline_mono > 0 and time.monotonic() >= self.deadline_mono

    def evaluate(self) -> bool:
        """Capture the screen once and report whether the condition holds."""
        from core.screen_watch import grab_region, load_template, match_template

        config = self.condition
        frame = grab_region(config.region)
        if config.mode == "region_changed":
            assert self.detector is not None
            return bool(self.detector.poll(frame))  # type: ignore[attr-defined]
        template = load_template(config.template_path)
        if template is None:
            return False
        found = match_template(frame, template, config.confidence)
        return found if config.mode == "image_found" else not found
