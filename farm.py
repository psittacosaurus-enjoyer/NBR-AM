"""Convenient root entry point for the existing farming controller."""
from importlib import import_module as _import_module
from pathlib import Path as _Path
import sys as _sys

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "src"))
_controller = _import_module("src.farm")

if __name__ == "__main__":
    try:
        _controller.main()
    except Exception as exc:
        _controller.event("fatal", error=str(exc))
        if _sys.stderr is not None:
            print(str(exc), file=_sys.stderr)
        raise SystemExit(1)
else:
    _sys.modules[__name__] = _controller
