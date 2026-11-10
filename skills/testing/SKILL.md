---
name: testing
description: Write and run the repo's headless pytest suite — monkeypatch seams, tmp_path fixtures, synthetic images, engine flow tests without real input. Use when adding tests or verifying any engine/store/vision change.
---

# Testing (Headless pytest)

Everything under `tests/` must pass **without a display, without real
keyboard/mouse synthesis, and without touching the real `data/` directory**.
Config lives in `pyproject.toml` (`[tool.pytest.ini_options]`,
`testpaths = ["tests"]`); run with plain `pytest`.

## Existing areas & their seams

| File | Covers | Seams / fixtures |
|---|---|---|
| `test_settings.py` | store round-trip, schema migrations, export/import sanitisation | point `store` at `tmp_path` instead of real settings file |
| `test_screen_conditions.py` | vision helpers, condition parsing/matching logic | synthetic numpy arrays as fake frames; no mss grab |
| `test_engine_loops_triggers.py` | loops, `wait_for`, stop/start triggers, input/action rules, cancellation | `monkeypatch.setattr(ConditionRuntime, "evaluate", fake)` (scripted rule outcomes) + `monkeypatch.setattr(macro_engine, "perform_action", fake)` (thread-safe execution log) |

The two documented seams are deliberate: engine tests drive *flow* by
scripting condition outcomes and recording performed actions — they never
touch the screen or OS input. Prefer these seams over inventing new patch
points; if you add a third seam, document it here and in
`docs/general/development_guide.md`.

## Rules

1. **One behaviour per test, named for it:**
   `test_<subject>_<behaviour>` (e.g.
   `test_stop_trigger_blocks_until_condition_clears`). Docstring states the
   scenario in one line.
2. **No sleeps to synchronise threads.** Drive waits with events/counters
   recorded by the seams, or poll token state with a bounded deadline helper.
   Flaky timing tests are worse than missing coverage.
3. **Isolate persistence:** any test that saves loads from `tmp_path`;
   never read/write `data/general/settings.json` in tests, never create
   files under `data/`.
4. **Synthetic pixels beat fixtures:** build small numpy images (solid
   colours, drawn rectangles) for matching tests; keep binary image fixtures
   out of the repo unless a real template edge-case needs one.
5. **Migrations get old-shape fixtures inline** (dict literals representing
   legacy JSON), asserting both the rewritten result AND that loading twice
   is idempotent.
6. **Engine tests assert observable state**, not internals: which actions
   ran and in what order (from the patched `perform_action` log), blocked
   reason strings, loop counts on stats, token cancelled flags.
7. **Cleanup guarantees:** every started engine/thread in a test is stopped
   in a fixture teardown (`try/finally` or fixture yield) even on assertion
   failure — leaked daemon threads poison later tests.
8. **UI code:** import-only smoke checks may run headless; widget
   construction requires a display and stays manual (note it in the PR).
   Don't add Qt-widget unit tests unless a display-backed CI exists.

## Recipes

**Test a new action kind's flow:** extend the scripted-seam pattern from
`test_engine_loops_triggers.py` — build a `MacroConfig` with the new step,
run the engine with patched `perform_action`/`evaluate`, assert the recorded
sequence + final stats.

**Test a store migration:** craft legacy dict → load via tmp settings path →
assert current-shape output, warning logged (`caplog`), re-save/re-load
round-trips unchanged.

**Test a vision helper:** feed known arrays, assert exact/expected match
score ranges; include a negative case (must NOT match).

## Verification protocol after any change

```bash
python -m compileall src tests        # everything byte-compiles
pytest -q                             # full suite green
git diff --stat                       # data/ untouched, no stray files
```

If you touched threading/engine: additionally run the engine tests with
`-p no:cacheprovider` twice to shake ordering-dependent leaks.

## Checklist

- [ ] New test passes headless (`xvfb` not required for your command?).
- [ ] No real IO outside `tmp_path`, no real input synthesis, no sleeps-as-sync.
- [ ] Seam usage limited to documented ones (or seam documented first).
- [ ] Teardown stops every thread/engine started.
- [ ] Full `pytest -q` green before declaring done.

Related: `docs/general/development_guide.md` (test table),
`docs/macros/engine_internals.md` (invariants to assert).
