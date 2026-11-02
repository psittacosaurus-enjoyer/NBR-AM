"""Transition tolerance: expected UNKNOWN/LOADING frames are waits, not faults.

Offline only: saved screenshots and fake devices; no emulator or ADB access.
"""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from automation import controller as farm
from instance_fixtures import legacy_target


def fixture(path):
    image = cv2.imread(str(farm.ROOT / "tests/fixtures" / path))
    assert image is not None, path
    return image


class ScriptedDevice:
    """Returns queued frames (repeating the last) and records every input."""

    def __init__(self, *frames):
        self.frames = list(frames)
        self.actions, self.verifications, self.foregrounds = [], 0, 0
        self.fail_dispatch = 0

    def connect(self): pass
    def launch(self): self.actions.append("LAUNCH")

    def verify(self, timeout=15):
        self.verifications += 1

    def foreground(self, timeout=15):
        self.foregrounds += 1
        return farm.PACKAGE

    def capture(self, timeout=15):
        frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        return frame.copy()

    def perform(self, action):
        if self.fail_dispatch:
            self.fail_dispatch -= 1
            try:
                raise farm.ScreenChanged("Screen changed before dispatch; no input sent")
            except farm.ScreenChanged as exc:
                raise farm.ActionNotDispatched(str(exc)) from exc
        self.actions.append(action["id"])


class TransitionGraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nbr-grace-")
        self.runtime = Path(self.temp.name)
        self.now = 10_000.0
        self.events = []
        self.patches = [
            patch.object(farm, "RUNTIME", self.runtime),
            patch.object(farm, "STOP", self.runtime / "STOP"),
            patch.object(farm, "selected_instance", return_value=legacy_target()),
            patch.object(farm, "validate_mapping"),
            patch.object(farm, "ocr_text", return_value=""),
            patch.object(farm, "process_claim", return_value={}),
            patch.object(farm.time, "time", side_effect=lambda: self.now),
            patch.object(farm.time, "sleep"),
            patch.object(farm, "event", side_effect=lambda kind, **v: self.events.append((kind, v))),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def engine(self, *frames):
        device = ScriptedDevice(*frames)
        with patch.object(farm, "Device", return_value=device):
            engine = farm.Engine("universal")
        engine.mission_verified = True
        return engine, device

    def kinds(self):
        return [kind for kind, _ in self.events]

    def test_unknown_after_sent_action_is_a_quiet_fast_wait(self):
        unknown = fixture("home.png")
        engine, device = self.engine(unknown)
        engine.data["pending"] = {"id": "start_mission", "source": "READY_TO_START",
                                  "expected": ["IN_BATTLE"], "time": self.now, "run_id": "r1"}
        engine.data["screen"] = "READY_TO_START"
        for _ in range(10):
            self.assertEqual(engine.tick(), farm.TRANSITION_POLL_SECONDS)
            self.now += 5
        self.assertNotIn("recognition_failure", self.kinds())
        self.assertNotIn("recovery_detected", self.kinds())
        self.assertNotIn("recovery", engine.data)
        self.assertFalse((self.runtime / "screenshots").exists(), "no diagnostics inside grace")
        self.assertEqual(device.actions, [])
        self.assertIn("transition", engine.data["blocked"])

    def test_unknown_beyond_grace_enters_bounded_recovery_with_evidence(self):
        engine, device = self.engine(fixture("home.png"))
        engine.data["screen"] = "RESULTS_NEXT"
        engine.tick()
        self.now += farm.TRANSITION_GRACE_SECONDS["idle"] + 1
        delay = engine.tick()
        self.assertEqual(delay, farm.RECOVERY_RETRY_SECONDS[0])
        self.assertIn("recognition_failure", self.kinds())
        self.assertEqual(engine.data["recovery"]["kind"], "image_recognition")
        self.assertTrue(list((self.runtime / "screenshots").glob("*_UNKNOWN.png")))
        self.assertEqual(device.actions, [])

    def test_grace_window_depends_on_what_the_game_is_doing(self):
        engine, _ = self.engine(fixture("home.png"))
        self.assertEqual(engine._transition_grace(), farm.TRANSITION_GRACE_SECONDS["idle"])
        engine.data["run"] = {"id": "run", "start": self.now, "outcome": None}
        self.assertEqual(engine._transition_grace(), farm.TRANSITION_GRACE_SECONDS["battle"])
        engine.data["pending"] = {"id": "next"}
        self.assertEqual(engine._transition_grace(), farm.TRANSITION_GRACE_SECONDS["pending"])
        # Ordering keeps windows bounded and short where nothing is expected.
        self.assertLess(farm.TRANSITION_GRACE_SECONDS["idle"], farm.TRANSITION_GRACE_SECONDS["pending"])

    def test_known_screen_resets_the_transition_window(self):
        unknown, ready = fixture("home.png"), fixture("rich_b/ready_to_start.png")
        engine, device = self.engine(unknown, ready, ready, unknown)
        engine.tick()
        self.now += 15
        engine.tick()  # READY_TO_START observed; Start is dispatched
        self.assertEqual(device.actions, ["start_mission"])
        self.assertIsNone(engine._unsettled_since)
        self.now += 15
        self.assertEqual(engine.tick(), farm.TRANSITION_POLL_SECONDS)
        self.assertNotIn("recognition_failure", self.kinds())

    def test_loss_ocr_is_throttled_during_battle_transitions(self):
        engine, _ = self.engine(fixture("home.png"))
        engine.data["run"] = {"id": "run", "start": self.now, "outcome": None}
        with patch.object(farm, "ocr_text", return_value="") as ocr:
            for _ in range(6):
                engine.tick()
                self.now += 1
        self.assertEqual(ocr.call_count, 1)

    def test_loading_overlay_is_recognized_and_never_actionable(self):
        name, spec, _ = farm.Detector("universal").classify(fixture("universal/loading.png"))
        self.assertEqual(name, "LOADING")
        self.assertNotIn("action", spec)
        self.assertFalse(spec["verified_safe"])
        shifted = np.roll(fixture("universal/loading.png"), (12, -15), axis=(0, 1))
        self.assertEqual(farm.Detector("universal").classify(shifted)[0], "LOADING")

    def test_loading_is_a_wait_not_an_action(self):
        loading = fixture("universal/loading.png")
        engine, device = self.engine(loading)
        engine.data["screen"] = "READY_TO_START"
        self.assertEqual(engine.tick(), farm.TRANSITION_POLL_SECONDS)
        self.assertEqual(engine.data["screen"], "LOADING")
        self.assertEqual(engine.data["blocked"], "Game is loading")
        self.assertEqual(device.actions, [])

    def test_loading_fixture_does_not_change_any_other_classification(self):
        detector = farm.Detector("universal")
        for path in sorted((farm.ROOT / "tests/fixtures").rglob("*.png")):
            if path.name == "loading.png":
                continue
            image = cv2.imread(str(path))
            if image is None or image.shape[:2] != (1600, 900):
                continue
            with self.subTest(path=path.name):
                self.assertNotEqual(detector.classify(image)[0], "LOADING")

    def test_binarized_anchors_cannot_authorize_input(self):
        catalog = farm.read_json(farm.CATALOG)
        loading = next(state for state in catalog["states"] if state["name"] == "LOADING")
        loading["action"] = {"id": "next", "kind": "tap", "point": [450, 580], "expected": []}
        loading["verified_safe"] = True
        with patch.object(farm, "read_json", return_value=catalog):
            with self.assertRaises(RuntimeError):
                farm.Detector("universal")

    def test_screen_change_at_dispatch_is_deferred_not_an_error(self):
        ready = fixture("rich_b/ready_to_start.png")
        engine, device = self.engine(ready)
        device.fail_dispatch = 2
        self.assertEqual(engine.tick(), farm.DISPATCH_DEFER_SECONDS)
        self.assertEqual(engine.tick(), farm.DISPATCH_DEFER_SECONDS)
        self.assertIsNone(engine.data["pending"])
        self.assertEqual(engine.tick(), 1)
        self.assertEqual(device.actions, ["start_mission"])
        self.assertEqual(self.kinds().count("action_deferred"), 2)
        self.assertNotIn("action_not_sent", self.kinds())

    def test_persistent_dispatch_instability_still_escalates(self):
        engine, device = self.engine(fixture("rich_b/ready_to_start.png"))
        device.fail_dispatch = farm.DISPATCH_DEFER_LIMIT + 1
        for _ in range(farm.DISPATCH_DEFER_LIMIT):
            engine.tick()
        with self.assertRaises(farm.ActionNotDispatched):
            engine.tick()
        self.assertIsNone(engine.data["pending"])
        self.assertEqual(device.actions, [])

    def test_observation_ticks_reuse_recent_identity_and_foreground_checks(self):
        engine, device = self.engine(fixture("battle.png"))
        engine.data["run"] = {"id": "run", "start": self.now, "outcome": None}
        for _ in range(5):
            engine.tick()
            self.now += 2
        self.assertEqual(device.verifications, 1)
        self.assertLessEqual(device.foregrounds, 2)
        self.now += farm.IDENTITY_RECHECK_SECONDS
        engine.tick()
        self.assertEqual(device.verifications, 2)


class DispatchGuardTests(unittest.TestCase):
    def guard(self, stage, name, spec, observed):
        engine = object.__new__(farm.Engine)
        engine.stage = {"id": stage}
        engine.detector = type("D", (), {"changed": lambda self: False})()
        engine.observe = lambda: (None, observed[0], observed[1], {})
        captured = {}

        class Device:
            def perform(self, action):
                captured["guard"] = self.input_guard
                self.input_guard()

        engine.device = Device()
        engine._perform_verified_action(spec["action"], name, spec)

    def spec(self, point=(450, 1305), variant="a"):
        return {"name": "READY_TO_START", "verified_safe": True, "variant": variant,
                "action": {"id": "start_mission", "kind": "tap", "point": list(point),
                           "expected": ["IN_BATTLE"]}}

    def test_universal_accepts_an_equivalent_layout_variant(self):
        self.guard("universal", "READY_TO_START", self.spec(), ("READY_TO_START", self.spec(variant="b")))

    def test_universal_rejects_a_different_action_or_screen(self):
        for observed in (("READY_TO_START", self.spec(point=(450, 1200))),
                         ("RESULTS_NEXT", self.spec()), ("UNKNOWN", None)):
            with self.subTest(observed=observed[0]), self.assertRaises(farm.ScreenChanged):
                self.guard("universal", "READY_TO_START", self.spec(), observed)

    def test_stage_engines_keep_exact_spec_comparison(self):
        with self.assertRaises(farm.ScreenChanged):
            self.guard("ninja_road", "READY_TO_START", self.spec(), ("READY_TO_START", self.spec(variant="b")))


class RawCaptureTests(unittest.TestCase):
    def raw(self, header_size, fmt=1, width=900, height=1600):
        pixels = np.zeros((height, width, 4), np.uint8)
        pixels[10, 20] = (255, 0, 0, 255)  # red in RGBA
        header = struct.pack("<III", width, height, fmt) + (b"\0" * (header_size - 12))
        return header + pixels.tobytes()

    def test_decodes_both_header_layouts_to_bgr(self):
        for size in (12, 16):
            with self.subTest(header=size):
                frame = farm._decode_raw_screencap(self.raw(size))
                self.assertEqual(frame.shape, (1600, 900, 3))
                self.assertEqual(tuple(frame[10, 20]), (0, 0, 255))

    def test_unfamiliar_raw_output_is_rejected(self):
        self.assertIsNone(farm._decode_raw_screencap(b"\x89PNG\r\n\x1a\n" + b"\0" * 64))
        self.assertIsNone(farm._decode_raw_screencap(self.raw(12, fmt=4)))
        self.assertIsNone(farm._decode_raw_screencap(self.raw(12)[:-7]))

    def test_device_falls_back_to_png_once_for_unfamiliar_raw_output(self):
        image = fixture("battle.png")
        png = cv2.imencode(".png", image)[1].tobytes()
        calls = []

        def command(args, timeout=15):
            calls.append(args)
            return png  # a device whose raw screencap is not the expected layout

        device = object.__new__(farm.Device)
        device.command = command
        with patch.object(farm, "event"):
            self.assertTrue(np.array_equal(device.capture(), image))
            self.assertTrue(np.array_equal(device.capture(), image))
        self.assertEqual(calls, [["exec-out", "screencap"], ["exec-out", "screencap", "-p"],
                                 ["exec-out", "screencap", "-p"]])

    def test_raw_capture_still_enforces_screen_size(self):
        device = object.__new__(farm.Device)
        device.command = lambda args, timeout=15: self.raw(16, width=1600, height=900)
        with self.assertRaisesRegex(RuntimeError, "dimensions changed"):
            device.capture()


if __name__ == "__main__":
    unittest.main()
