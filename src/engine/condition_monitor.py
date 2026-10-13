"""Background polling of armed trigger rules (the engine's monitor half).

While a macro runs, its ``stop_trigger``/``start_trigger`` steps leave rules
armed on the :class:`~engine.token.ExecutionToken`.  This module samples
those rules -- screen conditions through :class:`~engine.condition_runtime.ConditionRuntime`,
input/action conditions against the token's event counters -- and flips the
matching actions between triggered on and off.  The functions here are
pure helpers plus one loop body; :class:`engine.macro_engine.MacroEngine`
owns the thread that calls :func:`monitor_token` periodically.
"""

from __future__ import annotations

import logging

from engine.condition_runtime import ConditionRuntime
from engine.token import ExecutionToken
from models.condition import ScreenCondition

logger = logging.getLogger(__name__)


def rule_blocks_now(entry: dict) -> bool:
    """Whether one armed trigger rule currently blocks later actions.

    ``while`` blocks while the condition holds; ``until`` blocks until it
    holds once (then self-disarms); ``hold`` always blocks until a bare
    start-trigger step drops it.  Screen rules are sampled live; input
    rules hold until the user physically performs the watched event and
    action rules until another action of the watched kind executes --
    both are detected by comparing the per-run event counter against the
    baseline stored when the rule was armed.
    """
    mode = entry.get("mode")
    if mode == "hold":
        return True
    condition = entry.get("condition")
    if condition is None or not condition.configured:
        return False
    source = condition.source
    if source in ("input", "action"):
        holding = event_rule_fired(entry, condition)
    else:
        runtime = entry.get("_runtime")
        if runtime is None:
            runtime = ConditionRuntime.create(condition)
            entry["_runtime"] = runtime
        try:
            holding = bool(runtime.evaluate())
        except Exception:  # pragma: no cover - transient capture failures
            logger.exception("Screen-condition check failed (%s)", condition.description)
            return bool(entry.get("_blocking"))
    if mode == "while":
        return holding
    # mode == "until": met once => trigger on and disarm this rule.
    if holding:
        logger.info("Start-trigger condition met, triggering actions on: %s", condition.description)
        entry["disarm"] = True
        return False
    return True


def event_rule_fired(entry: dict, condition: ScreenCondition) -> bool:
    """True when the watched input/action event happened since arming.

    Arming stores the current counter value as a baseline; the rule
    "holds" as soon as the counter grows past it.  ``while`` rules clear
    again immediately after being consumed so they only gate while the
    most recent event matches; ``until`` rules latch via the caller's
    disarm logic.
    """
    token = entry.get("_token")
    if token is None:
        return False
    key = condition.event
    count = token.event_count(key)
    baseline = entry.get("_baseline")
    if baseline is None:
        entry["_baseline"] = count
        return False
    if count > baseline:
        entry["_baseline"] = count  # consume the event
        return True
    return False


def evaluate_once(condition: ScreenCondition) -> bool:
    """One synchronous screen check; False on any failure."""
    runtime = ConditionRuntime.create(condition)
    try:
        return bool(runtime.evaluate())
    except Exception:  # pragma: no cover - transient capture failures
        logger.exception("Screen-condition check failed (%s)", condition.description)
        return False


def monitor_token(token: ExecutionToken, record_block: object | None = None) -> bool:
    """Re-evaluate one macro's armed rules; True when triggers flipped.

    This is the automatic screen watching: every rule a running macro's
    ``stop_trigger``/``start_trigger`` steps left armed gets sampled here, and the
    blocked/on state of the actions after them follows the screen.  When
    an ``until`` rule has been met it is removed from the set entirely.

    *record_block* is an optional callback invoked with the token to keep
    the UI's "blocked_by" stat current even when the overall gated flag
    did not flip (e.g. rule A released, rule B took over).
    """
    with token.rules_lock:
        if not token.blocked_rules:
            # Nothing to watch -- make sure nothing stale is left blocking.
            was_blocked = token.gated
            token.refresh_block()
            return was_blocked
        for entry in token.blocked_rules:
            entry["_blocking"] = rule_blocks_now(entry)
        token.blocked_rules = [e for e in token.blocked_rules if not e.get("disarm")]
    before = token.gated
    token.refresh_block()
    now = token.gated
    if callable(record_block):
        record_block(token)  # type: ignore[operator]
    if before != now:
        if now:
            logger.info("Actions triggered off by screen rule")
        else:
            logger.info("Actions triggered on again")
        return True
    return False
