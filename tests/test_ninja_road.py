"""Offline Ninja Road transitions and input latches; no live device access."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from automation import controller as farm
from automation.ninja_road import NinjaRoadEngine
from fixture_images import load_fixture


class Detector:
    def __init__(self):
        self.name, self.spec = "UNKNOWN", None

    def changed(self):
        return False

    def classify(self, frame):
        return self.name, copy.deepcopy(self.spec), {}


class Device:
    def __init__(self, engine):
        self.engine = engine
        self.actions = []
        self.launches = 0
        self.failure = None
        self.package = farm.PACKAGE

    def foreground(self):
        return self.package

    def capture(self):
        return np.zeros((160, 90, 3), dtype=np.uint8)

    def perform(self, action):
        pending = json.loads(self.engine.progress.read_text())["pending"]
        if pending["id"] != action["id"]:
            raise AssertionError("Input was not durably latched before dispatch")
        if self.failure:
            raise self.failure
        self.actions.append(action["id"])

    def launch(self):
        if farm.STOP.exists():
            raise AssertionError("Recovery must honor Stop before relaunch")
        if self.failure:
            raise self.failure
        self.launches += 1


class NinjaRoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, "RUNTIME", self.runtime),
                        patch.object(farm, "STOP", self.runtime / "STOP"),
                        patch.object(farm, "event"), patch("automation.ninja_road.time.sleep")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.engine = object.__new__(NinjaRoadEngine)
        self.engine.target = None
        self.engine.progress = self.runtime / "ninja_road.json"
        self.engine.stage = {"id": "ninja_road"}
        self.engine.data = {"counts": {"started": 0, "completed": 0, "failed": 0, "resets": 0},
                            "current_team": None, "defeated_teams": [], "available_teams": []}
        self.engine.last_unknown = 0
        self.engine.unknown_hash = None
        self.engine.detector = Detector()
        self.engine.device = Device(self.engine)

    def screen(self, name, action=None, expected=(), **metadata):
        spec = {"name": name, **metadata}
        if action:
            spec["action"] = {"id": action, "kind": "tap", "point": [10, 10], "expected": list(expected)}
        if name == "STAMINA_PROMPT" and action == "dismiss_stamina":
            spec.setdefault("recovery", True)
            spec["anchors"] = [{}, {}, {
                "region": [200, 982, 399, 1054],
                "template": "assets/patterns/rich_b/stamina_prompt/2.png",
                "correlation": .96, "difference": 12,
            }]
            spec["action"].update(point=[299, 1018], point_anchor=2)
        self.engine.detector.name = name
        self.engine.detector.spec = spec

    def test_defeat_switch_and_fresh_attempt_reset(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["started"], 0)
        self.screen("NR_TEAM_SELECT", "nr_team_1", ["NR_COMBAT"], team_index=1, available_teams=[1, 2])
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.screen("NR_COMBAT", auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 1)
        self.screen("NR_TEAM_DEFEATED", "nr_team_2", ["NR_COMBAT"], team_index=2)
        self.engine.tick()
        self.assertEqual(self.engine.data["defeated_teams"], [1])
        self.screen("NR_COMBAT")
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 2)
        self.screen("NR_ALL_DEFEATED", "nr_return", ["NR_START"])
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["failed"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [1, 2])
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.screen("NR_TEAM_SELECT", "nr_team_1", ["NR_COMBAT"], team_index=1, available_teams=[1, 2])
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["resets"], 1)
        self.assertEqual(self.engine.data["counts"]["started"], 2)
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.assertIsNone(self.engine.data["current_team"])
        self.assertEqual(self.engine.data["pending"]["on_confirm"]["team_index"], 1)

    def test_same_screen_or_unexpected_transition_never_replays(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.engine.tick()
        self.engine.data["pending"]["time"] -= 100
        self.engine.tick()
        self.assertTrue(self.engine.data["transition_timed_out"])
        self.screen("NR_RESULTS", "nr_next", ["NR_START"])
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_START")

    def test_restart_directly_into_combat_resets_previous_teams(self):
        self.engine.data.update(current_team=3, defeated_teams=[1, 2, 3], available_teams=[1, 2, 3],
                                run={"id": "previous", "outcome": "failed"})
        self.screen("NR_START", "nr_start", ["NR_COMBAT"], attempt_start=True, team_index=1,
                    available_teams=[1, 2, 3])
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
        self.screen("NR_COMBAT")
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.assertEqual(self.engine.data["available_teams"], [1, 2, 3])
        self.assertEqual(self.engine.data["counts"]["resets"], 1)

    def test_explicit_observed_combat_adoption_does_not_overwrite_active_team(self):
        self.screen("NR_COMBAT", adopt_team=1, available_teams=[1, 2, 3])
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["run"]["origin"], "observed_combat")
        self.engine.data["current_team"] = 2
        self.engine.data["defeated_teams"] = [1]
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 2)
        self.assertEqual(self.engine.data["defeated_teams"], [1])

    def test_transient_combat_after_finish_never_adopts_a_new_attempt(self):
        for outcome in ("failed", "completed"):
            with self.subTest(outcome=outcome):
                self.engine.data.update(current_team=3, defeated_teams=[1, 2, 3],
                    run={"id": "legitimate", "outcome": outcome},
                    pending={"id": "nr_finish", "source": "NR_ALL_DEFEATED", "expected": ["NR_RESULTS"],
                             "time": farm.time.time(), "on_confirm": {}})
                self.screen("NR_COMBAT", adopt_team=1, auto_enabled=True, ninjutsu_enabled=True)
                self.engine.tick()
                self.assertEqual(self.engine.data["run"], {"id": "legitimate", "outcome": outcome})
                self.assertEqual(self.engine.data["current_team"], 3)
                self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
                self.assertNotIn("recovered_missions", self.engine.data["counts"])
                self.assertEqual(self.engine.data["pending"]["id"], "nr_finish")
                self.engine.data["pending"] = None
                self.engine.tick()
                self.assertEqual(self.engine.data["run"]["id"], "legitimate")
                self.assertEqual(self.engine.data["current_team"], 3)

    def test_untracked_combat_with_pending_input_is_not_adopted(self):
        self.engine.data["pending"] = {"id": "nr_finish", "source": "NR_ALL_DEFEATED", "expected": ["NR_RESULTS"],
                                       "time": farm.time.time(), "on_confirm": {}}
        self.screen("NR_COMBAT", adopt_team=1)
        self.engine.tick()
        self.assertNotIn("run", self.engine.data)
        self.assertIsNone(self.engine.data["current_team"])

    def test_team_count_comes_from_visible_labels(self):
        frame = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_start.png")
        self.assertIsNotNone(frame)
        slots = [{"index": index, "anchors": [{"region": list(region),
                  "template": "assets/patterns/ninja_road/team_slots/" + str(index) + ".png",
                  "correlation": .96, "difference": 12}]}
                 for index, region in enumerate(((72, 386, 328, 440), (72, 622, 328, 674),
                                                  (72, 854, 328, 906)), 1)]
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    team_slots=slots, require_team_slots=True)
        self.engine.device.capture = lambda: frame.copy()
        _, name, spec, _ = self.engine.observe()
        self.assertEqual(spec["available_teams"], [1, 2, 3])
        frame[854:906, 72:328] = 0
        _, name, spec, _ = self.engine.observe()
        self.assertEqual(spec["available_teams"], [1, 2])
        frame[622:674, 72:328] = 0
        _, name, spec, _ = self.engine.observe()
        self.assertEqual(spec["available_teams"], [1])
        frame[386:440, 72:328] = 0
        _, name, spec, _ = self.engine.observe()
        self.assertEqual(spec["available_teams"], [])
        self.assertIsNone(self.engine._choose_action(name, spec)[0])

    def detector(self, name, references):
        detector = object.__new__(farm.Detector)
        detector.states = [{"name": name, "anchors": [
            {"region": list(region), "template": reference, "correlation": .96, "difference": 12}
            for region, reference in references]}]
        detector.templates = {reference: farm.cv2.imread(str(farm.ROOT / reference), farm.cv2.IMREAD_GRAYSCALE)
                              for _, reference in references}
        self.assertTrue(all(template is not None for template in detector.templates.values()))
        return detector

    def test_actual_ninjutsu_off_and_enabled_images_are_distinct(self):
        references = [(region, "assets/patterns/ninja_road/nr_combat_ninjutsu_off/" + str(index) + ".png")
                      for index, region in enumerate(((549, 29, 700, 81), (48, 1382, 281, 1438),
                                                       (60, 1397, 92, 1429)))]
        detector = self.detector("NR_COMBAT_NINJUTSU_OFF", references)
        off = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_combat_ninjutsu_off.png")
        on = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_combat.png")
        self.assertEqual(detector.classify(off)[0], "NR_COMBAT_NINJUTSU_OFF")
        self.assertEqual(detector.classify(on)[0], "UNKNOWN")

    def test_actual_enabled_ready_glyph_rejects_zero_hp_disabled_ready(self):
        detector = self.detector("NR_TEAM_SELECT", [
            ((288, 1546, 615, 1578), "assets/patterns/ninja_road/nr_team_select/0.png"),
            ((558, 1190, 625, 1224), "assets/patterns/ninja_road/nr_team_select/enabled_glyph.png")])
        enabled = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_team_select.png")
        defeated = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_team_defeated.png")
        self.assertEqual(detector.classify(enabled)[0], "NR_TEAM_SELECT")
        self.assertEqual(detector.classify(defeated)[0], "UNKNOWN")

    def test_early_exit_requires_request_and_verified_results_before_counting(self):
        self.engine.data["run"] = {"id": "attempt", "outcome": None}
        self.screen("NR_RETREAT_CONFIRM", "nr_retreat_confirm", ["NR_RESULTS"],
                    phase="early_exit", on_confirm={"early_exit_completed": True})
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])
        self.assertEqual(self.engine.data["counts"].get("early_exits", 0), 0)
        self.engine.data["early_exit_requested"] = True
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_retreat_confirm"])
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"].get("early_exits", 0), 0)
        self.screen("NR_RESULTS", phase="results")
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["early_exits"], 1)
        self.assertEqual(self.engine.data["run"]["outcome"], "early_exit")

    def test_catalog_replays_every_genuine_ninja_fixture(self):
        expected = {
            "nr_home": "NR_HOME", "nr_home_unselected": "NR_HOME",
            "nr_home_sakura": "NR_HOME",
            "nr_available": "NR_AVAILABLE", "nr_entry": "NR_ENTRY",
            "nr_start": "NR_START", "nr_combat": "NR_COMBAT",
            # This first observed auto-off frame is still at initial Ready.
            # Its more specific selection guards must win over combat controls.
            "nr_combat_auto_off": "NR_TEAM_SELECT",
            "nr_combat_ninjutsu_off": "NR_COMBAT_NINJUTSU_OFF", "nr_team_select": "NR_TEAM_SELECT",
            "nr_team_defeated": "NR_TEAM_DEFEATED", "nr_team_defeated_2": "NR_TEAM_DEFEATED",
            "nr_all_defeated": "NR_ALL_DEFEATED",
            "nr_results_summary": "NR_RESULTS", "nr_return": "NR_RETURN", "nr_results": "NR_RESULTS",
            "nr_communication_error": "COMMUNICATION_ERROR",
            "nr_retreat_confirm": "NR_RETREAT_CONFIRM",
            "nr_character_info": "NR_CHARACTER_INFO",
        }
        folder = farm.ROOT / "tests/fixtures/ninja_road"
        self.assertEqual({path.stem for path in folder.glob("*.png")}, set(expected))
        detector = farm.Detector("ninja_road")
        for filename, state in expected.items():
            with self.subTest(frame=filename):
                frame = load_fixture(folder / (filename + ".png"))
                name, spec, _ = detector.classify(frame)
                self.assertEqual(name, state)
                if filename == "nr_combat_auto_off":
                    self.assertEqual(spec["team_index"], 1)
                    self.assertEqual(spec["action"]["point"], [448, 1207])
                if filename in {"nr_team_defeated", "nr_team_defeated_2"}:
                    self.assertEqual(spec["phase"], "team_defeated")
                    self.assertEqual(spec["action"]["point"], [878, 1286])

    def test_home_navigation_ignores_animated_icon_but_requires_available_button(self):
        detector = farm.Detector("ninja_road")
        folder = farm.ROOT / "tests/fixtures/ninja_road"
        for filename, expected in (("nr_home", "NR_HOME"), ("nr_home_unselected", "NR_HOME"),
                                   ("nr_available", "NR_AVAILABLE")):
            with self.subTest(frame=filename):
                frame = load_fixture(folder / (filename + ".png"))
                frame[1466:1563, 14:143] = 0  # The icon animation is not a navigation guard.
                name, spec, _ = detector.classify(frame)
                self.assertEqual(name, expected)
                self.assertEqual(spec["action"]["id"],
                                 "nr_available" if name == "NR_HOME" else "nr_select")
        frame = load_fixture(folder / "nr_home_unselected.png")
        frame[1016:1127, 744:874] = 0
        self.assertEqual(detector.classify(frame)[0], "UNKNOWN")

    def test_two_full_fixture_attempts_reset_and_reenter_team_one(self):
        self.engine.detector = farm.Detector("ninja_road")
        folder = farm.ROOT / "tests/fixtures/ninja_road"

        def replay(filename):
            frame = load_fixture(folder / (filename + ".png"))
            self.assertIsNotNone(frame)
            self.engine.device.capture = lambda: frame.copy()
            self.engine.tick()

        for filename in ("nr_home", "nr_available", "nr_entry", "nr_start"):
            replay(filename)
        self.assertEqual(self.engine.data["available_teams"], [1, 2, 3])
        self.assertTrue(self.engine.data["pending"]["on_confirm"]["begin_attempt"])
        self.assertEqual(self.engine.data["counts"]["started"], 0)
        replay("nr_combat_auto_off")
        replay("nr_combat")
        self.assertEqual(self.engine.data["current_team"], 1)
        for attempt in range(2):
            for next_team in (2, 3):
                replay("nr_team_defeated" if next_team == 2 else "nr_team_defeated_2")
                self.assertEqual(self.engine.data["current_team"], next_team - 1)
                self.assertEqual(self.engine.data["pending"]["on_confirm"]["team_index"], next_team)
                replay("nr_team_select")
                self.assertEqual(self.engine.data["current_team"], next_team)
                replay("nr_combat")
            replay("nr_all_defeated")
            ended_id = self.engine.data["run"]["id"]
            self.assertEqual(self.engine.data["run"]["outcome"], "failed")
            self.assertEqual(self.engine.data["counts"]["failed"], attempt + 1)
            self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
            replay("nr_combat")  # Actual brief battle frame between defeat OK and results.
            self.assertEqual(self.engine.data["run"]["id"], ended_id)
            self.assertEqual(self.engine.data["pending"]["id"], "nr_finish")
            for filename in ("nr_results_summary", "nr_return", "nr_results", "nr_entry", "nr_start"):
                replay(filename)
            self.assertTrue(self.engine.data["pending"]["on_confirm"]["begin_attempt"])
            self.assertEqual(self.engine.data["run"]["id"], ended_id)
            self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
            replay("nr_combat_auto_off")
            self.assertEqual(self.engine.data["defeated_teams"], [])
            self.assertEqual(self.engine.data["available_teams"], [1, 2, 3])
            replay("nr_combat")
            self.assertEqual(self.engine.data["current_team"], 1)
            self.assertEqual(self.engine.data["counts"]["resets"], attempt + 1)
            self.assertEqual(self.engine.data["counts"]["started"], attempt + 2)
            self.assertIsNone(self.engine.data["pending"])
            self.assertTrue(self.engine.data["auto_enabled"])
            self.assertTrue(self.engine.data["ninjutsu_enabled"])

    def test_stamina_cancel_wait_and_expiry_do_not_start_or_reset_rejected_attempt(self):
        self.engine.data.update(current_team=3, defeated_teams=[1, 2, 3], available_teams=[1, 2, 3],
                                run={"id": "previous", "outcome": "failed"})
        with patch.object(farm.time, "time", return_value=1000) as now, patch.object(farm, "stamina_wait_minutes", return_value=5):
            self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
            self.engine.tick()
            self.screen("STAMINA_PROMPT", "dismiss_stamina", ["NR_START"], phase="stamina", verified_safe=True)
            self.engine.tick()
            self.assertEqual(self.engine.data["counts"]["started"], 0)
            self.assertEqual(self.engine.data["counts"]["resets"], 0)
            self.assertEqual(self.engine.data["current_team"], 3)
            self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
            self.assertEqual(self.engine.data["run"]["id"], "previous")
            self.assertEqual(self.engine.data["stamina_until"], 1300)
            self.assertEqual(self.engine.data["pending"]["id"], "dismiss_stamina")
            now.return_value = 1100
            self.engine.tick()
            self.assertEqual(self.engine.data["stamina_until"], 1300)
            self.assertEqual(self.engine.device.actions, ["nr_start", "dismiss_stamina"])
            self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
            self.assertEqual(self.engine.tick(), 30)
            self.assertIsNone(self.engine.data["pending"])
            self.assertEqual(self.engine.device.actions, ["nr_start", "dismiss_stamina"])
            self.assertEqual(self.engine.data["current_team"], 3)
            now.return_value = 1301
            self.engine.tick()
            self.assertEqual(self.engine.device.actions, ["nr_start", "dismiss_stamina", "nr_start"])
            self.assertTrue(self.engine.data["pending"]["on_confirm"]["begin_attempt"])
            self.assertEqual(self.engine.data["counts"]["resets"], 0)
            self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], team_index=1)
            self.engine.tick()
            self.assertEqual(self.engine.data["counts"]["started"], 1)
            self.assertEqual(self.engine.data["counts"]["resets"], 1)
            self.assertEqual(self.engine.data["defeated_teams"], [])

    def test_stamina_request_uses_existing_manager_identity_checks(self):
        self.engine.data.update(stamina_until=farm.time.time() + 300, stamina_wait_episode=True)
        self.engine.save()
        request = {"worker_pid": os.getpid(), "stage": "ninja_road", "id": "skip",
                   "stamina_until": self.engine.data["stamina_until"]}
        farm.write_json(self.engine.path("stamina_request.json"), request)
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["stamina_until"], 0)
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertFalse(self.engine.path("stamina_request.json").exists())

    def test_stamina_rejection_requires_exact_free_cancel_schema(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.screen("STAMINA_PROMPT", "dismiss_stamina", ["NR_START"], phase="unverified", verified_safe=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")
        self.assertNotIn("stamina_wait_episode", self.engine.data)
        self.assertEqual(self.engine.device.actions, ["nr_start"])

    def connection_screen(self, **metadata):
        self.screen("COMMUNICATION_ERROR", "retry_connection",
                    ["NR_START", "NR_TEAM_SELECT", "NR_COMBAT", "NR_COMBAT_AUTO_OFF",
                     "NR_COMBAT_NINJUTSU_OFF", "NR_ENTRY", "NR_HOME", "NR_AVAILABLE"],
                    verified_safe=True, recovery=True, **metadata)

    def suspended_start(self):
        self.engine.data.update(current_team=3, defeated_teams=[1, 2, 3], available_teams=[1, 2, 3],
                                run={"id": "previous", "outcome": "failed"})
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        original = copy.deepcopy(self.engine.data["pending"])
        self.connection_screen()
        self.engine.tick()
        return original

    def test_connection_ack_preserves_original_start_and_menu_rejects_it(self):
        original = self.suspended_start()
        self.assertEqual(self.engine.data["connection_pending"], original)
        self.assertEqual(self.engine.data["pending"]["id"], "retry_connection")
        self.engine.tick()
        self.engine.data["pending"]["time"] -= 100
        self.engine.tick()
        self.assertEqual(self.engine.device.actions.count("retry_connection"), 1)
        self.assertEqual(self.engine.data["connection_pending"], original)
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        self.assertNotIn("connection_pending", self.engine.data)
        self.assertEqual(self.engine.data["run"]["id"], "previous")
        self.assertEqual(self.engine.data["current_team"], 3)
        self.assertEqual(self.engine.data["defeated_teams"], [1, 2, 3])
        self.assertEqual(self.engine.data["counts"]["started"], 0)
        self.assertEqual(self.engine.data["counts"]["resets"], 0)
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")
        self.assertNotEqual(self.engine.data["pending"]["run_id"], original["run_id"])

    def test_connection_ack_initial_ready_accepts_original_start_once(self):
        original = self.suspended_start()
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], team_index=1)
        self.engine.tick()
        self.assertNotIn("connection_pending", self.engine.data)
        self.assertEqual(self.engine.data["run"]["id"], original["run_id"])
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.assertEqual(self.engine.data["counts"]["resets"], 1)
        self.assertEqual(self.engine.data["available_teams"], [1, 2, 3])
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.screen("NR_COMBAT", auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.assertEqual(self.engine.data["connection_recoveries"], 0)

    def test_connection_ack_combat_accepts_original_start_as_team_one(self):
        original = self.suspended_start()
        self.screen("NR_COMBAT", adopt_team=1, auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["run"]["id"], original["run_id"])
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.assertNotIn("recovered_missions", self.engine.data["counts"])
        self.assertIsNone(self.engine.data["pending"])

    def test_connection_cannot_suspend_pending_team_selection(self):
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], team_index=1)
        self.engine.tick()
        original = copy.deepcopy(self.engine.data["pending"])
        self.connection_screen()
        self.engine.tick()
        self.assertEqual(self.engine.data["pending"], original)
        self.assertNotIn("connection_pending", self.engine.data)
        self.assertEqual(self.engine.device.actions, ["nr_team"])

    def test_connection_recovery_escalates_after_three_unaccepted_starts(self):
        self.suspended_start()
        for count in range(1, 4):
            self.assertEqual(self.engine.data["connection_recoveries"], count)
            self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
            self.engine.tick()
            self.connection_screen()
            self.engine.tick()
        self.assertEqual(self.engine.device.actions.count("retry_connection"), 3)
        self.assertTrue(self.engine.data["connection_recovery_limit"])
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")
        self.assertNotIn("connection_pending", self.engine.data)
        self.assertEqual(self.engine.data["counts"]["started"], 0)
        self.assertEqual(self.engine.data["counts"]["resets"], 0)
        self.engine.data["connection_escalation"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.tick()
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.data["connection_recoveries"], 0)
        self.assertNotIn("connection_recovery_limit", self.engine.data)
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")

    def test_unknown_screen_uses_backoff_then_verified_game_relaunch(self):
        clock = [1000.0]
        self.screen("UNKNOWN")
        with patch("automation.ninja_road.time.time", side_effect=lambda: clock[0]):
            # Inside the transition grace window: quick quiet re-observation only.
            for _ in range(3):
                self.assertEqual(self.engine.tick(), self.engine.UNKNOWN_POLL_SECONDS)
                clock[0] += self.engine.UNKNOWN_POLL_SECONDS
            self.assertNotIn("recognition_recovery", self.engine.data)
            self.assertNotIn("ninja_recognition_failed", [call.args[0] for call in farm.event.call_args_list])
            clock[0] += farm.TRANSITION_GRACE_SECONDS["idle"]
            for delay in (5, 10, 20, 40):
                self.assertEqual(self.engine.tick(), delay)
                self.assertEqual(self.engine.device.launches, 0)
                clock[0] += delay
            self.assertEqual(self.engine.tick(), 5)
            self.assertEqual(self.engine.device.launches, 1)
            self.assertEqual(self.engine.device.actions, [])
            self.assertEqual(self.engine.data["recognition_recovery"]["relaunches"], 1)
            self.screen("NR_HOME", verified_safe=True)
            self.engine.tick(act=False)
        self.assertNotIn("recognition_recovery", self.engine.data)
        events = [call.args[0] for call in farm.event.call_args_list]
        self.assertIn("ninja_recognition_failed", events)
        self.assertIn("ninja_recognition_relaunch_sent", events)
        self.assertIn("ninja_recognition_recovery_verified", events)

    def test_repeated_two_frame_disagreement_uses_same_recovery_backoff(self):
        clock = [2000.0]
        frame = np.zeros((160, 90, 3), dtype=np.uint8)
        known = {"name": "NR_START", "verified_safe": True}
        with patch("automation.ninja_road.time.time", side_effect=lambda: clock[0]):
            observations = [(frame, "NR_START", known, {}), (frame, "UNKNOWN", None, {})]
            with patch.object(self.engine, "observe", side_effect=observations):
                self.assertEqual(self.engine.tick(), self.engine.UNKNOWN_POLL_SECONDS)
            clock[0] += farm.TRANSITION_GRACE_SECONDS["idle"]
            for delay in (5, 10, 20, 40):
                observations = [(frame, "NR_START", known, {}),
                                (frame, "UNKNOWN", None, {})]
                with patch.object(self.engine, "observe", side_effect=observations):
                    self.assertEqual(self.engine.tick(), delay)
                clock[0] += delay
            observations = [(frame, "NR_START", known, {}),
                            (frame, "UNKNOWN", None, {})]
            with patch.object(self.engine, "observe", side_effect=observations):
                self.assertEqual(self.engine.tick(), 5)
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.device.actions, [])

    def test_foreground_loss_backs_off_then_relaunches_pinned_game_only(self):
        self.engine.device.package = "com.uncube.launcher3"
        self.assertEqual(self.engine.tick(), 5)
        self.engine.data["foreground_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.assertEqual(self.engine.tick(), 10)
        self.engine.data["foreground_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.assertEqual(self.engine.tick(), 5)
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.device.actions, [])
        self.engine.device.package = farm.PACKAGE
        self.engine.tick(act=False)
        self.assertNotIn("foreground_recovery", self.engine.data)
        events = [call.args[0] for call in farm.event.call_args_list]
        self.assertIn("ninja_foreground_relaunch_sent", events)
        self.assertIn("ninja_foreground_restored", events)

    def test_transition_timeout_retries_exact_safe_action_then_relaunches(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True)
        self.engine.tick()
        original_time = self.engine.data["pending"]["time"]
        for retry in range(1, self.engine.TRANSITION_RETRY_LIMIT + 1):
            self.engine.data["pending"]["last_attempt_at"] = farm.time.time() - self.engine.TRANSITION_TIMEOUT - 1
            self.engine.tick()
            self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
            self.engine.tick()
            self.assertEqual(self.engine.device.actions.count("nr_start"), retry + 1)
            self.assertEqual(self.engine.data["pending"]["time"], original_time)
        self.engine.data["pending"]["last_attempt_at"] = farm.time.time() - self.engine.TRANSITION_TIMEOUT - 1
        self.engine.tick()
        self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.tick()
        self.assertEqual(self.engine.device.actions.count("nr_start"),
                         self.engine.TRANSITION_RETRY_LIMIT + 1)
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")
        self.assertNotIn("transition_timed_out", self.engine.data)

    def test_transition_retry_geometry_change_uses_relaunch_without_tapping(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True)
        self.engine.tick()
        original = copy.deepcopy(self.engine.data["pending"])
        self.engine.detector.spec["action"]["point"] = [20, 20]
        self.engine.data["pending"]["last_attempt_at"] = farm.time.time() - self.engine.TRANSITION_TIMEOUT - 1
        self.engine.tick()
        self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.data["pending"]["retry_guard"], original["retry_guard"])
        self.assertEqual(self.engine.data["pending"]["time"], original["time"])

    def test_retry_exception_revokes_returned_evidence_for_map_advance(self):
        self.engine.data.update(current_team=1, available_teams=[1],
                                run={"id": "active", "outcome": None})
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"],
                    ready_map_index=3, verified_safe=True)
        self.engine.tick()
        self.assertTrue(self.engine.data["pending"]["returned"])
        self.engine.data["pending"]["last_attempt_at"] = farm.time.time() - self.engine.TRANSITION_TIMEOUT - 1
        self.engine.tick()
        self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.device.failure = RuntimeError("uncertain retry")
        with self.assertRaisesRegex(RuntimeError, "uncertain retry"):
            self.engine.tick()
        self.assertNotIn("returned", self.engine.data["pending"])
        self.assertNotIn("returned", farm.read_json(self.engine.progress)["pending"])

    def test_observe_only_never_runs_foreground_unknown_or_transition_recovery(self):
        self.engine.device.package = "com.uncube.launcher3"
        for _ in range(6):
            self.engine.tick(act=False)
        self.engine.device.package = farm.PACKAGE
        self.screen("UNKNOWN")
        for _ in range(6):
            self.engine.tick(act=False)
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True)
        self.engine.tick()
        self.engine.data["pending"]["last_attempt_at"] = farm.time.time() - self.engine.TRANSITION_TIMEOUT - 1
        self.engine.tick(act=False)
        self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.tick(act=False)
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertEqual(self.engine.device.launches, 0)
        self.assertNotIn("recognition_recovery", self.engine.data)
        self.assertNotIn("foreground_recovery", self.engine.data)

    def test_missing_team_slots_back_off_then_relaunch_without_starting(self):
        clock = [1000.0]
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True,
                    require_team_slots=True, available_teams=[])
        with patch("automation.ninja_road.time.time", side_effect=lambda: clock[0]):
            for delay in (5, 10, 20, 40):
                self.assertEqual(self.engine.tick(), delay)
                clock[0] += delay
            self.assertEqual(self.engine.tick(), 5)
        self.assertEqual(self.engine.device.actions, [])
        self.assertEqual(self.engine.device.launches, 1)
        self.engine.detector.spec["available_teams"] = [1, 2, 3]
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertNotIn("visual_prerequisite_recovery", self.engine.data)

    def test_stage15_missing_ready_map_recovers_without_entering_combat(self):
        self.engine.data["run"] = {"id": "stage15", "outcome": None, "exit_mode": "stage15"}
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], verified_safe=True)
        with patch.object(self.engine, "observe", return_value=(
                np.zeros((160, 90, 3), dtype=np.uint8), "NR_TEAM_SELECT",
                self.engine.detector.spec, {})):
            self.assertEqual(self.engine.tick(), 5)
        self.assertEqual(self.engine.device.actions, [])
        self.assertEqual(self.engine.data["visual_prerequisite_recovery"]["reason"],
                         "unambiguous_ready_map")

    def test_visual_prerequisite_recovery_does_not_bypass_stamina_or_progression(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True,
                    require_team_slots=True, available_teams=[1, 2, 3])
        self.engine.data["stamina_until"] = farm.time.time() + 1000
        self.assertEqual(self.engine.tick(), 30)
        self.assertNotIn("visual_prerequisite_recovery", self.engine.data)
        self.engine.data.update(stamina_until=0, current_team=1, defeated_teams=[], available_teams=[1, 2, 3])
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"],
                    team_index=2, verified_safe=True)
        for _ in range(6):
            self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])
        self.assertEqual(self.engine.device.launches, 0)
        self.assertNotIn("visual_prerequisite_recovery", self.engine.data)

    def test_transition_timeout_never_retries_unverified_action(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=True)
        self.engine.tick()
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"],
                    attempt_start=True, verified_safe=False)
        self.engine.data["pending"]["time"] -= self.engine.TRANSITION_TIMEOUT + 1
        self.engine.tick()
        self.engine.data["transition_recovery"]["next_attempt_at"] = farm.time.time() - 1
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])
        self.assertEqual(self.engine.device.launches, 1)
        self.assertEqual(self.engine.data["pending"]["id"], "nr_start")

    def test_connection_uncertain_ack_keeps_both_latches(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], attempt_start=True)
        self.engine.tick()
        original = copy.deepcopy(self.engine.data["pending"])
        self.connection_screen()
        self.engine.device.failure = RuntimeError("uncertain ACK")
        with self.assertRaisesRegex(RuntimeError, "uncertain ACK"):
            self.engine.tick()
        self.assertEqual(self.engine.data["connection_pending"], original)
        self.assertEqual(self.engine.data["pending"]["id"], "retry_connection")
        self.engine.device.failure = None
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_start"])

    def test_map_ready_preserves_team_and_settings_verification_is_bounded(self):
        self.engine.data.update(current_team=1, available_teams=[1, 2, 3],
                                run={"id": "active", "outcome": None})
        self.screen("NR_COMBAT", auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["settings_verified"], 1)
        self.screen("NR_TEAM_SELECT", "nr_ready", ["NR_COMBAT"])
        self.engine.tick()
        self.screen("NR_COMBAT", auto_enabled=True, ninjutsu_enabled=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.assertEqual(self.engine.data["counts"]["settings_verified"], 1)

    def ready_progress_frame(self, number):
        frame = load_fixture(farm.ROOT / "tests/fixtures/ninja_ready_progress" / f"map_{number}_ready.png")
        self.assertIsNotNone(frame)
        return frame

    def ready_progress_pending(self):
        self.engine.data.update(current_team=3, available_teams=[1, 2, 3], defeated_teams=[1, 2],
                                run={"id": "attempt", "outcome": None})
        pending = {"id": "nr_team", "source": "NR_TEAM_SELECT", "expected": ["NR_COMBAT"],
                   "time": 0, "on_confirm": {}, "ready_map_index": 9, "ready_attempt_id": "attempt",
                   "ready_team": 3, "returned": True}
        self.engine.data["pending"] = pending
        self.engine.data.pop("transition_timed_out", None)
        return pending

    def test_ready_map_reader_uses_known_numeric_references(self):
        for number in (9, 10):
            self.assertEqual(self.engine.read_ready_map(self.ready_progress_frame(number)), number)
        frame = self.ready_progress_frame(10)
        frame[8:46, 245:348] = 0
        self.assertIsNone(self.engine.read_ready_map(frame))

    def test_ready_map_reader_rejects_ambiguous_numeric_matches(self):
        reference = "assets/patterns/ninja_road/maps/9.png"
        config = {"region": [245, 8, 348, 46], "references": {"9": [reference], "10": reference}}
        with patch.object(farm, "read_json", return_value=config):
            self.assertIsNone(self.engine.read_ready_map(self.ready_progress_frame(9)))

    def test_missed_ready_combat_confirms_next_map_before_boss_auto_is_stopped(self):
        self.engine.data.update(current_team=3, available_teams=[1, 2, 3], defeated_teams=[1, 2],
                                run={"id": "attempt", "outcome": None})
        counts = copy.deepcopy(self.engine.data["counts"])
        frame = self.ready_progress_frame(9)
        self.engine.device.capture = lambda: frame.copy()

        def perform(action):
            saved = json.loads(self.engine.progress.read_text())
            if action["id"] == "nr_team":
                self.assertEqual(saved["pending"]["ready_map_index"], 9)
                self.assertNotIn("returned", saved["pending"])
            else:
                self.assertEqual(action["id"], "nr_auto")
                self.assertIsNone(saved["pending"])
                self.assertEqual(saved["boss_swap"]["pending"]["purpose"], "stop_auto")
            self.engine.device.actions.append(action["id"])

        self.engine.device.perform = perform
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], verified_safe=True)
        with patch.object(farm, "boss_swap_enabled", return_value=True):
            self.engine.tick()
            pending = copy.deepcopy(self.engine.data["pending"])
            self.assertEqual(pending["ready_map_index"], 9)
            self.assertEqual(pending["ready_attempt_id"], "attempt")
            self.assertEqual(pending["ready_team"], 3)
            self.assertIs(pending["returned"], True)
            self.assertIs(json.loads(self.engine.progress.read_text())["pending"]["returned"], True)
            self.screen("UNKNOWN")
            self.engine.tick()
            self.assertEqual(self.engine.data["pending"], pending)
            frame = self.ready_progress_frame(10)
            self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], verified_safe=True)
            self.engine.tick()
        self.assertIsNone(self.engine.data["pending"])
        self.assertEqual(self.engine.data["boss_swap"]["pending"]["purpose"], "stop_auto")
        self.assertEqual(self.engine.device.actions, ["nr_team", "nr_auto"])
        self.assertEqual(self.engine.data["counts"], counts)
        self.assertEqual(self.engine.data["current_team"], 3)
        self.assertEqual(self.engine.data["run"]["id"], "attempt")
        self.assertEqual(self.engine.data["defeated_teams"], [1, 2])
        self.assertNotIn("transition_timed_out", self.engine.data)

    def test_ready_map_advancement_rejects_incomplete_or_wrong_context(self):
        cases = ("same_map", "skipped_map", "unknown_map", "uncertain_return", "wrong_attempt",
                 "wrong_team", "team_selection", "mission_start", "missing_source_map", "ended_attempt",
                 "unsafe_ready", "unknown_screen")
        for case in cases:
            with self.subTest(case=case):
                pending = self.ready_progress_pending()
                frame = self.ready_progress_frame(9 if case == "same_map" else 10)
                name, verified = "NR_TEAM_SELECT", True
                if case == "skipped_map":
                    pending["ready_map_index"] = 8
                elif case == "unknown_map":
                    frame[8:46, 245:348] = 0
                elif case == "uncertain_return":
                    pending.pop("returned")
                elif case == "wrong_attempt":
                    pending["ready_attempt_id"] = "other_attempt"
                elif case == "wrong_team":
                    pending["ready_team"] = 2
                elif case == "team_selection":
                    pending["on_confirm"] = {"team_index": 3}
                elif case == "mission_start":
                    pending.update(id="nr_start", source="NR_START")
                elif case == "missing_source_map":
                    pending.pop("ready_map_index")
                elif case == "ended_attempt":
                    self.engine.data["run"]["outcome"] = "failed"
                elif case == "unsafe_ready":
                    verified = False
                elif case == "unknown_screen":
                    name = "UNKNOWN"
                original = copy.deepcopy(pending)
                self.engine.device.capture = lambda: frame.copy()
                self.screen(name, "nr_team", ["NR_COMBAT"], verified_safe=verified)
                self.engine.tick()
                self.assertEqual(self.engine.data["pending"], original)
                self.assertEqual(self.engine.device.actions, [])
                self.assertTrue(self.engine.data["transition_timed_out"])

    def test_ready_map_must_be_stable_across_both_observations(self):
        pending = copy.deepcopy(self.ready_progress_pending())
        frames = iter((self.ready_progress_frame(9), self.ready_progress_frame(10)))
        self.engine.device.capture = lambda: next(frames).copy()
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], verified_safe=True)
        self.engine.tick()
        self.assertEqual(self.engine.data["screen"], "UNKNOWN")
        self.assertEqual(self.engine.data["pending"], pending)
        self.assertEqual(self.engine.device.actions, [])

    def test_uncertain_ready_command_cannot_be_confirmed_by_next_map(self):
        self.engine.data.update(current_team=3, run={"id": "attempt", "outcome": None})
        frame = self.ready_progress_frame(9)
        self.engine.device.capture = lambda: frame.copy()
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], verified_safe=True)
        self.engine.device.failure = RuntimeError("uncertain Ready dispatch")
        with self.assertRaisesRegex(RuntimeError, "uncertain Ready dispatch"):
            self.engine.tick()
        pending = copy.deepcopy(self.engine.data["pending"])
        self.assertNotIn("returned", pending)
        self.assertEqual(pending["ready_map_index"], 9)
        self.engine.device.failure = None
        frame = self.ready_progress_frame(10)
        self.engine.tick()
        self.assertEqual(self.engine.data["pending"], pending)
        self.assertEqual(self.engine.device.actions, [])

    def test_next_map_early_exit_resolves_plain_ready_before_its_own_actions(self):
        for name in ("NR_EARLY_EXIT", "NR_EARLY_EXIT_PREPARE"):
            with self.subTest(screen=name):
                pending = self.ready_progress_pending()
                pending["ready_map_index"] = 10
                self.engine._confirm(name, {"verified_safe": True, "ready_map_index": 11})
                self.assertIsNone(self.engine.data["pending"])
                self.assertEqual(self.engine.data["current_team"], 3)
                self.assertEqual(self.engine.data["counts"]["started"], 0)

    def test_missed_boss_combat_hands_off_to_early_exit_with_auto_off_first(self):
        pending = self.ready_progress_pending()
        pending["ready_map_index"] = 10
        self.engine.detector = farm.Detector("ninja_road")
        off = load_fixture(farm.ROOT / "tests/fixtures/ninja_boss_swap/after_boss_ready.png")
        self.assertIsNotNone(off)
        on = off.copy()
        on[29:81, 549:700] = self.ready_progress_frame(10)[29:81, 549:700]
        frame = on
        self.engine.device.capture = lambda: frame.copy()
        counts = copy.deepcopy(self.engine.data["counts"])
        with patch.object(farm, "early_exit_enabled", return_value=True), patch.object(farm, "boss_swap_enabled", return_value=True):
            self.engine.tick()
            self.assertEqual(self.engine.device.actions, ["nr_auto"])
            self.assertEqual(self.engine.data["pending"]["source"], "NR_EARLY_EXIT_PREPARE")
            frame = off
            self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_auto", "nr_retreat"])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_EARLY_EXIT")
        self.assertEqual(self.engine.data["pending"]["on_confirm"], {"early_exit_requested": True})
        self.assertEqual(self.engine.data["counts"], counts)
        self.assertEqual(self.engine.data["current_team"], 3)

    def test_team_switch_requires_confirmed_defeat_and_order(self):
        self.engine.data.update(current_team=1, available_teams=[1, 2, 3])
        self.screen("NR_TEAM_SELECT", "nr_team_2", ["NR_COMBAT"], team_index=2)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])
        self.engine.data["defeated_teams"] = [1]
        self.screen("NR_TEAM_SELECT", "nr_team_3", ["NR_COMBAT"], team_index=3)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])
        self.screen("NR_TEAM_SELECT", "nr_team_2", ["NR_COMBAT"], team_index=2)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_team_2"])

    def test_disabled_ready_rejection_allows_guarded_next_team(self):
        self.engine.data.update(current_team=1, available_teams=[1, 2, 3])
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"])
        self.engine.tick()
        self.engine.data["pending"]["time"] -= 100
        self.engine.tick()
        self.assertTrue(self.engine.data["transition_timed_out"])
        self.screen("NR_TEAM_DEFEATED", "nr_team", ["NR_TEAM_SELECT"],
                    rejects_pending={"id": "nr_team", "source": "NR_TEAM_SELECT"}, team_index=2)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_team", "nr_team"])
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [1])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_TEAM_DEFEATED")
        self.assertEqual(self.engine.data["pending"]["on_confirm"]["team_index"], 2)
        self.assertNotIn("transition_timed_out", self.engine.data)
        self.screen("NR_TEAM_SELECT")
        self.engine.tick()
        self.assertEqual(self.engine.data["current_team"], 2)

    def test_defeat_rejection_cannot_discard_actual_team_selection(self):
        self.engine.data.update(current_team=None, available_teams=[1, 2, 3])
        self.screen("NR_TEAM_SELECT", "nr_team", ["NR_COMBAT"], team_index=1)
        self.engine.tick()
        self.screen("NR_TEAM_DEFEATED", "nr_team", ["NR_TEAM_SELECT"],
                    rejects_pending={"id": "nr_team", "source": "NR_TEAM_SELECT"}, team_index=2)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_team"])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_TEAM_SELECT")

    def test_toggle_requires_positive_off_evidence(self):
        for value in (None, True):
            self.screen("NR_COMBAT", "nr_auto", ["NR_COMBAT_ON"], enable_setting="auto", auto_enabled=value)
            self.engine.tick()
            self.assertEqual(self.engine.device.actions, [])
        self.screen("NR_COMBAT_AUTO_OFF", "nr_auto", ["NR_COMBAT"], enable_setting="auto", auto_enabled=False)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_auto"])

    def test_stop_and_foreground_prevent_input(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"])
        self.engine.device.package = "other.application"
        self.engine.tick()
        self.engine.device.package = farm.PACKAGE
        farm.STOP.write_text("stop")
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])

    def test_uncertain_input_keeps_latch_but_known_not_sent_clears_it(self):
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"])
        self.engine.device.failure = RuntimeError("uncertain delivery")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.engine.tick()
        self.assertIsNotNone(self.engine.data["pending"])
        self.engine.data["pending"] = None
        self.engine.device.failure = farm.ActionNotDispatched("verified before dispatch")
        with self.assertRaises(farm.ActionNotDispatched):
            self.engine.tick()
        self.assertIsNone(self.engine.data["pending"])


if __name__ == "__main__":
    unittest.main()
