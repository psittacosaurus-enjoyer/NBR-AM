"""Compatibility import for :mod:`app.gui.window`."""
import sys
from app.gui import window as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
