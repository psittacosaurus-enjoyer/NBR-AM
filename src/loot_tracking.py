"""Compatibility import for :mod:`app.backend.loot`."""
import sys
from app.backend import loot as _implementation

sys.modules[__name__] = _implementation
