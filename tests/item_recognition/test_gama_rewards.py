"""Current acquired Gama-chan cards, including Millionaire's animated glow."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image
from game_library import library
from item_recognition.recognizer import ItemRecognizer
from loot_tracking import LootStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/item_recognition'
MILLIONAIRE = 'item_948ed64af6d887bff7cece0f'


class GamaRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nbr-gama-rewards-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.manifest = json.loads((FIXTURES / 'gama_references.json').read_text('utf-8'))
        database = cls.root / 'data/library.sqlite3'
        db = library.connect(database)
        try:
            library.initialize(db)
            for record in cls.manifest['records']:
                rid, _ = library.upsert_record(db, **{k: v for k, v in record.items() if k not in ('id', 'assets')})
                assert rid == record['id']
                for asset in record['assets']:
                    source = FIXTURES / asset['file']
                    assert hashlib.sha256(source.read_bytes()).hexdigest() == asset['sha256']
                    relative = 'assets/' + source.name
                    target = cls.root / relative
                    target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, target)
                    with Image.open(target) as image:
                        width, height = image.size
                    db.execute('''INSERT INTO assets(id,source_image_url,original_filename,local_image_path,
                        sha256,width,height,status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                        (asset['id'], asset['source_image_url'], source.name, relative,
                         asset['sha256'], width, height, 'ok', library.now()))
                    db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, asset['id'], asset['role']))
            db.commit()
        finally:
            db.close()
        calibration = json.loads((ROOT / 'assets/item_recognition/calibration.json').read_text('utf-8'))
        calibration['library_sha256'] = hashlib.sha256(database.read_bytes()).hexdigest()
        cls.calibration = cls.root / 'calibration.json'
        cls.calibration.write_text(json.dumps(calibration), encoding='utf-8')
        cls.recognizer = ItemRecognizer(library_path=cls.root, calibration_path=cls.calibration,
                                        include_characters=False, reviewed_path=None)

    def test_animated_and_independent_rewards_count_every_icon_once(self):
        store = LootStore(self.root)
        session = store.ensure_session('fixture:gama-count', 'universal')
        expected_counts = Counter()
        for index, example in enumerate(self.manifest['screenshots']):
            with self.subTest(example=example['file']):
                path = FIXTURES / example['file']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), example['sha256'])
                result = self.recognizer.analyze_screenshot(path)
                self.assertFalse(result['unknown_rewards'])
                actual = {tuple(r['bounding_box']): r['item_id'] for r in result['rewards']}
                expected = {tuple(r['bounding_box']): r['item_id'] for r in example['expected_rewards']}
                self.assertEqual(actual, expected)
                self.assertTrue(all(r['identity_status'] == 'matched' and r['quantity'] is None
                                    for r in result['rewards']))
                self.assertEqual(result['totals'], {}, 'Rarity must not become quantity')
                event = dict(session_id=session, completion_id=f'gama-{index}', run_id=f'gama-{index}',
                             run_sequence=index+1, completed_at=index+100)
                for _ in range(2):
                    store.finish(event, 'recognized', result, None, [])
                expected_counts.update(expected.values())
        dashboard = store.dashboard(session)
        self.assertEqual(dashboard['drop_counts'], dict(expected_counts))
        self.assertEqual(dashboard['total_drops'], 16)
        self.assertEqual(dashboard['drop_counts'][MILLIONAIRE], 5)
        self.assertEqual(dashboard['unidentified_drops'], 0)

    def test_reviewed_source_alignment_handles_overlay_without_lowering_gates(self):
        self.assertEqual(self.recognizer.threshold, .75)
        self.assertEqual(self.recognizer.margin, .019265)
        path = FIXTURES / 'gama_millionaire_holdout.png'
        baseline = ItemRecognizer(library_path=self.root, calibration_path=self.calibration,
                                  include_characters=False, reviewed_path=None)
        baseline.identity_thresholds.pop(MILLIONAIRE)
        self.assertFalse(baseline.analyze_screenshot(path)['unknown_rewards'])

    def test_lucky_drop_label_does_not_hide_the_millionaire_identity(self):
        path = FIXTURES / 'gama_millionaire_lucky_overlay.png'
        result = self.recognizer.analyze_screenshot(path)
        self.assertFalse(result['unknown_rewards'])
        lucky = next(r for r in result['rewards'] if r['bounding_box'] == [543, 573, 134, 139])
        self.assertEqual(lucky['item_id'], MILLIONAIRE)
        self.assertGreaterEqual(lucky['match_score'], .8)
        self.assertGreaterEqual(lucky['score_margin'], .2)

    def test_existing_summary_screen_does_not_credit_items(self):
        result = self.recognizer.analyze_screenshot(ROOT / 'tests/fixtures/results_next.png')
        self.assertEqual(result['rewards'], [])
        self.assertEqual(result['unknown_rewards'], [])


if __name__ == '__main__':
    unittest.main()
