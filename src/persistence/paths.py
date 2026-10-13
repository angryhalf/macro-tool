"""Paths to the well-known data directories inside the project checkout.

Historically these were hard-coded CWD-relative strings scattered through the
code (``Path("data") / "settings.json"`` in settings, ``Path("templates")`` in
the condition editor, ``Path("logs")`` in main), which broke whenever the app
was launched from a different working directory.  Anchoring them at the
project root -- derived from this file's location -- makes every entry point
(``python src/main.py``, ``python -m src.main``, tests) behave identically.
"""

from __future__ import annotations

from pathlib import Path

#: ``src/`` package directory (this file lives in ``src/persistence/paths.py``).
SRC_DIR = Path(__file__).resolve().parent.parent
#: Repository root containing ``data/``, ``templates/`` and (at runtime) ``logs/``.
PROJECT_ROOT = SRC_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = LOGS_DIR / "macro.log"
