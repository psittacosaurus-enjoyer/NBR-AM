"""Targeted exit-mode and Stage 15 strategy tests; no live device input."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import controller as farm
from automation.ninja_road import NinjaRoadEngine
from automation.ninja_boss_swap import BossSwap


class Detector:
    def __init__(self):
        self.name = "NR_TEAM_SELECT"
        self.spec = {"verified_safe": True}

    def changed(self):
        return False

    def classify(self, frame):
        return self.name, {"name": self.name, **copy.deepcopy(self.spec)}, {}


class ExitModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        for item in (patch.object(farm, "RUNTIME", self.runtime),
                     patch.object(farm, "STOP", self.runtime / "STOP"),
                     patch.object(farm, "event"),
                     patch.object(farm, "boss_swap_enabled", return_value=False),
                     patch("automation.ninja_road.time.sleep")):
            item.start()
            self.addCleanup(item.stop)
        self.engine = object.__new__(NinjaRoadEngine)
        self.engine.target = None
        self.engine.progress = self.runtime / "ninja_road.json"
        self.engine.stage = {"id": "ninja_road"}
        self.engine.data = {
            "counts": {"started": 0, "completed": 0, "failed": 0, "resets": 0},
            "run": {"id": "attempt", "start": 1, "outcome": None, "exit_mode": "stage15"},
            "current_team": 1, "defeated_teams": [], "available_teams": [1, 2, 3],
        }
        self.engine.last_unknown = 0
        self.engine.unknown_hash = None
        self.engine.detector = Detector()
        self.engine.device = Mock()
        self.engine.device.foreground.return_value = farm.PACKAGE
        self.engine.device.capture.return_value = np.zeros((1600, 900, 3), dtype=np.uint8)
        self.engine.read_ready_map = Mock(return_value=16)
        self.engine.boss_swap_controller = Mock()
        self.engine.boss_swap_controller.reference.side_effect = (
            lambda frame, name: name == "early_exit_map" and self.engine.read_ready_map.return_value == 11)
        self.engine.boss_swap_controller.auto.return_value = False
        self.engine.boss_swap_controller.tick.return_value = None
        self.engine._stage15_controller = Mock()
        self.engine._stage15_controller.vision.read_map.side_effect = self.engine.read_ready_map
        self.engine._stage15_controller.tick.return_value = None

        def perform(action):
            saved = json.loads(self.engine.progress.read_text())
            self.assertEqual(saved["pending"]["id"], action["id"])

        self.engine.device.perform.side_effect = perform

    def test_stage15_ignores_stage11_early_exit_even_when_old_option_is_enabled(self):
        self.engine.read_ready_map.return_value = 11
        with patch.object(farm, "early_exit_enabled", return_value=True):
            _, name, spec, _ = self.engine.observe()
        self.assertEqual(name, "NR_TEAM_SELECT")
        self.assertEqual(spec["ready_map_index"], 11)
        self.assertNotIn("early_exit_requested", spec.get("on_confirm", {}))

    def test_stage15_retreat_requires_auto_stopped_and_never_starts_stage16(self):
        for auto, expected_name, expected_action in (
                (True, "NR_EARLY_EXIT_PREPARE", "nr_auto"),
                (None, "NR_EARLY_EXIT_PREPARE", None),
                (False, "NR_EARLY_EXIT", "nr_retreat")):
            with self.subTest(auto=auto):
                self.engine.boss_swap_controller.auto.return_value = auto
                self.engine.detector.spec["action"] = {
                    "id": "nr_team", "kind": "tap", "point": [10, 10], "expected": ["NR_COMBAT"]}
                with patch.object(farm, "early_exit_enabled", return_value=False):
                    _, name, spec, _ = self.engine.observe()
                self.assertEqual(name, expected_name)
                self.assertEqual(spec["ready_map_index"], 16)
                self.assertEqual(spec.get("action", {}).get("id"), expected_action)

    def test_stage10_preserves_flag_and_stage11_exit_without_stage16_retreat(self):
        self.engine.data["run"]["exit_mode"] = "stage10"
        for stage, enabled, expected in ((11, True, "NR_EARLY_EXIT"),
                                          (11, False, "NR_TEAM_SELECT"),
                                          (16, True, "NR_TEAM_SELECT")):
            with self.subTest(stage=stage, enabled=enabled):
                self.engine.read_ready_map.return_value = stage
                with patch.object(farm, "early_exit_enabled", return_value=enabled):
                    _, name, _, _ = self.engine.observe()
                self.assertEqual(name, expected)

    def test_active_run_keeps_mode_when_persisted_selection_changes(self):
        for active, selected, stage, expected in (
                ("stage15", "stage10", 11, "NR_TEAM_SELECT"),
                ("stage15", "stage10", 16, "NR_EARLY_EXIT"),
                ("stage10", "stage15", 11, "NR_EARLY_EXIT"),
                ("stage10", "stage15", 16, "NR_TEAM_SELECT")):
            with self.subTest(active=active, selected=selected, stage=stage):
                self.engine.data["run"]["exit_mode"] = active
                self.engine.read_ready_map.return_value = stage
                with patch.object(farm, "ninja_exit_mode", return_value=selected), \
                        patch.object(farm, "early_exit_enabled", return_value=True):
                    _, name, _, _ = self.engine.observe()
                self.assertEqual(name, expected)

    def test_existing_run_without_mode_keeps_stage10_behavior(self):
        self.engine.data["run"].pop("exit_mode")
        self.engine.read_ready_map.return_value = 11
        with patch.object(farm, "ninja_exit_mode", return_value="stage15"), \
                patch.object(farm, "early_exit_enabled", return_value=True):
            _, name, _, _ = self.engine.observe()
        self.assertEqual(self.engine.exit_mode, "stage10")
        self.assertEqual(name, "NR_EARLY_EXIT")
        self.engine._stage15_controller.vision.read_map.assert_not_called()

    def test_stage10_tick_never_invokes_stage15_strategy(self):
        self.engine.data["run"]["exit_mode"] = "stage10"
        self.engine.detector.name = "NR_COMBAT"
        self.engine.detector.spec = {"phase": "combat"}
        self.engine.tick()
        self.engine._stage15_controller.tick.assert_not_called()

    def startup_pending(self):
        return {"id": "nr_start", "source": "NR_START", "expected": ["NR_TEAM_SELECT"],
                "time": farm.time.time(), "run_id": "startup", "exit_mode": "stage15",
                "available_teams": [1, 2, 3], "returned": True,
                "on_confirm": {"begin_attempt": True}}

    def test_verified_stage15_startup_combat_accepts_original_start_once(self):
        self.engine.data.update(run=None, current_team=None, pending=self.startup_pending())
        spec = {"verified_safe": True, "combat_map_index": 1}
        self.engine._confirm("NR_COMBAT", spec)
        self.engine._confirm("NR_COMBAT", spec)
        self.assertIsNone(self.engine.data["pending"])
        self.assertEqual(self.engine.data["run"]["id"], "startup")
        self.assertEqual(self.engine.data["run"]["exit_mode"], "stage15")
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertEqual(self.engine.data["stage15_map"], 1)

    def test_startup_combat_rejects_stage10_wrong_map_unsafe_or_uncertain_start(self):
        for case in ("stage10", "wrong_map", "missing_map", "unsafe", "unreturned", "no_begin"):
            with self.subTest(case=case):
                pending = self.startup_pending()
                spec = {"verified_safe": True, "combat_map_index": 1}
                if case == "stage10":
                    pending["exit_mode"] = "stage10"
                elif case == "wrong_map":
                    spec["combat_map_index"] = 2
                elif case == "missing_map":
                    spec.pop("combat_map_index")
                elif case == "unsafe":
                    spec["verified_safe"] = False
                elif case == "unreturned":
                    pending.pop("returned")
                else:
                    pending["on_confirm"] = {}
                original = copy.deepcopy(pending)
                self.engine.data.update(run=None, current_team=None, pending=pending)
                self.engine._confirm("NR_COMBAT", spec)
                self.assertEqual(self.engine.data["pending"], original)
                self.assertIsNone(self.engine.data["run"])
                self.assertEqual(self.engine.data["counts"]["started"], 0)

    def test_initial_ready_still_dispatches_when_startup_already_adopted_team1(self):
        self.engine.read_ready_map.return_value = 1
        self.engine.detector.spec = {
            "verified_safe": True, "team_index": 1,
            "action": {"id": "nr_team", "kind": "tap", "point": [655, 1208],
                       "expected": ["NR_COMBAT"]},
        }
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.device.perform.call_count, 1)
        self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_team")
        self.assertEqual(self.engine.data["pending"]["on_confirm"], {})
        self.assertEqual(self.engine.data["current_team"], 1)

    def test_unstable_combat_map_observations_never_dispatch_input(self):
        self.engine.detector.name = "NR_COMBAT"
        self.engine.detector.spec = {"verified_safe": True, "phase": "combat"}
        self.engine.read_ready_map.side_effect = [1, 2]
        self.engine.tick()
        self.assertEqual(self.engine.data["screen"], "UNKNOWN")
        self.engine.device.perform.assert_not_called()
        self.engine._stage15_controller.tick.assert_not_called()

    def test_opening_stage15_policy_suppresses_legacy_normal_ninjutsu_off_action(self):
        catalog = farm.read_json(farm.ROOT / "assets/patterns/ninja_road.json")
        spec = next(state for state in catalog["states"] if state["name"] == "NR_COMBAT_NINJUTSU_OFF")
        for stage in range(1, 5):
            with self.subTest(stage=stage):
                self.engine.data["stage15_map"] = stage
                action, changes = self.engine._choose_action("NR_COMBAT_NINJUTSU_OFF", spec)
                self.assertIsNone(action)
                self.assertEqual(changes, {})
        self.engine.data["stage15_map"] = 5
        action, _ = self.engine._choose_action("NR_COMBAT_NINJUTSU_OFF", spec)
        self.assertEqual(action["id"], "nr_ninjutsu")
        self.engine.data.update(stage15_map=1)
        self.engine.data["run"]["exit_mode"] = "stage10"
        action, _ = self.engine._choose_action("NR_COMBAT_NINJUTSU_OFF", spec)
        self.assertEqual(action["id"], "nr_ninjutsu")

    def test_stage16_retreat_counts_only_after_confirmed_result_and_does_not_repeat(self):
        self.engine.detector.name = "NR_RETREAT_CONFIRM"
        self.engine.detector.spec = {
            "verified_safe": True, "phase": "early_exit",
            "on_confirm": {"early_exit_completed": True},
            "action": {"id": "nr_retreat_confirm", "kind": "tap", "point": [637, 785],
                       "expected": ["NR_RESULTS"]},
        }
        # Confirmation is forbidden until the existing retreat-request input
        # has reached its expected dialog.
        self.engine.tick()
        self.engine.device.perform.assert_not_called()
        self.engine.data["early_exit_requested"] = True
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.engine.device.perform.call_count, 1)
        self.assertEqual(self.engine.data["counts"].get("early_exits", 0), 0)
        self.assertIsNone(self.engine.data["run"]["outcome"])
        self.engine.detector.name = "NR_RESULTS"
        self.engine.detector.spec = {"phase": "results"}
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["early_exits"], 1)
        self.assertEqual(self.engine.data["run"]["outcome"], "early_exit")
        self.engine.tick()
        self.assertEqual(self.engine.data["counts"]["early_exits"], 1)

    def test_stage16_resolves_missed_boss_combat_then_stops_auto_and_retreats(self):
        self.engine.data["pending"] = {
            "id": "nr_team", "source": "NR_TEAM_SELECT", "expected": ["NR_COMBAT"],
            "time": farm.time.time(), "on_confirm": {}, "ready_map_index": 15,
            "ready_attempt_id": "attempt", "ready_team": 1, "returned": True,
        }
        self.engine.boss_swap_controller.auto.return_value = True
        self.engine.tick()
        self.engine.tick()
        self.assertEqual([call.args[0]["id"] for call in self.engine.device.perform.call_args_list],
                         ["nr_auto"])
        self.assertEqual(self.engine.data["pending"]["source"], "NR_EARLY_EXIT_PREPARE")
        self.engine.boss_swap_controller.auto.return_value = False
        self.engine.tick()
        self.engine.tick()
        self.assertEqual([call.args[0]["id"] for call in self.engine.device.perform.call_args_list],
                         ["nr_auto", "nr_retreat"])
        self.assertEqual(self.engine.data["pending"]["exit_after_stage"], 15)
        self.engine.detector.name = "NR_RETREAT_CONFIRM"
        self.engine.detector.spec = {"phase": "early_exit", "verified_safe": True}
        self.engine.tick()
        self.assertTrue(self.engine.data["early_exit_requested"])
        self.assertEqual(self.engine.data["exit_after_stage"], 15)
        self.assertEqual(self.engine.data["counts"].get("early_exits", 0), 0)

    def test_next_run_resets_stage_strategy_and_pins_mode_from_start_dispatch(self):
        self.engine.data.update(
            run={"id": "old", "outcome": "early_exit", "exit_mode": "stage15"},
            stage15_map=16, stage15_strategy={"phase": "restore", "usage": {"0": True}},
            stage15_formations={"1": {"phase": "restore", "team_key": ["old", 1]}},
            boss_swap={"phase": "boss"}, early_exit_requested=True, exit_after_stage=15,
            current_team=3, defeated_teams=[1, 2, 3],
        )
        self.engine.detector.name = "NR_START"
        self.engine.detector.spec = {
            "attempt_start": True,
            "action": {"id": "nr_start", "kind": "tap", "point": [10, 10],
                       "expected": ["NR_COMBAT"]},
        }
        with patch.object(farm, "ninja_exit_mode", return_value="stage15") as selected:
            self.engine.tick()
            self.assertEqual(self.engine.data["pending"]["exit_mode"], "stage15")
            self.assertIs(self.engine.data["pending"]["returned"], True)
            selected.return_value = "stage10"
            self.engine.detector.name = "NR_COMBAT"
            self.engine.detector.spec = {"phase": "combat"}
            self.engine.tick()
        self.assertEqual(self.engine.data["run"]["exit_mode"], "stage15")
        self.assertEqual(self.engine.exit_mode, "stage15")
        self.assertEqual(self.engine.data["counts"]["started"], 1)
        self.assertEqual(self.engine.data["counts"]["resets"], 1)
        self.assertEqual(self.engine.data["defeated_teams"], [])
        self.assertIsNone(self.engine.data["current_team"])
        for key in ("stage15_map", "stage15_strategy", "stage15_formations", "boss_swap",
                    "early_exit_requested", "exit_after_stage"):
            self.assertNotIn(key, self.engine.data)

    def test_stage15_death_recovery_can_return_to_an_earlier_surviving_team(self):
        catalog = farm.read_json(farm.ROOT / "assets/patterns/ninja_road.json")
        defeated_spec = next(spec for spec in catalog["states"] if spec["name"] == "NR_TEAM_DEFEATED")
        for current, defeated, target, point in ((3, [2, 3], 1, [878, 1286]),
                                                  (1, [1, 2], 3, [28, 1286])):
            with self.subTest(current=current):
                self.engine.data.update(current_team=current, defeated_teams=defeated)
                action, changes = self.engine._choose_action("NR_TEAM_DEFEATED", defeated_spec)
                self.assertEqual(changes["team_index"], target)
                self.assertEqual(action["point"], point)
                self.assertEqual(self.engine.data["run"]["id"], "attempt")
                self.assertEqual(self.engine.data["counts"]["resets"], 0)
        self.engine.data["run"]["exit_mode"] = "stage10"
        self.engine.data.update(current_team=3, defeated_teams=[2, 3])
        action, _ = self.engine._choose_action("NR_TEAM_DEFEATED", defeated_spec)
        self.assertIsNone(action)


class Stage15StrategyTests(unittest.TestCase):
    def setUp(self):
        from automation.ninja_stage15 import Stage15Strategy

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        for item in (patch.object(farm, "STOP", self.runtime / "STOP"), patch.object(farm, "event")):
            item.start()
            self.addCleanup(item.stop)
        self.frame = np.zeros((1600, 900, 3), dtype=np.uint8)
        self.engine = SimpleNamespace(
            data={"run": {"id": "attempt", "exit_mode": "stage15"}, "current_team": 2,
                  "available_teams": [1, 2, 3], "defeated_teams": [], "stage15_map": 7},
            READY_SCREENS=NinjaRoadEngine.READY_SCREENS, device=Mock())
        self.engine.save = lambda: farm.write_json(self.runtime / "state.json", self.engine.data)
        self.boss = BossSwap(self.engine)
        self.boss.auto = Mock(return_value=False)
        self.boss.settled_active = Mock(return_value=None)
        self.engine.boss_swap_controller = self.boss
        with patch("automation.ninja_stage15.Stage15Vision"):
            self.strategy = Stage15Strategy(self.engine)
        self.settings = {"ninjutsu": True, "secret": False}
        self.charges = [.1, .1, .1]
        self.panel = np.arange(64, dtype=np.uint8).reshape(8, 8)
        self.strategy.vision.setting.side_effect = lambda frame, setting: self.settings.get(setting)
        self.strategy.vision.chakra.side_effect = lambda frame, column: self.charges[column]
        self.strategy.vision.team_panel.side_effect = lambda frame: self.panel

        def perform(action):
            saved = farm.read_json(self.runtime / "state.json")
            self.assertTrue(saved["stage15_strategy"]["pending"])
            self.assertNotIn("returned", saved["stage15_strategy"]["pending"])

        self.engine.device.perform.side_effect = perform

    def state(self, stage=7, phase="front_usage"):
        state = {"run_id": "attempt", "map": stage, "team": self.engine.data["current_team"],
                 "phase": phase, "pending": None, "settings": {}, "usage_since": farm.time.time(),
                 "peaks": {}, "spent": [], "pulses": 0}
        self.engine.data.update(stage15_map=stage, stage15_strategy=state)
        return state

    def formation(self, team, phase="restore"):
        return {
            "phase": phase, "team_key": ["attempt", team], "pending": None,
            "boss_map": self.boss.pack(self.boss.crop(self.frame, self.boss.config["map_region"])),
            "swapped": [0, 1, 2], "done": [], "targets": [0, 1, 2],
            "original": {str(column): self.boss.pack(self.boss.crop(self.frame, region))
                         for column, region in enumerate(self.boss.config["portraits"])},
        }

    def test_ready11_selects_team3_before_restoring_inactive_team1(self):
        self.engine.data["current_team"] = 1
        state = self.state(10, "restore")
        old = self.formation(1)
        self.engine.data["boss_swap"] = old
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 11})
        self.assertNotIn("boss_swap", self.engine.data)
        self.assertEqual(self.engine.data["stage15_formations"]["1"]["team_key"], ["attempt", 1])
        self.assertEqual(self.engine.data["stage15_formations"]["1"]["phase"], "restore")
        self.assertEqual(state["pending"]["purpose"], "team")
        self.assertEqual(state["pending"]["target"], 3)
        self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_strategy_team")
        self.assertEqual(self.engine.device.perform.call_args.args[0]["point"], [28, 1286])

    def test_returning_team_restores_saved_frontline_before_stage13_sequence(self):
        self.engine.data["current_team"] = 3
        state = self.state(12, "restore")
        saved = self.formation(1)
        self.engine.data["stage15_formations"] = {"1": saved}
        self.engine.data["boss_swap"] = self.formation(3)
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 13})
        self.panel = 255 - self.panel
        for _ in range(2):
            self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 13})
        self.assertEqual(self.engine.data["current_team"], 1)
        self.assertNotIn("1", self.engine.data["stage15_formations"])
        self.assertIn("3", self.engine.data["stage15_formations"])
        self.assertEqual(self.engine.data["boss_swap"], saved)
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 13})
        self.assertEqual(self.engine.data["boss_swap"]["phase"], "restore")
        self.assertNotIn("stage15_sequence", self.engine.data["boss_swap"])
        self.engine.data.pop("boss_swap")  # Existing driver verifies all three original roles.
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 13})
        self.assertEqual(self.engine.data["boss_swap"]["stage15_sequence"], 13)
        self.assertEqual(state["phase"], "back_enter")
        self.assertEqual(self.engine.device.perform.call_count, 1)

    def test_new_strategy_after_death_resumes_current_teams_deferred_formation(self):
        self.engine.data.update(current_team=2, defeated_teams=[1])
        saved = self.formation(2)
        self.engine.data["stage15_formations"] = {"2": saved}
        self.assertNotIn("stage15_strategy", self.engine.data)
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 7})
        self.assertEqual(self.engine.data.get("boss_swap"), saved)
        self.assertNotIn("2", self.engine.data["stage15_formations"])
        self.assertEqual(self.engine.data["stage15_strategy"]["phase"], "setup")
        self.engine.device.perform.assert_not_called()

    def test_unverified_shared_input_cannot_be_deferred_to_switch_teams(self):
        self.engine.data["current_team"] = 1
        self.state(10, "restore")
        old = self.formation(1)
        old["pending"] = {"purpose": "swipe", "column": 0, "time": farm.time.time()}
        self.engine.data["boss_swap"] = old
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 11})
        self.assertIs(self.engine.data["boss_swap"], old)
        self.assertNotIn("stage15_formations", self.engine.data)
        self.engine.device.perform.assert_not_called()

    def test_death_discards_only_its_own_deferred_formation_and_results_clear_all(self):
        self.state()
        self.engine.data["stage15_formations"] = {str(team): self.formation(team) for team in (1, 2, 3)}
        self.engine.data["defeated_teams"] = [2]
        self.strategy.tick(self.frame, "NR_TEAM_DEFEATED", {})
        self.assertEqual(set(self.engine.data["stage15_formations"]), {"1", "3"})
        self.strategy.tick(self.frame, "NR_RESULTS", {})
        self.assertNotIn("stage15_formations", self.engine.data)
        self.engine.device.perform.assert_not_called()

    def test_team_plans_use_verified_selection_once_and_skip_defeated_preferences(self):
        for stage, target in ((1, 1), (4, 1), (5, 1), (7, 2), (8, 1), (9, 1),
                              (11, 3), (12, 3), (13, 1)):
            with self.subTest(stage=stage):
                self.engine.device.perform.reset_mock()
                self.engine.data.update(current_team=2 if target != 2 else 1, defeated_teams=[])
                state = self.state(stage, "setup")
                self.strategy.select_team(state, self.frame, "NR_TEAM_SELECT", stage)
                self.assertEqual(state["pending"]["target"], target)
                self.assertNotEqual(self.engine.data["current_team"], target)
                self.strategy.confirm(state, self.frame, "NR_TEAM_SELECT", stage)
                self.assertIsNotNone(state["pending"])
                self.panel = 255 - self.panel
                self.strategy.confirm(state, self.frame, "NR_TEAM_SELECT", stage)
                self.assertNotEqual(self.engine.data["current_team"], target)
                self.strategy.confirm(state, self.frame, "NR_TEAM_SELECT", stage)
                self.assertEqual(self.engine.data["current_team"], target)
                self.assertIsNone(state["pending"])
                self.strategy.select_team(state, self.frame, "NR_TEAM_SELECT", stage)
                self.assertEqual(self.engine.device.perform.call_count, 1)
                self.engine.data.update(current_team=2 if target != 2 else 1, defeated_teams=[target])
                self.assertIsNone(self.strategy.select_team(state, self.frame, "NR_TEAM_SELECT", stage))
                self.assertEqual(self.engine.device.perform.call_count, 1)

    def test_team3_returns_to_living_team1_without_selecting_dead_team2(self):
        self.engine.data.update(current_team=3, defeated_teams=[2])
        state = self.state(13, "setup")
        self.strategy.select_team(state, self.frame, "NR_TEAM_SELECT", 13)
        self.assertEqual(state["pending"]["target"], 1)
        self.assertEqual(self.engine.device.perform.call_args.args[0]["point"], [878, 1286])

    def test_usage_requires_each_characters_observed_charge_drop(self):
        state = self.state()
        self.strategy.usage(state, self.frame)
        self.assertEqual(state["spent"], [])
        self.charges = [.8, .8, .8]
        self.strategy.usage(state, self.frame)
        for charges, spent in (([.1, .8, .8], [0]), ([.1, .1, .8], [0, 1])):
            self.charges = charges
            self.strategy.usage(state, self.frame)
            self.strategy.usage(state, self.frame)
            self.assertEqual(state["spent"], spent)
            self.assertEqual(state["phase"], "front_usage")
            self.assertNotIn("boss_swap", self.engine.data)
        self.charges = [.1, .1, .1]
        self.strategy.usage(state, self.frame)
        self.assertEqual(state["phase"], "back_enter")
        self.assertEqual(self.engine.data["boss_swap"]["stage15_sequence"], 7)
        self.engine.device.perform.assert_not_called()

    def test_ready_frontline_priming_records_charge_without_turn_pulse(self):
        self.charges = [.8, .8, .8]
        self.boss.settled_active.return_value = 0
        for _ in range(2):
            self.assertIsNone(self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 7}))
        state = self.engine.data["stage15_strategy"]
        self.assertEqual(state["phase"], "front_usage")
        self.assertEqual(state["peaks"], {"0": .8, "1": .8, "2": .8})
        self.assertEqual(state["spent"], [])
        self.engine.device.perform.assert_not_called()
        self.charges = [.1, .1, .1]
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "back_enter")
        self.assertEqual(self.engine.data["boss_swap"]["stage15_sequence"], 7)
        self.engine.device.perform.assert_not_called()

    def test_shared_boss_pending_input_prevents_strategy_toggles(self):
        self.settings = {"ninjutsu": False, "secret": False}
        for purpose in ("stop_auto", "swipe", "pulse_on"):
            with self.subTest(purpose=purpose):
                self.state(10, "setup")
                self.engine.data["boss_swap"] = {"phase": "enter", "pending": {"purpose": purpose}}
                self.assertIsNone(self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 10}))
                self.engine.device.perform.assert_not_called()
                self.strategy.vision.setting.assert_not_called()

    def test_hidden_ready_unknown_ninjutsu_stops_auto_once_before_manual_ready(self):
        self.engine.data["current_team"] = 1
        state = self.state(1, "setup")
        self.settings = {}
        self.boss.auto.return_value = True
        for _ in range(3):
            self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 1})
        self.assertEqual(self.engine.device.perform.call_count, 1)
        self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_auto")
        self.assertEqual(state["pending"]["purpose"], "auto")
        self.assertIs(state["pending"]["target"], False)
        self.boss.auto.return_value = False
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 1})
        self.assertIsNone(self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 1}))
        self.assertIsNone(state["pending"])
        self.assertEqual(self.engine.device.perform.call_count, 1)

    def test_hidden_ready_with_auto_off_allows_ready_without_setting_taps(self):
        self.engine.data["current_team"] = 1
        self.settings = {}
        for _ in range(2):
            self.assertIsNone(self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 1}))
        self.engine.device.perform.assert_not_called()

    def test_hidden_ready_reuses_confirmed_normal_off_state(self):
        state = self.state(1, "setup")
        self.engine.data["ninjutsu_enabled"] = False
        self.settings = {}
        self.boss.auto.return_value = True
        self.assertIsNone(self.strategy.settings(state, self.frame, 1, ready=True))
        self.assertIs(state["settings"]["ninjutsu"], False)
        self.engine.device.perform.assert_not_called()

    def test_fresh_combat_overlay_latches_normal_off_target_before_setting_input(self):
        state = self.state(1, "setup")
        fresh = self.frame.copy()
        self.boss.auto.side_effect = lambda frame: frame is fresh
        self.strategy.vision.setting.side_effect = lambda frame, setting: True if frame is fresh else None
        self.engine.device.capture.return_value = fresh
        with patch("automation.ninja_stage15.time.sleep"):
            self.strategy.settings(state, self.frame, 1)
        self.assertEqual([call.args[0]["id"] for call in self.engine.device.perform.call_args_list],
                         ["nr_auto", "nr_ninjutsu"])
        self.assertEqual(self.engine.device.capture.call_count, 1)
        self.assertEqual(state["pending"]["purpose"], "setting")
        self.assertEqual(state["pending"]["setting"], "ninjutsu")
        self.assertIs(state["pending"]["target"], False)
        self.assertIs(state["pending"]["returned"], True)
        self.strategy.vision.setting.side_effect = lambda frame, setting: False
        self.strategy.confirm(state, fresh, "NR_COMBAT_NINJUTSU_OFF", 1)
        self.assertIsNone(state["pending"])
        self.assertIs(self.engine.data["ninjutsu_enabled"], False)

    def test_backline_spending_restores_three_roles_and_sequence_does_not_repeat(self):
        state = self.state()
        self.strategy.start_swap(state, self.frame)
        swap = self.engine.data["boss_swap"]
        swap.update(phase="boss", swapped=[0, 1, 2])
        self.charges = [.8, .8, .8]
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "back_usage")
        self.assertEqual(state["spent"], [])
        self.charges = [.1, .1, .1]
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "restore")
        self.assertEqual(swap["phase"], "restore")
        self.assertEqual(swap["targets"], [0, 1, 2])
        self.engine.data.pop("boss_swap")  # Existing BossSwap verified restoration.
        for _ in range(3):
            self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "done")
        self.assertNotIn("boss_swap", self.engine.data)
        self.engine.device.perform.assert_not_called()

    def test_back_role_callback_persists_coherent_completed_swap_before_any_pulse(self):
        state = self.state()
        self.strategy.start_swap(state, self.frame)
        self.engine._stage15_controller = self.strategy
        swap = self.engine.data["boss_swap"]
        swap["pending"] = {"purpose": "swipe", "column": 0, "returned": True}
        self.charges = [.8, .8, .8]
        original_observer = self.strategy.record_back_role
        calls = []

        def observe(frame, column):
            self.assertIsNone(swap["pending"])
            self.assertEqual(swap["swapped"], [0])
            self.assertEqual(swap["done"], [0])
            self.assertTrue(swap["advance"])
            self.assertEqual(swap["advance_from"], column)
            original_observer(frame, column)
            saved = farm.read_json(self.runtime / "state.json")
            self.assertIsNone(saved["boss_swap"]["pending"])
            self.assertEqual(saved["boss_swap"]["swapped"], [0])
            self.assertEqual(saved["stage15_strategy"]["back_usage_cache"]["peaks"], {"0": .8})
            calls.append(column)

        with patch.object(self.strategy, "record_back_role", side_effect=observe):
            self.boss.complete(swap, 0, frame=self.frame)
        self.assertEqual(calls, [0])
        self.engine.device.perform.assert_not_called()

    def test_backline_casts_during_entry_survive_transition_after_last_swap(self):
        state = self.state()
        self.strategy.start_swap(state, self.frame)
        self.engine._stage15_controller = self.strategy
        swap = self.engine.data["boss_swap"]
        self.charges = [.8, .8, .8]
        for column in (0, 1):
            self.boss.complete(swap, column, frame=self.frame)
            self.charges[column] = .1
            swap["pending"] = {"purpose": "pulse_off", "returned": True}
            self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
            self.assertIn(column, state["back_usage_cache"]["spent"])
            swap["pending"] = None
        self.boss.complete(swap, 2, frame=self.frame)
        swap["phase"] = "boss"
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "back_usage")
        self.assertEqual(state["spent"], [0, 1])
        self.assertEqual(state["peaks"], {"0": .8, "1": .8, "2": .8})
        self.assertNotIn("back_usage_cache", state)
        self.assertEqual(swap["phase"], "boss")
        self.charges[2] = .1
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["phase"], "restore")
        self.assertEqual(swap["targets"], [0, 1, 2])
        self.engine.device.perform.assert_not_called()

    def test_stage10_swap_completion_never_invokes_stage15_observer(self):
        self.engine.data["run"]["exit_mode"] = "stage10"
        self.engine._stage15_controller = Mock()
        swap = {"phase": "enter", "done": [], "swapped": [],
                "pending": {"purpose": "swipe", "column": 0}}
        self.engine.data["boss_swap"] = swap
        self.boss.complete(swap, 0, frame=self.frame)
        self.engine._stage15_controller.record_back_role.assert_not_called()
        self.strategy.vision.chakra.assert_not_called()
        self.assertNotIn("stage15_strategy", self.engine.data)

    def test_usage_timeout_restores_swapped_roles_and_allows_combat_to_continue(self):
        for swapped in (False, True):
            with self.subTest(swapped=swapped):
                state = self.state(9, "back_usage" if swapped else "front_usage")
                state["usage_since"] -= self.strategy.WAIT_LIMIT + 1
                if swapped:
                    self.strategy.start_swap(state, self.frame)
                    self.engine.data["boss_swap"].update(phase="boss", swapped=[0, 1, 2])
                    state["phase"] = "back_usage"
                self.strategy.usage(state, self.frame)
                self.assertEqual(state["phase"], "restore" if swapped else "done")
                self.engine.device.perform.assert_not_called()

    def test_character_replacement_never_counts_as_spent_and_restores_surviving_roles(self):
        state = self.state(9, "back_usage")
        state["usage_since"] = 1000
        self.strategy.start_swap(state, self.frame)
        swap = self.engine.data["boss_swap"]
        swap.update(phase="boss", swapped=[0, 1, 2])
        state["phase"] = "back_usage"
        self.charges = [.8, .8, .8]
        with patch("automation.ninja_stage15.time.time", return_value=1000) as now, \
                patch("automation.ninja_stage15.time.sleep"), patch.object(self.strategy, "auto"):
            self.strategy.usage(state, self.frame)
            x1, y1, x2, y2 = self.boss.config["portraits"][0]
            self.frame[y1:y2, x1:x2] = 255
            self.charges = [.1, .1, .1]
            self.boss.settled_active.return_value = 0
            now.return_value = 1001
            self.strategy.usage(state, self.frame)
            self.assertNotIn(0, state["spent"])
            now.return_value = 1004
            self.strategy.usage(state, self.frame)
        self.assertEqual(state["phase"], "restore")
        self.assertEqual(swap["targets"], [1, 2])
        self.engine.device.perform.assert_not_called()

    def test_stage_progression_discards_previous_per_character_usage(self):
        self.engine.data["current_team"] = 1
        state = self.state(7)
        state.update(spent=[0, 1], peaks={"0": .8}, pulses=8)
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 8})
        self.assertEqual(state["map"], 8)
        self.assertEqual(state["phase"], "setup")
        for key in ("spent", "peaks", "usage_since", "pulses"):
            self.assertNotIn(key, state)
        self.engine.device.perform.assert_not_called()

    def test_death_recovery_cancels_strategy_without_new_input(self):
        state = self.state()
        state["pending"] = {"purpose": "auto", "target": False, "time": farm.time.time()}
        self.engine.data["defeated_teams"] = [2]
        self.assertIsNone(self.strategy.tick(self.frame, "NR_TEAM_DEFEATED", {}))
        self.assertNotIn("stage15_strategy", self.engine.data)
        self.engine.device.perform.assert_not_called()

    def test_recovered_team_discards_old_pending_setting_and_charge_history(self):
        state = self.state()
        state.update(spent=[0, 1], peaks={"0": .8})
        state["pending"] = {"purpose": "setting", "setting": "ninjutsu", "target": False,
                            "time": farm.time.time()}
        self.engine.data.update(current_team=3, defeated_teams=[2])
        self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {})
        self.assertEqual(state["team"], 3)
        self.assertIsNone(state["pending"])
        self.assertEqual(state.get("spent", []), [])
        self.assertEqual(state.get("peaks", {}).get("0", .1), .1)
        self.engine.device.perform.assert_not_called()

    def test_stage16_with_auto_off_yields_immediately_even_if_settings_are_hidden(self):
        self.state(15, "restore")
        self.settings = {}
        self.assertIsNone(self.strategy.tick(self.frame, "NR_EARLY_EXIT", {"ready_map_index": 16}))
        self.engine.device.perform.assert_not_called()
        self.assertNotIn("stage15_strategy", self.engine.data)

    def test_stage16_combat_stops_auto_once_then_holds_without_reenabling_it(self):
        self.state(15, "restore")
        self.boss.auto.return_value = True
        for _ in range(2):
            self.assertIsNotNone(self.strategy.tick(self.frame, "NR_COMBAT", {"combat_map_index": 16}))
        self.assertEqual(self.engine.device.perform.call_count, 1)
        state = self.engine.data["stage15_strategy"]
        self.assertEqual(state["pending"]["purpose"], "auto")
        self.assertIs(state["pending"]["target"], False)
        self.boss.auto.return_value = False
        for _ in range(3):
            self.assertIsNotNone(self.strategy.tick(self.frame, "NR_COMBAT_AUTO_OFF", {"combat_map_index": 16}))
        self.assertEqual(self.engine.device.perform.call_count, 1)
        self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_auto")

    def test_visible_post_boss_secret_is_disabled_before_stage11_team_navigation(self):
        self.engine.data["current_team"] = 1
        state = self.state(10, "setup")
        self.settings["secret"] = True
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 11})
        self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_secret")
        self.assertEqual(state["pending"]["setting"], "secret")
        self.assertIs(state["pending"]["target"], False)
        self.assertEqual(self.engine.data["current_team"], 1)
        self.settings["secret"] = False
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 11})
        self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": 11})
        self.assertEqual([call.args[0]["id"] for call in self.engine.device.perform.call_args_list],
                         ["nr_secret", "nr_strategy_team"])

    def test_ninjutsu_and_secret_controls_are_confirmed_and_toggled_independently(self):
        for stage in (10, 15):
            with self.subTest(stage=stage):
                self.engine.data["current_team"] = 1
                self.engine.device.perform.reset_mock()
                self.settings = {"ninjutsu": False, "secret": False}
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.assertEqual(self.engine.device.perform.call_count, 1)
                self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_ninjutsu")
                self.settings["ninjutsu"] = True
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.assertEqual(self.engine.device.perform.call_count, 2)
                self.assertEqual(self.engine.device.perform.call_args.args[0]["id"], "nr_secret")
                self.settings["secret"] = True
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.strategy.tick(self.frame, "NR_TEAM_SELECT", {"ready_map_index": stage})
                self.assertIs(self.engine.data["ninjutsu_enabled"], True)
                self.assertIs(self.engine.data["secret_enabled"], True)
                self.assertEqual(self.engine.device.perform.call_count, 2)

    def test_normal_ninjutsu_starts_disabled_and_stays_enabled_after_stage5(self):
        for stage in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
            with self.subTest(stage=stage):
                state = self.state(stage, "setup")
                self.settings = {"ninjutsu": stage >= 5, "secret": stage in {10, 15}}
                self.assertIsNone(self.strategy.settings(state, self.frame, stage))
                self.assertEqual(state["settings"]["ninjutsu"], stage >= 5)
                self.engine.device.perform.assert_not_called()

    def test_stage5_enables_ninjutsu_without_touching_existing_secret_policy(self):
        state = self.state(5, "setup")
        self.settings = {"ninjutsu": True}
        self.assertIsNone(self.strategy.settings(state, self.frame, 5))
        self.assertEqual([call.args[1] for call in self.strategy.vision.setting.call_args_list], ["ninjutsu"])
        self.engine.device.perform.assert_not_called()


class CleanStage16ExitTests(unittest.TestCase):
    """Stage 16 Exit ("stage15_clean"): Stage 16 retreat without the strategy."""

    def setUp(self):
        ExitModeTests.setUp(self)
        self.engine.data["run"]["exit_mode"] = "stage15_clean"

    def test_clean_mode_retreats_at_map16_and_ignores_map11_early_exit(self):
        self.engine.read_ready_map.return_value = 16
        _, name, spec, _ = self.engine.observe()
        self.assertEqual((name, spec["action"]["id"]), ("NR_EARLY_EXIT", "nr_retreat"))
        self.engine.read_ready_map.return_value = 11
        with patch.object(farm, "early_exit_enabled", return_value=True):
            _, name, _, _ = self.engine.observe()
        self.assertEqual(name, "NR_TEAM_SELECT")

    def test_clean_mode_defeat_rotates_to_next_living_team_without_one_team(self):
        spec = {"name": "NR_TEAM_DEFEATED", "verified_safe": True, "phase": "team_defeated", "team_defeated": True,
                "team_choices": [{"index": 2, "action": {"id": "nr_team", "kind": "tap", "point": [878, 1286],
                                                        "expected": ["NR_TEAM_SELECT"]}},
                                 {"index": 3, "action": {"id": "nr_team", "kind": "tap", "point": [878, 1286],
                                                        "expected": ["NR_TEAM_SELECT"]}}],
                "action": {"id": "nr_team", "kind": "tap", "point": [878, 1286], "expected": ["NR_TEAM_SELECT"]}}
        self.engine.data.update(current_team=1, defeated_teams=[1], available_teams=[1, 2, 3])
        with patch.object(farm, "one_team_enabled", return_value=False):
            action, changes = self.engine._choose_action("NR_TEAM_DEFEATED", spec)
        self.assertEqual((action["id"], changes["team_index"]), ("nr_team", 2))
        self.engine.data.update(current_team=2, defeated_teams=[1, 2])
        with patch.object(farm, "one_team_enabled", return_value=False):
            action, changes = self.engine._choose_action("NR_TEAM_DEFEATED", spec)
        self.assertEqual(changes["team_index"], 3)
        self.engine.data.update(current_team=3, defeated_teams=[1, 2, 3])
        with patch.object(farm, "one_team_enabled", return_value=False):
            action, _ = self.engine._choose_action("NR_TEAM_DEFEATED", spec)
        self.assertIsNone(action)  # All defeated: the game's all-defeated screen ends the run.

    def test_clean_mode_label_and_setting(self):
        self.assertEqual(farm.NINJA_MODE_LABELS,
                         {"stage10": "Stage 11 Exit", "stage15_clean": "Stage 16 Exit",
                          "stage15": "Stage 16 Strategy"})
        self.assertIn("stage15_clean", farm.STAGE15_MODES)


class CleanStrategyPolicyTests(unittest.TestCase):
    """The shared Stage 15 controller with the strategy parts switched off."""

    def strategy(self, mode, observed):
        from automation.ninja_stage15 import Stage15Strategy
        engine = SimpleNamespace(exit_mode=mode, save=Mock(), device=Mock(),
                                 READY_SCREENS=NinjaRoadEngine.READY_SCREENS,
                                 data={"run": {"id": "attempt"}, "current_team": 1,
                                       "defeated_teams": [], "available_teams": [1, 2, 3]})
        strategy = object.__new__(Stage15Strategy)
        strategy.engine = engine
        strategy.vision = Mock()
        strategy.vision.setting.side_effect = lambda frame, setting: observed.get(setting)
        strategy.vision.read_map.return_value = None
        strategy.boss = Mock()
        strategy.boss.auto.return_value = True
        return strategy, engine

    def test_clean_settings_ninjutsu_from_map5_and_secret_always_off(self):
        for mode, stage, observed, expected in (
                ("stage15_clean", 3, {"ninjutsu": False, "secret": True}, ("secret", False)),
                ("stage15_clean", 10, {"ninjutsu": True, "secret": True}, ("secret", False)),
                ("stage15_clean", 15, {"ninjutsu": True, "secret": True}, ("secret", False)),
                ("stage15_clean", 4, {"ninjutsu": True, "secret": False}, ("ninjutsu", False)),
                ("stage15_clean", 5, {"ninjutsu": False, "secret": False}, ("ninjutsu", True)),
                ("stage15_clean", 9, {"ninjutsu": True, "secret": False}, None),
                ("stage15", 3, {"ninjutsu": False, "secret": True}, None),     # strategy leaves 1-5
                ("stage15", 10, {"ninjutsu": True, "secret": False}, ("secret", True))):
            with self.subTest(mode=mode, stage=stage):
                strategy, engine = self.strategy(mode, observed)
                state = {"settings": {}, "pending": None}
                strategy.settings(state, None, stage)
                if expected is None:
                    engine.device.perform.assert_not_called()
                else:
                    self.assertEqual((state["pending"]["setting"], state["pending"]["target"]), expected)

    def test_clean_mode_never_plans_teams_or_starts_sequences(self):
        for mode, planned in (("stage15_clean", 1), ("stage15", 2)):
            strategy, _ = self.strategy(mode, {})
            with patch.object(farm, "one_team_enabled", return_value=False):
                self.assertEqual(strategy.planned_team(7, 1), planned)
        for mode, swapped in (("stage15_clean", False), ("stage15", True)):
            with self.subTest(mode=mode):
                strategy, engine = self.strategy(mode, {"ninjutsu": True, "secret": False})
                strategy.boss.config = {"map_region": [0, 0, 1, 1]}
                with patch.object(farm, "one_team_enabled", return_value=False), \
                     patch.object(farm, "boss_swap_enabled", return_value=False):
                    strategy.tick(None, "NR_COMBAT", {"combat_map_index": 9})
                self.assertEqual("boss_swap" in engine.data, swapped)
                self.assertNotIn("nr_strategy_team", [call.args[0]["id"] for call in engine.device.perform.call_args_list])


if __name__ == "__main__":
    unittest.main()
