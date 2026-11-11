# fishing-macro — Agent Documentation Index

Docs describing the codebase for AI agents and new contributors.  The README
covers *user-facing* usage; this directory covers *internal* structure and
semantics.

## Files

### `general/` — app-wide knowledge

| Doc | Read when |
|---|---|
| [architecture.md](general/architecture.md) | Understanding layers, threading model, data flow, performance design. **Start here.** |
| [repository_map.md](general/repository_map.md) | Finding which module owns what; adding files in the right place. |
| [data_schemas.md](general/data_schemas.md) | Touching persistence: `settings.json` shape, action/condition JSON, export format, migrations. |
| [development_guide.md](general/development_guide.md) | Setting up, running tests, adding features (worked recipes), style guardrails. |

### `macros/` — macro feature domain

| Doc | Read when |
|---|---|
| [macro_model.md](macros/macro_model.md) | Anything about action kinds and flow-control semantics (loops, wait_for, stop/start triggers, condition sources & screen modes). |
| [engine_internals.md](macros/engine_internals.md) | Working on playback: tokens, runner walk, monitor thread, evaluation seams, invariants to preserve. |

## Quick orientation

- **Structure**: `src/app` shell · `src/components/{macro,playback,input,screen_watch,capture,ui,macro_editor}` features · `src/common` shared · `tests/` headless pytest · `data/general` settings+logs · `data/macros` templates+exports.
- **Golden rule of the domain**: a macro is a flat list of actions; loops and
  screen/input gating are expressed as dedicated action kinds, never as
  separate config trees.
- **Golden rule of concurrency**: worker threads execute, one monitor thread
  evaluates rules against `ExecutionToken`, everything parks on cancellable
  events, UI never blocks.
- **Test seams**: `ConditionRuntime.evaluate` (screen truth) and
  `macro_engine.perform_action` (input synthesis) — monkeypatch these in
  headless tests.

## Maintaining these docs

Update the matching doc whenever you change:

- action kinds / condition modes → `macros/macro_model.md` (+ store schema notes)
- threading or engine behaviour → `macros/engine_internals.md` + `general/architecture.md`
- file layout or new components → `general/repository_map.md`
- settings JSON shape or migrations → `general/data_schemas.md`

Keep statements verifiable against the code — prefer naming the exact module
and symbol each claim refers to.
