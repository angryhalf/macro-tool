---
name: macro-domain
description: Evolve the macro/condition/settings data model and its JSON persistence safely — action kinds, condition rules, schema migrations, export/import sanitisation. Use when touching components/macro/, settings.json shape, or anything that serialises macros.
---

# Macro Domain & Persistence

The domain layer (`src/components/macro/`) is **pure stdlib** — dataclasses,
string parsing, JSON. No Qt, no pynput, no cv2 imports here (enforced rule;
if you need one, the code is in the wrong layer).

## The model at a glance

- `ActionConfig` — one step of a macro. `kind` selects which fields are
  meaningful. Kind families (see the inline comment on `ActionConfig.kind`):
  - *simple input*: `key`, `hold_key`, `combo`, `type`, `move`, `click`,
    `double_click`, `mouse_down`, `mouse_up`, `drag`, `scroll`, `wait`
  - *flow control*: `loop_start` / `loop_end` (paired markers; count lives
    on the start step, inter-pass delay on the end step),
    `wait_for`, `stop_trigger`, `start_trigger` (`CONDITION_ACTION_KINDS`)
  - Retired shapes (`if_else` kind, legacy `repeat`/`loops`/`interval_ms`
    fields) exist only as migration inputs — never re-add them to the editor.
- `MacroConfig` — frozen dataclass: name, `uid` (stable identity across
  renames), `start_hotkey`, `enabled` flag, ordered `actions` tuple.
  Conditions live *inside* actions (there are no standalone watchers);
  trigger actions carry a `ScreenCondition` in their `condition` field.
- `ScreenCondition` (`condition.py`) — source selected by the `event` field
  via `parse_event()` (`screen` | `input` like `"key:a"` | `action`),
  mode (`image_found` / `image_missing` / `color_present` / `color_absent` /
  `region_changed` for screen rules), plus regions, `template_path`,
  `confidence`, colour/tolerance and `poll_interval_ms`. `parse_event()` is
  the single parser for event strings.
- `store.py` — the whole app state is ONE JSON document
  (`data/general/settings.json`, keys documented in
  `docs/general/data_schemas.md`). Also owns export/import of single macros.

## Rules for changing the model

1. **Additive changes first.** New action kind = new `kind` literal + field
   semantics documented in `macro.py`'s docstring + handling in runner/UI +
   tests. Never repurpose an existing kind's meaning.
2. **Old files must keep loading.** Bump `schema_version` only for breaking
   changes; otherwise write idempotent migration helpers in `store.py`
   (`_migrate_legacy_*` pattern): accept missing fields with defaults,
   rewrite retired shapes into current ones, log dropped data at `warning`.
   Migrations run on load, not lazily.
3. **Round-trip integrity:** every field added to a dataclass must survive
   `to_dict()/from_dict()` (or equivalent) both ways, including through
   export→import. Add a round-trip test for every new field/kind.
4. **Import sanitisation is security:** imported macros land *disabled* with
   hotkey cleared (they must never steal an existing binding). Keep absolute
   paths out of exports; template images referenced by relative filename
   under `data/macros/templates/`.
5. **Unknown kinds on load:** preserve-or-drop decision must be explicit and
   logged (see how `if_else` steps are rewritten into start/stop trigger
   pairs). Silent data loss is a bug.
6. **Domain models are immutable:** `ActionConfig`, `MacroConfig`,
   `AppSettings`, `ScreenCondition` are all `@dataclass(frozen=True)` with
   tuple-valued collections (`actions: tuple[ActionConfig, ...]`). "Editing"
   means constructing a new instance (e.g. `dataclasses.replace`) and
   swapping it into the store — never mutate in place, because running
   workers hold references to the snapshot they started with. Keep any new
   model frozen too; use `field(default_factory=...)` for mutable defaults.
7. **Naming discipline:** key/button names canonical lowercase
   (`common/events.py` helpers); hotkeys normalised modifier-first
   (`ctrl+alt+f6`). Validate at parse time, reject with clear messages.

## Recipes

**Add a new action kind:**
1. `macro.py`: add literal to the right family set, document fields.
2. `store.py`: ensure serialise/deserialise + defaults for old files.
3. `playback/macro_flow.py`: execute/interpret it (respect token waits &
   cancellation — see threading skill).
4. UI: editor row type + `condition_editor` if it carries a condition.
5. Tests: store round-trip + engine behaviour (monkeypatched seams).
6. Docs: `docs/macros/macro_model.md` table + `data_schemas.md` example.

**Change settings.json shape:** decide additive vs breaking → migration in
`store.py` → update `data_schemas.md` with before/after snippets → test that
a v(n-1) fixture loads cleanly.

## Checklist

- [ ] `components/macro/` still imports stdlib only.
- [ ] Old-version JSON fixtures load without manual edits; warnings logged.
- [ ] Round-trip test added/updated for every changed field or kind.
- [ ] Export format unchanged or versioned; imports still sanitised.
- [ ] `macro_model.md` + `data_schemas.md` updated in same commit.
- [ ] `pytest tests/test_settings.py` green.

Related: `docs/macros/macro_model.md`, `docs/general/data_schemas.md`.
