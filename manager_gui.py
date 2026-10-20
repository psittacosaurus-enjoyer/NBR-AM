"""Convenient root entry point for NBR Automation Manager."""
from importlib import import_module as _import_module
from pathlib import Path as _Path
import sys as _sys

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "src"))
_manager = _import_module("src.manager_gui")

if __name__ == "__main__":
    raise SystemExit(_manager.main())
else:
    _sys.modules[__name__] = _manager
