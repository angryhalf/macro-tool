# Playback Engine Internals

Deep dive into `src/components/playback/` — the heart of the app.  Read
`docs/general/architecture.md` first for the layer overview.

## Objects in play (per macro run)

```
MacroEngine.start_macro(name)
  ├─ ExecutionToken          shared mutable state, worker ⇄ monitor
  ├─ MacroRunner(token)      runs on a new daemon *worker* thread
  └─ MacroStats              loops completed, block reason, start time
Monitor thread (one, lazily started) evaluates armed rules every ~50 ms tick.
```

### `ExecutionToken` (`token.py`)

The only object both threads touch; all fields guarded by `token.lock`:

- `cancel()` / `.cancelled` — cooperative stop; wakes any parked wait.
- **Blocked state**: set/cleared by the monitor when an armed rule gates
  execution; `gated` reports whether any gate is active;
  `wait_while_blocked()` parks the worker (event-based, not sleep-looping)
  and returns immediately on cancel.
- **Event counters**: `bump_event(key)` / `event_count(key)` — the runner
  bumps `action:<kind>` as steps execute; the shared input listener bumps
  `key:<name>` / `mouse:<button>` / `wheel` from physical events. Input/action
  rules are evaluated by *comparing counters*, so they need no polling and
  cannot miss a fast event between samples.
- Loop bookkeeping: stack of `(loop_start index, iterations done)`.
- `refresh_block()` recomputes the aggregate blocked flag from individual
  rule states; `block_reason` string feeds the status bar.

### `MacroRunner` (`macro_flow.py`)

Single index walk over `macro.actions`:

1. Before each normal step: `token.wait_while_blocked()`.
   Flow-control steps (`stop_trigger`, `start_trigger`, loop markers) skip
   this check — otherwise a trigger could deadlock behind its own gate.
2. `loop_start`: push frame (iteration cap from `amount`, 0 = forever).
   `loop_end`: increment stats, sleep inter-loop delay (cancellable), jump
   back to matching `loop_start`, or pop and continue past it.
3. `wait_for`: create a `ConditionRuntime`, sample until holds (or cancel).
4. `stop_trigger` / `start_trigger`: arm/disarm rules on the token; the
   monitor then owns their evaluation.
5. Everything else → `perform_action` (`input/synthesis.py`).

Cancellation checks sit between every step and inside waits, so Stop reacts
within milliseconds even mid-loop.

### `MacroEngine` (`macro_engine.py`)

- Owns `HotkeyManager`; `apply_settings()` re-syncs per-macro hotkeys and the
  global stop key.  Starting an already-running macro stops it first
  (toggle-style behaviour); disabled macros ignore hotkey and Run clicks.
- `_run_macro()` wraps `MacroRunner` with try/finally cleanup and
  `_finish()` bookkeeping; `stop_all()` cancels every token (used by the
  emergency hotkey and window close).
- **Monitor thread** (`_monitor_loop`): one thread for all macros.
  - Idle: parks on `_work_available` event (bounded 1 s wait as a safety net)
    — zero CPU while nothing runs.
  - Busy: for each live token calls `condition_monitor.monitor_token(...)`;
    flips blocked states via `token.refresh_block()`; records block reasons;
    throttled `_notify()` (~0.4 s min) lets the UI refresh; ticks at 50 ms.
- **Input watch demand**: `sync_input_watch()` starts/stops the shared pynput
  listener depending on whether any armed rule needs user-input events;
  engine callbacks `_on_user_key/_on_user_button/_on_user_scroll` bump
  counters and wake the monitor.

### Condition evaluation (`condition_monitor.py`, `condition_runtime.py`)

`ConditionRuntime` = live state for one armed rule during one run:

- Screen rules: capture region(s) through `screen_watch.vision` (thread-local
  `mss`), template-match / HSV-count / diff-vs-baseline, honouring
  `region_logic` any/all.  Each runtime samples only when its
  `poll_interval_ms` has elapsed since last sample — the 50 ms monitor tick
  just provides the opportunity.
- Input/action rules: compare `token.event_count` against the value captured
  when the rule armed (fires on *new* events only).

`condition_monitor.monitor_token()` walks the token's armed rules, applies
gate semantics (stop-trigger blocks while true; start-trigger releases once
true then disarms), and returns whether anything changed.

## Invariants agents must preserve

1. **No blocking in the UI thread.** Engine reads (`stats()`,
   `running_macros()`) take the lock briefly and copy out.
2. **Every park is cancellable.** Any wait in token/runner/monitor must be an
   event wait with timeout or woken by `cancel()`/`_notify()`.
3. **Exactly one monitor thread**, started lazily by the first
   `start_macro`/`start_monitor`; shut down via `engine.shutdown()`.
4. **Held inputs always released**: synthesis cleans up on cancel; don't add
   perform paths that bypass it.
5. Trigger flow steps never consult the blocked flag before executing.
6. Headless testability: keep screen sampling behind
   `ConditionRuntime.evaluate` and action performance behind
   `macro_engine.perform_action` — the tests monkeypatch exactly these seams
   (see `tests/test_engine_loops_triggers.py`).
