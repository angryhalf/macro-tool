"""Headless tests for looping/triggering as *actions* in the macro engine.

Covers the migration away from the general-section loop option and the
``if_else`` flow step: ``loop_start``/``loop_end`` pairs repeat their body,
and ``stop_trigger``/``start_trigger`` pairs gate the stretch between them --
with rules that watch the screen, physical user input or other actions.

Everything here runs without a display: screen evaluation is monkeypatched
and real key synthesis is replaced by an event recorder.
"""

from __future__ import annotations

import time

import pytest

from app.conditions import ScreenCondition
from app.settings import ActionConfig, MacroConfig, new_macro_uid
from core import macro_engine
from core.macro_engine import ExecutionToken, MacroEngine


def wait_until(predicate, timeout=3.0, interval=0.01):
    """Poll *predicate* until it returns True; False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture()
def recorder(monkeypatch):
    """Replace perform_action with a thread-safe execution log."""
    events: list[str] = []

    def fake_perform(action, cancel=None):
        del cancel
        events.append(action.kind)

    monkeypatch.setattr(macro_engine, "perform_action", fake_perform)
    return events


def make_macro(*actions: ActionConfig) -> MacroConfig:
    return MacroConfig(name="T", uid=new_macro_uid(), enabled=True, actions=tuple(actions))


# ---------------------------------------------------------------------------
# Loop actions
# ---------------------------------------------------------------------------
def test_loop_pair_repeats_body(recorder):
    """loop_start(amount=N)/loop_end repeats the body N times, then falls through."""
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="click"),
        ActionConfig(kind="loop_start", amount=3),
        ActionConfig(kind="key"),
        ActionConfig(kind="loop_end"),
        ActionConfig(kind="scroll"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: not engine.is_macro_running("T"))
    assert recorder == ["click"] + ["key"] * 3 + ["scroll"]
    stats = engine.macro_stats(macro)
    assert stats["loops"] == 3
    engine.shutdown()


def test_nested_loops(recorder):
    """Inner loop restarts on every outer pass; frames peel correctly."""
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="loop_start", amount=2),      # outer
        ActionConfig(kind="combo"),                     # once per outer pass
        ActionConfig(kind="loop_start", amount=3),      # inner
        ActionConfig(kind="key"),                       # 3x per outer pass
        ActionConfig(kind="loop_end"),                  # inner end
        ActionConfig(kind="loop_end"),                  # outer end
        ActionConfig(kind="scroll"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: not engine.is_macro_running("T"))
    expected = ["combo", "key", "key", "key"] * 2 + ["scroll"]
    assert recorder == expected
    engine.shutdown()


def test_loop_forever_stops_on_cancel(recorder):
    """amount=0 means endless; the emergency stop must still terminate it."""
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="loop_start", amount=0),
        ActionConfig(kind="key"),
        ActionConfig(kind="wait", duration_ms=10),
        ActionConfig(kind="loop_end"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: len(recorder) >= 2)
    engine.stop_macro("T")
    assert wait_until(lambda: not engine.is_macro_running("T"))
    engine.shutdown()


def test_stray_markers_degrade_gracefully(recorder):
    """Unpaired loop markers run as no-ops instead of breaking the sequence."""
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="loop_end"),   # stray end (no start) -> ignored
        ActionConfig(kind="key"),
        ActionConfig(kind="loop_start", amount=2),  # stray start -> no-op marker
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: not engine.is_macro_running("T"))
    assert recorder == ["key"]
    engine.shutdown()


def test_top_loop_frame_matches_innermost():
    stack = MacroEngine._match_loop_markers(
        (
            ActionConfig(kind="loop_start", amount=1),   # 0 outer
            ActionConfig(kind="key"),                    # 1
            ActionConfig(kind="loop_start", amount=1),   # 2 inner
            ActionConfig(kind="scroll"),                 # 3
            ActionConfig(kind="loop_end"),               # 4 inner end
            ActionConfig(kind="loop_end"),               # 5 outer end
        )
    )
    assert len(stack) == 3  # sentinel + outer + inner
    inner, outer = stack[-1], stack[1]
    assert MacroEngine._top_loop_frame(stack, 4) is inner
    # Once the inner loop's budget is spent, its frame is skipped and the
    # outer ``loop_end`` resolves to the still-open outer frame.
    inner["completed"] = 1
    assert MacroEngine._top_loop_frame(stack, 5) is outer
    # A stray index no open frame covers -> None (sentinel alone).
    assert MacroEngine._top_loop_frame([stack[0]], 6) is None


# ---------------------------------------------------------------------------
# Trigger actions (the if/else replacement)
# ---------------------------------------------------------------------------
def test_stop_trigger_blocks_until_start_trigger(recorder, monkeypatch):
    """The stop/start pair behaves like an if/else over the stretch between.

    ``stop_trigger <rule>`` holds every later action while the rule holds;
    the closing ``start_trigger`` step releases those rules when its own
    condition fires -- and a bare ``start_trigger`` releases immediately.
    """
    state = {"holds": True}
    monkeypatch.setattr(
        MacroEngine, "_evaluate_once", staticmethod(lambda condition: state["holds"])
    )
    engine = MacroEngine()
    cond = ScreenCondition(template_path="x.png")
    macro = make_macro(
        ActionConfig(kind="click"),
        ActionConfig(kind="stop_trigger", condition=cond),   # off while cond holds
        ActionConfig(kind="key"),                             # gated action
        ActionConfig(kind="start_trigger"),                   # bare close: release
        ActionConfig(kind="scroll"),                          # tail runs freely
    )
    token = engine.start_macro(macro)
    assert token is not None
    # The worker arms the rule at the marker step...
    assert wait_until(lambda: recorder == ["click"])
    with token.rules_lock:
        assert any(e["mode"] == "while" for e in token.blocked_rules)
    time.sleep(0.2)
    assert "key" not in recorder  # held back by the stop-trigger rule
    state["holds"] = False        # rule clears -> monitor triggers actions on
    # Worker proceeds through the bare start_trigger, which drops the armed
    # while-rule, so even a re-appearing screen can no longer gate the tail.
    assert wait_until(lambda: recorder == ["click", "key"])
    state["holds"] = True
    assert wait_until(lambda: "scroll" in recorder)
    assert wait_until(lambda: not engine.is_macro_running("T"))
    with token.rules_lock:
        assert token.blocked_rules == []  # closed stretch leaves nothing armed
    engine.shutdown()


def test_start_trigger_rule_holds_tail_until_it_fires(recorder, monkeypatch):
    """A start_trigger *with* a rule holds the tail until the rule fires once."""
    state = {"holds": False}
    monkeypatch.setattr(
        MacroEngine, "_evaluate_once", staticmethod(lambda condition: state["holds"])
    )
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="click"),
        ActionConfig(kind="start_trigger", condition=ScreenCondition(template_path="x.png")),
        ActionConfig(kind="key"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: recorder == ["click"])
    with token.rules_lock:
        assert any(e["mode"] == "until" for e in token.blocked_rules)
    time.sleep(0.15)
    assert "key" not in recorder
    state["holds"] = True  # rule fires -> until self-disarms, tail runs
    assert wait_until(lambda: "key" in recorder)
    assert wait_until(lambda: not engine.is_macro_running("T"))
    engine.shutdown()


def test_bare_stop_start_pair_holds_and_releases(recorder):
    """Bare markers: everything between them waits for the closing marker."""
    engine = MacroEngine()
    macro = make_macro(
        ActionConfig(kind="click"),
        ActionConfig(kind="stop_trigger"),   # bare: hold the stretch
        ActionConfig(kind="key"),
        ActionConfig(kind="start_trigger"),  # bare: release the stretch
        ActionConfig(kind="scroll"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    # The worker parks on the hold; the tail must not have run yet.
    assert wait_until(lambda: recorder == ["click"])
    time.sleep(0.15)
    assert recorder == ["click"]
    # Reaching the bare start_trigger happens on the worker *after* the
    # gated key, so release the hold from outside to mirror the monitor:
    with token.rules_lock:
        token.blocked_rules = [e for e in token.blocked_rules if e["mode"] != "hold"]
    token.refresh_block()
    assert wait_until(lambda: recorder == ["click", "key", "scroll"])
    engine.shutdown()


def test_start_trigger_waits_for_user_input_event(recorder):
    """An ``input``-source start trigger releases when the event counter bumps."""
    engine = MacroEngine()
    cond = ScreenCondition(event="key:a")
    macro = make_macro(
        ActionConfig(kind="start_trigger", condition=cond),
        ActionConfig(kind="key"),
    )
    token = engine.start_macro(macro)
    assert token is not None
    assert wait_until(lambda: token.gated)
    # Simulate the physical 'a' press reaching the token's watchers.
    engine._bump_matching_tokens("key:a")
    assert wait_until(lambda: "key" in recorder)
    assert wait_until(lambda: not engine.is_macro_running("T"))
    engine.shutdown()


def test_action_source_rule_fires_when_action_executes(recorder):
    """An ``action``-source stop trigger gates later steps after the watched kind ran."""
    engine = MacroEngine()
    cond = ScreenCondition(event="action:click")
    macro = make_macro(
        ActionConfig(kind="click"),
        ActionConfig(kind="stop_trigger", condition=cond),
        ActionConfig(kind="key"),
        ActionConfig(kind="start_trigger"),  # bare close so the run can finish
    )
    token = engine.start_macro(macro)
    assert token is not None
    # The click bumped ``action:click`` past the rule baseline, so the
    # while-rule should hold the following action back.
    assert wait_until(lambda: recorder == ["click"] and token.gated)
    time.sleep(0.1)
    assert "key" not in recorder
    engine.stop_all()
    assert wait_until(lambda: not engine.is_macro_running("T"))
    engine.shutdown()


def test_token_event_counters():
    token = ExecutionToken()
    assert token.event_count("key:a") == 0
    token.bump_event("key:a")
    token.bump_event("key:a")
    assert token.event_count("key:a") == 2
    assert token.event_count("mouse:left") == 0
