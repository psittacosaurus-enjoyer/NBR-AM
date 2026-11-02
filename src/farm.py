"""Command-line entry point for the controller and its managed worker processes."""
import sys
from automation import controller as _implementation

if __name__ == "__main__":
    try:
        _implementation.main()
    except Exception as exc:
        _implementation.event("fatal", error=str(exc))
        if sys.stderr is not None:
            print(str(exc), file=sys.stderr)
        sys.exit(1)
else:
    sys.modules[__name__] = _implementation
