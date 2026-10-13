"""Offline recognizer regressions; all libraries and screenshots are synthetic fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[2] / 'src/item_recognition'
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT.parent / 'game_library'))
import library as L
from recognizer import ItemRecognizer


def icon(pattern=0):
    """High-information shapes exercise color and structure, rather than constant pixels."""
    image = Image.new('RGB', (72, 72), (24, 29, 40))
    draw = ImageDraw.Draw(image)
    if pattern == 0:
        draw.rounded_rectangle((8, 8, 64, 64), radius=8, fill=(175, 56, 30), outline=(255, 226, 108), width=4)
        draw.line((16, 48, 36, 17, 58, 48), fill=(255, 225, 110), width=7)
        draw.rectangle((28, 44, 42, 58), fill=(245, 237, 217))
    elif pattern == 1:
        draw.ellipse((7, 7, 64, 64), fill=(35, 160, 85), outline=(175, 253, 215), width=4)
        draw.polygon((36, 13, 58, 36, 36, 59, 14, 36), fill=(127, 222, 250))
        draw.line((15, 36, 59, 36), fill=(16, 60, 85), width=5)
    else:
        draw.rectangle((9, 10, 62, 61), fill=(100, 38, 181), outline=(236, 210, 252), width=3)
        for y in (18, 31, 44, 57):
            draw.line((15, y, 57, y), fill=(200, 149, 240), width=3)
    return image


class RecognizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-recognition-test-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/library.sqlite3'
        self.db = L.connect(self.path)
        L.initialize(self.db)
        self.items = {}
        self.add_reference('scroll', 'Heart Awakening Scroll', icon(0))
        self.add_reference('crystal', 'Body Crystal', icon(1), verification='manually_verified')
        self.add_reference('uncertain', 'Uncertain Purple Reward', icon(2), verification='inferred')
        self.db.commit()
        self.db.close()
        self.before_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def add_reference(self, key, name, image, verification='source_verified', record_type='item', asset_suffix='', metadata=None, image_source=None):
        rid, _ = L.upsert_record(self.db, record_type=record_type, source_key=key,
            display_name=name, source_url='https://gachawiki.net/naruto-blazing',
            category='material', game_id=key, verification_status=verification,
            element='Heart' if key == 'scroll' else 'Body', rarity='4', metadata=metadata)
        relative = 'assets/items/' + key + asset_suffix + '.png'
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        aid = L.stable_id('asset', relative)
        self.db.execute('''INSERT INTO assets(id,source_image_url,original_filename,
           local_image_path,sha256,width,height,status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)''',
           (aid, image_source or 'https://gachawiki.net/naruto-blazing/' + key + asset_suffix + '.png',
            target.name, relative, hashlib.sha256(target.read_bytes()).hexdigest(),
            image.width, image.height, 'ok', L.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, aid, 'icon'))
        self.items[key] = rid
        return rid

    def recognizer(self, **kwargs):
        return ItemRecognizer(library_path=self.path, **kwargs)

    def test_strong_source_match_is_repeatable_candidate_and_readonly(self):
        recognizer = self.recognizer()
        first = recognizer.match_icon(icon(0))
        second = recognizer.match_icon(icon(0))
        self.assertEqual(first['item_id'], self.items['scroll'])
        self.assertEqual(first['identity_status'], 'candidate')
        self.assertEqual(first['item_name'], 'Heart Awakening Scroll')
        self.assertGreaterEqual(first['match_score'], 0.88)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before_hash)

    def test_uncertain_source_and_blank_icons_remain_unknown(self):
        recognizer = self.recognizer()
        for image in (icon(2), Image.new('RGB', (72, 72), 'black')):
            with self.subTest(image=image.getpixel((36, 36))):
                result = recognizer.match_icon(image)
                self.assertEqual(result['identity_status'], 'unknown')
                self.assertIsNone(result['item_id'])

    def test_identical_references_for_distinct_items_are_ambiguous(self):
        self.db = L.connect(self.path)
        self.add_reference('other-scroll', 'Bravery Awakening Scroll', icon(0))
        self.db.commit()
        self.db.close()
        result = self.recognizer().match_icon(icon(0))
        self.assertEqual(result['identity_status'], 'ambiguous')
        self.assertIsNone(result['item_id'])
        self.assertGreaterEqual(len(result['candidates']), 2)

    def test_multiple_images_for_one_item_are_one_identity(self):
        self.db = L.connect(self.path)
        self.add_reference('scroll', 'Heart Awakening Scroll', icon(0), asset_suffix='_alternate')
        self.db.commit()
        self.db.close()
        result = self.recognizer().match_icon(icon(0))
        self.assertEqual(result['identity_status'], 'candidate')
        self.assertEqual(result['item_id'], self.items['scroll'])

    def test_only_verified_item_references_load_with_metadata(self):
        recognizer = self.recognizer()
        references = {r['id']: r for r in recognizer.references}
        self.assertEqual(set(references), {self.items['scroll'], self.items['crystal']})
        self.assertEqual(references[self.items['scroll']]['element'], 'Heart')
        self.assertEqual(references[self.items['scroll']]['rarity'], '4')
        self.assertTrue(all(Path(p).is_file() for r in references.values() for p in r['reference_paths']))
        self.assertIsInstance(recognizer.reference_report(), dict)

    def test_source_item_with_enemy_combat_evidence_is_excluded_without_db_changes(self):
        self.db = L.connect(self.path)
        enemy = self.add_reference('enemy', 'Enemy Misclassified As Item', icon(2),
            metadata={'original': {'hp': 100, 'attack': 12}},
            image_source='https://gachawiki.net/naruto-blazing/road/enemies/enemy.png')
        self.db.commit()
        self.db.close()
        original_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        recognizer = self.recognizer(calibration_path=None)
        self.assertNotIn(enemy, {record['id'] for record in recognizer.references})
        result = recognizer.match_icon(icon(2))
        self.assertEqual(result['identity_status'], 'unknown')
        self.assertIsNone(result['item_id'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), original_hash)

    def test_numpy_and_image_path_inputs_match_the_same_item(self):
        recognizer = self.recognizer()
        path = self.root / 'query.png'
        icon(0).save(path)
        array_result = recognizer.match_icon(np.asarray(icon(0)))
        path_result = recognizer.match_icon(path)
        self.assertEqual(array_result['item_id'], self.items['scroll'])
        self.assertEqual(path_result['item_id'], array_result['item_id'])
        self.assertAlmostEqual(path_result['match_score'], array_result['match_score'], places=6)

    def test_unreadable_reference_asset_does_not_abort_valid_reference_loading(self):
        self.db = L.connect(self.path)
        self.add_reference('corrupt', 'Corrupt Source Icon', icon(2))
        self.db.commit()
        self.db.close()
        (self.root / 'assets/items/corrupt.png').write_bytes(b'invalid image')
        result = self.recognizer().match_icon(icon(0))
        self.assertEqual(result['item_id'], self.items['scroll'])

    def test_transparent_reference_margins_do_not_change_exact_identity(self):
        reference = Image.new('RGBA', (104, 104), (0, 0, 0, 0))
        reference.paste(icon(2).convert('RGBA'), (16, 16))
        self.db = L.connect(self.path)
        self.add_reference('transparent', 'Purple Framed Material', reference)
        self.db.commit()
        self.db.close()
        result = self.recognizer().match_icon(reference)
        self.assertEqual(result['item_id'], self.items['transparent'])
        self.assertEqual(result['identity_status'], 'candidate')

    def test_supported_calibration_does_not_promote_unvalidated_items(self):
        calibration = self.root / 'calibration.json'
        calibration.write_text(json.dumps({'threshold': 0.88, 'margin': 0.04,
            'supported_item_ids': [self.items['scroll']]}), encoding='utf-8')
        recognizer = self.recognizer(calibration_path=calibration)
        self.assertEqual(recognizer.match_icon(icon(0))['identity_status'], 'matched')
        self.assertEqual(recognizer.match_icon(icon(1))['identity_status'], 'candidate')

    def screenshot(self):
        screen = Image.new('RGB', (320, 240), (19, 24, 35))
        screen.paste(icon(0), (30, 80))
        path = self.root / 'results.png'
        screen.save(path)
        return path

    def test_explicit_icon_crop_preserves_identity_when_quantity_unreadable(self):
        screenshot = self.screenshot()
        result = self.recognizer().analyze_screenshot(screenshot, stage='Synthetic Stage',
            icon_boxes=[[30, 80, 72, 72]], quantity_boxes=[[115, 115, 35, 25]])
        self.assertEqual(result['stage'], 'Synthetic Stage')
        self.assertEqual(len(result['rewards']), 1)
        reward = result['rewards'][0]
        self.assertEqual(reward['item_id'], self.items['scroll'])
        self.assertEqual(reward['identity_status'], 'candidate')
        self.assertIsNone(reward['quantity'])
        self.assertEqual(reward['quantity_status'], 'unknown')
        self.assertEqual(reward['bounding_box'], [30, 80, 72, 72])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['totals'], {})
        json.dumps(result)

    def test_numeric_totals_require_both_validated_identity_and_quantity(self):
        screenshot = self.screenshot()
        quantity = {'quantity': 3, 'quantity_status': 'verified', 'score': 0.99,
                    'reason': 'Synthetic test reader result'}
        candidate = self.recognizer()
        candidate.quantity_reader = Mock()
        candidate.quantity_reader.read.return_value = quantity
        result = candidate.analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]],
                                             quantity_boxes=[[115, 115, 35, 25]])
        self.assertEqual(result['rewards'][0]['quantity'], 3)
        self.assertEqual(result['totals'], {})
        calibration = self.root / 'calibration.json'
        calibration.write_text(json.dumps({'threshold': 0.88, 'margin': 0.04,
            'supported_item_ids': [self.items['scroll']]}), encoding='utf-8')
        matched = self.recognizer(calibration_path=calibration)
        matched.quantity_reader = Mock()
        matched.quantity_reader.read.return_value = quantity
        result = matched.analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]],
                                           quantity_boxes=[[115, 115, 35, 25]])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['totals'], {self.items['scroll']: 3})

    def test_repeat_processing_exposes_same_screenshot_hash_without_mutable_totals(self):
        screenshot = self.screenshot()
        recognizer = self.recognizer()
        first = recognizer.analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]])
        second = recognizer.analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]])
        self.assertEqual(first, second)
        self.assertEqual(first['screenshot_sha256'], hashlib.sha256(screenshot.read_bytes()).hexdigest())
        self.assertEqual(first['totals'], {})

    def test_missing_and_corrupted_screenshots_return_structured_errors(self):
        corrupted = self.root / 'corrupt.png'
        corrupted.write_bytes(b'not an image')
        for screenshot in (self.root / 'missing.png', corrupted):
            with self.subTest(screenshot=screenshot.name):
                result = self.recognizer().analyze_screenshot(screenshot)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['rewards'], [])
                self.assertTrue(result['warnings'])
                json.dumps(result)

    def test_unvalidated_screen_geometry_does_not_match_whole_image(self):
        result = self.recognizer().analyze_screenshot(self.screenshot())
        self.assertEqual(result['rewards'], [])
        self.assertIn(result['status'], ('unknown', 'unsupported', 'unsupported_screen'))
        self.assertTrue(result['warnings'])

    def test_debug_output_is_separate_and_original_pixels_are_unchanged(self):
        screenshot = self.screenshot()
        original = screenshot.read_bytes()
        debug = self.root / 'debug/results.png'
        self.recognizer().analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]], debug_path=debug)
        self.assertTrue(debug.is_file())
        self.assertEqual(screenshot.read_bytes(), original)
        with Image.open(debug) as rendered:
            self.assertEqual(rendered.size, (320, 240))

    def test_invalid_icon_boxes_return_structured_errors(self):
        path = self.screenshot()
        for box in ([-1, 10, 40, 40], [310, 200, 40, 40], [0, 0, 0, 5]):
            with self.subTest(box=box):
                result = self.recognizer().analyze_screenshot(path, icon_boxes=[box])
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['rewards'], [])
                self.assertTrue(result['warnings'])

    def test_debug_path_cannot_overwrite_input(self):
        screenshot = self.screenshot()
        original = screenshot.read_bytes()
        with self.assertRaises(ValueError):
            self.recognizer().analyze_screenshot(screenshot, icon_boxes=[[30, 80, 72, 72]], debug_path=screenshot)
        self.assertEqual(screenshot.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
