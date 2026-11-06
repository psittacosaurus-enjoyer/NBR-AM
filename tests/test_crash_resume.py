"""Crash resume screens: game title and the free Ninja Road quicksave resume (offline only)."""
import copy
import json
from pathlib import Path
import unittest

import cv2

from automation import controller as farm
from automation import safety
import test_ninja_road

FIXTURES = farm.ROOT / "tests" / "fixtures"
CATALOG = json.loads((farm.ROOT / "assets/patterns/ninja_road.json").read_text(encoding="utf-8"))


def catalog_state(name):
    return copy.deepcopy(next(state for state in CATALOG["states"] if state["name"] == name))


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = farm.Detector("ninja_road")

    def classify(self, path):
        image = cv2.imread(str(path))
        self.assertIsNotNone(image, path)
        return self.detector.classify(image)

    def test_title_screen_taps_only_the_fixed_text_point(self):
        name, spec, _ = self.classify(FIXTURES / "crash_recovery/title_start.png")
        self.assertEqual(name, "TITLE_START")
        self.assertEqual(spec["action"]["id"], "title_start")
        self.assertEqual(spec["action"]["point"], [450, 1008])

    def test_quicksave_prompt_taps_the_yes_center(self):
        name, spec, _ = self.classify(FIXTURES / "crash_recovery/nr_quicksave_prompt.png")
        self.assertEqual(name, "NR_QUICKSAVE_PROMPT")
        self.assertEqual(spec["action"]["id"], "nr_resume_quicksave")
        self.assertEqual(spec["action"]["point"], [592, 919])

    def test_boot_splash_is_unknown(self):
        self.assertEqual(self.classify(FIXTURES / "crash_recovery/boot_splash_blank.png")[0], "UNKNOWN")

    def test_no_existing_fixture_is_mistaken_for_a_resume_screen(self):
        paths = [p for p in FIXTURES.rglob("*.png")
                 if "item_recognition" not in p.parts and "crash_recovery" not in p.parts]
        self.assertGreater(len(paths), 50)
        for path in paths:
            with self.subTest(path=path.name):
                image = cv2.imread(str(path))
                if image is None or image.shape[:2] != (1600, 900):
                    continue
                self.assertNotIn(self.detector.classify(image)[0], {"TITLE_START", "NR_QUICKSAVE_PROMPT"})

    def test_universal_still_has_no_title_navigation(self):
        names = {state["name"] for state in farm.Detector("universal").states}
        self.assertNotIn("TITLE_START", names)
        self.assertNotIn("NR_QUICKSAVE_PROMPT", names)


class SafetyTests(unittest.TestCase):
    def title(self):
        return {**catalog_state("TITLE_START")}

    def quicksave(self):
        return {**catalog_state("NR_QUICKSAVE_PROMPT")}

    def test_catalog_states_pass(self):
        safety.require_pearl_safe_action("ninja_road", "TITLE_START", self.title())
        safety.require_pearl_safe_action("ninja_road", "NR_QUICKSAVE_PROMPT", self.quicksave())

    def test_moved_title_point_is_refused(self):
        state = self.title()
        state["action"]["point"] = [130, 60]  # Delete Cache
        with self.assertRaises(safety.PearlSafetyError):
            safety.require_pearl_safe_action("ninja_road", "TITLE_START", state)

    def test_title_without_button_anchors_is_refused(self):
        state = self.title()
        state["anchors"] = state["anchors"][2:]
        with self.assertRaises(safety.PearlSafetyError):
            safety.require_pearl_safe_action("ninja_road", "TITLE_START", state)

    def test_quicksave_needs_the_exact_question_text(self):
        state = self.quicksave()
        state["anchors"][0]["template"] = "assets/patterns/boot/other.png"
        with self.assertRaises(safety.PearlSafetyError):
            safety.require_pearl_safe_action("ninja_road", "NR_QUICKSAVE_PROMPT", state)

    def test_quicksave_tap_must_be_the_yes_center(self):
        state = self.quicksave()
        state["action"]["point"] = [300, 919]  # No
        with self.assertRaises(safety.PearlSafetyError):
            safety.require_pearl_safe_action("ninja_road", "NR_QUICKSAVE_PROMPT", state)

    def test_quicksave_is_not_authorized_outside_ninja_road(self):
        with self.assertRaises(safety.PearlSafetyError):
            safety.require_pearl_safe_action("universal", "NR_QUICKSAVE_PROMPT", self.quicksave())

    def test_action_ids_carry_no_premium_markers(self):
        for action_id in ("title_start", "nr_resume_quicksave"):
            safety.require_pearl_free_action_id(action_id)


class EngineResumeTests(unittest.TestCase):
    setUp = test_ninja_road.NinjaRoadTests.setUp

    def show(self, name, **extra):
        spec = catalog_state(name) if name in {"TITLE_START", "NR_QUICKSAVE_PROMPT"} else {"name": name}
        spec.update(extra)
        self.engine.detector.name = name
        self.engine.detector.spec = spec

    def events(self):
        return [call.args[0] for call in farm.event.call_args_list]

    def test_restart_path_title_then_quicksave_then_resumed_combat(self):
        run = {"id": "interrupted", "start": 1, "outcome": None}
        self.engine.data.update(run=copy.deepcopy(run), current_team=1)
        self.show("TITLE_START")
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["title_start"])
        self.assertEqual(self.engine.data["pending"]["source"], "TITLE_START")
        self.show("NR_QUICKSAVE_PROMPT")
        self.engine.tick()  # confirms the title transition
        self.engine.tick()  # resumes from the quicksave
        self.assertEqual(self.engine.device.actions, ["title_start", "nr_resume_quicksave"])
        self.show("NR_COMBAT", auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.assertIsNone(self.engine.data["pending"])
        self.assertEqual(self.engine.data["run"]["id"], "interrupted")
        self.assertIsNone(self.engine.data["run"]["outcome"])
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.device.actions, ["title_start", "nr_resume_quicksave"])

    def test_title_releases_input_latched_before_the_crash_without_replaying_it(self):
        self.engine.data["pending"] = {"id": "nr_team", "source": "NR_TEAM_SELECT", "expected": ["NR_COMBAT"],
                                       "time": 0, "run_id": "before-crash", "on_confirm": {}}
        self.show("TITLE_START")
        self.engine.tick()
        self.assertIn("ninja_pending_ended_at_title", self.events())
        self.assertEqual(self.engine.device.actions, ["title_start"])
        self.assertEqual(self.engine.data["pending"]["id"], "title_start")

    def test_title_tap_waits_while_its_own_tap_is_pending(self):
        self.show("TITLE_START")
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["title_start"])
        self.assertNotIn("ninja_pending_ended_at_title", self.events())

    def test_stop_blocks_the_title_tap(self):
        farm.STOP.write_text("user stop", encoding="utf-8")
        self.show("TITLE_START")
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
