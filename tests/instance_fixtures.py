"""Synthetic legacy target for offline controller compatibility tests."""
from bluestacks import Instance
import farm


def legacy_target():
    return Instance(str(farm.CONF), "Pie64", "acc1", str(farm.ADB), farm.SERIAL,
                    "offline-fixture-android-id", "running", "connected", "Offline fixture")
