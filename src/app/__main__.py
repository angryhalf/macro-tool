"""Console-script shim so ``fishing-macro`` maps to ``app.__main__:main``."""

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
