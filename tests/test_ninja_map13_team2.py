"""Stage 16 Exit option: Team 2 on Map 13, back to Team 1 on Map 14."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import controller as farm
from automation.ninja_road import NinjaRoadEngine
from automation.ninja_stage15 import Stage15Strategy


def strategy(mode="stage15_clean", current=1, defeated=(), teams=(1, 2, 3)):
    sent = []
    engine = SimpleNamespace(exit_mode=mode, save=lambda: None,
                             device=Mock(perform=lambda action: sent.append(action)),
                             data={"run": {"id": "attempt"}, "current_team": current,
                                   "defeated_teams": list(defeated), "available_teams": list(teams)})
    result = object.__new__(Stage15Strategy)
    result.engine = engine
    result.vision = Mock()
    result.vision.team_panel.return_value = np.zeros((4, 4), dtype=np.uint8)
    result.boss = Mock()
    result.boss.pack.return_value = "packed"
    return result, engine, sent


class PlanTests(unittest.TestCase):
    def plan(self, mode, enabled, one_team=False):
        s, _, _ = strategy(mode)
        with patch.object(farm, "map13_team2_enabled", return_value=enabled), \
             patch.object(farm, "one_team_enabled", return_value=one_team):
            return [s.planned_team(stage, 1) for stage in (12, 13, 14, 15)]

    def test_clean_mode_plan(self):
        self.assertEqual(self.plan("stage15_clean", True), [1, 2, 1, 1])
        self.assertEqual(self.plan("stage15_clean", False), [1, 1, 1, 1])
        self.assertEqual(self.plan("stage15_clean", True, one_team=True), [1, 1, 1, 1])

    def test_strategy_mode_ignores_option(self):
        self.assertEqual(self.plan("stage15", True), self.plan("stage15", False))

    def test_wrap_recovery_only_for_strategy_or_clean_with_option(self):
        engine = object.__new__(NinjaRoadEngine)
        for mode, enabled, expected in (("stage15", False, True), ("stage15_clean", True, True),
                                        ("stage15_clean", False, False), ("stage10", True, False)):
            engine.data = {"run": {"id": "x", "outcome": None, "exit_mode": mode}}
            with patch.object(farm, "map13_team2_enabled", return_value=enabled):
                self.assertEqual(engine._wrap_recovery(), expected, (mode, enabled))


class SwitchTests(unittest.TestCase):
    def select(self, stage, current, defeated=()):
        s, _, sent = strategy(current=current, defeated=defeated)
        state = {"pending": None, "settings": {}}
        with patch.object(farm, "map13_team2_enabled", return_value=True), \
             patch.object(farm, "one_team_enabled", return_value=False):
            s.select_team(state, None, "NR_TEAM_SELECT", stage)
        return sent, state

    def test_map13_moves_right_to_team2_and_map14_moves_left_to_team1(self):
        sent, state = self.select(13, current=1)
        self.assertEqual([a["point"] for a in sent], [[878, 1286]])
        self.assertEqual((state["pending"]["purpose"], state["pending"]["target"]), ("team", 2))
        sent, state = self.select(14, current=2)
        self.assertEqual([a["point"] for a in sent], [[28, 1286]])
        self.assertEqual(state["pending"]["target"], 1)

    def test_no_switch_when_target_team_is_defeated_or_already_active(self):
        for stage, current, defeated in ((13, 1, [2]), (14, 2, [1]), (13, 2, []), (12, 1, [])):
            sent, state = self.select(stage, current, defeated)
            self.assertEqual(sent, [], (stage, current, defeated))
            self.assertIsNone(state["pending"])

    def test_switch_only_on_verified_ready_screen(self):
        s, engine, sent = strategy(current=1)
        state = {"pending": None}
        with patch.object(farm, "map13_team2_enabled", return_value=True), \
             patch.object(farm, "one_team_enabled", return_value=False):
            s.select_team(state, None, "NR_COMBAT", 13)
        self.assertEqual(sent, [])
        self.assertIn("Ready", engine.data["blocked"])


class SettingTests(unittest.TestCase):
    def test_setting_round_trip_and_cli(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(farm, "RUNTIME", Path(temp)), patch.object(farm, "event"):
            self.assertFalse(farm.map13_team2_enabled())
            farm.set_map13_team2(True)
            self.assertTrue(farm.map13_team2_enabled())
            farm.set_map13_team2(False)
            self.assertFalse(farm.map13_team2_enabled())
            with self.assertRaises(ValueError):
                farm.set_map13_team2(1)
        with patch.object(sys, "argv", ["farm.py", "map13-team2", "--enabled", "on"]), \
             patch.object(farm, "set_map13_team2") as setter:
            farm.main()
        setter.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
