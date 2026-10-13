"""Condition dataclass used by macro flow-control actions.

A :class:`ScreenCondition` describes one rule the app reacts to.  Rules come
from three sources, selected by the ``event`` field:

* **screen** (``event=""``, the default): an image appearing or disappearing
  (template matching), a specific colour appearing/disappearing inside a
  region (HSV-range pixel counting) or a region changing (pixel-difference
  detection).
* **user input** (``key:<name>`` / ``mouse:<button>`` / ``wheel``): fires when
  the user physically performs that input while the macro runs.
* **action** (``action:<kind>``): fires when another macro action of that
  kind executes.

``wait_for`` / ``stop_trigger`` / ``start_trigger`` macro actions each embed
one of these; while a macro runs, the engine's background monitor evaluates
the armed rules automatically -- no separate watcher setup is needed.

This module is pure data + parsing: it imports nothing from the rest of the
project so every layer can depend on it without cycles.  The runtime
bookkeeping (:class:`ConditionRuntime`) lives with the evaluation machinery
in :mod:`engine.condition_runtime`, which *does* reach into
:mod:`vision.vision` for capture and matching.
"""

from __future__ import annotations

from dataclasses import dataclass


def parse_event(event: str) -> tuple[str, str]:
    """Split an ``event`` string into ``(source, target)``.

    ``""`` -> ``("screen", "")``; ``"key:a"`` -> ``("input", "a")``;
    ``"mouse:left"`` -> ``("input", "left")``; ``"wheel"`` -> ``("input",
    "wheel")``; ``"action:key"`` -> ``("action", "key")``.
    """
    if not event:
        return "screen", ""
    if event == "wheel":
        return "input", "wheel"
    prefix, _, target = event.partition(":")
    if prefix in ("key", "mouse"):
        return "input", target
    if prefix == "action":
        return "action", target
    # Unknown shape: treat the whole string as an input key name so the
    # rule still does something sensible instead of watching the screen.
    return "input", event


@dataclass(frozen=True)
class ScreenCondition:
    """One reaction rule (screen, user input, or executed action).

    Attributes:
        mode: ``image_found`` | ``image_missing`` | ``region_changed`` |
            ``color_present`` | ``color_absent`` for screen rules; mirrored
            from the event target for input/action rules (kept only for
            backwards-compatible serialisation).
        event: Empty (default) for pure screen rules; otherwise
            ``key:<name>`` / ``mouse:<button>`` / ``wheel`` for user-input
            rules and ``action:<kind>`` for rules that fire when another
            action executes.
        template_path: PNG used by the image modes (empty = not configured).
        confidence: Minimum normalized match score in [0, 1] for image modes.
        change_threshold: Mean per-pixel difference that counts as "changed".
        poll_interval_ms: How often the source is sampled.
        timeout_ms: Give up waiting after this long (initial wait_for only;
            0 disables the timeout).
        region: Screen rectangle (left, top, width, height) to watch.
    """

    mode: str = "image_found"
    #: Rule source selector -- see :func:`parse_event`.
    event: str = ""
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
    #: Target colour for the ``color_present`` / ``color_absent`` modes as a
    #: ``#RRGGBB`` hex string (empty = not configured).
    color_hex: str = ""
    #: Per-channel tolerance in [0, 255] applied around the target colour when
    #: counting matching pixels (hue uses half of it -- OpenCV's HSV scale).
    color_tolerance: int = 40
    #: Fraction (0..1) of the watched region that must match the colour before
    #: ``color_present`` holds; ``color_absent`` holds below this fraction.
    color_ratio: float = 0.01

    @property
    def source(self) -> str:
        """Where this rule observes: ``screen`` | ``input`` | ``action``."""
        return parse_event(self.event)[0]

    @property
    def target(self) -> str:
        """Event subject for input/action rules (key name, button, kind...)."""
        return parse_event(self.event)[1]

    @property
    def watch_regions(self) -> list[tuple[int, int, int, int]]:
        """Rectangles this rule samples, primary ``region`` first."""
        return [self.region, *self.all_regions] if self.all_regions else [self.region]

    @property
    def configured(self) -> bool:
        """True when the condition has everything needed to be evaluated."""
        source = self.source
        if source in ("input", "action"):
            return bool(self.target)
        if self.mode == "region_changed":
            return True
        if self.mode in ("color_present", "color_absent"):
            # Local validation keeps this module dependency-free; the exact
            # same grammar is implemented once in vision.vision.parse_hex_color.
            cleaned = self.color_hex.strip().lstrip("#")
            if len(cleaned) == 3:
                cleaned = "".join(ch * 2 for ch in cleaned)
            return len(cleaned) == 6 and all(c in "0123456789abcdefABCDEF" for c in cleaned)
        return bool(self.template_path)

    @property
    def description(self) -> str:
        """Short human-readable summary for tables and labels."""
        labels = {
            "image_found": "image appears",
            "image_missing": "image disappears",
            "region_changed": "region changes",
            "color_present": f"colour {self.color_hex or '?'} appears",
            "color_absent": f"colour {self.color_hex or '?'} disappears",
        }
        if self.source == "input":
            if self.target == "wheel":
                base = "user scrolls"
            elif self.event.startswith("mouse:"):
                base = f"user clicks {self.target}"
            else:
                base = f"user presses '{self.target}'"
        elif self.source == "action":
            base = f"action '{self.target}' executes"
        else:
            base = labels.get(self.mode, self.mode)
        extra = f", {len(self.all_regions) + 1} regions" if self.all_regions else ""
        return f"{base} ({self.poll_interval_ms} ms poll{extra})"
