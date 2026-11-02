"""Explicit cross-stage saved-input recovery stays read-only until verified."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import controller as farm
from instance_fixtures import legacy_target


class FakeDevice:
    def __init__(self, frames=None, foreground=None, on_capture=None):
        self.frames = list(frames or [np.zeros((1600, 900, 3), dtype=np.uint8) for _ in range(2)])
        self.foreground_name = foreground or farm.PACKAGE
        self.on_capture = on_capture
        self.connected = 0
        self.verified = 0
        self.foreground_checks = 0
        self.captures = 0
        self.perform_calls = 0

    def connect(self):
        self.connected += 1

    def verify(self):
        self.verified += 1

    def foreground(self):
        self.foreground_checks += 1
        return self.foreground_name

    def capture(self):
        if self.on_capture:
            self.on_capture(self.captures)
        frame = self.frames[self.captures]
        self.captures += 1
        return frame.copy()

    def perform(self, action):
        self.perform_calls += 1
        raise AssertionError("Saved-input recovery must never dispatch input")


class FakeDetector:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or ["READY_TO_START", "READY_TO_START"])
        self.calls = 0

    def classify(self, frame):
        name = self.outcomes[self.calls]
        self.calls += 1
        spec = {"verified_safe": True} if name == "READY_TO_START" else None
        return name, spec, {}


class SavedInputResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.instance = legacy_target()
        self.patches = [
            patch.object(farm, "RUNTIME", self.runtime),
            patch.object(farm, "STOP", self.runtime / "STOP"),
            patch.object(farm, "process_target", return_value=self.instance),
            patch.object(farm, "controller_active", return_value=False),
            patch.object(farm, "lock_busy", return_value=False),
            patch.object(farm, "event"),
            patch.object(farm.time, "sleep"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        farm.STOP.write_text("Intentional user stop\n", encoding="utf-8")
        self.source_path = farm.load_stage("ninja_road", self.instance)["progress"]

    def base(self):
        return {
            "stage": "ninja_road",
            "instance": self.instance.to_dict(),
            "current_team": 1,
            "blocked": "Input remains safely latched",
        }

    def boss_state(self):
        state = self.base()
        state.update(
            run={"id": "ninja-run", "start": 1, "outcome": None},
            boss_swap={
                "phase": "restore",
                "team_key": ["ninja-run", 1],
                "targets": [0, 1, 2],
                "done": [2, 1],
                "pending": {
                    "purpose": "swipe",
                    "time": 1,
                    "column": 0,
                    "returned": True,
                },
            },
        )
        return state

    def menu_state(self, *, active=False):
        state = self.base()
        state.update(
            pending={
                "id": "nr_available",
                "source": "NR_HOME",
                "expected": ["NR_AVAILABLE"],
                "time": 1,
                "run_id": "menu-input",
            },
            run={"id": "earlier-run", "start": 1,
                 "outcome": None if active else "abandoned"},
        )
        return state

    def run_recovery(self, device=None, detector=None):
        device = device or FakeDevice()
        detector = detector or FakeDetector()
        with patch.object(farm, "Device", return_value=device), \
             patch.object(farm, "Detector", return_value=detector):
            farm.resolve_saved_input("universal", reason="Verified departure to Universal")
        return device, detector

    def test_archives_and_clears_entire_boss_restore_then_abandons_run(self):
        farm.write_json(self.source_path, self.boss_state())
        original = self.source_path.read_bytes()
        device, detector = self.run_recovery()

        saved = farm.read_json(self.source_path)
        self.assertNotIn("boss_swap", saved)
        self.assertEqual(saved["run"]["outcome"], "abandoned")
        self.assertGreater(saved["run"]["ended"], 0)
        self.assertIsNone(saved["blocked"])
        audit = saved["saved_input_resolutions"][-1]
        self.assertEqual(audit["blocker"], "boss_swap")
        self.assertEqual(Path(audit["original_state"]).read_bytes(), original)
        self.assertEqual(audit["original_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(len(audit["observations"]), 2)
        self.assertTrue(all(Path(item["image"]).is_file() for item in audit["observations"]))
        self.assertEqual((device.connected, device.verified, device.foreground_checks,
                          device.captures, device.perform_calls), (1, 2, 2, 2, 0))
        self.assertEqual(detector.calls, 2)

    def test_resolves_one_terminal_menu_navigation_latch(self):
        state = self.menu_state()
        farm.write_json(self.source_path, state)
        self.run_recovery()
        saved = farm.read_json(self.source_path)
        self.assertIsNone(saved["pending"])
        self.assertEqual(saved["run"], state["run"])
        self.assertEqual(saved["saved_input_resolutions"][-1]["blocker"], "pending")
        farm.verify_saved_input(self.instance, "universal")

    def test_refuses_nested_multiple_and_active_menu_latches_without_mutation(self):
        cases = {}
        connection = self.base()
        connection["connection_pending"] = {"id": "nr_start"}
        cases["connection"] = connection
        strategy = self.base()
        strategy["stage15_strategy"] = {"pending": {"purpose": "setting"}}
        cases["strategy"] = strategy
        multiple = self.boss_state()
        multiple["pending"] = self.menu_state()["pending"]
        cases["multiple"] = multiple
        cases["active_menu"] = self.menu_state(active=True)

        for label, state in cases.items():
            with self.subTest(label=label):
                farm.write_json(self.source_path, state)
                before = self.source_path.read_bytes()
                with patch.object(farm, "Device") as device, self.assertRaises(RuntimeError):
                    farm.resolve_saved_input("universal", reason="Should be refused")
                device.assert_not_called()
                self.assertEqual(self.source_path.read_bytes(), before)

    def test_refuses_boss_restore_with_mismatched_run_team_identity(self):
        for label, mutation in (
                ("run", lambda state: state["boss_swap"].update(team_key=["other", 1])),
                ("team", lambda state: state["boss_swap"].update(team_key=["ninja-run", 2]))):
            with self.subTest(label=label):
                state = self.boss_state()
                mutation(state)
                farm.write_json(self.source_path, state)
                before = self.source_path.read_bytes()
                with patch.object(farm, "Device") as device, self.assertRaisesRegex(RuntimeError, "not eligible"):
                    farm.resolve_saved_input("universal", reason="Should be refused")
                device.assert_not_called()
                self.assertEqual(self.source_path.read_bytes(), before)

    def test_requires_two_agreeing_verified_universal_start_observations(self):
        farm.write_json(self.source_path, self.boss_state())
        before = self.source_path.read_bytes()
        device = FakeDevice()
        detector = FakeDetector(["READY_TO_START", "UNKNOWN"])
        with self.assertRaisesRegex(RuntimeError, "Two fresh verified"):
            self.run_recovery(device, detector)
        self.assertEqual(self.source_path.read_bytes(), before)
        self.assertEqual(device.perform_calls, 0)
        self.assertFalse((self.runtime / "recovery").exists())

    def test_detects_source_change_before_commit_and_preserves_newer_state(self):
        farm.write_json(self.source_path, self.boss_state())

        def mutate(index):
            if index == 1:
                changed = farm.read_json(self.source_path)
                changed["external_update"] = True
                farm.write_json(self.source_path, changed)

        device = FakeDevice(on_capture=mutate)
        with self.assertRaisesRegex(RuntimeError, "changed during recovery"):
            self.run_recovery(device)
        saved = farm.read_json(self.source_path)
        self.assertTrue(saved["external_update"])
        self.assertIn("boss_swap", saved)
        self.assertEqual(device.perform_calls, 0)

    def test_requires_stop_idle_exact_target_and_universal_destination(self):
        farm.write_json(self.source_path, self.boss_state())
        before = self.source_path.read_bytes()
        farm.STOP.unlink()
        with self.assertRaisesRegex(RuntimeError, "Stop farming"):
            farm.resolve_saved_input("universal", reason="No Stop")
        farm.STOP.write_text("Intentional user stop\n", encoding="utf-8")
        with patch.object(farm, "controller_active", return_value=True), \
             self.assertRaisesRegex(RuntimeError, "Stop the controller"):
            farm.resolve_saved_input("universal", reason="Still running")
        with self.assertRaisesRegex(RuntimeError, "only supports switching"):
            farm.resolve_saved_input("ninja_road", reason="Reverse direction")
        mismatched = copy.deepcopy(self.boss_state())
        mismatched["instance"]["android_id"] = "another-device"
        farm.write_json(self.source_path, mismatched)
        mismatched_before = self.source_path.read_bytes()
        with patch.object(farm, "Device") as device, \
             self.assertRaisesRegex(RuntimeError, "does not match"):
            farm.resolve_saved_input("universal", reason="Wrong target")
        device.assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), mismatched_before)

    def test_refuses_destination_latch_and_unresolved_recording(self):
        destination = farm.load_stage("universal", self.instance)["progress"]
        farm.write_json(self.source_path, self.menu_state())
        farm.write_json(destination, {"pending": {"id": "start_mission"}})
        before = self.source_path.read_bytes()
        with patch.object(farm, "Device") as device, self.assertRaisesRegex(RuntimeError, "destination"):
            farm.resolve_saved_input("universal", reason="Destination busy")
        device.assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), before)
        destination.unlink()
        unresolved = self.menu_state()
        unresolved["run"]["recording"] = {"state": "operator_attention"}
        farm.write_json(self.source_path, unresolved)
        before = self.source_path.read_bytes()
        with patch.object(farm, "Device") as device, self.assertRaisesRegex(RuntimeError, "recording"):
            farm.resolve_saved_input("universal", reason="Recording busy")
        device.assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), before)

    def test_cli_routes_resolve_saved_input_with_reason(self):
        with patch.object(sys, "argv", ["farm.py", "resolve-saved-input", "--stage", "universal",
                                        "--reason", "Operator verified destination",
                                        "--instance-key", self.instance.key]), \
             patch.object(farm, "resolve_saved_input") as resolve:
            farm.main()
        resolve.assert_called_once_with("universal", reason="Operator verified destination",
                                        expected_instance_key=self.instance.key)

    def test_refuses_a_changed_instance_key_before_device_or_state_changes(self):
        farm.write_json(self.source_path, self.boss_state())
        before = self.source_path.read_bytes()
        with patch.object(farm, "Device") as device, \
             self.assertRaisesRegex(RuntimeError, "selected instance changed"):
            farm.resolve_saved_input("universal", reason="Pinned Manager request",
                                     expected_instance_key="another-instance")
        device.assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), before)

    def test_failed_evidence_save_preserves_the_input_and_run(self):
        farm.write_json(self.source_path, self.boss_state())
        before = self.source_path.read_bytes()
        with patch.object(farm.cv2, "imwrite", return_value=False), \
             self.assertRaisesRegex(OSError, "Could not save recovery observation"):
            self.run_recovery()
        self.assertEqual(self.source_path.read_bytes(), before)

    def test_wrong_foreground_and_device_verification_failure_preserve_input(self):
        farm.write_json(self.source_path, self.boss_state())
        before = self.source_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Restore the game"):
            self.run_recovery(FakeDevice(foreground="another.application"))
        self.assertEqual(self.source_path.read_bytes(), before)
        device = FakeDevice()
        device.verify = Mock(side_effect=RuntimeError("Device identity changed"))
        with self.assertRaisesRegex(RuntimeError, "Device identity changed"):
            self.run_recovery(device)
        self.assertEqual(device.captures, 0)
        self.assertEqual(self.source_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
