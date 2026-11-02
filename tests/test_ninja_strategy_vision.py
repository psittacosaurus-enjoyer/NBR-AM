"""Saved visual evidence and explicitly synthetic composition; no game input."""
from unittest import TestCase, main
from unittest.mock import patch

from automation import controller as farm
from automation.ninja_road import NinjaRoadEngine
from automation.ninja_strategy_vision import Stage15Vision
from fixture_images import load_fixture


class Stage15VisionTests(TestCase):
    def setUp(self):
        self.engine = object.__new__(NinjaRoadEngine)
        self.vision = Stage15Vision(self.engine)

    @staticmethod
    def frame(name, folder="ninja_road"):
        return load_fixture(farm.ROOT / "tests/fixtures" / folder / (name + ".png"))

    def counter(self, number):
        # Test-only composition exercises unseen two-digit readings. It is not
        # represented as a live screenshot or calibration for Stage 12..16.
        frame = self.frame("boss_ready", "ninja_boss_swap")
        reference = self.vision._image("assets/patterns/ninja_road/maps/11.png").copy()
        ones = number % 10
        source = 11 if ones == 1 else 10 if ones == 0 else ones
        glyph = self.vision._image("assets/patterns/ninja_road/maps/" + str(source) + ".png")
        reference[7:34, 33:51] = glyph[7:34, 33:51]
        if number < 10:
            reference[7:34, 15:33] = reference[33, 15:33]
        frame[8:46, 245:348] = reference
        return frame

    def test_existing_counter_references_are_reused(self):
        for value in range(2, 12):
            frame = self.frame("boss_ready", "ninja_boss_swap")
            frame[8:46, 245:348] = self.vision._image(
                "assets/patterns/ninja_road/maps/" + str(value) + ".png")
            with self.subTest(value=value):
                self.assertEqual(self.vision.read_map(frame), value)

    def test_real_map_one_and_actual_digit_compositions_through_sixteen(self):
        with patch.object(self.engine, "read_ready_map", return_value=None):
            self.assertEqual(self.vision.read_map(self.frame("nr_combat")), 1)
            for value in range(2, 17):
                with self.subTest(value=value):
                    self.assertEqual(self.vision.read_map(self.counter(value)), value)
            for value in (0, 17, 18, 19):
                with self.subTest(rejected=value):
                    self.assertIsNone(self.vision.read_map(self.counter(value)))

    def test_counter_rejects_missing_suffix_and_other_mission_denominator(self):
        frame = self.counter(16)
        frame[15:42, 295:343] = 0
        self.assertIsNone(self.vision.read_map(frame))
        self.assertIsNone(self.vision.read_map(self.frame("nr_combat_ninjutsu_off")))  # MAP 2/3
        self.assertIsNone(self.vision.read_map(farm.np.zeros((1600, 900, 3), farm.np.uint8)))

    def test_settings_are_observed_independently(self):
        on = self.frame("nr_combat")
        off = self.frame("nr_combat_ninjutsu_off")
        self.assertIs(self.vision.setting(on, "ninjutsu"), True)
        self.assertIs(self.vision.setting(on, "secret"), False)
        self.assertIs(self.vision.setting(off, "ninjutsu"), False)
        self.assertIs(self.vision.setting(off, "secret"), False)
        # The same observed widget's ON lamp transferred to the secret control
        # proves independence, not live secret-technique coverage.
        on[1404:1426, 358:380] = on[1404:1426, 64:86]
        self.assertIs(self.vision.setting(on, "secret"), True)
        on[1404:1426, 64:86] = off[1404:1426, 64:86]
        self.assertIs(self.vision.setting(on, "ninjutsu"), False)
        self.assertIs(self.vision.setting(on, "secret"), True)

    def test_setting_requires_a_recognized_panel_and_lamp(self):
        self.assertIsNone(self.vision.setting(self.frame("boss_manual", "ninja_boss_swap"), "secret"))
        frame = self.frame("nr_combat")
        frame[1404:1426, 64:86] = 0
        self.assertIsNone(self.vision.setting(frame, "ninjutsu"))
        with self.assertRaises(ValueError):
            self.vision.setting(frame, "auto")

    def test_saved_full_empty_and_partial_gauges_are_distinct(self):
        frame = self.frame("boss_ready", "ninja_boss_swap")
        full, empty, partial = [self.vision.chakra(frame, column) for column in range(3)]
        self.assertGreater(full, .75)
        self.assertLess(empty, .10)
        self.assertGreater(partial, .45)
        self.assertLess(partial, full)
        # Turn highlighting changes scale but is not chakra expenditure.
        selected = self.frame("boss_manual", "ninja_boss_swap")
        self.assertLess(abs(self.vision.chakra(selected, 0) - full), .10)

    def test_gauge_is_unknown_when_auto_overlay_or_structure_is_hidden(self):
        for column in range(3):
            self.assertIsNone(self.vision.chakra(self.frame("nr_combat"), column))
        frame = self.frame("boss_ready", "ninja_boss_swap")
        frame[1300:1550] = 0
        self.assertIsNone(self.vision.chakra(frame, 0))
        with self.assertRaises(ValueError):
            self.vision.chakra(frame, 3)

    def test_actual_stage_nine_expenditure_keeps_each_character_identity(self):
        folder = farm.ROOT / "tests/fixtures/ninja_strategy_vision"
        manifest = farm.read_json(folder / "stage9_backline.json")
        x1, y1, x2, y2 = manifest["crop_region"]
        frames = []
        for moment in ("before", "after"):
            frame = farm.np.zeros((1600, 900, 3), farm.np.uint8)
            frame[y1:y2, x1:x2] = load_fixture(folder / ("stage9_backline_" + moment + ".png"))
            frames.append(frame)
        # The small fixtures deliberately omit the unrelated top Auto button.
        # Original source screenshots had Auto OFF; this test exercises actual
        # measured gauges and identity, with no synthesized ring/portrait data.
        with patch.object(self.vision.boss, "auto", return_value=False):
            for column, expected in enumerate(((.902, .230), (.770, .066), (.770, .033))):
                with self.subTest(column=column):
                    before, after = [self.vision.chakra(frame, column) for frame in frames]
                    self.assertAlmostEqual(before, expected[0], delta=.002)
                    self.assertAlmostEqual(after, expected[1], delta=.002)
                    self.assertGreater(before - after, .60)
                    region = self.vision.boss.config["portraits"][column]
                    self.assertTrue(self.vision.boss.same_portrait(*[
                        self.vision.boss.crop(frame, region) for frame in frames]))

    def test_team_panel_excludes_gauge_and_health_changes(self):
        frame = self.frame("boss_ready", "ninja_boss_swap")
        panel = self.vision.team_panel(frame)
        changed = frame.copy()
        changed[1250:1300] = 0
        changed[1347:1380] = 0
        changed[1470:1530, 210:280] = 0
        self.assertTrue(farm.np.array_equal(panel, self.vision.team_panel(changed)))
        changed[1395:1447, 145:205] = 0
        self.assertFalse(farm.np.array_equal(panel, self.vision.team_panel(changed)))


if __name__ == "__main__":
    main()
