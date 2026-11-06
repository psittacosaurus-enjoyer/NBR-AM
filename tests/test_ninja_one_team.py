"""One Team Run: retreat on the first defeated team instead of switching teams."""
import sys
import unittest
from unittest.mock import patch

from automation import controller as farm
from automation import safety
from automation.ninja_boss_swap import BossSwap
from automation.ninja_road import NinjaRoadEngine
from automation.ninja_stage15 import Stage15Strategy
from fixture_images import load_fixture
import test_ninja_road


DEFEATED = {"verified_safe": True, "phase": "team_defeated", "team_defeated": True,
            "rejects_pending": {"id": "nr_team", "source": "NR_TEAM_SELECT"},
            "team_choices": [{"index": 2, "action": {"id": "nr_team", "kind": "tap", "point": [878, 1286],
                                                    "expected": ["NR_TEAM_SELECT"]}}],
            "action": {"id": "nr_team", "kind": "tap", "point": [878, 1286], "expected": ["NR_TEAM_SELECT"]}}


class OneTeamFlowTests(unittest.TestCase):
    """Reuses the offline Ninja Road harness (fake detector and device)."""

    screen = test_ninja_road.NinjaRoadTests.screen

    def setUp(self):
        test_ninja_road.NinjaRoadTests.setUp(self)
        for item in (patch.object(NinjaRoadEngine, "read_ready_map", return_value=7),
                     patch.object(BossSwap, "__init__", lambda self, engine: None)):
            item.start()
            self.addCleanup(item.stop)
        self.auto = patch.object(BossSwap, "auto", return_value=True)
        self.auto_mock = self.auto.start()
        self.addCleanup(self.auto.stop)
        self.engine.data.update(current_team=1, available_teams=[1, 2, 3],
                                run={"id": "attempt", "start": 1, "outcome": None})

    def defeated(self):
        self.screen("NR_TEAM_DEFEATED", **{k: v for k, v in DEFEATED.items() if k != "action"})
        self.engine.detector.spec["action"] = dict(DEFEATED["action"])

    def test_defeat_turns_auto_off_then_retreats_and_counts_once(self):
        with patch.object(farm, "one_team_enabled", return_value=True):
            self.defeated()
            self.engine.tick()
            self.assertEqual(self.engine.device.actions, ["nr_auto"])
            self.assertEqual(self.engine.data["pending"]["source"], "NR_ONE_TEAM_EXIT_PREPARE")
            self.assertEqual(self.engine.data["defeated_teams"], [1])
            self.auto_mock.return_value = False
            self.engine.tick()
            self.assertEqual(self.engine.device.actions, ["nr_auto", "nr_retreat"])
            self.assertEqual(self.engine.data["pending"]["exit_after_stage"], 6)
            self.screen("NR_RETREAT_CONFIRM", "nr_retreat_confirm", ["NR_RESULTS"],
                        phase="early_exit", verified_safe=True, on_confirm={"early_exit_completed": True})
            self.engine.tick()
            self.assertTrue(self.engine.data["early_exit_requested"])
            self.assertTrue(self.engine.data["one_team_exit"])
            self.assertEqual(self.engine.device.actions, ["nr_auto", "nr_retreat", "nr_retreat_confirm"])
            self.screen("NR_RESULTS", phase="results")
            self.engine.tick()
            self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["early_exits"], 1)
        self.assertEqual(self.engine.data["run"]["outcome"], "early_exit")
        self.assertNotIn("nr_team", self.engine.device.actions)
        farm.event.assert_any_call("ninja_early_exit_completed", run_id="attempt",
                                   cleared_map=6, one_team=True)

    def test_unknown_auto_state_waits_without_input(self):
        self.auto_mock.return_value = None
        with patch.object(farm, "one_team_enabled", return_value=True):
            self.defeated()
            self.engine.tick()
        self.assertEqual(self.engine.device.actions, [])
        self.assertIsNone(self.engine.data.get("pending"))

    def test_disabled_keeps_existing_team_switch(self):
        with patch.object(farm, "one_team_enabled", return_value=False):
            self.defeated()
            self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_team"])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_TEAM_DEFEATED")


class OneTeamRecognitionTests(unittest.TestCase):
    def engine(self, frame):
        engine = object.__new__(NinjaRoadEngine)
        engine.detector = farm.Detector("ninja_road")
        engine.data = {"run": None}
        engine.device = unittest.mock.Mock()
        engine.device.capture.return_value = frame
        return engine

    def test_real_defeated_frame_becomes_guarded_one_team_exit(self):
        frame = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_team_defeated.png")
        for auto, expected_name, action in ((True, "NR_ONE_TEAM_EXIT_PREPARE", "nr_auto"),
                                            (False, "NR_ONE_TEAM_EXIT", "nr_retreat")):
            with patch.object(farm, "one_team_enabled", return_value=True), \
                 patch.object(farm, "ninja_exit_mode", return_value="stage10"), \
                 patch.object(BossSwap, "auto", return_value=auto):
                _, name, spec, _ = self.engine(frame).observe()
            self.assertEqual(name, expected_name)
            self.assertEqual(spec["action"]["id"], action)
            self.assertNotIn("team_choices", spec)
            self.assertTrue(spec["team_defeated"])
            self.assertEqual(farm.NINJA_STATE_ACTIONS[name], action)
            safety.require_pearl_safe_action("ninja_road", name, spec)
        with patch.object(farm, "one_team_enabled", return_value=False), \
             patch.object(farm, "ninja_exit_mode", return_value="stage10"):
            _, name, spec, _ = self.engine(frame).observe()
        self.assertEqual(name, "NR_TEAM_DEFEATED")
        self.assertIn("team_choices", spec)


class OneTeamStage15Tests(unittest.TestCase):
    def test_planned_team_stays_on_current_team(self):
        strategy = object.__new__(Stage15Strategy)
        strategy.engine = unittest.mock.Mock(exit_mode="stage15")
        with patch.object(farm, "one_team_enabled", return_value=False):
            self.assertEqual(strategy.planned_team(7, 1), 2)
            self.assertEqual(strategy.planned_team(11, 1), 3)
        with patch.object(farm, "one_team_enabled", return_value=True):
            self.assertEqual(strategy.planned_team(7, 1), 1)
            self.assertEqual(strategy.planned_team(11, 1), 1)


class OneTeamSettingTests(unittest.TestCase):
    def test_setting_round_trips_and_cli_routes(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(farm, "RUNTIME", Path(temp)), patch.object(farm, "event"):
            self.assertFalse(farm.one_team_enabled())
            farm.set_one_team(True)
            self.assertTrue(farm.one_team_enabled())
            self.assertTrue(farm.read_json(Path(temp) / "manager_settings.json")["ninja_one_team"])
            farm.set_one_team(False)
            self.assertFalse(farm.one_team_enabled())
            with self.assertRaises(ValueError):
                farm.set_one_team("yes")
        with patch.object(sys, "argv", ["farm.py", "one-team", "--enabled", "on"]), \
             patch.object(farm, "set_one_team") as setter:
            farm.main()
        setter.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
