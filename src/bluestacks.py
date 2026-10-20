"""Compatibility import for :mod:`automation.adb`."""
import sys
from automation import adb as _implementation

sys.modules[__name__] = _implementation
