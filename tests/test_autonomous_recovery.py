"""Offline recovery-state checks; no process, emulator, or ADB access."""
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch
import contextlib

import cv2

from automation import controller as farm


class FakeDevice:
    def __init__(self):
        self.actions = []
        self.launches = 0

    def perform(self, action):
        self.actions.append(action["id"])

    def launch(self):
        self.launches += 1


class AutonomousRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nbr-auto-recovery-")
        self.runtime = Path(self.temporary.name)
        self.now = 1000.0
        self.events = []
        self.patches = [
            patch.object(farm, "RUNTIME", self.runtime),
            patch.object(farm, "STOP", self.runtime / "STOP"),
            patch.object(farm.time, "time", side_effect=lambda: self.now),
            patch.object(farm, "process_claim", return_value={}),
            patch.object(farm, "event", side_effect=lambda kind, **values:
                         self.events.append((kind, values))),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def engine(self):
        engine = object.__new__(farm.Engine)
        engine.device = FakeDevice()
        engine.stage = {"id": "universal"}
        engine.progress = self.runtime / "universal.json"
        engine.target = None
        engine.data = {
            "stage": "universal",
            "screen": "UNKNOWN",
            "counts": {"started": 0, "completed": 0, "failed": 0, "launches": 0},
        }
        return engine

    def ready_spec(self):
        image = cv2.imread(str(farm.ROOT / "tests/fixtures/rich_b/ready_to_start.png"))
        self.assertIsNotNone(image)
        name, spec, _ = farm.Detector("universal").classify(image)
        self.assertEqual(name, "READY_TO_START")
        return name, spec

    def test_recognition_uses_bounded_backoff_then_verified_activity_launch(self):
        engine = self.engine()
        delays = []
        for _ in range(4):
            delay = engine._recover_unknown({})
            delays.append(delay)
            self.now += delay
        self.assertEqual(delays, [5, 10, 20, farm.RECOVERY_BROAD_SECONDS])
        self.assertEqual(engine.device.launches, 1)
        self.assertEqual(engine.data["recovery"]["attempts"], 4)
        self.assertIn("recovery_escalated", [kind for kind, _ in self.events])
        self.assertFalse(farm.STOP.exists())

    def test_recognition_relaunch_preserves_an_uncertain_input(self):
        engine = self.engine()
        engine.data["pending"] = {
            "id": "start_mission", "source": "READY_TO_START",
            "expected": ["IN_BATTLE"], "time": self.now,
        }
        original = copy.deepcopy(engine.data["pending"])
        for _ in range(4):
            self.now += engine._recover_unknown({})
        self.assertEqual(engine.device.launches, 1)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data["pending"], original)

    def test_early_recovery_poll_does_not_consume_retries(self):
        engine = self.engine()
        self.assertEqual(engine._recover_unknown({}), 5)
        self.assertEqual(engine._recover_unknown({}), 5)
        self.assertEqual(engine.data["recovery"]["attempts"], 1)

    def test_same_verified_action_retries_three_times_then_escalates(self):
        engine = self.engine()
        name, spec = self.ready_spec()
        engine.data["screen"] = name
        engine.data["pending"] = {
            "id": spec["action"]["id"], "source": name,
            "expected": spec["action"]["expected"],
            "time": 0, "last_attempt_at": 0, "run_id": "original-run",
            "retry_guard": engine._action_guard(spec["action"]),
        }
        delays = []
        for _ in range(4):
            engine.data["pending"]["last_attempt_at"] = 0
            delays.append(engine._retry_pending_transition(name, spec))
            self.now += 100
        self.assertEqual(delays, [1, 1, 1, farm.RECOVERY_BROAD_SECONDS])
        self.assertEqual(engine.device.actions, ["start_mission"] * 3)
        self.assertEqual(engine.device.launches, 1)
        self.assertEqual(engine.data["pending"]["run_id"], "original-run")
        self.assertFalse(farm.STOP.exists())

    def test_mutated_button_geometry_cannot_retry_the_original_action(self):
        engine = self.engine()
        name, spec = self.ready_spec()
        engine.data["pending"] = {
            "id": spec["action"]["id"], "source": name,
            "expected": spec["action"]["expected"], "time": 0,
            "retry_guard": engine._action_guard(spec["action"]),
        }
        changed = copy.deepcopy(spec)
        changed["action"]["point"][0] += 100
        engine._retry_pending_transition(name, changed)
        self.assertEqual(engine.device.actions, [])

    def test_different_screen_never_replays_the_latched_action(self):
        engine = self.engine()
        name, spec = self.ready_spec()
        engine.data["pending"] = {
            "id": spec["action"]["id"], "source": name,
            "expected": spec["action"]["expected"],
            "time": 0, "run_id": "original-run",
        }
        original = dict(engine.data["pending"])
        engine._retry_pending_transition("RESULTS_NEXT", spec)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data["pending"], original)

    def test_known_state_verification_clears_episode_and_logs_resume(self):
        engine = self.engine()
        engine._recover_unknown({})
        engine._verified_recovery_state("READY_TO_START")
        self.assertNotIn("recovery", engine.data)
        kinds = [kind for kind, _ in self.events]
        self.assertIn("recovery_verified", kinds)
        self.assertIn("farming_resumed", kinds)

    def test_observation_only_never_launches_or_consumes_recovery_attempts(self):
        engine = self.engine()
        engine.device.foreground = lambda: "com.uncube.launcher3"
        engine.detector = Mock()
        engine.detector.changed.return_value = False
        for _ in range(5):
            engine.tick(act=False)
        self.assertEqual(engine.device.launches, 0)
        self.assertNotIn("recovery", engine.data)

    def test_native_dispatch_refuses_a_new_dialog_before_input(self):
        engine = self.engine()
        name, spec = self.ready_spec()
        device = object.__new__(farm.Device)
        device.verify = Mock()
        device.foreground = Mock(return_value=farm.PACKAGE)
        device._input = Mock()
        engine.device = device
        engine.detector = Mock()
        engine.detector.changed.return_value = False
        engine.observe = Mock(return_value=(None, "UNKNOWN", None, {}))
        with self.assertRaisesRegex(farm.ActionNotDispatched, "Screen changed"):
            engine._perform_verified_action(spec["action"], name, spec)
        device._input.assert_not_called()
        self.assertIsNone(device.input_guard)

    def test_recovery_cannot_authorize_a_rejected_mission_origin(self):
        engine = self.engine()
        engine.mission_verified = False
        engine._rejected_recovery_origin = {
            "key": "run", "evidence": None, "fields": {"stage": "another-stage"}}
        engine._recover_unknown({})
        engine._verified_recovery_state("IN_BATTLE")
        self.assertIn("recovery", engine.data)
        self.assertNotIn("farming_resumed", [kind for kind, _ in self.events])

    def test_worker_recovers_a_single_adb_exception_without_user_restart(self):
        target = Mock(android_id="fixture-device")
        collector = Mock()
        first = Mock()
        first.data = {}
        first._recovery_context.return_value = {"stage": "universal", "screen": "UNKNOWN"}
        first.tick.side_effect = RuntimeError("temporary ADB interruption")
        first.save.side_effect = OSError("temporary progress sharing conflict")
        resumed = Mock()
        resumed.data = {}

        def finish_tick():
            farm.STOP.write_text("Intentional fixture stop", encoding="utf-8")
            return 0

        resumed.tick.side_effect = finish_tick
        with contextlib.ExitStack() as stack:
            for item in (
                patch.object(farm, "lock", side_effect=lambda *a, **k: contextlib.nullcontext()),
                patch.object(farm, "lock_busy", return_value=False),
                patch.object(farm, "live_controller_pids", return_value=[]),
                patch.object(farm, "controller_exclusions", return_value=[]),
                patch.object(farm, "process_target", return_value=target),
                patch.object(farm, "instance_dict", return_value={"fixture": True}),
                patch.object(farm, "state_path", side_effect=lambda name, *_: self.runtime / name),
                patch.object(farm, "_loot_start", return_value=collector),
                patch.object(farm, "create_engine", side_effect=[first, resumed]),
                patch.object(farm.time, "monotonic", side_effect=range(0, 1000, 10)),
                patch.object(farm.time, "sleep"),
            ):
                stack.enter_context(item)
            farm.worker("universal", instance=target)
        first.tick.assert_called_once()
        resumed.tick.assert_called_once()
        kinds = [kind for kind, _ in self.events]
        self.assertIn("worker_error", kinds)
        self.assertIn("worker_recovery_state_error", kinds)
        self.assertIn("worker_stopped", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
