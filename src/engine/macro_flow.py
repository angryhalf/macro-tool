"""Worker-thread action playback: loops, triggers and waits.

:class:`MacroRunner` executes one pass of a macro's action list.  It is the
*forward* half of the engine (the *background* half that polls armed rules
is :mod:`engine.condition_monitor`); both cooperate through the shared
:class:`~engine.token.ExecutionToken`:

* ``wait_for`` -- block until its condition holds (optional timeout).
* ``stop_trigger <rule>`` -- hold later actions while the rule holds.
* ``start_trigger <rule>`` -- keep later actions held until the rule becomes
  true.  A bare ``start_trigger`` closes the stretch: it drops every armed
  rule so later actions run freely again.  Together the two steps work like
  an if/else over the stretch of actions written between them.
* ``loop_start`` / ``loop_end`` -- jump back/forward pair that repeats the
  steps between them (iteration count on the start step, delay on the end
  step) instead of a general per-macro loop option.

Flow-control kinds never reach real input synthesis; ordinary actions are
played through :func:`automation.synthesis.perform_action`, which the
engine injects via the runner constructor (tests replace it there).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from engine.condition_runtime import ConditionRuntime
from engine.token import ExecutionToken, MacroStats
from models.condition import ScreenCondition
from models.macro import ActionConfig

logger = logging.getLogger(__name__)


class MacroRunner:
    """Plays one macro's action list against a shared execution token.

    *engine* is duck-typed: the runner reads ``engine.lock`` (the engine's
    threading lock guarding the stats dict) and ``engine.stats`` (uid ->
    :class:`~engine.token.MacroStats`).  The engine object itself owns the
    token set, hotkeys and monitor; this class only runs the worker pass.
    """

    def __init__(
        self,
        engine: object,
        perform: Callable[[ActionConfig, Callable[[], bool] | None], None],
    ) -> None:
        self._engine = engine
        #: Injected action executor -- ``automation.synthesis.perform_action``
        #: in production, a recorder in tests.
        self.perform_action = perform

    # ------------------------------------------------------------------
    # Main pass
    # ------------------------------------------------------------------
    def run_actions(self, actions: tuple[ActionConfig, ...], token: ExecutionToken) -> bool:
        """Execute one pass of *actions*; False when the pass ended early.

        A ``stop_trigger`` with a rule arms it (later actions held while the
        rule holds); a ``start_trigger`` with a rule holds later actions
        until the rule fires once -- together they gate the stretch between
        them like an if/else.  Rules can watch the screen, physical user
        input or other actions executing.  Bare markers hold/release the
        stretch between them.  ``loop_start``/``loop_end`` repeat the steps
        between them.  Flow steps never block themselves -- normal actions
        call :meth:`ExecutionToken.wait_while_blocked` before firing.
        """
        loop_frames = self._match_loop_markers(actions)
        index = 0
        total = len(actions)
        while index < total:
            if token.cancelled:
                return False
            action = actions[index]
            kind = action.kind
            condition = action.condition
            has_rule = condition is not None and condition.configured
            if kind not in ("loop_start", "loop_end", "wait_for", "stop_trigger", "start_trigger"):
                # Normal action: fires only while no trigger rule holds it back.
                # Flow markers (wait_for / stop_trigger / start_trigger) are
                # never held back by the very rules they arm or close -- a
                # marker blocked by its own rule would deadlock the run.
                token.wait_while_blocked()
                if token.cancelled:
                    return False
            if kind == "wait_for":
                if not self._await_condition(condition, token):
                    return False
            elif kind == "loop_end":
                # Reached from inside the loop body (or a stray marker).
                entry = self._top_loop_frame(loop_frames, index)
                if entry is None:
                    logger.warning("Unmatched loop_end at step %d/%d; ignoring", index + 1, total)
                    index += 1
                    continue
                entry["completed"] = entry.get("completed", 0) + 1
                iterations = entry["iterations"]
                if iterations and entry["completed"] >= iterations:
                    logger.info(
                        "Loop %d/%d completed at step %d/%d, falling through",
                        entry["completed"], iterations, index + 1, total,
                    )
                    if token.macro is not None:
                        with self._engine.lock:  # type: ignore[attr-defined]
                            stats = self._engine.stats.get(token.macro.uid or token.macro.name)  # type: ignore[attr-defined]
                            if isinstance(stats, MacroStats):
                                stats.loops = entry["completed"]
                    index += 1  # exhausted: leave the loop, run on past it
                else:
                    if action.duration_ms > 0:
                        token.wait(action.duration_ms / 1000.0)
                    if token.cancelled:
                        return False
                    # Restarting this frame re-enters its whole body, so any
                    # inner loop nested inside it starts over as well -- a
                    # stale "completed" count would make the inner loop fall
                    # through on later outer passes instead of repeating.
                    for inner in loop_frames:
                        if entry["start"] < inner["start"] < index and inner is not entry:
                            inner.pop("completed", None)
                    index = entry["start"] + 1  # jump back into the loop body
                continue
            elif kind == "loop_start":
                # Marker only: the frame was pre-paired in ``stack``; the
                # body between this step and its loop_end just runs on.
                pass
            elif kind == "stop_trigger":
                # Trigger this step's rule(s) off for everything after it.
                with token.rules_lock:
                    if has_rule:
                        assert condition is not None
                        entry = {
                            "mode": "while", "condition": condition, "_runtime": None,
                            "_blocking": False, "_token": token,
                        }
                        if condition.source in ("input", "action"):
                            # Baseline the counter at arming so only events
                            # happening *from now on* gate later actions --
                            # a click that ran before this marker must not
                            # instantly satisfy an ``action:click`` rule.
                            entry["_baseline"] = token.event_count(condition.event)
                        token.blocked_rules.append(entry)
                        message = (
                            "Stop trigger armed at step %d/%d: actions after this point are "
                            "triggered off while '%s' holds"
                        )
                        args: tuple = (index + 1, total, condition.description)
                    else:
                        # Bare marker: hold everything after it until the next
                        # bare start_trigger releases the hold.
                        token.blocked_rules.append({"mode": "hold", "condition": None, "_blocking": True})
                        message = (
                            "Trigger stretch opened at step %d/%d: actions hold until the "
                            "next start-trigger step"
                        )
                        args = (index + 1, total)
                logger.info(message, *args)
                self._sync_triggers(token)
            elif kind == "start_trigger":
                if has_rule:
                    # Trigger on only after this rule fires once.
                    assert condition is not None
                    entry = {
                        "mode": "until", "condition": condition, "_runtime": None,
                        "_blocking": True, "_token": token,
                    }
                    if condition.source in ("input", "action"):
                        # Only events happening *after* arming release the tail.
                        entry["_baseline"] = token.event_count(condition.event)
                    with token.rules_lock:
                        token.blocked_rules.append(entry)
                    logger.info(
                        "Start trigger armed at step %d/%d: actions after this point "
                        "stay triggered off until '%s' fires",
                        index + 1, total, condition.description,
                    )
                else:
                    # Close the current stretch: drop every hold/until entry
                    # *and* any stop-trigger rule that gated the stretch, so
                    # the actions written after this marker run freely again.
                    with token.rules_lock:
                        token.blocked_rules = []
                    token.refresh_block()
                    logger.info("Trigger stretch closed at step %d/%d", index + 1, total)
                self._sync_triggers(token)
            else:
                token.wait_while_blocked()  # don't fire while triggered off
                if token.cancelled:
                    return False
                # Pass the cancellation predicate so long actions (key holds,
                # typing runs, waits) abort promptly instead of blocking the
                # emergency stop for their full duration.
                # (keyword form matches perform_action's signature; the tests'
                # fakes accept ``cancel`` as a keyword too)
                self.perform_action(action, cancel=lambda: token.cancelled or token.gated)
                # Record the execution so ``action:<kind>`` rules armed by
                # stop/start triggers can react to it.
                token.bump_event(f"action:{kind}")
            index += 1
        return True

    # ------------------------------------------------------------------
    # Loop pairing
    # ------------------------------------------------------------------
    @staticmethod
    def _match_loop_markers(actions: tuple[ActionConfig, ...]) -> list[dict]:
        """Pre-pair every ``loop_start`` with its matching ``loop_end``.

        Returns a stack of loop frames (outermost first) plus a sentinel
        frame covering the whole list; :meth:`_top_loop_frame` peels the
        innermost frame whose body contains the current index.  Unpaired
        markers degrade gracefully (a stray ``loop_end`` is ignored, a stray
        ``loop_start`` simply runs as a no-op marker).
        """
        pending: list[int] = []
        pairs: dict[int, int] = {}
        for i, action in enumerate(actions):
            if action.kind == "loop_start":
                pending.append(i)
            elif action.kind == "loop_end" and pending:
                pairs[pending.pop()] = i
        sentinel = {"start": -1, "end": len(actions), "iterations": 0}
        stack = [sentinel]
        # Push outermost-first so popping at runtime yields innermost first.
        for start in sorted(pairs):
            iterations = max(0, actions[start].amount)
            stack.append({"start": start, "end": pairs[start], "iterations": iterations})
        return stack

    @staticmethod
    def _top_loop_frame(stack: list[dict], index: int) -> dict | None:
        """Return the innermost *still-open* loop frame containing *index*.

        Frames stay on the stack for their whole life (iteration state lives
        on the dict itself), so a finished inner frame sits above the outer
        one that is still looping.  Scan from the top down and skip frames
        whose iteration budget is exhausted or whose body ended before this
        ``loop_end``; only such frames can nest *above* an open one, so the
        first match is the correct innermost target.  Returns None when only
        the sentinel covers the index (a stray ``loop_end``).
        """
        for entry in reversed(stack[1:]):
            if entry["start"] < index <= entry["end"]:
                iterations = entry["iterations"]
                completed = entry.get("completed", 0)
                if iterations and completed >= iterations:
                    continue  # exhausted earlier -- look one level out
                return entry
        return None

    # ------------------------------------------------------------------
    # Trigger bookkeeping (worker side)
    # ------------------------------------------------------------------
    def _sync_triggers(self, token: ExecutionToken) -> None:
        """Reflect the *current* state of the armed rules right away.

        Also (re)starts or stops the shared user-input listener so physical
        key/mouse events reach exactly the macros waiting on them.
        """
        self._apply_rule_states(token)
        token.refresh_block()
        self._record_token_block(token)
        # Ask the engine to keep the global input watcher in step with demand.
        sync = getattr(self._engine, "sync_input_watch", None)
        if sync is not None:
            sync()

    @staticmethod
    def _apply_rule_states(token: ExecutionToken) -> None:
        """Re-evaluate every armed rule and store its blocking state."""
        # Imported lazily: the monitor module imports nothing from here, but
        # keeping the dependency one-way documents the layering.
        from engine.condition_monitor import rule_blocks_now

        with token.rules_lock:
            for entry in token.blocked_rules:
                entry["_blocking"] = rule_blocks_now(entry)

    @staticmethod
    def _first_blocking_reason(token: ExecutionToken) -> str | None:
        """Human-readable name of the rule currently holding actions back."""
        with token.rules_lock:
            for entry in token.blocked_rules:
                if not entry.get("_blocking"):
                    continue
                condition = entry.get("condition")
                mode = entry.get("mode", "?")
                return condition.description if condition is not None else f"{mode} hold"
        return None

    def _record_token_block(self, token: ExecutionToken) -> None:
        """Publish the rule that currently blocks *token* into its stats.

        The engine re-acquires its lock inside, so this must run *outside*
        ``token.rules_lock`` (lock ordering: the engine lock is never held
        while taking a token's rules lock).
        """
        if token.macro is None:
            return
        reason = self._first_blocking_reason(token)
        record = getattr(self._engine, "record_block_reason", None)
        if record is not None:
            record(token, reason)

    # ------------------------------------------------------------------
    # wait_for support
    # ------------------------------------------------------------------
    def _await_condition(self, condition: ScreenCondition | None, token: ExecutionToken) -> bool:
        """Block until *condition* holds; False when cancelled or timed out."""
        if condition is None or not condition.configured:
            logger.warning("wait_for action without a usable condition; skipping wait")
            return True
        runtime = ConditionRuntime.create(condition, timeout_s=condition.timeout_ms / 1000.0)
        interval_s = max(condition.poll_interval_ms, 10) / 1000.0
        logger.info("Waiting for condition: %s", condition.description)
        while not token.cancelled:
            try:
                if runtime.evaluate():
                    logger.info("Condition met, continuing: %s", condition.description)
                    return True
            except Exception:  # pragma: no cover - transient capture failures
                logger.exception("Condition check failed (%s)", condition.description)
            if runtime.expired():
                logger.info("Condition timeout reached, giving up: %s", condition.description)
                return False
            token.wait(interval_s)
        return False
