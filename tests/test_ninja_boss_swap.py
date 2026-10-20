"""Boss turns and restoration use saved game frames and fake input only."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import farm
from automation.ninja_boss_swap import BossSwap
from automation.ninja_road import NinjaRoadEngine
from fixture_images import load_fixture


class BossSwapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        for item in (patch.object(farm, "RUNTIME", self.runtime),
                     patch.object(farm, "STOP", self.runtime / "STOP"),
                     patch.object(farm, "event"), patch.object(farm, "boss_swap_enabled", return_value=True),
                     patch("automation.ninja_boss_swap.time.sleep")):
            item.start()
            self.addCleanup(item.stop)
        self.engine = SimpleNamespace(data={"run": {"id": "attempt"}, "current_team": 1}, device=Mock())
        self.engine.save = lambda: farm.write_json(self.runtime / "state.json", self.engine.data)
        self.actions = []

        def perform(action):
            saved = json.loads((self.runtime / "state.json").read_text())
            self.assertTrue(saved["boss_swap"]["pending"])
            self.actions.append(copy.deepcopy(action))

        self.engine.device.perform.side_effect = perform
        self.engine.device.capture.return_value = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_combat.png")
        self.boss = BossSwap(self.engine)
        settled = patch.object(self.boss, "settled_active", side_effect=self.boss.active)
        settled.start()
        self.addCleanup(settled.stop)

    def frame(self, name):
        return load_fixture(farm.ROOT / "tests/fixtures/ninja_boss_swap" / (name + ".png"))

    def tick(self, frame, name="NR_COMBAT_AUTO_OFF"):
        return self.boss.tick(frame, name, {})

    def test_real_active_turns_and_boss_counter_are_distinct(self):
        for index in range(3):
            self.assertEqual(self.boss.active(self.frame("active_" + str(index + 1))), index)
        self.assertEqual(self.boss.active(self.frame("active_3_forest")), 2)
        self.assertTrue(self.boss.reference(self.frame("boss_ready"), "boss"))
        self.assertFalse(self.boss.reference(self.frame("after_boss_ready"), "boss"))
        self.assertTrue(self.boss.reference(self.frame("after_boss_ready"), "early_exit_map"))
        self.assertFalse(self.boss.reference(self.frame("boss_ready"), "early_exit_map"))

    def test_only_active_character_is_swiped_and_uncertain_swipe_is_not_replayed(self):
        frame = self.frame("boss_manual")
        self.tick(frame)
        self.assertEqual(self.actions, [self.boss.config["swipes"][2]])
        self.assertEqual(self.engine.data["boss_swap"]["pending"]["column"], 2)
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(len(self.actions), 1)

    def test_restore_all_three_once_with_two_auto_pulses_then_leave_auto_on(self):
        original = self.frame("restored_all")
        swapped = self.frame("after_boss_ready")
        self.engine.data["boss_swap"] = {
            "phase": "restore", "team_key": ["attempt", 1], "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [0, 1, 2], "done": [], "targets": [0, 1, 2], "pending": None,
            "original": {str(i): self.boss.pack(self.boss.crop(original, region))
                         for i, region in enumerate(self.boss.config["portraits"])}}
        done = []
        for column in (2, 0, 1):
            frame = self.frame("active_" + str(column + 1))
            for i in range(3):
                source = original if i in done else swapped
                x1, y1, x2, y2 = self.boss.config["portraits"][i]
                frame[y1:y2, x1:x2] = source[y1:y2, x1:x2]
            self.tick(frame)
            self.assertEqual(self.actions[-1], self.boss.config["swipes"][column])
            x1, y1, x2, y2 = self.boss.config["portraits"][column]
            frame[y1:y2, x1:x2] = original[y1:y2, x1:x2]
            self.tick(frame)
            done.append(column)
            if len(done) < 3:
                self.tick(frame)  # Verify Auto OFF after the immediate ON/OFF pulse.
        self.assertEqual([action["kind"] for action in self.actions],
                         ["swipe", "tap", "tap", "swipe", "tap", "tap", "swipe", "tap"])
        self.tick(self.engine.device.capture.return_value, "NR_COMBAT")
        self.assertNotIn("boss_swap", self.engine.data)

    def _live_flash_restore_pending(self):
        fixtures = farm.ROOT / "tests/fixtures/ninja_boss_swap"
        frame = load_fixture(fixtures / "restore_flash_live.jpg")
        original = load_fixture(fixtures / "restore_flash_original.png", 0)
        before = load_fixture(fixtures / "restore_flash_before.png", 0)
        self.assertIsNotNone(frame)
        self.assertIsNotNone(original)
        self.assertIsNotNone(before)
        state = {
            "phase": "restore", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [0, 1, 2], "done": [], "targets": [0, 1, 2],
            "original": {str(i): self.boss.pack(original) for i in range(3)},
            "pending": {"purpose": "swipe", "time": 0, "column": 0,
                        "before": self.boss.pack(before), "returned": True}}
        self.engine.data["boss_swap"] = state
        return frame, state, original, before

    def test_live_restore_confirms_flash_tinted_original_once_then_advances(self):
        frame, state, original, before = self._live_flash_restore_pending()
        portrait = self.boss.crop(frame, self.boss.config["portraits"][0])
        self.assertFalse(self.boss.matches(portrait, original))
        self.assertTrue(self.boss.changed_portrait(portrait, before))
        self.tick(frame)
        self.assertEqual(state["done"], [0])
        self.assertEqual(state["pending"]["purpose"], "pulse_off")
        self.assertEqual(state["advance_from"], 0)
        self.assertEqual([action["kind"] for action in self.actions], ["tap", "tap"])
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(state["done"], [0])
        self.assertEqual(len(self.actions), 2)  # Neither the swipe nor the pulse repeats.

    def test_changed_but_wrong_restore_portrait_keeps_the_swipe_latched(self):
        frame, state, _, before = self._live_flash_restore_pending()
        x1, y1, x2, y2 = self.boss.config["portraits"][0]
        sx1, sy1, sx2, sy2 = self.boss.config["portraits"][1]
        frame[y1:y2, x1:x2] = frame[sy1:sy2, sx1:sx2].copy()
        self.assertTrue(self.boss.changed_portrait(self.boss.crop(frame, [x1, y1, x2, y2]), before))
        pending = copy.deepcopy(state["pending"])
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(state["pending"], pending)
        self.assertEqual(state["done"], [])
        self.assertEqual(self.actions, [])
        self.assertIn("portrait change not verified", self.engine.data["blocked"])

    def _live_no_effect_swipe_pending(self):
        frame = self.frame("enter_no_effect_live")
        before = load_fixture(farm.ROOT / "tests/fixtures/ninja_boss_swap/enter_no_effect_before.png", 0)
        self.assertIsNotNone(frame)
        self.assertIsNotNone(before)
        state = {
            "phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(frame, self.boss.config["map_region"])),
            "swapped": [2], "done": [2],
            "original": {str(i): self.boss.pack(before) for i in (0, 2)},
            "pending": {"purpose": "swipe", "time": 0, "column": 0,
                        "before": self.boss.pack(before), "returned": True}}
        self.engine.data["boss_swap"] = state
        return frame, state, before

    def test_verified_no_effect_swipe_defers_once_without_replaying_same_actor(self):
        frame, state, before = self._live_no_effect_swipe_pending()
        self.assertEqual(self.boss.active(frame), 0)
        self.assertFalse(self.boss.auto(frame))
        self.assertTrue(self.boss.matches(self.boss.crop(frame, self.boss.config["portraits"][0]), before))
        self.tick(frame)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["rejected_column"], 0)
        self.assertEqual(state["swipe_deferrals"], {"enter:0": 1})
        self.assertEqual(self.actions, [])
        saved = json.loads((self.runtime / "state.json").read_text())["boss_swap"]
        self.assertEqual(saved["swipe_deferrals"], {"enter:0": 1})
        self.assertIsNone(saved["pending"])
        self.tick(frame)
        self.assertEqual(state["pending"]["purpose"], "pulse_off")
        self.assertEqual([action["kind"] for action in self.actions], ["tap", "tap"])
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(len(self.actions), 2)
        self.assertEqual(state["done"], [2])
        self.assertEqual(state["swapped"], [2])
        self.tick(self.frame("active_2"))  # A different actor can proceed normally.
        self.assertEqual(self.actions[-1], self.boss.config["swipes"][1])
        self.assertEqual(state["pending"]["column"], 1)

    def test_no_effect_swipe_keeps_latch_when_deferral_evidence_is_incomplete(self):
        cases = [
            ("no_returned_command", False, False, 0, False),
            ("unknown_auto", True, None, 0, False),
            ("enabled_auto", True, True, 0, False),
            ("unsettled_actor", True, False, None, False),
            ("portrait_drift", True, False, 0, True)]
        for label, returned, auto, actor, drift in cases:
            with self.subTest(label=label):
                frame, state, before = self._live_no_effect_swipe_pending()
                self.actions.clear()
                if not returned:
                    state["pending"].pop("returned")
                if drift:
                    # Preserve the face geometry but fail the strict unchanged proof.
                    glow = farm.cv2.convertScaleAbs(before, alpha=.5, beta=100)
                    self.assertFalse(self.boss.matches(glow, before))
                    self.assertFalse(self.boss.changed_portrait(glow, before))
                    x1, y1, x2, y2 = self.boss.config["portraits"][0]
                    frame[y1:y2, x1:x2] = farm.cv2.cvtColor(glow, farm.cv2.COLOR_GRAY2BGR)
                pending = copy.deepcopy(state["pending"])
                with patch.object(self.boss, "auto", return_value=auto), patch.object(self.boss, "settled_active", return_value=actor):
                    self.tick(frame)
                self.assertEqual(state["pending"], pending)
                self.assertNotIn("rejected_column", state)
                self.assertFalse(state.get("swipe_deferrals"))
                self.assertEqual(self.actions, [])
                self.assertIn("portrait change not verified", self.engine.data["blocked"])

    def test_no_effect_swipe_keeps_latch_after_phase_column_budget_is_spent(self):
        frame, state, _ = self._live_no_effect_swipe_pending()
        state["swipe_deferrals"] = {"enter:0": 1}
        pending = copy.deepcopy(state["pending"])
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(state["pending"], pending)
        self.assertEqual(state["swipe_deferrals"], {"enter:0": 1})
        self.assertEqual(state["done"], [2])
        self.assertEqual(state["swapped"], [2])
        self.assertEqual(self.actions, [])
        self.assertIn("portrait change not verified", self.engine.data["blocked"])

    def test_boss_cleared_early_restores_only_swapped_characters(self):
        self.engine.data["boss_swap"] = {"phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [2], "done": [2],
            "original": {"2": self.boss.pack(self.boss.crop(self.frame("boss_manual"), self.boss.config["portraits"][2]))},
            "pending": None}
        self.tick(self.frame("after_boss_ready"), "NR_TEAM_SELECT")
        self.assertEqual(self.engine.data["boss_swap"]["phase"], "restore")
        self.assertEqual(self.engine.data["boss_swap"]["targets"], [2])
        self.assertEqual(self.actions, [])

    def test_disabled_option_leaves_normal_farming_alone(self):
        with patch.object(farm, "boss_swap_enabled", return_value=False):
            self.assertIsNone(self.tick(self.frame("boss_manual")))
        self.assertEqual(self.actions, [])

    def test_boss_counter_stops_auto_before_ready_when_banner_is_not_visible(self):
        frame = self.frame("boss_ready")
        frame[108:153,119:236] = 0
        x1,y1,x2,y2 = self.boss.config["auto_on"]["region"]
        frame[y1:y2,x1:x2] = self.engine.device.capture.return_value[y1:y2,x1:x2]
        self.tick(frame, "NR_TEAM_SELECT")
        self.assertEqual([a["id"] for a in self.actions], ["nr_auto"])
        self.assertEqual(self.engine.data["boss_swap"]["pending"]["purpose"], "stop_auto")
        self.tick(frame, "NR_TEAM_SELECT")
        self.assertEqual(len(self.actions), 1)  # No Ready before OFF is observed.

    def test_post_boss_ready_stops_auto_before_returning_to_normal_start(self):
        self.tick(self.frame("boss_manual"))
        state = self.engine.data["boss_swap"]
        state.update(phase="boss", pending=None, swapped=[2], done=[2])
        frame = self.frame("after_boss_ready")
        x1,y1,x2,y2 = self.boss.config["auto_on"]["region"]
        frame[y1:y2,x1:x2] = self.engine.device.capture.return_value[y1:y2,x1:x2]
        self.tick(frame, "NR_TEAM_SELECT")
        self.assertEqual(state["phase"], "restore")
        self.assertEqual(self.actions[-1]["id"], "nr_auto")
        self.assertEqual(state["pending"]["purpose"], "stop_auto")

    def test_early_exit_disables_auto_on_ready_before_retreat(self):
        engine = object.__new__(NinjaRoadEngine)
        engine.detector = farm.Detector("ninja_road")
        engine.device = Mock()
        frame = self.frame("after_boss_ready")
        x1,y1,x2,y2 = self.boss.config["auto_on"]["region"]
        frame[y1:y2,x1:x2] = self.engine.device.capture.return_value[y1:y2,x1:x2]
        engine.device.capture.return_value = frame
        with patch.object(farm, "early_exit_enabled", return_value=True):
            _, name, spec, _ = engine.observe()
        self.assertEqual(name, "NR_EARLY_EXIT_PREPARE")
        self.assertEqual(spec["action"]["id"], "nr_auto")
        self.assertEqual(spec["action"]["expected"], ["NR_EARLY_EXIT"])
        self.assertNotIn("team_choices", spec)

    def test_auto_pulse_crossing_map_recovers_only_at_verified_new_ready(self):
        self.tick(self.frame("boss_manual"))
        state = self.engine.data["boss_swap"]
        state["pending"] = {"purpose": "pulse_off", "time": 0}
        frame = self.frame("after_boss_ready")
        x1,y1,x2,y2 = self.boss.config["auto_on"]["region"]
        enabled = self.engine.device.capture.return_value
        frame[y1:y2,x1:x2] = enabled[y1:y2,x1:x2]
        self.tick(frame, "NR_COMBAT")
        self.assertEqual(len(self.actions), 1)  # No blind retry in combat.
        self.tick(frame, "NR_TEAM_SELECT")
        self.assertEqual(self.actions[-1]["id"], "nr_auto")
        self.assertEqual(state["pending"]["purpose"], "stop_auto")

    def test_restore_clears_rejected_actor_from_boss_ending_pulse(self):
        original = self.boss.crop(self.frame("boss_manual"), self.boss.config["portraits"][2])
        state = {
            "phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [2], "done": [2], "original": {"2": self.boss.pack(original)},
            "pending": {"purpose": "pulse_off", "time": 0, "returned": True},
            "rejected_column": 0, "skipped_from": 0, "advanced": True,
            "swipe_deferrals": {"enter:0": 1}}
        self.engine.data["boss_swap"] = state
        self.boss._turn_candidate = (0, 0, 0, 3)
        self.tick(self.frame("after_boss_ready"), "NR_TEAM_SELECT")
        self.assertEqual(state["phase"], "restore")
        self.assertEqual(state["targets"], [2])
        for key in ("rejected_column", "skipped_from", "advanced"):
            self.assertNotIn(key, state.keys())
        self.assertIsNone(self.boss._turn_candidate)
        self.assertEqual(state["swipe_deferrals"], {"enter:0": 1})
        self.assertEqual(self.actions, [])
        # The new map can start on that same actor. It must advance toward the
        # remaining restoration target, rather than inherit the old skip latch.
        self.tick(self.frame("enter_no_effect_live"))
        self.assertEqual([action["kind"] for action in self.actions], ["tap", "tap"])
        self.assertEqual(state["pending"]["purpose"], "pulse_off")

    def test_charge_glow_does_not_count_as_a_changed_character(self):
        before = self.boss.crop(self.frame("boss_manual"), self.boss.config["portraits"][0])
        glow = farm.cv2.convertScaleAbs(before, alpha=.8, beta=30)
        self.assertFalse(self.boss.changed_portrait(glow, before))
        other = self.boss.crop(self.frame("after_boss_ready"), self.boss.config["portraits"][0])
        self.assertTrue(self.boss.changed_portrait(other, before))

    def test_active_turn_must_settle_across_three_observations(self):
        boss = BossSwap(self.engine)
        with patch("automation.ninja_boss_swap.time.monotonic", side_effect=[0, 1, 3, 4, 5, 7]):
            self.assertIsNone(boss.settled_active(self.frame("active_1")))
            self.assertIsNone(boss.settled_active(self.frame("active_1")))
            self.assertEqual(boss.settled_active(self.frame("active_1")), 0)
            self.assertIsNone(boss.settled_active(self.frame("active_2")))
            self.assertIsNone(boss.settled_active(self.frame("active_2")))
            self.assertEqual(boss.settled_active(self.frame("active_2")), 1)

    def test_gold_band_without_active_outer_frame_is_rejected(self):
        frame = self.frame("active_3")
        frame[1294:1317,695:875] = 0
        frame[1294:1317,105:114] = (40, 210, 255)  # A bright terrain strip, no scroll frame.
        self.assertIsNone(self.boss.active(frame))

    def test_unrelated_gold_band_does_not_hide_the_active_outer_frame(self):
        frame = self.frame("active_3")
        frame[1312:1317, 155:205] = (40, 210, 255)
        self.assertEqual(self.boss.active(frame), 2)

    def test_two_qualified_outer_frames_are_ambiguous(self):
        frame = self.frame("active_3")
        first = self.frame("active_1")
        frame[1270:1460, :320] = first[1270:1460, :320]
        self.assertIsNone(self.boss.active(frame))

    def _identical_pair_frame(self, name="identical_pair"):
        frame = self.frame(name)
        # The live map-five boss header has a different backdrop. Establish an
        # already active swap session; these cases exercise its portrait guards.
        self.engine.data["boss_swap"] = {
            "phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(frame, self.boss.config["map_region"])),
            "swapped": [], "done": [], "original": {}, "pending": None}
        return frame

    def test_identical_art_attempts_one_swipe_in_each_phase_without_latching(self):
        frame = self._identical_pair_frame()
        self.assertTrue(self.boss.identical_pair(frame, 0, self.boss.elements(frame, 0)))
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        state.update(done=[1, 2], swapped=[1, 2])
        self.assertEqual([a["kind"] for a in self.actions], ["swipe"])
        self.tick(frame)
        self.assertEqual(state["done"], [1, 2, 0])
        self.assertEqual(state["swapped"], [1, 2, 0])
        self.assertEqual(state["indistinguishable"], [0])
        self.assertEqual(state["pending"]["purpose"], "resume_auto")
        self.tick(frame)
        self.assertEqual(len(self.actions), 2)
        state.update(phase="restore", done=[], targets=[0], pending=None, advance=False)
        self.tick(frame)
        self.assertEqual(self.actions[-1]["kind"], "swipe")
        self.assertEqual(state["done"], [])  # Same art must not skip restoration.
        self.tick(frame)
        self.assertEqual(state["done"], [0])
        self.assertEqual([a["kind"] for a in self.actions], ["swipe", "tap", "swipe", "tap"])

    def test_legacy_identical_art_recovers_a_returned_swipe_after_deferral_budget(self):
        frame = self._identical_pair_frame()
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        state.update(done=[1, 2], swapped=[1, 2], swipe_deferrals={"enter:0": 1})
        state["pending"].pop("reserve_before")
        state["pending"].pop("elements_before")
        state["pending"]["time"] = 0
        self.engine.data["blocked"] = "Boss Swap: portrait change not verified; swipe remains latched"
        self.tick(frame)
        self.assertEqual(state["done"], [1, 2, 0])
        self.assertIsNone(self.engine.data["blocked"])
        self.assertEqual(sum(a["kind"] == "swipe" for a in self.actions), 1)
        farm.event.assert_any_call("ninja_boss_swap_visually_indistinguishable", column=1,
                                   phase="enter", legacy_guard=True)

    def test_identical_art_cannot_clear_an_uncertain_unreturned_swipe(self):
        frame = self._identical_pair_frame()
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        state["pending"].pop("returned")
        state["pending"]["time"] = 0
        pending = copy.deepcopy(state["pending"])
        self.tick(frame)
        self.tick(frame)
        self.assertEqual(state["pending"], pending)
        self.assertEqual(state["done"], [])
        self.assertEqual([a["kind"] for a in self.actions], ["swipe"])

    def test_charged_identical_portraits_attempt_once_and_restore_without_stalling(self):
        frame = self._identical_pair_frame("identical_charge")
        face = self.boss.crop(frame, self.boss.config["portraits"][0])
        correlation, _ = self.boss.face_fit(face, self.boss.reserve(frame, 0))
        self.assertLess(correlation, .93)  # The live charge flare defeated this guard.
        self.assertTrue(self.boss.identical_pair(frame, 0, self.boss.elements(frame, 0)))
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        self.tick(frame)
        self.assertEqual(state["done"], [0])
        self.assertEqual(state["swapped"], [0])
        self.assertEqual(state["indistinguishable"], [0])
        state.update(phase="restore", done=[], targets=[0], pending=None, advance=False)
        self.tick(frame)
        self.assertEqual(self.actions[-1]["kind"], "swipe")
        self.tick(frame)
        self.assertEqual(state["done"], [0])
        self.assertEqual(sum(a["kind"] == "swipe" for a in self.actions), 2)

    def test_interior_face_fallback_rejects_other_portrait_art(self):
        for name in ("boss_manual", "active_1", "after_boss_ready", "enter_no_effect_live"):
            with self.subTest(frame=name):
                frame = self.frame(name)
                self.assertFalse(self.boss.identical_pair(frame, 0, self.boss.elements(frame, 0)))

    def _buff_restore_pending(self):
        fixtures = farm.ROOT / "tests/fixtures/ninja_boss_swap"
        state = farm.read_json(fixtures / "identical_buff_restore_state.json")
        state["team_key"] = ["attempt", 1]
        self.engine.data["boss_swap"] = state
        self.engine.data["blocked"] = "Boss Swap: portrait change not verified; swipe remains latched"
        return self.frame("identical_buff_restore"), state

    def test_saved_identical_restore_survives_buff_glow_and_unreadable_badge(self):
        frame, state = self._buff_restore_pending()
        elements = self.boss.elements(frame, 0)
        self.assertIsNone(elements[0])
        self.assertFalse(self.boss.identical_pair(frame, 0, elements))
        pending = state["pending"]
        self.assertTrue(self.boss.matches(self.boss.crop(frame, self.boss.config["portraits"][0]),
                                         self.boss.unpack(pending["before"])))
        self.assertTrue(self.boss.matches(self.boss.reserve(frame, 0), self.boss.unpack(pending["reserve_before"])))
        self.tick(frame)
        self.assertEqual(state["done"], [2, 1, 0])
        self.assertIsNone(self.engine.data["blocked"])
        self.assertEqual([a["kind"] for a in self.actions], ["tap"])
        self.assertEqual(state["pending"]["purpose"], "resume_auto")
        farm.event.assert_any_call("ninja_boss_swap_visually_indistinguishable", column=1,
                                   phase="restore", legacy_guard=False, proof="persisted_roles")
        self.tick(self.engine.device.capture.return_value, "NR_COMBAT")
        self.assertNotIn("boss_swap", self.engine.data)

    def test_saved_identical_restore_rejects_incomplete_or_contradictory_proof(self):
        for case in ("wrong_phase", "wrong_column", "unreturned", "missing_original_badges",
                     "unknown_original_badge", "unknown_pending_badge", "different_saved_badge",
                     "wrong_current_badge", "wrong_front", "wrong_reserve", "wrong_original",
                     "unsettled_actor"):
            with self.subTest(case=case):
                frame, state = self._buff_restore_pending()
                pending = state["pending"]
                if case == "wrong_phase":
                    state["phase"] = "enter"
                elif case == "wrong_column":
                    state["indistinguishable"] = [1]
                elif case == "unreturned":
                    pending.pop("returned")
                elif case == "missing_original_badges":
                    state.pop("original_elements")
                elif case == "unknown_original_badge":
                    state["original_elements"]["0"][0] = None
                elif case == "unknown_pending_badge":
                    pending["elements_before"][0] = None
                elif case == "different_saved_badge":
                    pending["elements_before"] = [105, 105]
                elif case == "wrong_current_badge":
                    self._paint_element(frame, True, 105)
                elif case == "wrong_front":
                    frame[1384:1461, 132:217] = self.frame("after_boss_ready")[1384:1461, 427:512]
                elif case == "wrong_reserve":
                    frame[1450:1535, 20:130] = self.frame("after_boss_ready")[1450:1535, 315:425]
                elif case == "wrong_original":
                    state["original"]["0"] = self.boss.pack(self.boss.crop(self.frame("after_boss_ready"), self.boss.config["portraits"][1]))
                saved_pending = copy.deepcopy(pending)
                with patch.object(self.boss, "settled_active", return_value=None if case == "unsettled_actor" else 0):
                    self.tick(frame)
                self.assertEqual(state["done"], [2, 1])
                self.assertEqual(state["pending"], saved_pending)
                self.assertEqual(self.actions, [])

    def test_saved_identical_restore_settles_across_animated_buff_phases(self):
        bright, state = self._buff_restore_pending()
        shifted = self.frame("identical_buff_restore_shift")
        before = self.boss.unpack(state["pending"]["before"])
        portrait = self.boss.crop(shifted, self.boss.config["portraits"][0])
        self.assertFalse(self.boss.matches(portrait, before))
        self.assertTrue(self.boss.same_portrait(portrait, before))
        settled = BossSwap.settled_active.__get__(self.boss)
        with patch.object(self.boss, "settled_active", side_effect=settled), \
             patch("automation.ninja_boss_swap.time.monotonic", side_effect=[0, 1.5, 3]):
            self.tick(bright)
            self.assertEqual(state["done"], [2, 1])
            self.tick(shifted)
            self.assertEqual(state["done"], [2, 1])
            self.tick(bright)
        self.assertEqual(state["done"], [2, 1, 0])
        self.assertEqual(state["pending"]["purpose"], "resume_auto")
        self.assertEqual([action["kind"] for action in self.actions], ["tap"])

    def test_identical_restore_ignores_changed_reserve_rim_and_gauge(self):
        before = self._identical_pair_frame()
        fixtures = farm.ROOT / "tests/fixtures/ninja_boss_swap"
        saved_reserve = load_fixture(fixtures / "identical_reserve_before.png", 0)
        live_reserve = load_fixture(fixtures / "identical_reserve_after.png", 0)
        self.assertFalse(self.boss.matches(live_reserve, saved_reserve))
        before[1450:1535, 20:130] = farm.cv2.cvtColor(saved_reserve, farm.cv2.COLOR_GRAY2BGR)
        state = self.engine.data["boss_swap"]
        state.update(phase="restore", swapped=[0], targets=[0], indistinguishable=[0],
                     original={"0": self.boss.pack(self.boss.crop(before, self.boss.config["portraits"][0]))})
        self.tick(before)
        self.assertEqual(self.actions[-1]["kind"], "swipe")
        after = before.copy()
        after[1450:1535, 20:130] = farm.cv2.cvtColor(live_reserve, farm.cv2.COLOR_GRAY2BGR)
        self.tick(after)
        self.assertEqual(state["done"], [0])
        self.assertEqual(state["pending"]["purpose"], "resume_auto")
        self.assertEqual(sum(a["kind"] == "swipe" for a in self.actions), 1)

    def _paint_element(self, frame, reserve, hue):
        x, y = (31, 1418) if reserve else (86, 1338)
        hsv = farm.np.full((16, 16, 3), (hue, 220, 220), farm.np.uint8)
        frame[y-8:y+8, x-8:x+8] = farm.cv2.cvtColor(hsv, farm.cv2.COLOR_HSV2BGR)

    def test_same_art_different_elements_requires_both_roles_to_invert(self):
        frame = self._identical_pair_frame()
        self._paint_element(frame, True, 105)
        self.assertFalse(self.boss.identical_pair(frame, 0, self.boss.elements(frame, 0)))
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        before = list(state["pending"]["elements_before"])
        partial = frame.copy()
        self._paint_element(partial, False, 105)
        self.tick(partial)
        self.assertEqual(state["done"], [])
        self.assertEqual(state["pending"]["purpose"], "swipe")
        after = partial.copy()
        self._paint_element(after, True, 30)
        self.tick(after)
        self.assertEqual(state["done"], [0])
        self.assertEqual(state["swapped"], [0])
        self.assertEqual(state["original_elements"]["0"], before)
        self.assertFalse(state.get("indistinguishable"))
        state.update(phase="restore", done=[], targets=[0], pending=None, advance=False)
        self.tick(after)
        self.assertEqual(self.actions[-1]["kind"], "swipe")
        self.tick(frame)
        self.assertEqual(state["done"], [0])

    def test_unreadable_element_is_not_persisted_as_a_required_identity(self):
        frame = self.frame("boss_manual")
        with patch.object(self.boss, "elements", return_value=[None, 30]):
            self.tick(frame)
        state = self.engine.data["boss_swap"]
        self.assertNotIn("elements_before", state["pending"])
        self.assertNotIn("2", state.get("original_elements", {}))

    def _similar_pair_frames(self):
        before = self._identical_pair_frame()
        original = self.boss.crop(before, self.boss.config["portraits"][0])
        similar = farm.cv2.convertScaleAbs(original, alpha=.9, beta=40)
        self.assertFalse(self.boss.changed_portrait(similar, original))
        self.assertFalse(self.boss.matches(similar, original))
        # Give the reserve the alternate portrait at its observed scale. The
        # after frame then exchanges both roles without changing their badges.
        alternate = farm.cv2.resize(similar, None, fx=.64, fy=.64, interpolation=farm.cv2.INTER_AREA)
        normal = farm.cv2.resize(original, None, fx=.64, fy=.64, interpolation=farm.cv2.INTER_AREA)
        y, x = 1464, 49
        height, width = alternate.shape
        before[y:y+height, x:x+width] = farm.cv2.cvtColor(alternate, farm.cv2.COLOR_GRAY2BGR)
        after = before.copy()
        x1, y1, x2, y2 = self.boss.config["portraits"][0]
        after[y1:y2, x1:x2] = farm.cv2.cvtColor(similar, farm.cv2.COLOR_GRAY2BGR)
        one_sided = after.copy()
        after[y:y+height, x:x+width] = farm.cv2.cvtColor(normal, farm.cv2.COLOR_GRAY2BGR)
        return before, after, one_sided

    def test_similar_art_same_element_is_verified_by_both_portrait_roles(self):
        before, after, _ = self._similar_pair_frames()
        self.tick(before)
        state = self.engine.data["boss_swap"]
        self.tick(after)
        self.assertEqual(state["done"], [0])
        self.assertEqual(state["swapped"], [0])
        self.assertFalse(state.get("indistinguishable"))

    def test_similar_art_one_sided_change_does_not_prove_pair_inversion(self):
        before, _, one_sided = self._similar_pair_frames()
        self.tick(before)
        state = self.engine.data["boss_swap"]
        pending = copy.deepcopy(state["pending"])
        self.tick(one_sided)
        self.assertEqual(state["done"], [])
        self.assertEqual(state["pending"], pending)
        self.assertEqual([a["kind"] for a in self.actions], ["swipe"])

    def test_cached_reference_reloads_after_the_file_changes(self):
        frame = self.frame("boss_ready")
        path = self.runtime / "boss.png"
        patch_image = self.boss.crop(frame, self.boss.config["boss"]["region"])
        farm.cv2.imwrite(str(path), patch_image)
        self.boss.config["boss"]["template"] = str(path)
        original_read = farm.cv2.imread
        with patch.object(farm.cv2, "imread", wraps=original_read) as read:
            self.assertTrue(self.boss.reference(frame, "boss"))
            self.assertTrue(self.boss.reference(frame, "boss"))
            self.assertEqual(read.call_count, 1)
            farm.cv2.imwrite(str(path), farm.np.zeros_like(patch_image))
            self.assertFalse(self.boss.reference(frame, "boss"))
            self.assertEqual(read.call_count, 2)

    def test_settled_boss_phase_does_not_recheck_map_auto_or_boss_art(self):
        frame = self.frame("boss_manual")
        self.tick(frame)
        self.engine.data["boss_swap"].update(phase="boss", pending=None)
        with patch.object(self.boss, "reference", side_effect=AssertionError("Unexpected template work")):
            self.assertIsNone(self.tick(frame))

    def test_stats_panel_closes_once_and_proven_rejected_swipe_is_rechecked(self):
        frame = self.frame("boss_manual")
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        info = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_character_info.png")
        name, spec, _ = farm.Detector("ninja_road").classify(info)
        self.assertEqual(name, "NR_CHARACTER_INFO")
        self.boss.tick(info, name, spec)
        self.assertEqual(self.actions[-1]["id"], "nr_close_info")
        self.boss.tick(info, name, spec)
        self.assertEqual(len(self.actions), 2)
        self.tick(frame)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["rejected_column"], 2)
        self.assertEqual(state["done"], [])
        self.tick(frame)
        self.assertEqual([a["kind"] for a in self.actions], ["swipe", "tap", "tap", "tap"])

    def test_auto_pulse_cannot_be_skipped_when_active_marker_changes(self):
        self.engine.data["boss_swap"] = {"phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [0], "done": [0], "original": {}, "pending": None,
            "advance": True, "advance_from": 0, "wait_since": 0}
        self.tick(self.frame("active_2"))
        self.assertEqual([a["kind"] for a in self.actions], ["tap", "tap"])
        self.assertEqual(self.engine.data["boss_swap"]["pending"]["purpose"], "pulse_off")

    def test_confirmed_swap_advances_without_waiting_for_another_marker(self):
        self.engine.data["boss_swap"] = {"phase": "enter", "team_key": ["attempt", 1],
            "boss_map": self.boss.pack(self.boss.crop(self.frame("boss_ready"), self.boss.config["map_region"])),
            "swapped": [0], "done": [0], "original": {}, "pending": None,
            "advance": True, "advance_from": 0, "wait_since": 0}
        with patch.object(self.boss, "settled_active", return_value=None) as marker:
            self.tick(self.frame("active_1"))
        marker.assert_not_called()
        self.assertEqual([a["kind"] for a in self.actions], ["tap", "tap"])
        self.assertEqual(self.engine.data["boss_swap"]["pending"]["purpose"], "pulse_off")

    def test_next_ready_with_zero_swaps_still_requires_auto_off(self):
        self.tick(self.frame("boss_manual"))
        state = self.engine.data["boss_swap"]
        state.update(phase="boss", pending=None, swapped=[], done=[])
        frame = self.frame("after_boss_ready")
        x1,y1,x2,y2 = self.boss.config["auto_on"]["region"]
        frame[y1:y2,x1:x2] = self.engine.device.capture.return_value[y1:y2,x1:x2]
        self.assertIsNotNone(self.tick(frame, "NR_TEAM_SELECT"))
        self.assertEqual(state["pending"]["purpose"], "stop_auto")
        count = len(self.actions)
        self.assertIsNone(self.tick(self.frame("after_boss_ready"), "NR_TEAM_SELECT"))
        self.assertEqual(len(self.actions), count)  # Auto stays OFF for normal Ready.
        self.assertNotIn("boss_swap", self.engine.data)

    def test_rejected_auto_on_does_not_latch_turn_advance_bookkeeping(self):
        self.tick(self.frame("boss_manual"))
        state = self.engine.data["boss_swap"]
        state.update(pending=None, advanced=True, skipped_from=2)
        self.engine.device.perform.side_effect = farm.ActionNotDispatched("No input sent")
        with self.assertRaises(farm.ActionNotDispatched):
            self.boss.pulse(state)
        self.assertIsNone(state["pending"])
        self.assertNotIn("advanced", state)
        self.assertNotIn("skipped_from", state)

    def test_auto_on_retry_requires_returned_command_and_is_bounded(self):
        frame = self.frame("boss_manual")
        self.tick(frame)
        state = self.engine.data["boss_swap"]
        state["pending"] = {"purpose": "pulse_on", "time": 0}
        self.tick(frame)
        self.assertEqual(len(self.actions), 1)  # Uncertain command is never replayed.
        state["pending"]["returned"] = True
        self.engine.device.capture.return_value = frame
        self.tick(frame)
        self.assertEqual(len(self.actions), 2)
        self.assertEqual(state["pending"]["retry"], 1)
        self.assertTrue(state["pending"]["returned"])
        state["pending"]["time"] = 0
        self.tick(frame)
        self.assertEqual(len(self.actions), 2)
        self.assertIn("retry was not verified", self.engine.data["blocked"])

    def test_device_swipes_are_restricted_to_ninja_portraits(self):
        device = object.__new__(farm.Device)
        device.verify = Mock()
        device.foreground = Mock(return_value=farm.PACKAGE)
        device._input = Mock()
        device.perform(self.boss.config["swipes"][0])
        self.assertEqual(device._input.call_args.args[0][:3], ["shell", "input", "swipe"])
        device._input.reset_mock()
        for bad in ({**self.boss.config["swipes"][0], "id": "start_mission"},
                    {**self.boss.config["swipes"][0], "end": [350, 1490]},
                    {**self.boss.config["swipes"][0], "start": [180, 100]}):
            with self.assertRaises(farm.ActionNotDispatched):
                device.perform(bad)
        device._input.assert_not_called()

    def test_early_exit_is_only_decorated_on_map_eleven_ready(self):
        engine = object.__new__(NinjaRoadEngine)
        engine.detector = farm.Detector("ninja_road")
        engine.device = Mock()
        engine.device.capture.return_value = self.frame("after_boss_ready")
        with patch.object(farm, "early_exit_enabled", return_value=False):
            self.assertEqual(engine.observe()[1], "NR_TEAM_SELECT")
        with patch.object(farm, "early_exit_enabled", return_value=True):
            _, name, spec, _ = engine.observe()
            self.assertEqual(name, "NR_EARLY_EXIT")
            self.assertEqual(spec["action"]["id"], "nr_retreat")
            engine.device.capture.return_value = self.frame("boss_ready")
            self.assertEqual(engine.observe()[1], "NR_TEAM_SELECT")

    def test_option_settings_preserve_existing_stamina_preferences(self):
        path = self.runtime / "manager_settings.json"
        farm.write_json(path, {"stamina_wait_minutes": 20})
        farm.set_boss_swap(True)
        farm.set_early_exit(True)
        self.assertEqual(farm.read_json(path), {"stamina_wait_minutes": 20, "ninja_boss_swap": True,
                                               "ninja_early_exit": True})
        farm.set_early_exit(False)
        self.assertFalse(farm.early_exit_enabled())
        self.assertTrue(farm.read_json(path)["ninja_boss_swap"])


if __name__ == "__main__":
    unittest.main()
