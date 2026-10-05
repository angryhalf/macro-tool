# Macro Tool

A general-purpose macro tool with **screen-aware triggers**, built with PySide6.
Record keyboard/mouse macros, then let *screen watchers* react to what happens
on screen — for example when an image appears/disappears (OpenCV template
matching) or when a region of the screen changes — and run a macro in response.

## Features

- One window, all settings (tabs: **Macros**, **Screen watchers**, **Settings**)
- Normal macro playback: key press/hold, mouse move/click/double-click/scroll, waits
- Loop options: fixed loop count, delay between loops, or repeat until stopped
- Global hotkeys: start any macro from anywhere, one emergency stop key
- Screen watchers:
  - *Image appears / disappears* inside a chosen screen region (template matching with adjustable confidence)
  - *Region changes* (motion/pixel-difference detection)
  - Cooldown, poll interval, optional auto-start on launch
  - On trigger: run a macro and/or a small inline action list
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
2. **Screen watchers tab** – *New*, pick a trigger mode:
   - **Image appears / disappears**: capture a template from the screen (or load
     an image file), optionally pick the region to search, set confidence.
   - **Region changes**: pick a rectangle and a sensitivity threshold.
   Then choose what to do when it fires (run a macro and/or extra actions) and
   press *Start watcher*.
3. **Settings tab** – emergency-stop hotkey (default F8) and macro start delay.
4. The red **STOP ALL** button (or the stop hotkey) cancels every running macro
   and stops all watchers.

## Project layout

```
src/
├── main.py                 # entry point (logging, QApplication)
├── app/
│   ├── settings.py         # frozen dataclasses + JSON load/save
│   └── main_window.py      # single QMainWindow wiring tabs ↔ engines
├── core/
│   ├── macro_engine.py     # threaded macro playback, global hotkeys, stop-all
│   └── watcher_engine.py   # polling threads: template match & change detection
├── services/
│   ├── input.py              # pynput keyboard/mouse synthesis + global hotkeys
│   ├── screen_capture.py     # mss/OpenCV capture helpers
│   └── region_picker.py      # fullscreen drag-select overlay
└── ui/
    ├── macros_tab.py       # macro list + run controls
    ├── macro_editor.py     # macro & action editor dialogs
    ├── watchers_tab.py     # watcher list + inline editor
    ├── settings_tab.py     # global options
    └── widgets.py          # HotkeyButton, RegionPreviewWidget, shared bits
templates/                  # captured template images
data/settings.json          # persisted configuration
```
