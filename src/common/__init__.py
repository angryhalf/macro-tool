"""Shared code used across multiple components (no feature logic here).

* :mod:`common.paths`  -- project-rooted ``data/`` locations for settings,
  logs, templates and macro exports.
* :mod:`common.events` -- canonical key/button naming helpers shared by the
  input component and the UI widgets.
"""

from __future__ import annotations

from common.events import (
    MODIFIER_NAMES,
    describe_hotkey,
    is_modifier_name,
    sort_combo,
)
from common.paths import (
    DATA_DIR,
    DOCS_DIR,
    GENERAL_DATA_DIR,
    LOGS_DIR,
    LOG_PATH,
    MACRO_EXPORTS_DIR,
    PROJECT_ROOT,
    SETTINGS_PATH,
    SRC_DIR,
    TEMPLATES_DIR,
    ensure_data_dirs,
)

__all__ = [
    "DATA_DIR",
    "DOCS_DIR",
    "GENERAL_DATA_DIR",
    "LOGS_DIR",
    "LOG_PATH",
    "MACRO_EXPORTS_DIR",
    "MODIFIER_NAMES",
    "PROJECT_ROOT",
    "SETTINGS_PATH",
    "SRC_DIR",
    "TEMPLATES_DIR",
    "describe_hotkey",
    "ensure_data_dirs",
    "is_modifier_name",
    "sort_combo",
]
