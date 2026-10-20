"""Map recognition snapshot/reload checks; no device or user runtime access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from automation.ninja_road import NinjaRoadEngine
from fixture_images import load_fixture


class ReadyMapSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        root_patch = patch.object(farm, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.config = self.root / "assets/patterns/ninja_road/map_progress.json"
        self.config.parent.mkdir(parents=True)
        self.reference = self.root / "map.png"
        self.portrait = farm.np.random.default_rng(10).integers(0, 256, (12, 20), dtype=farm.np.uint8)
        farm.cv2.imwrite(str(self.reference), self.portrait)
        self.write_config({"5": "map.png"})
        self.frame = farm.cv2.cvtColor(self.portrait, farm.cv2.COLOR_GRAY2BGR)
        self.engine = object.__new__(NinjaRoadEngine)

    def write_config(self, references):
        self.config.write_text(json.dumps({"region": [0, 0, 20, 12], "references": references}))

    def test_reuses_decoded_references_but_reloads_catalog_and_images(self):
        with patch.object(farm.cv2, "imread", wraps=farm.cv2.imread) as read:
            self.assertEqual(self.engine.read_ready_map(self.frame), 5)
            self.assertEqual(self.engine.read_ready_map(self.frame.copy()), 5)
            self.assertEqual(read.call_count, 1)
            self.write_config({"10": "map.png"})
            self.assertEqual(self.engine.read_ready_map(self.frame), 10)
            self.assertEqual(read.call_count, 2)
            farm.cv2.imwrite(str(self.reference), 255 - self.portrait)
            self.assertIsNone(self.engine.read_ready_map(self.frame))
            self.assertEqual(read.call_count, 3)

    def test_deleted_cached_reference_fails_closed(self):
        self.assertEqual(self.engine.read_ready_map(self.frame), 5)
        self.reference.unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing Ninja Road map reference"):
            self.engine.read_ready_map(self.frame)

    def test_reference_changed_while_loading_never_publishes_mixed_cache(self):
        read_image = farm.cv2.imread

        def replacing_read(path, mode):
            result = read_image(path, mode)
            farm.cv2.imwrite(str(self.reference), 255 - self.portrait)
            return result

        with patch.object(farm.cv2, "imread", side_effect=replacing_read):
            with self.assertRaisesRegex(RuntimeError, "changed during recognition"):
                self.engine.read_ready_map(self.frame)
        self.assertFalse(hasattr(self.engine, "_ready_map_cache"))

    def test_ambiguous_and_invalid_reference_lists_are_unknown(self):
        self.write_config({"5": "map.png", "10": ["map.png"]})
        self.assertIsNone(self.engine.read_ready_map(self.frame))
        self.write_config({"5": None})
        self.assertIsNone(self.engine.read_ready_map(self.frame))


class ReadyAnimationTests(unittest.TestCase):
    """An enabled Ready pulse must stay distinct from the disabled gray label."""

    @classmethod
    def setUpClass(cls):
        cls.detector = farm.Detector("ninja_road")
        cls.folder = farm.ROOT / "tests/fixtures/ninja_ready_detection"

    def assert_ready(self, frame):
        name, spec, _ = self.detector.classify(frame)
        self.assertEqual(name, "NR_TEAM_SELECT")
        self.assertEqual(spec["action"]["id"], "nr_team")
        self.assertEqual(spec["action"]["point"], [655, 1208])

    def test_every_saved_stall_pulse_and_independent_ready_frames_are_recognized(self):
        frame = load_fixture(self.folder / "map_5_enabled_ready.png")
        pulses = load_fixture(self.folder / "enabled_pulse_glyphs.png", farm.cv2.IMREAD_GRAYSCALE)
        self.assertEqual(pulses.shape, (13 * 34, 67))
        self.assert_ready(frame)
        for index in range(13):
            with self.subTest(pulse=index):
                frame[1190:1224, 558:625] = farm.cv2.cvtColor(
                    pulses[index * 34:(index + 1) * 34], farm.cv2.COLOR_GRAY2BGR)
                self.assert_ready(frame)
        for reference in ("ninja_road/nr_team_select.png", "ninja_boss_swap/boss_ready.png",
                          "ninja_boss_swap/after_boss_ready.png", "ninja_ready_progress/map_9_ready.png",
                          "ninja_ready_progress/map_10_ready.png"):
            with self.subTest(reference=reference):
                self.assert_ready(load_fixture(farm.ROOT / "tests/fixtures" / reference))

    def test_disabled_ready_combat_and_modal_overlays_do_not_become_enabled_ready(self):
        for filename in ("nr_team_defeated", "nr_team_defeated_2", "nr_all_defeated",
                         "nr_combat", "nr_combat_ninjutsu_off", "nr_character_info",
                         "nr_communication_error", "nr_retreat_confirm"):
            with self.subTest(frame=filename):
                frame = load_fixture(farm.ROOT / "tests/fixtures/ninja_road" / (filename + ".png"))
                self.assertNotEqual(self.detector.classify(frame)[0], "NR_TEAM_SELECT")
        # The same enabled layout with a disabled button must fail even though
        # the disabled and glowing glyph outlines correlate very strongly.
        enabled = load_fixture(self.folder / "map_5_enabled_ready.png")
        disabled = load_fixture(farm.ROOT / "tests/fixtures/ninja_road/nr_team_defeated.png")
        enabled[1190:1224, 558:625] = disabled[1190:1224, 558:625]
        self.assertNotEqual(self.detector.classify(enabled)[0], "NR_TEAM_SELECT")

    def test_each_positive_ready_guard_is_required_and_character_art_is_irrelevant(self):
        source = load_fixture(self.folder / "map_5_enabled_ready.png")
        for region in ((288, 1546, 615, 1578), (454, 1170, 509, 1248), (558, 1190, 625, 1224)):
            with self.subTest(missing_guard=region):
                frame = source.copy()
                x1, y1, x2, y2 = region
                frame[y1:y2, x1:x2] = 0
                self.assertNotEqual(self.detector.classify(frame)[0], "NR_TEAM_SELECT")
        source[1270:1545] = 0
        self.assert_ready(source)
