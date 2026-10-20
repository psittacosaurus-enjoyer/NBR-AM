"""Compatibility import for :mod:`app.gui.art`."""
import sys
from app.gui import art as _implementation

sys.modules[__name__] = _implementation
