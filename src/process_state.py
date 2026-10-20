"""Compatibility import for :mod:`app.core.process_state`."""
import sys
from app.core import process_state as _implementation

sys.modules[__name__] = _implementation
