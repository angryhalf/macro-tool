---
name: ui-components
description: Build Qt UI the repo's way — feature tabs/dialogs in components, generic widgets in components/ui, signals instead of polling, incremental updates, capture tools. Use when editing tabs, editors, dialogs, or adding widgets.
---

# UI Components (PySide6)

Two tiers, strict direction of reuse:

- `src/components/ui/` — **generic widgets** (hotkey button, small helpers).
  Feature-agnostic, safe to import everywhere.
- `src/components/<feature>.py` or feature package — **tabs & dialogs**
  (`macro_list_tab.py`, `settings_tab.py`, `macro_editor/` package,
  `condition_editor.py`). Wired together only by `app/main_window.py`
  (composition root).

## Rules

1. **UI never reaches into another feature's internals.** Tabs talk to the
   engine/store through their public API; cross-tab effects go via signals
   handled in `main_window.py`.
2. **Signals over polling.** Engine notifies UI (macro started/stopped,
   stats changed); status-bar refreshes subscribe to those. A widget must
   not spin a `QTimer` to discover engine state it could have been told about.
3. **Incremental repaints.** Update changed rows/cells only (the macro list
   keeps per-row widgets and patches text/state). Rebuilding whole layouts
   per tick is forbidden — it fights the ~50 ms monitor cadence.
4. **Dialogs own their draft state.** Editors work on a *copy* of the
   macro/action; OK commits back to the store, Cancel discards. Never mutate
   live settings from an open dialog.
5. **Blocking UX around OS hooks:** region picker / eyedropper / capture use
   the transparent-overlay flow in `components/capture/`; restore focus and
   clean up window flags even on exception paths.
6. **Recording inside editors** uses `components/input/recorder.py` with its
   move-throttling; drag gestures arrive pre-synthesised as single `drag`
   steps — don't re-implement gesture detection in UI code.
7. **Hotkey capture widgets** delegate naming/normalisation to
   `common/events.py` + `input/naming.py`; display strings come from
   `describe_hotkey`, never hand-formatted in each widget.
8. **User feedback = message + log.** Failures show `QMessageBox`/status
   text AND `logger.warning/error`. Silent no-ops are bugs.
9. **Headless safety:** importing any UI module must not require a display
   (constructing widgets does). Keep heavy work out of `__init__` so tests
   can import modules; instantiate widgets only in display-backed tests.

## Recipes

**New tab:** create `components/<name>_tab.py` (QWidget subclass, takes the
engine/store in `__init__`), add generic bits to `ui/widgets.py` only if a
second consumer exists, mount it in `main_window.py`, connect engine signals
for status updates, add to README feature list + `repository_map.md`.

**New reusable widget:** put in `ui/widgets.py` if tiny, else
`ui/<widget>.py`; no feature imports inside it; docstring states purpose.

**Editor gains a field:** mirror it in the action/macro draft copy →
commit path → store round-trip (see macro-domain skill) → editor test or at
minimum manual verification steps listed in the PR description.

## Checklist

- [ ] Imports point downward only (features → ui/common; never ui → feature).
- [ ] No QTimer state-polling; engine events drive updates.
- [ ] Dialog edits a copy; cancel leaves store untouched.
- [ ] Capture/picker windows cleaned up on all exit paths.
- [ ] Error paths surface a message AND a log line.
- [ ] App launches (`python -m app`) and the touched screen works manually
      when a display is available; otherwise note it was unverified.

Related: `docs/general/repository_map.md`, `docs/general/architecture.md`.
