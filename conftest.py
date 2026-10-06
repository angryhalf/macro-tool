"""Pytest bootstrap: make the ``src``-rooted packages importable everywhere.

The app itself runs with ``src`` on sys.path (see src/main.py); tests mirror
that here so ``import app.settings`` works from any invocation directory and
CI does not need a separate install step.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
