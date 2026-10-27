"""Pytest bootstrap for zero-install test runs.

The supported way to run tests is against an editable install
(``pip install -e .``), which puts every package on sys.path automatically.
This file only covers the fallback case where the project has *not* been
installed: it mirrors what the install would do by putting ``src/`` on
sys.path, so ``import components.macro.macro`` works from any invocation
directory.  When the packages are already importable (editable install
active), this is a no-op.
"""

import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"

if importlib.util.find_spec("components") is None:
    sys.path.insert(0, str(SRC))
