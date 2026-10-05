# Macro Tool

A general-purpose macro tool with **screen-aware trigger conditions**, built
with PySide6.  Record keyboard/mouse macros, then let *trigger conditions*
react to what happens on screen — for example when an image appears/disappears
(OpenCV template matching) or when a region of the screen changes — and gate
the macro's actions in response.

## Features

- One window, all settings (tabs: **Macros**, **Settings**)
- Normal macro playback: key press/hold, mouse move/click/double-click/scroll, waits
- Loop options: fixed loop count, delay between loops, or repeat until stopped
- Global hotkeys: start any macro from anywhere, one emergency stop key
- Screen-aware trigger conditions as macro *actions* (no separate watchers):
  - *Image appears / disappears* inside a chosen screen region (template matching with adjustable confidence)
  - *Region changes* (motion/pixel-difference detection)
  - **Wait until** (`wait_for`) blocks execution until the rule holds on screen
  - **Stop trigger** (`stop_trigger`) holds back every action written after it while the rule is present
  - **Start trigger** (`start_trigger`) releases those actions as soon as the rule appears
  - The screen is watched automatically by a background poller while a macro runs
- Built-in tools: fullscreen region picker, template crop-capture, live preview thumbnail
- Settings persisted to `data/settings.json`; logs written to `logs/macro.log`

## Installation

Requires Python 3.10+ (Windows recommended; pynput needs root on Linux for
keyboard/mouse control).

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
```

## Usage

```bash
python -m src.main            # or: python src/main.py
python -m src.main --verbose  # also log to the console
```

1. **Macros tab** – *Add macro*, define actions (press keys, clicks, waits…),
   assign a start hotkey, choose loops/repeat. Select a macro and hit
   *Run selected macro* (or its hotkey). A configurable start delay lets you
   focus the target window first.
2. **Trigger conditions are actions** – inside the macro editor, add a
   *Wait until (screen)*, *Stop trigger (screen)* or *Start trigger (screen)*
   step and configure its screen rule:
   - **Image appears / disappears**: capture a template from the screen (or load
     an image file), optionally pick the region to search, set confidence.
   - **Region changes**: pick a rectangle and a sensitivity threshold.
   A *Stop trigger* step opens a gated stretch whose later actions are held
   back while its rule is on screen; a *Start trigger* step closes it (or waits
   for its own rule before releasing the actions that follow).
3. **Settings tab** – emergency-stop hotkey (default F8) and macro start delay.
4. The red **STOP ALL** button (or the stop hotkey) cancels every running macro.

## Project layout

```
src/
├── main.py                 # entry point (logging, QApplication)
├── app/
│   ├── settings.py         # frozen dataclasses + JSON load/save
│   └── main_window.py      # single QMainWindow wiring tabs ↔ engines
├── core/
│   ├── macro_engine.py     # threaded macro playback, global hotkeys, stop-all
│   └── screen_watch.py     # template match & change detection primitives
├── services/
│   ├── input.py              # pynput keyboard/mouse synthesis + global hotkeys
│   ├── screen_capture.py     # mss/OpenCV capture helpers
│   └── region_picker.py      # fullscreen drag-select overlay
└── ui/
    ├── macros_tab.py       # macro list + run controls
    ├── macro_editor.py     # macro & action editor dialogs
    ├── condition_editor.py # screen-rule editor used by trigger actions
    ├── settings_tab.py     # global options
    └── widgets.py          # HotkeyButton, RegionPreviewWidget, shared bits
templates/                  # captured template images
data/settings.json          # persisted configuration
```
