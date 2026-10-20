"""Compatibility import for :mod:`app.gui.theme`."""
import sys
from app.gui import theme as _implementation

sys.modules[__name__] = _implementation
