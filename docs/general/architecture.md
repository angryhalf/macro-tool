# Architecture

`fishing-macro` is a PySide6 desktop app that records and replays keyboard/mouse
macros, with **screen-aware flow control**: macros can block, hold or release
actions based on what is happening on screen (or on user input / other actions).

## Layered structure

Dependencies point strictly downward; the domain layer has no Qt/OS imports so
it stays unit-testable.

```
app/                 composition root (Qt shell, logging, entry point)
 ├─ components/ui/           generic Qt widgets (HotkeyButton, capture glue)
 ├─ components/macro_editor/ editor dialogs package (action / macro / recorder)
 ├─ components/playback/     engine: threads, tokens, condition monitor
 ├─ components/input/        pynput glue: hotkeys, synthesis, recording
 ├─ components/screen_watch/ OpenCV/mss vision helpers
 ├─ components/capture/      region picker & template crop tools
 └─ components/macro/        pure domain models + JSON persistence
                              + recording→actions conversion
common/              paths, shared event-name helpers
data/                runtime state (settings.json, logs/, templates/, exports/)
```

### Layers

| Layer | Packages | May import |
|---|---|---|
| Domain | `components.macro` (`macro.py`, `condition.py`, `recording.py`) | stdlib only |
| Persistence | `components.macro.store` | domain, `common.paths` |
| Engine | `components.playback` | domain, input, screen_watch, common |
| IO adapters | `components.input`, `components.screen_watch`, `components.capture` | domain, common |
| UI | `components.ui.*`, `components.macro_editor.*`, top-level tabs, `app` | everything above |

## Key runtime objects

- **`MacroConfig` / `ActionConfig`** (`components/macro/macro.py`): frozen
  dataclasses; a macro is a *flat list of actions*. Loops are explicit
  `loop_start`/`loop_end` action pairs; branching/gating are `wait_for`,
  `stop_trigger`, `start_trigger`, `if_else` actions carrying a
  `ScreenCondition`. This keeps the model uniform and trivially serialisable.
- **`ScreenCondition`** (`components/macro/condition.py`): one reaction rule.
  Source is chosen by the `event` field: empty → screen rule (image/color/
  change modes), `key:x` / `mouse:b` / `wheel` → user-input rule,
  `action:<kind>` → fires when another action kind executes.
- **`ExecutionToken`** (`components/playback/token.py`): per-run mutable state
  shared between the worker thread and the monitor thread — cancellation flag,
  blocked-state flags, per-event counters, loop bookkeeping.
- **`MacroEngine`** (`components/playback/macro_engine.py`): owns hotkey
  bindings, spawns one daemon worker thread per running macro (executed by
  `MacroRunner` from `macro_flow.py`), and runs a **single background monitor
  thread** that evaluates all armed rules.
- **`condition_monitor` / `condition_runtime`**: evaluate rules each poll tick;
  runtime captures regions and does matching, monitor compares against
  baselines and flips the token's blocked state.

## Threading model

1. **UI thread** — Qt event loop; never blocks on engine work. Communicates
   with the engine via signals and short lock-guarded reads of `engine.stats()`.
2. **Hotkey listener** (pynput) — parses global combos; firing launches a
   worker via the engine.
3. **Worker thread per macro** — `MacroRunner` walks the action list, performs
   inputs through `synthesis.py`, and parks in `token.wait_while_blocked()`
   when gated.
4. **Monitor thread** — polls every ~50 ms *only while at least one macro
   runs*; otherwise parks on an event (`_work_available`) instead of
   busy-sleeping. Screen rules are sampled live; input/action rules compare
   event counters bumped by the shared input listener / runner.
5. **Shared input listener** — enabled on demand (`sync_input_watch`) only
   while some armed rule needs user-input events.

Cancellation is cooperative: `token.cancel()` sets a flag and wakes parked
waits; synthesis guarantees held keys/buttons are released on abort.

## Data flow: settings document

Everything persists in one JSON file, `data/general/settings.json`:

```
UI edits → AppSettings/MacroConfig dataclasses
         → components.macro.store.save_settings()
loading  → load_settings() applies migrations for legacy schemas
           (old repeat/interval options → loop_start/loop_end actions,
            old watcher configs → trigger actions)
```

Single-macro export/import uses the same action encoding (`save_macro_json` /
`load_macro_json`); imports land disabled with the hotkey cleared.

Template images captured by the condition editor are stored under
`data/macros/templates/` and referenced by path from conditions.

## Performance notes

- Thread-local `mss` capture contexts and template cache keyed by
  (path, mtime) in `screen_watch/vision.py`.
- Hotkeys pre-parsed into canonical frozensets (modifier variants unified).
- Monitor parks on events; UI status updates throttled (~0.4 s).
- Recorder throttles mouse-move events to keep recordings small.
