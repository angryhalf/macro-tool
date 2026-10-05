"""Application entry point for the macro tool."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# The project uses absolute imports (`app.*`, `core.*`, ...).  Insert the
# package directory on ``sys.path`` so both `python src/main.py` and
# `python -m src.main` work as documented entry points.
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from app.main_window import MainWindow  # noqa: E402

LOG_PATH = Path("logs") / "macro.log"


def configure_logging(verbose: bool) -> None:
    """Write logs to file and (optionally) stderr."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
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
        default=Path("data/settings.json"),
        help="Path to the settings JSON file (default: data/settings.json)",
    )
    parser.add_argument("--verbose", action="store_true", help="Also print logs to stderr")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    configure_logging(args.verbose)

    app = QApplication(argv or sys.argv)
    app.setApplicationName("Macro Tool")

    window = MainWindow(settings_path=args.settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
