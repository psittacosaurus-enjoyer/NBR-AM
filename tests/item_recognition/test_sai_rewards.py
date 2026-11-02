"""Compact source-linked Sai reward fixtures; no live library or device access."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from game_library import library
from item_recognition import recognizer as recognition
from item_recognition.recognizer import ItemRecognizer


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/item_recognition/sai_rewards'
CALIBRATION_PATH = ROOT / 'assets/item_recognition/calibration.json'
REVIEWED_PATH = ROOT / 'assets/item_recognition/reviewed_rewards.json'
SAI = 'character_31a58300c0533b1a399f80fe'
AWAKENED_SAI = 'character_353e377b4bd236970103e076'
EXISTING_CHARACTERS = {'character_2c6e2c21ff0640de0776e944', 'character_e9111c9d9093bb54efd3eb2e'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SaiRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nbr-sai-rewards-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.manifest = json.loads((FIXTURES / 'manifest.json').read_text('utf-8'))
        if cls.manifest.get('schema_version') != 1:
            raise AssertionError('Unsupported Sai fixture manifest')
        if cls.manifest.get('sai_id') != SAI or cls.manifest.get('excluded_sai_id') != AWAKENED_SAI:
            raise AssertionError('Sai forms changed identity')
        cls.database = cls.root / 'data/library.sqlite3'
        db = library.connect(cls.database)
        try:
            library.initialize(db)
            seen = set()
            for reference in cls.manifest['references']:
                rid = reference['item_id']
                if rid not in seen:
                    seen.add(rid)
                    # Preserve manifest IDs and source facts in the real schema;
                    # isolated fixtures need not recreate the source crawl key.
                    metadata = reference.get('metadata', {'original': {'cardNo': int(reference['game_id'])}})
                    title = reference.get('display_name', reference.get('item_name', 'Sai "Mask-like Smile"'))
                    fields = (rid, reference['record_type'], reference.get('source_key', rid), title,
                              str(reference['game_id']), reference['source_url'], reference.get('category', 'ninja'),
                              str(reference['element']), str(reference['rarity']),
                              reference.get('verification_status', 'source_verified'), json.dumps(metadata),
                              '[]', reference['sha256'], library.now(), library.now())
                    db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', fields)
                source = FIXTURES / reference['file']
                if digest(source) != reference['sha256']:
                    raise AssertionError('Source-linked Sai reference changed: ' + reference['file'])
                target = cls.root / 'assets' / source.name
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
                with Image.open(target) as image:
                    width, height = image.size
                aid = library.stable_id('asset', reference['file'])
                image_url = reference.get('asset_source_url', reference.get('source_image_url', reference['source_url']))
                db.execute('''INSERT INTO assets
                    (id,source_image_url,original_filename,local_image_path,sha256,width,height,status,collected_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                           (aid, image_url, source.name, 'assets/' + source.name,
                            reference['sha256'], width, height, 'ok', library.now()))
                library.link_asset(db, rid, aid, reference.get('role', 'card'), image_url)
            db.commit()
        finally:
            db.close()
        cls.production_calibration = json.loads(CALIBRATION_PATH.read_text('utf-8'))
        cls.calibration = copy.deepcopy(cls.production_calibration)
        cls.calibration['library_sha256'] = digest(cls.database)
        cls.calibration_path = cls.root / 'calibration.json'
        cls.calibration_path.write_text(json.dumps(cls.calibration), encoding='utf-8')
        cls.recognizer = cls.make_recognizer()

    @classmethod
    def make_recognizer(cls, calibration_path=None):
        return ItemRecognizer(library_path=cls.root, calibration_path=calibration_path or cls.calibration_path,
                              reviewed_path=None)

    def test_production_admission_preserves_global_gates_and_existing_characters(self):
        calibration = self.production_calibration
        self.assertEqual(calibration['threshold'], .75)
        self.assertEqual(calibration['margin'], .019265)
        self.assertIn(SAI, calibration['supported_character_ids'])
        self.assertNotIn(AWAKENED_SAI, calibration['supported_character_ids'])
        self.assertTrue(EXISTING_CHARACTERS <= set(calibration['supported_character_ids']))
        self.assertEqual(calibration['per_identity_thresholds'][SAI]['threshold'], .78)
        self.assertEqual(calibration['per_identity_thresholds'][SAI]['margin'], .10)
        self.assertTrue({'item_fa8e7d7bde0ad4e897d1ab78', 'item_725cded89eb0b00ec07113c1'}
                        <= set(calibration['supported_item_ids']))

    def test_reviewed_supplement_remains_bound_to_exact_calibration_bytes(self):
        reviewed = json.loads(REVIEWED_PATH.read_text('utf-8'))
        self.assertEqual(reviewed['base_calibration_sha256'], digest(CALIBRATION_PATH))
        self.assertEqual(reviewed['library_sha256'], self.production_calibration['library_sha256'])
        self.assertEqual(self.manifest['library_sha256'], self.production_calibration['library_sha256'])

    def test_clean_lucky_and_holdout_icons_match_only_five_star_card_239(self):
        self.assertGreaterEqual(len(self.manifest['positives']), 4)
        self.assertTrue(any(example['split'] == 'holdout' for example in self.manifest['positives']))
        for example in self.manifest['positives']:
            with self.subTest(file=example['file']):
                path = FIXTURES / example['file']
                self.assertEqual(digest(path), example['sha256'])
                self.assertEqual(example['expected_item_id'], SAI)
                self.assertEqual(len(example['source_screenshot_sha256']), 64)
                self.assertEqual(len(example['bounding_box']), 4)
                result = self.recognizer.match_icon(path)
                self.assertEqual(result['identity_status'], 'matched')
                self.assertEqual(result['item_id'], SAI)
                self.assertEqual(result['game_id'], '239')
                self.assertEqual(result['rarity'], '5')
                self.assertEqual(result['element'], 'Body')
                self.assertTrue(result['confidence']['calibrated'])
                self.assertEqual(result['confidence']['threshold'], .78)
                self.assertEqual(result['confidence']['required_margin'], .10)
                self.assertGreaterEqual(result['match_score'], .78)
                self.assertGreaterEqual(result['score_margin'], .10)

    def test_acquired_character_icons_keep_quantities_unknown(self):
        for example in self.manifest['positives']:
            with self.subTest(file=example['file']):
                path = FIXTURES / example['file']
                with Image.open(path) as image:
                    width, height = image.size
                result = self.recognizer.analyze_screenshot(path, icon_boxes=[[0, 0, width, height]])
                self.assertFalse(result['unknown_rewards'])
                self.assertEqual(len(result['rewards']), 1)
                reward = result['rewards'][0]
                self.assertEqual(reward['item_id'], SAI)
                self.assertEqual(reward['identity_status'], 'matched')
                self.assertEqual(reward['reward_scope'], 'acquired')
                self.assertIsNone(reward['quantity'])
                self.assertEqual(reward['quantity_status'], 'unknown')
                self.assertEqual(result['totals'], {})

    def test_six_star_card_240_stays_separate_and_uncalibrated(self):
        references = [reference for reference in self.manifest['references'] if reference['item_id'] == AWAKENED_SAI]
        self.assertTrue(references, 'Include the same-name six-star rejection control')
        for reference in references:
            with self.subTest(file=reference['file']):
                result = self.recognizer.match_icon(FIXTURES / reference['file'])
                self.assertEqual(result['item_id'], AWAKENED_SAI)
                self.assertEqual(result['game_id'], '240')
                self.assertEqual(result['rarity'], '6')
                self.assertEqual(result['identity_status'], 'candidate')
                self.assertFalse(result['confidence']['calibrated'])
                self.assertNotEqual(result['item_id'], SAI)

    def test_threshold_without_supported_membership_keeps_sai_a_candidate(self):
        baseline = copy.deepcopy(self.calibration)
        baseline['supported_character_ids'].remove(SAI)
        path = self.root / 'unadmitted-sai.json'
        path.write_text(json.dumps(baseline), encoding='utf-8')
        recognizer = self.make_recognizer(path)
        for example in self.manifest['positives']:
            with self.subTest(file=example['file']):
                result = recognizer.match_icon(FIXTURES / example['file'])
                self.assertEqual(result['item_id'], SAI)
                self.assertEqual(result['identity_status'], 'candidate')
                self.assertFalse(result['confidence']['calibrated'])

    def test_stale_library_fingerprint_blocks_character_admission(self):
        stale = copy.deepcopy(self.calibration)
        stale['library_sha256'] = '0' * 64
        folder = self.root / 'stale-calibration'
        folder.mkdir(exist_ok=True)
        path = folder / 'calibration.json'
        path.write_text(json.dumps(stale), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'another library revision'):
            self.make_recognizer(path)
        with patch.object(recognition, 'DEFAULT_TEMPLATES', folder):
            uncalibrated = ItemRecognizer(library_path=self.root, reviewed_path=None)
        self.assertNotIn(SAI, uncalibrated.supported_characters)
        for example in self.manifest['positives']:
            with self.subTest(file=example['file']):
                result = uncalibrated.match_icon(FIXTURES / example['file'])
                self.assertFalse(result['identity_status'] == 'matched' and result['item_id'] == SAI)


if __name__ == '__main__':
    unittest.main()
