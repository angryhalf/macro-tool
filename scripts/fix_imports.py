"""One-shot refactor helper: rewrite old flat-package imports to the new layout.

Old layout:  app / automation / engine / models / persistence / qt / ui / vision
New layout:  components/<feature> + common (bare-name packages under src/)

The renames are applied as whole-word substitutions on lines that actually
import from a project package, so ordinary English words in docstrings
("ui", "models", ...) are left alone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Old top-level package -> new dotted module path.
MODULE_MAP = {
    "persistence.paths": "common.paths",
    "persistence.settings_store": "components.macro.store",
    "models.condition": "components.macro.condition",
    "models.macro": "components.macro.macro",
    "automation.hotkeys": "components.input.hotkeys",
    "automation.recorder": "components.input.recorder",
    "automation.synthesis": "components.input.synthesis",
    "automation.naming": "components.input.naming",
    "qt.capture": "components.capture.capture",
    "qt.region_picker": "components.capture.region_picker",
    "vision.vision": "components.screen_watch.vision",
    "engine.condition_monitor": "components.playback.condition_monitor",
    "engine.condition_runtime": "components.playback.condition_runtime",
    "engine.macro_engine": "components.playback.macro_engine",
    "engine.macro_flow": "components.playback.macro_flow",
    "engine.token": "components.playback.token",
    "ui.macros_tab": "components.macro_list_tab",
    "ui.macro_editor": "components.macro_editor",
    "ui.condition_editor": "components.condition_editor",
    "ui.settings_tab": "components.settings_tab",
    "ui.widgets": "components.ui.widgets",
    # Bare package fallbacks (only used when no deeper submodule matched).
    "persistence": "common",
    "models": "components.macro",
    "automation": "components.input",
    "vision": "components.screen_watch",
    "engine": "components.playback",
    "qt": "components.capture",
    "ui": "components.ui",
}

#: Old attribute names renamed during the refactor.
ATTR_MAP = {
    "DEFAULT_SETTINGS_PATH": "SETTINGS_PATH",
    "MacrosTab": "MacroListTab",
    "macros_tab": "macro_list_tab",
}

PROJECT_PKGS = r"(?:app|automation|engine|models|persistence|qt|ui|vision)"
IMPORT_LINE = re.compile(rf"^(\s*)(from|import)\s+({PROJECT_PKGS}\S*)", re.M)


def map_module(old: str) -> str:
    """Map an imported module path (possibly ``pkg.sub`` or ``pkg.sub:x``)."""
    suffix = ""
    if ":" in old:  # e.g. "engine.macro_engine:main" console-script entry
        old, _, suffix = old.partition(":")
        suffix = ":" + suffix
    if old in MODULE_MAP:
        return MODULE_MAP[old] + suffix
    head = old.split(".")[0]
    if head in MODULE_MAP and MODULE_MAP[head].startswith(("common", "components")):
        rest = old[len(head):]  # keeps ".submodule" tail
        return MODULE_MAP[head] + rest + suffix
    return old + suffix


def fix_source(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        indent, keyword, module = match.groups()
        return f"{indent}{keyword} {map_module(module)}"

    text = IMPORT_LINE.sub(repl, text)
    for old, new in ATTR_MAP.items():
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    return text


def main() -> int:
    changed = []
    targets = list(ROOT.glob("conftest.py"))
    for directory in ("src", "tests"):
        targets.extend((ROOT / directory).rglob("*.py"))
    for path in targets:
        original = path.read_text(encoding="utf-8")
        updated = fix_source(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(ROOT)))
    print(f"rewrote {len(changed)} file(s):")
    for name in changed:
        print("  ", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
