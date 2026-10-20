"""Compatibility import for :mod:`app.backend.controller`."""
import sys
from app.backend import controller as _implementation

sys.modules[__name__] = _implementation
