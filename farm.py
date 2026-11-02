"""Root command-line entry point for the farming controller."""
from pathlib import Path as _Path
import sys as _sys

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "src"))
from automation import controller as _controller

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
