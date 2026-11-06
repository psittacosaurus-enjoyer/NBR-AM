"""Lucky Drop medallion: bounded reveal wait, then counted as one drop. Offline only."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from automation import controller as farm
from item_recognition import reward_layout
from item_recognition.recognizer import ItemRecognizer

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets/item_recognition"
PENDING = ROOT / "tests/fixtures/item_recognition/bravery_lucky_unrevealed.png"
REVEALED = ROOT / "tests/fixtures/item_recognition/bravery_lucky_revealed.png"


class LuckyDropTests(unittest.TestCase):
    def setUp(self):
        self.now = 5_000.0
        self.events = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.patches = [
            patch.object(farm.time, "time", side_effect=lambda: self.now),
            patch.object(farm, "event", side_effect=lambda kind, **v: self.events.append(kind)),
            patch.object(farm, "RUNTIME", Path(temp.name)),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def engine(self):
        engine = farm.Engine.__new__(farm.Engine)
        engine.stage = {"id": "universal"}
        engine.data = {"run": {"id": "run-1", "start": 0, "outcome": "completed"}}
        engine.save = Mock()
        engine.loot_collector = Mock()
        engine.loot_collector.health.return_value = None
        engine.loot_collector.resume_pending.return_value = []
        engine.loot_collector.evidence.return_value = True
        return engine

    def test_labels_distinguish_the_lucky_medallion(self):
        self.assertEqual(reward_layout.unrevealed_labels(cv2.imread(str(PENDING)), ASSETS),
                         [reward_layout.LUCKY_DROP_LABEL])
        self.assertEqual(reward_layout.unrevealed_labels(cv2.imread(str(REVEALED)), ASSETS), [])

    def test_medallion_that_flips_is_saved_after_the_flip(self):
        engine = self.engine()
        engine._loot_observe(cv2.imread(str(PENDING)), "READY_TO_RETRY")
        self.assertTrue(engine._loot_waiting_for_reveal)
        self.now += 2
        final = cv2.imread(str(REVEALED))
        engine._loot_observe(final, "READY_TO_RETRY")
        engine.loot_collector.evidence.assert_called_once()
        np.testing.assert_array_equal(engine.loot_collector.evidence.call_args.args[1], final)

    def test_medallion_that_stays_is_saved_after_the_bounded_wait(self):
        engine = self.engine()
        frame = cv2.imread(str(PENDING))
        engine._loot_observe(frame, "READY_TO_RETRY")
        self.now += farm.LUCKY_DROP_SETTLE_SECONDS - 1
        engine._loot_observe(frame, "READY_TO_RETRY")
        engine.loot_collector.evidence.assert_not_called()
        self.now += 2
        engine._loot_observe(frame, "READY_TO_RETRY")
        self.assertFalse(engine._loot_waiting_for_reveal)
        engine.loot_collector.evidence.assert_called_once()
        self.assertIn("reward_reveal_settled", self.events)

    def test_other_reward_backs_get_the_longer_limit(self):
        engine = self.engine()
        frame = cv2.imread(str(PENDING))
        with patch.object(reward_layout, "unrevealed_labels", return_value=["green sealed package"]):
            engine._loot_observe(frame, "READY_TO_RETRY")
            self.now += farm.LUCKY_DROP_SETTLE_SECONDS + 1
            engine._loot_observe(frame, "READY_TO_RETRY")
            engine.loot_collector.evidence.assert_not_called()
            self.now += farm.REWARD_REVEAL_LIMIT_SECONDS
            engine._loot_observe(frame, "READY_TO_RETRY")
        engine.loot_collector.evidence.assert_called_once()

    def test_recognizer_counts_a_settled_medallion_as_lucky_drop(self):
        recognizer = ItemRecognizer.__new__(ItemRecognizer)
        recognizer.profile = reward_layout.json.loads((ASSETS / "profiles.json").read_text("utf-8"))
        recognizer.templates_dir = ASSETS
        recognizer.match_icon = lambda tile: {"identity_status": "unknown", "item_id": None,
                                              "item_name": None, "match_score": None, "candidates": []}
        rgb = cv2.cvtColor(cv2.imread(str(PENDING)), cv2.COLOR_BGR2RGB)
        found = {tuple(box): (kind, identity) for box, kind, identity in recognizer._grid(rgb)}
        kind, identity = found[(381, 411, 134, 139)]
        self.assertEqual(kind, "icon")
        self.assertEqual((identity["item_id"], identity["item_name"], identity["identity_status"]),
                         ("lucky_drop", "Lucky Drop", "matched"))


if __name__ == "__main__":
    unittest.main()
