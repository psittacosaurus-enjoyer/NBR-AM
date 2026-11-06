"""Ninja Road responsiveness: quick transition polling without weaker guards."""
import copy
import unittest
from unittest.mock import patch

import numpy as np

from automation import controller as farm
import test_ninja_road


class ResponsivenessTests(unittest.TestCase):
    screen = test_ninja_road.NinjaRoadTests.screen

    def setUp(self):
        test_ninja_road.NinjaRoadTests.setUp(self)
        self.clock = [10_000.0]
        clock = patch("automation.ninja_road.time.time", side_effect=lambda: self.clock[0])
        clock.start()
        self.addCleanup(clock.stop)
        self.foreground_calls = 0
        original = self.engine.device.foreground

        def foreground():
            self.foreground_calls += 1
            return original()
        self.engine.device.foreground = foreground

    def events(self):
        return [call.args[0] for call in farm.event.call_args_list]

    def test_unknown_inside_battle_grace_polls_quietly_then_starts_ladder(self):
        self.engine.data["run"] = {"id": "attempt", "start": 1, "outcome": None}
        self.screen("UNKNOWN")
        grace = farm.TRANSITION_GRACE_SECONDS["battle"]
        elapsed = 0.0
        while elapsed < grace - 1:
            self.assertEqual(self.engine.tick(), self.engine.UNKNOWN_POLL_SECONDS)
            self.clock[0] += 10
            elapsed += 10
        self.assertNotIn("ninja_recognition_failed", self.events())
        self.assertNotIn("recognition_recovery", self.engine.data)
        self.clock[0] += 2
        self.assertEqual(self.engine.tick(), 5)  # First bounded recovery step.
        self.assertIn("ninja_recognition_failed", self.events())
        self.assertEqual(self.engine.device.actions, [])
        self.assertEqual(self.engine.device.launches, 0)

    def test_pending_tap_uses_pending_grace_and_fast_confirmation_polls(self):
        self.screen("NR_ENTRY", "nr_enter", ["NR_START"], verified_safe=True)
        self.engine.tick()
        self.assertEqual(self.engine.device.actions, ["nr_enter"])
        self.assertEqual(self.engine.tick(), self.engine.PENDING_POLL_SECONDS)  # Same screen, tap in flight.
        self.screen("UNKNOWN")
        self.assertEqual(self.engine.tick(), self.engine.UNKNOWN_POLL_SECONDS)
        self.clock[0] += farm.TRANSITION_GRACE_SECONDS["pending"] - 1
        self.assertEqual(self.engine.tick(), self.engine.UNKNOWN_POLL_SECONDS)
        self.assertNotIn("ninja_recognition_failed", self.events())
        self.screen("NR_START", verified_safe=True)
        self.engine.tick()
        self.assertIsNone(self.engine.data.get("pending"))
        self.assertIn("transition_confirmed", self.events())
        self.assertNotIn("ninja_recognition_recovery_verified", self.events())

    def test_idle_delays_by_phase_and_stamina_wait_unchanged(self):
        self.screen("NR_COMBAT", phase="combat", verified_safe=True)
        self.assertEqual(self.engine.tick(), self.engine.COMBAT_POLL_SECONDS)
        self.screen("NR_ENTRY", verified_safe=True)  # Recognized, no action available.
        self.assertEqual(self.engine.tick(), self.engine.IDLE_POLL_SECONDS)
        self.engine.data["stamina_until"] = self.clock[0] + 300
        self.screen("NR_START", "nr_start", ["NR_TEAM_SELECT"], verified_safe=True)
        self.assertEqual(self.engine.tick(), 30)
        self.assertEqual(self.engine.device.actions, [])

    def test_screen_change_before_dispatch_defers_briefly_then_escalates(self):
        self.screen("NR_ENTRY", "nr_enter", ["NR_START"], verified_safe=True)

        def changed(action, name, spec):
            raise farm.ActionNotDispatched("no input") from farm.ScreenChanged("Screen changed before dispatch")

        with patch.object(self.engine, "_perform_verified_action", side_effect=changed):
            for attempt in range(1, farm.DISPATCH_DEFER_LIMIT + 1):
                self.assertEqual(self.engine.tick(), farm.DISPATCH_DEFER_SECONDS)
                self.assertIsNone(self.engine.data["pending"])
            with self.assertRaises(farm.ActionNotDispatched):
                self.engine.tick()
        self.assertEqual(self.events().count("action_deferred"), farm.DISPATCH_DEFER_LIMIT)
        self.assertIn("action_not_sent", self.events())
        self.assertEqual(self.engine.device.actions, [])

    def test_other_dispatch_failures_still_raise_immediately(self):
        self.screen("NR_ENTRY", "nr_enter", ["NR_START"], verified_safe=True)
        with patch.object(self.engine, "_perform_verified_action",
                          side_effect=farm.ActionNotDispatched("identity mismatch")), \
             self.assertRaises(farm.ActionNotDispatched):
            self.engine.tick()
        self.assertNotIn("action_deferred", self.events())

    def test_foreground_reused_only_on_settled_observation_ticks(self):
        monotonic = [500.0]
        with patch("automation.ninja_road.time.monotonic", side_effect=lambda: monotonic[0]):
            self.screen("NR_COMBAT", phase="combat", verified_safe=True)
            self.engine.tick()
            self.assertEqual(self.foreground_calls, 1)
            for _ in range(3):
                monotonic[0] += 2
                self.engine.tick()
            self.assertEqual(self.foreground_calls, 1)
            monotonic[0] += farm.FOREGROUND_RECHECK_SECONDS
            self.engine.tick()
            self.assertEqual(self.foreground_calls, 2)
            self.screen("UNKNOWN")
            self.engine.tick()                      # Last screen was combat: reused.
            self.engine.tick()                      # Last screen UNKNOWN: checked fresh.
            self.assertEqual(self.foreground_calls, 3)
            self.engine.tick(act=False)             # Status-only ticks never reuse.
            self.assertEqual(self.foreground_calls, 4)

    def test_lost_foreground_is_still_detected_after_reuse_window(self):
        monotonic = [500.0]
        with patch("automation.ninja_road.time.monotonic", side_effect=lambda: monotonic[0]):
            self.screen("NR_COMBAT", phase="combat", verified_safe=True)
            self.engine.tick()
            self.engine.device.package = "com.uncube.launcher3"
            monotonic[0] += farm.FOREGROUND_RECHECK_SECONDS
            self.engine.tick()
        self.assertEqual(self.engine.data["screen"], "GAME_CLOSED")
        self.assertEqual(self.engine.device.actions, [])


class DispatchComparisonTests(unittest.TestCase):
    BASE = {"name": "NR_TEAM_SELECT", "verified_safe": True, "phase": "team_select", "priority": 3,
            "action": {"id": "nr_team", "kind": "tap", "point": [655, 1208], "expected": ["NR_COMBAT"]},
            "anchors": [{"template": "nr_team_select/enabled_glyph.png"}], "notes": "glow phase A",
            "ready_map_index": 4, "available_teams": [1, 2, 3]}

    def test_only_recognition_evidence_may_differ(self):
        pulse = copy.deepcopy(self.BASE)
        pulse.update(anchors=[{"template": "nr_team_select/enabled_pulse_glyph.png"}], notes="glow phase B")
        self.assertEqual(farm._dispatch_fields(pulse), farm._dispatch_fields(self.BASE))
        for key, value in (("action", {**self.BASE["action"], "point": [10, 10]}), ("ready_map_index", 5),
                           ("available_teams", [1, 2]), ("verified_safe", False), ("team_index", 1),
                           ("on_confirm", {"early_exit_requested": True})):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.BASE)
                changed[key] = value
                self.assertNotEqual(farm._dispatch_fields(changed), farm._dispatch_fields(self.BASE))

    def test_stage_engine_dispatch_accepts_glow_variant_and_rejects_other_changes(self):
        engine = object.__new__(farm.Engine)
        engine.stage = {"id": "ninja_road"}
        engine.detector = unittest.mock.Mock(changed=lambda: False)
        sent = []

        class Device:
            input_guard = None

            def perform(self, action):
                self.input_guard()
                sent.append(action["id"])
        engine.device = Device()
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        pulse = copy.deepcopy(self.BASE)
        pulse.update(anchors=[{"template": "other.png"}], notes="glow phase B")
        engine.observe = lambda: (frame, "NR_TEAM_SELECT", copy.deepcopy(pulse), {})
        engine._perform_verified_action(self.BASE["action"], "NR_TEAM_SELECT", copy.deepcopy(self.BASE))
        self.assertEqual(sent, ["nr_team"])
        moved = copy.deepcopy(pulse)
        moved["ready_map_index"] = 5
        engine.observe = lambda: (frame, "NR_TEAM_SELECT", copy.deepcopy(moved), {})
        with self.assertRaises(farm.ScreenChanged):
            engine._perform_verified_action(self.BASE["action"], "NR_TEAM_SELECT", copy.deepcopy(self.BASE))
        self.assertEqual(sent, ["nr_team"])


if __name__ == "__main__":
    unittest.main()


class Stage15SettingGuardTests(unittest.TestCase):
    """Strategy-layer setting taps get a fresh visibility check, and stale latches clear."""

    def make(self, auto_states, setting_values):
        from types import SimpleNamespace
        from automation.ninja_stage15 import Stage15Strategy
        sent = []

        class Device:
            input_guard = None

            def capture(self):
                return np.zeros((1600, 900, 3), dtype=np.uint8)

            def perform(self, action):
                try:
                    if self.input_guard:
                        self.input_guard()
                except Exception as exc:
                    raise farm.ActionNotDispatched(str(exc)) from exc
                sent.append(action["id"])

        engine = SimpleNamespace(exit_mode="stage15_clean", save=lambda: None, device=Device(),
                                 data={"run": {"id": "attempt"}, "current_team": 1})
        strategy = object.__new__(Stage15Strategy)
        strategy.engine = engine
        strategy.vision = unittest.mock.Mock()
        strategy.vision.setting.side_effect = list(setting_values)
        strategy.boss = unittest.mock.Mock()
        strategy.boss.auto.side_effect = list(auto_states)
        return strategy, engine, sent

    def test_setting_tap_refused_when_auto_overlay_gone(self):
        strategy, engine, sent = self.make(auto_states=[False], setting_values=[])
        state = {"pending": None, "settings": {}}
        with patch.object(farm, "event") as event:
            delay = strategy.dispatch(state, {"id": "nr_ninjutsu", "kind": "tap", "point": [159, 1415]},
                                      "setting", setting="ninjutsu", target=False)
        self.assertEqual((delay, sent, state["pending"]), (.25, [], None))
        self.assertEqual(event.call_args.args[0], "ninja_strategy_input_deferred")
        self.assertIsNone(engine.device.input_guard)

    def test_setting_tap_refused_when_value_already_correct_or_unreadable(self):
        for value in (False, None):
            strategy, _, sent = self.make(auto_states=[True], setting_values=[value])
            state = {"pending": None, "settings": {}}
            with patch.object(farm, "event"):
                strategy.dispatch(state, {"id": "nr_ninjutsu", "kind": "tap", "point": [159, 1415]},
                                  "setting", setting="ninjutsu", target=False)
            self.assertEqual(sent, [])

    def test_setting_tap_sent_only_with_visible_wrong_value(self):
        strategy, engine, sent = self.make(auto_states=[True], setting_values=[True])
        state = {"pending": None, "settings": {}}
        strategy.dispatch(state, {"id": "nr_ninjutsu", "kind": "tap", "point": [159, 1415]},
                          "setting", setting="ninjutsu", target=False)
        self.assertEqual(sent, ["nr_ninjutsu"])
        self.assertTrue(state["pending"]["returned"])
        self.assertIsNone(engine.device.input_guard)

    def test_auto_taps_are_not_given_the_setting_guard(self):
        strategy, _, sent = self.make(auto_states=[], setting_values=[])
        state = {"pending": None}
        strategy.auto(state, True)
        self.assertEqual(sent, ["nr_auto"])

    def test_unconfirmable_setting_latch_clears_then_blocks_after_limit(self):
        strategy, engine, _ = self.make(auto_states=[], setting_values=[None] * 10)
        engine.data["ninjutsu_enabled"] = True
        state = {"pending": None, "settings": {"ninjutsu": False}}
        with patch.object(farm, "event") as event:
            for attempt in range(1, strategy.UNVERIFIED_CLEAR_LIMIT + 1):
                state["pending"] = {"purpose": "setting", "setting": "ninjutsu", "target": False,
                                    "time": farm.time.time() - 21, "returned": True}
                self.assertEqual(strategy.confirm(state, None, "NR_TEAM_SELECT", 1), .25)
                self.assertIsNone(state["pending"])
                self.assertNotIn("ninjutsu", state["settings"])
                self.assertIsNone(engine.data["ninjutsu_enabled"])
            self.assertEqual(event.call_args.args[0], "ninja_strategy_unverified_cleared")
            state["pending"] = {"purpose": "setting", "setting": "ninjutsu", "target": False,
                                "time": farm.time.time() - 21, "returned": True}
            self.assertEqual(strategy.confirm(state, None, "NR_TEAM_SELECT", 1), 2)
            self.assertIn("unverified", engine.data["blocked"])
            # A fresh map gets its own allowance.
            self.assertEqual(strategy.confirm(state, None, "NR_TEAM_SELECT", 2), .25)

    def test_team_latch_still_blocks_without_clearing(self):
        strategy, engine, _ = self.make(auto_states=[], setting_values=[])
        state = {"pending": {"purpose": "team", "target": 2, "map": 7, "time": farm.time.time() - 21,
                             "returned": True, "before": None}}
        self.assertEqual(strategy.confirm(state, None, "NR_COMBAT", 7), 2)
        self.assertIsNotNone(state["pending"])
