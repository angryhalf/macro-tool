# Data Schemas & Storage

All persistent state is plain JSON / PNG under `data/`.  Paths are defined in
`src/common/paths.py` and anchored at the project root — code must never build
CWD-relative data paths.

## Layout

```
data/
├── general/
│   ├── settings.json      # entire app document (macros + settings)
│   └── logs/macro.log     # rotating log (5 × 1 MiB)
└── macros/
    ├── templates/*.png    # captured template images referenced by conditions
    └── exports/*.json     # single-macro export files (share/import)
```

`ensure_data_dirs()` creates these on startup; `.gitkeep` files keep them in
version control while contents are git-ignored.

## `settings.json` document

Written by `components.macro.store.save_settings()` (key: `schema_version`,
currently 1).  Shape:

```jsonc
{
  "schema_version": 1,
  "stop_hotkey": "f8",                 // global emergency stop
  "execution_delay_ms": 500,           // countdown before a macro starts
                                       // (time to focus the target window)
  "macros": [                          // list[MacroConfig]
    {
      "uid": "hex...",                 // stable identity (survives renames)
      "name": "cast line",             // unique display name
      "enabled": true,                 // disabled ⇒ hotkey/Run ignored
      "hotkey": "ctrl+f6",             // "" = unbound; canonical sorted combo
      "initial_delay_ms": 0,
      "actions": [ /* ActionConfig dicts, see below */ ]
    }
  ]
}
```

`load_settings()` is defensive: malformed JSON or wrong shapes fall back to
defaults instead of raising.

### Action objects

Every action is a flat object with `"kind"` plus kind-relevant fields from
`ActionConfig`; unset fields are omitted when serialised.

| kind | fields used |
|---|---|
| `key` | `key` |
| `hold_key` | `key`, `duration_ms` |
| `combo` | `combo` (e.g. `"ctrl+shift+d"`) |
| `type` | `text`, `amount` (per-char delay ms) |
| `move` | `x`, `y` |
| `click` / `double_click` | `button`, optional `x`,`y` |
| `mouse_down` / `mouse_up` | `button` |
| `drag` | `button`, `x`,`y` (start), `amount`,`duration_ms` (end x,y) |
| `scroll` | `amount` (+ optional `x`,`y`) |
| `wait` | `duration_ms` |
| `wait_for` / `stop_trigger` / `start_trigger` | `condition` (see below) |
| `loop_start` | `amount` (iterations; 0 = forever) |
| `loop_end` | `duration_ms` (delay between loops) |

Key/button names follow pynput conventions normalised through
`components.input.naming` (`"a"`, `"space"`, `"f1"`, `"left"`, ...).

### Condition objects (`ScreenCondition`)

```jsonc
{
  "mode": "image_found",   // image_found|image_missing|region_changed
                          // |color_present|color_absent (screen rules)
  "event": "",             // "" = screen rule; "key:x"/"mouse:b"/"wheel"
                          // = user-input rule; "action:<kind>" = action rule
  "regions": [{"left":0,"top":0,"width":200,"height":100}],  // AND/OR combined
  "region_logic": "any",   // "any" | "all"
  "template_path": "data/macros/templates/indicator.png",
  "threshold": 0.85,       // match confidence for image modes
  "color": "#3fbf4a",      // target colour for color_* modes
  "color_tolerance": 20,   // per-channel HSV tolerance
  "min_fraction": 0.02,    // colour coverage needed to count as present
  "poll_interval_ms": 100  // how often this rule is sampled
}
```

For input/action rules `mode` mirrors the event target (kept only for
backwards-compatible serialisation); they fire when the corresponding event
counter bumps during the run.

## Single-macro export format

`save_macro_json` writes one `MacroConfig` object wrapped with a
`{"kind": "macro", "schema_version": 1, ...}` header (same macro shape as an
entry of `macros[]`).  `import_macro_files` accepts `.json` / `.macro` files
and sanitises imports: fresh uid if colliding, deduplicated name, **forced
`enabled=false` and cleared hotkey** so an import can never steal an existing
binding or auto-run.

## Legacy migrations (applied on load, then saved back)

- Old macro-level `repeat` / `loops` / `interval_ms` options → explicit
  `loop_start`/`loop_end` action pair wrapping the sequence
  (`_needs_loop_migration`, `_migrate_legacy_actions`).
- Old standalone watcher configs → `stop_trigger`/`start_trigger` actions with
  embedded conditions (`_migrate_legacy_conditions`).
- Unknown/extra keys are preserved where possible and dropped silently
  otherwise, so forward edits don't crash on older documents.

## Rules for agents editing schemas

1. Change domain dataclasses in `components/macro/` first; `store.py`'s
   `_to_plain` / `_*_from_dict` handle (de)serialisation generically via
   dataclass fields — new fields work automatically, removed fields need a
   migration.
2. Bump `SETTINGS_SCHEMA_VERSION` and extend the tolerant loaders/migrations
   in `store.py` for any breaking change; never break loading of older files
   (`load_settings` must keep falling back to defaults on malformed input).
3. Add tests to `tests/test_settings.py` (round-trip + migration) and run
   `pytest`.
