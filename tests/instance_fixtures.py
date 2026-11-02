"""Synthetic legacy target for offline controller compatibility tests."""
from automation.adb import Instance
from automation import controller as farm


def legacy_target():
    return Instance(str(farm.CONF), "Pie64", "acc1", str(farm.ADB), farm.SERIAL,
                    "offline-fixture-android-id", "running", "connected", "Offline fixture")
