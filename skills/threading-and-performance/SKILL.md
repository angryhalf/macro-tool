---
name: threading-and-performance
description: Keep the engine responsive and correct across threads — token-based cancellation, event parking instead of busy-wait, cached captures, throttled input. Use when editing playback, monitor loop, vision, hotkeys, recorder, or anything running per tick/frame.
---

# Threading & Performance

The app runs four concurrent worlds: **Qt GUI thread**, one **worker thread
per running macro**, a single **monitor thread** (~50 ms tick), and
**pynput listener threads**. Rules below keep them deadlock-free and cheap.

## Threading rules

1. **Threads communicate only through `ExecutionToken`** (`playback/token.py`)
   and Qt signals. No shared plain dicts/lists guarded by ad-hoc checks.
   The token provides: cancellation flag, blocked-state + reason, event
   counters, and cancellable waits (`wait_while_blocked`, sleeps that abort
   on stop).
2. **Never block the GUI thread.** Screen capture, template matching, and
   synthesis happen off-thread; UI polls state or receives signals. Long
   setup work uses `QTimer.singleShot` deferral, not synchronous loops.
3. **Park, don't spin.** Idle loops wait on `threading.Event` with a timeout
   (`_monitor_loop` parks when no macros run; tokens park while blocked).
   Any new polling loop must exit promptly when its Event is set — check
   cancellation *before and after* every wait.
4. **Cancellation is cooperative and final.** Workers poll the token between
   actions; `perform_action` and condition evaluation must honour it. On
   stop/cancel, always release held keys/buttons (synthesis cleanup path) —
   a stuck modifier is the worst failure mode this app can have.
5. **Start/stop trigger arming never deadlocks:** flow-control actions
   bypass `wait_while_blocked` when they themselves change the blocked
   state. Preserve this bypass when touching `MacroRunner`.
6. **Thread-locals for OS handles:** `mss` capture contexts are per-thread
   (cached thread-local). Never share a `mss.mss()` instance across threads;
   new off-thread capture code follows the same cache pattern in
   `screen_watch/vision.py`.
7. **Listener callbacks stay tiny.** pynput hotkey/recorder callbacks record
   minimal state (counters, last-event) and let consumers do the work —
   never call Qt APIs from listener threads except via signals.

## Performance rules

- **Hot paths** (monitor tick, per-frame matching, recorder move stream):
  no allocations-heavy code, no disk IO, no repeated parsing.
  - Hotkey combos are pre-parsed into canonical frozensets once, per press.
  - Templates are cached by `(path, mtime)`; region captures reused within
    a single evaluation pass (one grab serves all conditions on that region).
  - Mouse-move recording is throttled to a fixed interval; drops
    intermediate positions deliberately.
- **Poll cadence:** monitor ticks ~50 ms; tune via existing constants, not
  scattered magic numbers. UI updates are incremental (only changed rows /
  dirty flags), never full-model rebuilds per tick.
- **Measure before optimising further:** numpy/OpenCV ops dominate; prefer
  grayscale matching, smallest sufficient ROI, and early-exit comparisons
  already used in `vision.py`. Don't add caching without an invalidation
  story (mtime, explicit clear, or lifetime bound to settings save).
- JSON saves happen on user actions, not per tick. If you ever persist
  runtime stats, batch and debounce.

## Recipes

**Add a new per-tick evaluation step:**
1. Implement pure logic behind a documented seam (`ConditionRuntime.evaluate`
   or a helper in `condition_monitor.py`) so tests can monkeypatch it.
2. Reuse the tick's captured frames; do not grab the screen twice per tick.
3. Update token blocked-state via token methods only; set reason strings for
   the status bar.
4. Verify: start two macros + a blocking trigger, hit global stop mid-block,
   confirm both threads exit and inputs release.

**New background worker:** daemon thread, named (`threading.Thread(name=...)`),
parked on an Event, cancellable via token/flag, joined (with timeout) in
engine shutdown.

## Checklist

- [ ] No new lock needed because state flows through token/signals only.
- [ ] Every wait checks cancellation; every exit releases synthesized input.
- [ ] GUI thread performs no capture/matching/synthesis.
- [ ] Hot path free of IO, parsing, and redundant screen grabs.
- [ ] New loops park on Events; engine shutdown joins them.
- [ ] `pytest tests/test_engine_loops_triggers.py` green (covers triggers,
      loops, cancellation).

Related: `docs/macros/engine_internals.md`, `docs/general/architecture.md`.
