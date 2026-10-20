"""Compatibility import for :mod:`app.gui.loot`."""
import sys
from app.gui import loot as _implementation

sys.modules[__name__] = _implementation
