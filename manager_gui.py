"""Convenient root entry point for NBR Automation Manager."""
from pathlib import Path as _Path
import sys as _sys

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "src"))
from app.gui import window as _manager

if __name__ == "__main__":
    raise SystemExit(_manager.main())
else:
    _sys.modules[__name__] = _manager
