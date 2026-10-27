# Development & Testing Guide

How to set up, run, test and extend the project.  For structure see
`repository_map.md`; for design see `architecture.md`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .                   # app + deps (PySide6, opencv, numpy, mss, pynput)
pip install pytest                 # dev-only; not in runtime deps
```

Python ≥ 3.11.  Windows is the primary target; on Linux pynput needs an X
connection (and often root/uinput for synthesis).

## Run

```bash
fishing-macro            # console script installed by pyproject.toml
python -m app            # equivalent, no install needed if src/ on path
```

Logs: `data/general/logs/macro.log` (rotating, 5 × 1 MiB).

## Test

```bash
pytest                   # config in pyproject ([tool.pytest.ini_options])
```

All tests are **headless** — no display, no real input synthesis:

| File | Covers | Seams used |
|---|---|---|
| `tests/test_settings.py` | store round-trip, migrations, export/import sanitisation | tmp_path instead of real data dir |
| `tests/test_screen_conditions.py` | vision helpers, condition parsing/matching logic | synthetic numpy images |
| `tests/test_engine_loops_triggers.py` | loops, wait_for, stop/start triggers, input/action rules, cancellation | monkeypatched `ConditionRuntime.evaluate` + `macro_engine.perform_action` |

Conventions when adding tests:

- Import by bare package names (`from components.macro.macro import ...`);
  `conftest.py` puts `src/` on `sys.path` only as a fallback for uninstalled
  checkouts.
- Never touch the real `data/` directory or global hotkeys in tests; use
  `tmp_path` and the monkeypatch seams above.
- Timing-sensitive waits use the `wait_until(predicate, timeout)` helper
  pattern from the engine tests — poll with small sleeps, never fixed
  `sleep`s that make CI flaky.

## Adding a new action kind (worked example)

1. **Domain**: document fields in `ActionConfig` (`components/macro/macro.py`);
   add to the relevant kind set (`CONDITION_ACTION_KINDS` /
   `LOOP_ACTION_KINDS`) only if it gates or loops.
2. **Persistence**: `store.py` serialises dataclass fields generically — plain
   new kinds need no change; conditions/nested payloads need `_to_plain` /
   `_action_from_dict` handling.
3. **Execution**: branch in `MacroRunner.run` (`macro_flow.py`) for flow
   kinds, or in `synthesis.perform_action` for input kinds. Respect the
   blocked-flag bypass rule for flow steps.
4. **UI**: entry in the editor's kind list + parameter widget
   (`macro_editor.py`), label via `input/naming.py` if user-visible.
5. **Docs**: table row in `docs/macros/macro_model.md`.
6. **Tests**: round-trip in `test_settings.py`, behaviour in
   `test_engine_loops_triggers.py`.

## Adding a screen-condition mode

1. Field(s) on `ScreenCondition` (`condition.py`) + parsing defaults.
2. Matching function in `screen_watch/vision.py` — reuse thread-local capture
   contexts and caches; keep it pure numpy/cv2 so it is unit-testable.
3. Wire into `ConditionRuntime.evaluate` (`condition_runtime.py`).
4. UI option in `condition_editor.py`; docs table in `macro_model.md`;
   synthetic-image test in `test_screen_conditions.py`.

## Code style & guardrails

- `from __future__ import annotations` everywhere; full type hints on public
  functions; module docstrings state purpose *and* dependency position.
- Frozen dataclasses for domain/persistence models; mutable runtime state only
  inside `ExecutionToken` (lock-guarded).
- All file paths through `common.paths` — never CWD-relative literals.
- Threads: daemon workers, cooperative cancellation, event-based parking;
  no `time.sleep` longer than a poll tick without wake-up support.
- Keep the layering table in `architecture.md` honest — imports point
  downward only; domain layer stays Qt/OS-free.
- Performance: anything per-frame (vision, monitor) must avoid allocations in
  steady state; prefer caching keyed by (path, mtime) or pre-parsed forms
  (see hotkey frozensets).

## Release checklist for agents

1. `pytest` green; app launches and a trivial macro runs/stops.
2. Old `settings.json` still loads (migration smoke test).
3. Version bump in `pyproject.toml` + changelog note in `README.md`.
