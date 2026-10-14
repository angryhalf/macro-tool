"""Well-known directories and files inside the project checkout.

Historically these were hard-coded CWD-relative strings scattered through the
code (``Path("data") / "settings.json"`` in settings, ``Path("templates")`` in
the condition editor, ``Path("logs")`` in main), which broke whenever the app
was launched from a different working directory.  Anchoring them at the
project root -- derived from this file's location -- makes every entry point
(``fishing-macro``, ``python -m app``, tests) behave identically.

Data layout conventions (see README):

* ``data/general/``  -- app-wide data: the settings document, rotating logs.
* ``data/macros/``   -- per-macro data: captured template images and
  exported/imported single-macro JSON files.
"""

from __future__ import annotations

from pathlib import Path

#: ``src/`` package directory (this file lives in ``src/common/paths.py``).
SRC_DIR = Path(__file__).resolve().parent.parent
#: Repository root containing ``src/``, ``data/``, ``docs/`` and ``tests/``.
PROJECT_ROOT = SRC_DIR.parent

# ------------------------------------------------------------------ general
DATA_DIR = PROJECT_ROOT / "data"
GENERAL_DATA_DIR = DATA_DIR / "general"
LOGS_DIR = GENERAL_DATA_DIR / "logs"

SETTINGS_PATH = GENERAL_DATA_DIR / "settings.json"
LOG_PATH = LOGS_DIR / "macro.log"

# ------------------------------------------------------------------- macros
MACROS_DATA_DIR = DATA_DIR / "macros"
#: Captured template images used by screen conditions.
TEMPLATES_DIR = MACROS_DATA_DIR / "templates"
#: Default folder for macro export/import JSON files.
MACRO_EXPORTS_DIR = MACROS_DATA_DIR / "exports"

#: Directories the app expects to exist at runtime.
_DATA_DIRS = (GENERAL_DATA_DIR, LOGS_DIR, TEMPLATES_DIR, MACRO_EXPORTS_DIR)


def ensure_data_dirs() -> None:
    """Create the data directories if they do not exist yet (idempotent)."""
    for directory in _DATA_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------- agent docs
#: Docs for agents (architecture notes live next to the code they describe).
DOCS_DIR = PROJECT_ROOT / "docs"
