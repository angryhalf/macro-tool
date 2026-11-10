---
name: project-structure
description: Place every file in the canonical directory layout; respect layer boundaries when importing. Use when creating, moving, or refactoring any module or data file in this repo.
---

# Project Structure

The repo layout is fixed. New code must land in exactly one of these slots —
never invent top-level directories, never scatter helper files.

```
project/
├── src/
│   ├── app/                  # Application shell only (entry point, main window)
│   ├── common/               # Shared code used across MULTIPLE components
│   └── components/
│       ├── ui/               # Reusable, feature-agnostic Qt widgets
│       ├── <feature>/        # One directory per feature (macro, playback, input, ...)
│       └── <single-file UI feature>.py   # e.g. macro_editor.py (see note below)
├── tests/                    # pytest suite, flat, test_<area>.py
├── data/
│   ├── macros/               # Data for macros (templates/, exports/)
│   └── general/              # App-wide data (settings.json, logs/)
├── docs/                     # Docs for agents (general/, macros/)
├── .gitignore
└── README.md
```

## Rules

1. **Features are directories under `src/components/`.** Existing features:
   `macro/`, `playback/`, `input/`, `screen_watch/`, `capture/`,
   `condition_editor.py`, `macro_editor.py`, `macro_list_tab.py`,
   `settings_tab.py`, `ui/`.
   - A multi-module feature gets its own package dir with an `__init__.py`
     that re-exports the public API (see `components/input/__init__.py`).
   - A single-file Qt tab/dialog may live directly in `components/` until it
     grows past ~one screen of responsibilities — then split it into a
     feature package (`editor/`) and move the pieces. Do not half-migrate.
2. **`components/ui/` is leaf-tier:** generic widgets with no feature logic.
   Feature tabs import *from* `ui/`, never the reverse (beyond the small
   display helpers already living in `widgets.py`).
3. **`common/` = used by ≥2 components.** If you write a helper and only one
   feature uses it, keep it inside that feature. Promote to `common/` only
   when a second consumer appears — and then update all imports in the same
   commit. `common/` must stay dependency-free of PySide6/pynput/cv2/mss.
4. **Layered dependencies** (arrows = "may import"):
   `app → components/* → common`; `playback → macro, screen_watch, common`;
   `macro → stdlib only` (domain layer stays pure — no Qt, no OS libs);
   `input/screen_watch/capture → common` (+ their own OS libs).
   Never import upward (`components → app`) or from `tests` into private
   internals beyond documented seams.
5. **Imports use bare package roots** (`app`, `components`, `common`) —
   provided by the editable install's `package-dir = {"" = "src"}`. No
   `sys.path` hacks, ever.
6. **Data paths come from `src/common/paths.py`** (`SETTINGS_PATH`,
   `TEMPLATES_DIR`, `MACRO_EXPORTS_DIR`, `LOGS_DIR`, ... — all anchored at
   `PROJECT_ROOT`). Never hard-code `Path("data")` relative to CWD.
   App-wide artifacts → `data/general/`; macro artifacts → `data/macros/`.
7. **Tests mirror areas, not modules:** `test_settings.py` (store/schema),
   `test_screen_conditions.py` (vision/conditions),
   `test_engine_loops_triggers.py` (engine flow). New area → new
   `tests/test_<area>.py`.

## Recipe: adding a new feature

1. Decide the slot: new package `src/components/<feature>/__init__.py`
   re-exporting its API, or extend an existing feature package.
2. Put shared-but-feature-specific types in the domain layer
   (`components/macro/`) only if they belong to the macro model; otherwise
   keep them in the feature package.
3. Wire UI into `app/main_window.py` (composition root) — features never
   reach into each other's windows.
4. Add data subdirs via `common/paths.py` constants first, then use them.
5. Update `docs/general/repository_map.md` and `README.md` structure notes
   in the same commit.

## Checklist before committing

- [ ] File sits in exactly one canonical slot (no stray dirs/files at root).
- [ ] No upward or cross-feature imports; `macro/` still imports stdlib only.
- [ ] All data paths go through `common/paths.py`.
- [ ] `repository_map.md` updated if a module was added/moved.
- [ ] `python -m compileall src tests` passes; `pytest` green.

Related: `docs/general/repository_map.md`, `docs/general/architecture.md`.
