"""Headless tests for the recording -> actions conversion (pure domain).

:func:`components.macro.recording.events_to_actions` used to live inside
the 1232-line Qt ``macro_editor.py``, untestable without a display; moving
it to the domain layer is what makes this suite possible (see
``docs/architecture-review-2026-11-11.md``, candidate 2).  All timestamps
below are seconds since the recording started; key/button names are the
canonical names the recorder dialog appends.
"""

from __future__ import annotations

from components.macro.macro import ActionConfig
from components.macro.recording import events_to_actions


def kinds(actions: list[ActionConfig]) -> list[str]:
    return [a.kind for a in actions]


# ------------------------------------------------------------------- keys
def test_short_key_press_becomes_key():
    actions = events_to_actions(
        [
            (0.00, "key_down", {"key": "a"}),
            (0.10, "key_up", {"key": "a"}),
        ]
    )
    assert actions == [ActionConfig(kind="key", key="a")]


def test_long_key_press_becomes_hold_key():
    actions = events_to_actions(
        [
            (0.00, "key_down", {"key": "f5"}),
            (0.60, "key_up", {"key": "f5"}),
        ]
    )
    assert actions == [ActionConfig(kind="hold_key", key="f5", duration_ms=600)]


def test_key_pair_threshold_is_250ms():
    """250 ms is the last duration that still counts as a plain press."""
    actions = events_to_actions(
        [
            (0.00, "key_down", {"key": "a"}),
            (0.25, "key_up", {"key": "a"}),
        ]
    )
    assert kinds(actions) == ["key"]


# ------------------------------------------------------------------- mouse
def test_click_folding():
    """A short press/release pair with almost no travel becomes one click."""
    actions = events_to_actions(
        [
            (0.00, "button_down", {"button": "left", "x": 100, "y": 100}),
            (0.15, "button_up", {"button": "left", "x": 102, "y": 101}),
        ]
    )
    assert actions == [ActionConfig(kind="click", button="left")]


def test_drag_folding_encodes_start_and_end():
    """A long or travelling press/release pair becomes a single drag step."""
    actions = events_to_actions(
        [
            (0.00, "button_down", {"button": "left", "x": 100, "y": 100}),
            (0.60, "button_up", {"button": "left", "x": 300, "y": 200}),
        ]
    )
    # NOTE: drag reuses amount/duration_ms for the end point (see
    # ActionConfig's field-overloading warning in components.macro.macro).
    assert actions == [
        ActionConfig(
            kind="drag", button="left", x=100, y=100, amount=300, duration_ms=200
        )
    ]


def test_long_press_without_movement_is_still_a_drag():
    """Held > 300 ms with zero travel is a drag whose start equals its end."""
    actions = events_to_actions(
        [
            (0.00, "button_down", {"button": "right", "x": 50, "y": 60}),
            (0.40, "button_up", {"button": "right", "x": 50, "y": 60}),
        ]
    )
    assert kinds(actions) == ["drag"]
    assert actions[0].x == actions[0].amount == 50
    assert actions[0].y == actions[0].duration_ms == 60


def test_move_burst_thins_to_last_position():
    actions = events_to_actions(
        [
            (0.00, "move", {"x": 10, "y": 10}),
            (0.10, "move", {"x": 20, "y": 20}),
            (0.20, "move", {"x": 30, "y": 30}),
            (0.30, "key_down", {"key": "a"}),
            (0.35, "key_up", {"key": "a"}),
        ]
    )
    # The burst collapses to its last position; the idle time before the
    # key press becomes a wait between the move and the key.
    assert kinds(actions) == ["move", "wait", "key"]
    assert actions[0] == ActionConfig(kind="move", x=30, y=30)
    assert actions[1] == ActionConfig(kind="wait", duration_ms=300)


def test_scroll_event_becomes_scroll_step():
    actions = events_to_actions([(0.05, "scroll", {"amount": -2})])
    assert kinds(actions) == ["wait", "scroll"]
    assert actions[1] == ActionConfig(kind="scroll", amount=-2)


# ------------------------------------------------------------------- timing
def test_idle_gap_becomes_wait():
    actions = events_to_actions(
        [
            (0.00, "key_down", {"key": "a"}),
            (0.05, "key_up", {"key": "a"}),
            (1.00, "key_down", {"key": "b"}),
            (1.05, "key_up", {"key": "b"}),
        ]
    )
    assert kinds(actions) == ["key", "wait", "key"]
    assert actions[1] == ActionConfig(kind="wait", duration_ms=950)


def test_tiny_gaps_do_not_become_waits():
    actions = events_to_actions(
        [
            (0.000, "key_down", {"key": "a"}),
            (0.012, "key_up", {"key": "a"}),
            (0.012, "key_down", {"key": "b"}),
            (0.024, "key_up", {"key": "b"}),
        ]
    )
    assert actions == [
        ActionConfig(kind="key", key="a"),
        ActionConfig(kind="key", key="b"),
    ]


def test_trailing_move_flushes_as_final_action():
    """A move burst that ends the recording still becomes a move step."""
    actions = events_to_actions(
        [
            (0.00, "move", {"x": 5, "y": 5}),
            (0.10, "move", {"x": 15, "y": 15}),
        ]
    )
    assert kinds(actions) == ["move", "wait"]
    assert actions[0] == ActionConfig(kind="move", x=15, y=15)


# ------------------------------------------------------------------- edge cases
def test_unmatched_releases_are_ignored():
    actions = events_to_actions(
        [
            (0.00, "key_up", {"key": "a"}),
            (0.00, "button_up", {"button": "left", "x": 0, "y": 0}),
        ]
    )
    assert actions == []


def test_empty_stream_returns_no_actions():
    assert events_to_actions([]) == []
