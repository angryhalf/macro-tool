"""Application shell: Qt main window and entry point.

``main`` is re-exported lazily (PEP 562) so ``from app import main`` works for
the documented ``python -m app`` invocation, while importing submodules such as
``app.main_window`` stays free of circular-import surprises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checker only
    from app.main import main


def __getattr__(name: str):
    if name == "main":
        from app.main import main as _main

        return _main
    raise AttributeError(f"module 'app' has no attribute {name!r}")
