# Macro Tool

A general-purpose macro tool with **screen-aware trigger conditions**, built
with PySide6.  Record keyboard/mouse macros, then let *trigger conditions*
react to what happens on screen — for example when an image appears/disappears
(OpenCV template matching) or when a region of the screen changes — and gate
the macro's actions in response.

## Features

- One window, all settings (tabs: **Macros**, **Settings**)
- Per-macro **enable/disable** checkbox in the list (park a misbehaving macro
  without deleting it; disabled macros ignore their hotkey and Run clicks)
- **Export / Import** single macros as JSON files for sharing (imports land
  disabled with the hotkey cleared so they can't steal an existing binding)
- Normal macro playback: key press/hold, mouse move/click/double-click/scroll, waits
- Loop options: fixed loop count, delay between loops, or repeat until stopped
- Global hotkeys: start any macro from anywhere, one emergency stop key
- Screen-aware trigger conditions as macro *actions* (no separate watchers):
  - *Image appears / disappears* inside chosen screen region(s) — pick
    multiple regions to AND/OR together (template matching with adjustable
    confidence)
  - *Colour appears / disappears* — detect a specific colour (hex value,
    colour dialog, or eyedropper sampled from the screen) covering a minimum
    fraction of the watched region(s), with adjustable per-channel tolerance
  - *Region changes* (motion/pixel-difference detection)
  - **Wait until** (`wait_for`) blocks execution until the rule holds on screen
  - **Stop trigger** (`stop_trigger`) holds back every action written after it while the rule is present
  - **Start trigger** (`start_trigger`) releases those actions as soon as the rule appears
  - **If / else** (`if_else`) branches: run one sub-sequence while the rule
    holds, another when it doesn't (one nesting level supported)
  - The screen is watched automatically by a background poller while a macro runs
- Built-in tools: fullscreen region picker, template crop-capture, whole-session
  input recorder (drag gestures are synthesized into single `drag` steps)
- Status bar shows live per-macro stats: loops completed and which trigger
  rule currently blocks a running macro ("why did my macro silently stop?")
- Settings persisted to `data/general/settings.json`; logs written to a
  rotating `data/general/logs/macro.log` (5 × 1 MiB)

## Installation

Requires Python 3.11+ (Windows recommended; on Linux pynput needs an X
connection, and root/uinput access for keyboard/mouse control).

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -e .              # installs the app + all dependencies from pyproject.toml
```

(``pip install -r requirements.txt`` still works if you only want the
dependencies, but the editable install is what enables the commands below.)

## Usage

```bash
fishing-macro                 # console script from the editable install
fishing-macro --verbose       # also log to the console
python -m app                 # equivalent, no console script needed
python src/app/main.py        # zero-install fallback straight from a checkout
```

1. **Macros tab** – *Add macro*, define actions (press keys, clicks, waits…),
   assign a start hotkey, choose loops/repeat. Select a macro and hit
   *Run selected macro* (or its hotkey). A configurable start delay lets you
   focus the target window first.  The checkbox in each row enables/disables
   that macro; *Export…* saves it as a shareable JSON file and *Import…* adds
   one back (disabled, no hotkey — set those yourself).
2. **Trigger conditions are actions** – inside the macro editor, add a
   *Wait until (screen)*, *Stop trigger (screen)*, *Start trigger (screen)*
   or *If / else (screen)* step and configure its screen rule:
   - **Image appears / disappears**: capture a template from the screen (or load
     an image file), optionally pick one or more regions to search, set confidence.
   - **Region changes**: pick a rectangle and a sensitivity threshold.
   A *Stop trigger* step opens a gated stretch whose later actions are held
   back while its rule is on screen; a *Start trigger* step closes it (or waits
   for its own rule before releasing the actions that follow).  An *If / else*
   step runs its "then" sub-sequence while the rule holds and its "else"
   sub-sequence otherwise.
3. **Settings tab** – emergency-stop hotkey (default F8) and macro start delay.
4. The red **STOP ALL** button (or the stop hotkey) cancels every running macro.

## Project layout

```
pyproject.toml                # packaging metadata, deps, `fishing-macro` script
src/                          # import root: every package below is imported
                              # by its bare name (`app`, `components`, `common`)
                              # once the project is installed (or via conftest.py)
├── app/
│   ├── main.py               # entry point (logging, QApplication); also
│   │                         # runnable as `python -m app` / console script
│   └── main_window.py        # single QMainWindow wiring tabs ↔ engines
├── common/
│   ├── paths.py              # project-rooted data/templates/logs paths
│   └── events.py             # canonical key-name helpers (no Qt, no pynput)
└── components/
    ├── macro/                # pure domain models + JSON persistence
    │   ├── macro.py          #   ActionConfig, MacroConfig, AppSettings
    │   ├── condition.py      #   ScreenCondition rules (image/colour/change)
    │   ├── store.py           #   settings load/save, migrations, import/export
    │   └── recording.py       #   recorded events → action list (pure logic)
    ├── playback/            # engine: threads, tokens, flow, condition monitor
    ├── input/                # pynput glue: hotkeys, synthesis, recording, naming
    ├── screen_watch/        # OpenCV/mss vision helpers
    ├── capture/             # region picker & template crop tools
    ├── ui/                  # reusable widgets (HotkeyButton, capture glue)
    ├── macro_editor/         # action / macro / recorder dialogs (a package;
    │                         # re-exports keep `components.macro_editor` stable)
    ├── condition_editor.py  # screen-rule editor used by trigger actions
    ├── macro_list_tab.py     # macro table + run controls
    └── settings_tab.py      # global options
data/general/                 # settings.json, rotating logs/
data/macros/                  # templates/, exports/
docs/                         # agent docs (general/, macros/)
tests/                        # pytest suite (settings, screen conditions,
                              # engine loops/triggers, recording conversion)
```

Import roots are bare package names (`app`, `components`, `common`) — an
editable install (`pip install -e .`) maps them onto `src/` via setuptools
`package-dir`, so there are no `sys.path` hacks at runtime.
