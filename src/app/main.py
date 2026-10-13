"""Application entry point for the macro tool.

This module is the canonical home of ``main()``.  It can be reached in three
ways, all equivalent once the project is installed (``pip install -e .``):

* the ``fishing-macro`` console script declared in ``pyproject.toml``
* ``python -m app`` (via ``app/__init__.py`` re-exporting ``main``)
* ``python src/app/main.py`` (the sys.path bootstrap below makes the bare
  package imports work without an install step)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

# When this file is executed directly (``python src/app/main.py``), Python puts
# ``src/app`` -- not the package root -- on sys.path, so the absolute imports
# below would fail unless the project has been installed.  Inserting ``src/``
# keeps that invocation working as a zero-install convenience; installed
# environments already have the packages on sys.path and skip this entirely.
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    try:
        import app  # noqa: F401  (already importable -> installed or -m run)
    except ImportError:
        sys.path.insert(0, str(_SRC_DIR))

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from persistence.paths import DEFAULT_SETTINGS_PATH, LOG_PATH


def configure_logging(verbose: bool) -> None:
    """Write logs to a rotating file and (optionally) stderr.

    The engine logs every loop while macros run, so an unbounded file would
    grow forever; 5 rotations of 1 MiB cap the damage.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
    ]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PySide6 macro tool with screen-aware stop-trigger/start-trigger triggers.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help="Path to the settings JSON file (default: data/settings.json)",
    )
    parser.add_argument("--verbose", action="store_true", help="Also print logs to stderr")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    configure_logging(args.verbose)

    # Qt parses its own flags out of argv; our argparse options (--settings,
    # --verbose) would make QApplication warn or choke, so hand it only the
    # program name.
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Macro Tool")

    window = MainWindow(settings_path=args.settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
