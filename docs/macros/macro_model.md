# Macro Model & Flow-Control Semantics

The authoritative description of what a macro *is* and how every action kind
behaves during playback.  Implementation: `components/macro/macro.py`
(models), `components/playback/macro_flow.py` (execution).

## Core principle: everything is an action

A `MacroConfig` holds a **flat, ordered list of `ActionConfig`s**.  There are
no nested control trees, no separate watcher objects, no macro-level loop
options.  Loops, waits and triggers are all expressed as dedicated action
kinds inside the sequence (the retired `if_else` step is migrated on load into
the equivalent `start_trigger`/`stop_trigger` gating pair — see
`store._migrate_legacy_actions`).  Consequences:

- The model serialises to a uniform JSON array (see `data_schemas.md`).
- Any step can be inserted/reordered in the editor without restructuring.
- Playback is a single index walk with jumps for loops.

## Action kinds

### Input actions (executed via pynput synthesis)

| Kind | Behaviour |
|---|---|
| `key` | press+release one key (`key`) |
| `hold_key` | press, wait `duration_ms`, release |
| `combo` | press modifiers then key of `combo`, release in reverse |
| `type` | type literal `text`, `amount` ms delay per char |
| `move` | warp cursor to absolute (`x`,`y`) |
| `click` / `double_click` | click at optional position, `button` |
| `mouse_down` / `mouse_up` | explicit press/release (split clicks/drags) |
| `drag` | press at (`x`,`y`), move to (`amount`,`duration_ms` as end x/y), release |
| `scroll` | wheel by `amount` (+ optional position) |
| `wait` | sleep `duration_ms` (cancellable) |

Synthesis always releases held keys/buttons even when the run is cancelled —
never leave physical input states dangling.

### Loop pair: `loop_start` / `loop_end`

- `loop_start.amount` = iterations (0 → infinite until stopped).
- `loop_end.duration_ms` = delay between iterations.
- Pairs must be balanced and properly nested; the runner matches them by
  scanning (innermost-first), so nesting beyond one level works.
- Each completed pass increments `MacroStats.loops_completed` (shown in the
  status bar).

### Gating actions: conditions

All three embed a `ScreenCondition` (see below) and interact with the monitor
thread through the token's blocked state.

| Kind | Semantics |
|---|---|
| `wait_for` | Blocks the worker *at this step* until the condition holds, then advances. One-shot gate. |
| `stop_trigger` | Arms a rule that gates **every later action**: while the condition is true, subsequent steps park (`wait_while_blocked`); execution resumes automatically when it turns false. Think "pause while X is on screen". |
| `start_trigger` | Inverse gate: subsequent steps stay held **until** the condition first becomes true, then run freely (rule disarms). Think "don't proceed before X appears". |

A `start_trigger` / `stop_trigger` pair written around a stretch of actions
behaves like an if/else over live conditions — this replaced the retired
`if_else` action kind (legacy documents are migrated on load).

Important engine details:

- Trigger *themselves* bypass the block check (arming/disarming never
  deadlocks behind their own gate).
- A `stop_trigger` re-armed each loop iteration re-evaluates fresh.
- The status bar reports which rule currently blocks a running macro
  ("why did my macro silently stop?").

## Condition sources (`ScreenCondition.event`)

Parsed by `condition.parse_event()`:

| `event` | Source | Fires when |
|---|---|---|
| `""` (default) | **screen** | mode-based match in watched regions |
| `key:<name>` | user input | user presses that key while macro runs |
| `mouse:<button>` | user input | user clicks that button |
| `wheel` | user input | user scrolls |
| `action:<kind>` | executed actions | another action of that kind just ran (counter bumped by the runner) |

Input rules use per-run event counters compared by the monitor — no polling.
The shared pynput listener that feeds them is enabled only while some armed
rule needs it (`MacroEngine.sync_input_watch`).

### Screen modes

| Mode | Holds when |
|---|---|
| `image_found` | template matches ≥ `threshold` in region(s) |
| `image_missing` | template does **not** match anywhere in region(s) |
| `color_present` | target colour (HSV ± `color_tolerance`) covers ≥ `min_fraction` of pixels |
| `color_absent` | coverage below `min_fraction` |
| `region_changed` | pixel difference vs. previous sample exceeds threshold (motion) |

Multiple regions combine with `region_logic`: `"any"` (OR) or `"all"` (AND).
Each rule samples at its own `poll_interval_ms`; `region_changed` compares
against a rolling baseline captured by `condition_runtime`.

## Typical compositions

```
loop_start(0)                 # forever
  key(space)                  # cast
  wait_for image_found bob.png   # bite indicator appeared
  click left                  # reel!
  stop_trigger color_present #ff0000   # pause while "danger" red shows
  key(r)                      # ...skipped while red is on screen
loop_end(delay 2000)
```

```
start_trigger image_found ready.png   # hold everything until READY shows
key(f)                                # fires the moment READY appears
```

## Rules for agents changing flow semantics

1. New gating behaviour should be a new action kind + docstring here, not a
   special case in the runner.
2. Keep `CONDITION_ACTION_KINDS` / `LOOP_ACTION_KINDS` in `macro.py` updated —
   UI, store migrations and the engine all branch on these sets.
3. Anything touching blocking/loops must keep `tests/test_engine_loops_triggers.py`
   green (it drives the engine headlessly with fake conditions).
