# Repository Map & Module Reference

Where everything lives and what each module is responsible for.  Import roots
are bare package names (`app`, `components`, `common`) — an editable install
(`pip install -e .`) maps them onto `src/` via setuptools `package-dir`, so
there are no `sys.path` hacks at runtime.

## Top level

| Path | Purpose |
|---|---|
| `src/app/` | Application shell (entry point, main window) |
| `src/components/` | Feature components (see below) |
| `src/common/` | Shared code used across components (paths, event helpers) |
| `tests/` | pytest suite (`test_settings`, `test_screen_conditions`, `test_engine_loops_triggers`) |
| `data/general/` | App state: `settings.json`, rotating logs in `logs/` |
| `data/macros/` | Macro data: captured `templates/*.png`, `exports/*.json` |
| `docs/` | Docs for agents (`general/`, `macros/`) |
| `pyproject.toml` | Packaging, deps, console script `fishing-macro`, pytest config |

## `src/app/`

- `main.py` — entry point: arg parsing, logging setup (rotating file handler,
  5 × 1 MiB to `data/general/logs/macro.log`), Qt app bootstrap.
- `__main__.py` — enables `python -m app`.
- `main_window.py` — composition root: creates the `MacroEngine`, hosts the
  Macros / Settings tabs, global stop hotkey, status bar with live per-macro
  stats (loops completed, currently blocking trigger rule).

## `src/components/macro/` — domain + persistence (no Qt/OS imports)

- `macro.py` — `ActionConfig` (all action kinds; field meanings documented
  inline), `MacroConfig`, `AppSettings`, kind sets (`CONDITION_ACTION_KINDS`,
  `LOOP_ACTION_KINDS`), `new_macro_uid()`.
- `condition.py` — `ScreenCondition` dataclass + `parse_event()` (splits
  `"key:a"` → `("input","a")` etc.). Pure data, importable by every layer.
- `store.py` — JSON load/save for the whole settings document, legacy schema
  migrations (`_migrate_legacy_actions`, `_migrate_legacy_conditions`),
  single-macro export/import (`save_macro_json`, `load_macro_json`,
  `import_macro_files`), `rename_in_settings`.

## `src/components/playback/` — engine

- `token.py` — `ExecutionToken` (cancellation, blocked-state, event counters,
  cancellable waits) and `MacroStats`.
- `macro_flow.py` — `MacroRunner`: executes one macro's action list on a
  worker thread; handles loop pairs, gating (`wait_while_blocked`), trigger
  arming/disarming without deadlocks.
- `macro_engine.py` — `MacroEngine`: start/stop/shutdown of macros, hotkey
  sync from settings, the single monitor thread (`_monitor_loop`, ~50 ms tick,
  event-parked when idle), input-watch demand tracking, UI notification.
- `condition_monitor.py` — per-tick evaluation of a token's armed rules;
  records block reasons.
- `condition_runtime.py` — live capture + matching bookkeeping for one
  condition (baselines for `region_changed`, template/color checks via
  `screen_watch.vision`).

## `src/components/input/` — pynput adapters

- `hotkeys.py` — `HotkeyManager`: global combos, pre-parsed canonical
  frozensets, press-cycle re-trigger guard, modifier release safety.
- `synthesis.py` — performs `ActionConfig`s as OS-level keyboard/mouse input;
  guarantees release of held keys/buttons on cancel.
- `recorder.py` — captures user input into actions while recording (throttled
  mouse moves, drag gestures folded into single `drag` steps).
- `naming.py` — pynput ↔ canonical name conversions (`to_pynput_key`,
  `to_kb_key`, button helpers) shared by recorder, hotkey manager and
  synthesis.

## `src/components/screen_watch/vision.py`

Optimised screen analysis: thread-local `mss` contexts, template cache keyed
by (path, mtime), HSV color-range presence, grayscale pixel-difference change
detection, region capture helpers.

## `src/components/capture/`

- `region_picker.py` — fullscreen transparent overlay to select screen
  regions by dragging.
- `capture.py` — crop-and-save the selected region as a template PNG into
  `data/macros/templates/`.

## `src/components/ui/` + top-level widgets

Qt UI.  Feature dialogs currently sit directly under `components/`
(`macro_editor.py`, `condition_editor.py`, `macro_list_tab.py`,
`settings_tab.py`); `ui/widgets.py` holds small shared widgets (key-capture
button, region spinners, etc.).  When adding new UI files prefer
`components/ui/`.

- `macro_list_tab.py` — macro table: enable checkbox, run/stop, edit, delete,
  export/import, live status column.
- `macro_editor.py` — dialog: metadata (name/hotkey), ordered action list with
  add/reorder, record mode, per-kind parameter editors.
- `condition_editor.py` — rule builder: source (screen/input/action), mode,
  region picker, template load/crop, confidence threshold, color eyedropper,
  poll interval.
- `settings_tab.py` — app-wide settings (global stop hotkey, defaults).

## `src/common/`

- `paths.py` — all well-known dirs/files anchored at the project root
  (`SETTINGS_PATH`, `LOG_PATH`, `TEMPLATES_DIR`, `MACRO_EXPORTS_DIR`,
  `ensure_data_dirs()`). Never hard-code CWD-relative data paths.
- `events.py` — combo/event-name helpers shared by the hotkey manager,
  recorder and engine (`is_modifier_name`, `sort_combo`, `describe_hotkey`).
