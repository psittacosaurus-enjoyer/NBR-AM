"""Saved All Cups rewards with source-linked references; no device or network access."""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from game_library import library
from item_recognition.recognizer import ItemRecognizer

FIXTURES = ROOT / 'tests/fixtures/item_recognition'


class RamenRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nbr-ramen-rewards-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.manifest = json.loads((FIXTURES / 'ramen_references.json').read_text('utf-8'))
        cls.crystal_fixture = json.loads((FIXTURES / 'bravery_crystal_references.json').read_text('utf-8'))
        cls.alias_fixture = json.loads((FIXTURES / 'crystal_alias_references.json').read_text('utf-8'))
        cls.database = cls.root / 'data/library.sqlite3'
        db = library.connect(cls.database)
        try:
            library.initialize(db)
            for record in cls.manifest['records'] + cls.crystal_fixture['records'] + cls.alias_fixture['records']:
                fields = {k: v for k, v in record.items() if k not in {'id', 'assets'}}
                rid, _ = library.upsert_record(db, **fields)
                if rid != record['id']:
                    raise AssertionError('Fixture record identity changed')
                for asset in record['assets']:
                    source = FIXTURES / asset['file']
                    if hashlib.sha256(source.read_bytes()).hexdigest() != asset['sha256']:
                        raise AssertionError('Source-linked artwork fixture changed')
                    relative = 'assets/' + source.name
                    target = cls.root / relative
                    target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, target)
                    with Image.open(target) as image:
                        width, height = image.size
                    db.execute('''INSERT INTO assets
                        (id,source_image_url,original_filename,local_image_path,
                         sha256,width,height,status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                        (asset['id'], asset['source_image_url'], source.name, relative,
                         asset['sha256'], width, height, 'ok', library.now()))
                    db.execute('INSERT INTO record_assets VALUES(?,?,?)',
                               (rid, asset['id'], asset['role']))
            db.commit()
        finally:
            db.close()
        cls.calibration = json.loads((ROOT / 'assets/item_recognition/calibration.json').read_text('utf-8'))
        cls.calibration['library_sha256'] = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.calibration_path = cls.root / 'calibration.json'
        cls.calibration_path.write_text(json.dumps(cls.calibration), encoding='utf-8')
        reviewed = json.loads((ROOT / 'assets/item_recognition/reviewed_rewards.json').read_text('utf-8'))
        reviewed['library_sha256'] = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        reviewed['base_calibration_sha256'] = hashlib.sha256(cls.calibration_path.read_bytes()).hexdigest()
        cls.reviewed_path = cls.root / 'reviewed_rewards.json'
        cls.reviewed_path.write_text(json.dumps(reviewed), encoding='utf-8')
        cls.recognizer = ItemRecognizer(library_path=cls.root, calibration_path=cls.calibration_path,
                                        include_characters=False, reviewed_path=cls.reviewed_path)

    def assert_rewards(self, path, expected):
        result = self.recognizer.analyze_screenshot(path)
        entries = result['rewards'] + result['unknown_rewards']
        self.assertEqual(len(entries), len(expected))
        self.assertFalse(result['unknown_rewards'])
        for entry in entries:
            self.assertEqual(entry['identity_status'], 'matched')
            self.assertEqual(entry['item_id'], expected[tuple(entry['bounding_box'])])
            self.assertEqual(entry['reward_scope'], 'acquired')
            self.assertIsNone(entry['quantity'], 'Rarity stars must not become a quantity')
        self.assertEqual(result['totals'], {})
        return entries

    def test_current_five_drop_failure_recognizes_each_distinct_element(self):
        expected = {(57 + 162 * i, 411, 134, 139): rid
                    for i, rid in enumerate(self.manifest['expected_top_row'])}
        entries = self.assert_rewards(FIXTURES / 'all_cups_ramen_results.png', expected)
        self.assertEqual({r['element'] for r in entries}, {'Bravery', 'Heart', 'Wisdom', 'Body', 'Skill'})

    def test_duplicate_crystal_source_records_count_two_icons_as_two_drops(self):
        from loot_tracking import LootStore
        fixture = self.crystal_fixture
        path = FIXTURES / 'bravery_crystal_results.png'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), fixture['evidence']['screenshot_sha256'])
        expected = {tuple(box): fixture['canonical'] for box in fixture['evidence']['bounding_boxes']}
        self.assert_rewards(path, expected)
        result = self.recognizer.analyze_screenshot(path)
        self.assertTrue(all(set(r['member_ids']) == {fixture['canonical'], fixture['alias']}
                            for r in result['rewards']))
        store = LootStore(self.root)
        session = store.ensure_session('fixture:bravery-count', 'universal')
        event = dict(session_id=session, completion_id='bravery', run_id='bravery',
                     run_sequence=1, completed_at=100)
        for _ in range(2):
            store.finish(event, 'recognized', result, None, [])
        counts = store.dashboard(session)
        self.assertEqual(counts['total_drops'], 2)
        self.assertEqual(counts['drop_counts'], {fixture['canonical']: 2})
        self.assertEqual(counts['unidentified_drops'], 0)
        baseline = ItemRecognizer(library_path=self.root, calibration_path=self.calibration_path,
                                  include_characters=False, reviewed_path=None).analyze_screenshot(path)
        self.assertEqual(len(baseline['unknown_rewards']), 2)
        self.assertTrue(all(r['identity_status'] == 'ambiguous' for r in baseline['unknown_rewards']))

    def test_skill_crystal_aliases_count_two_and_three_icons_once(self):
        from loot_tracking import LootStore
        canonical = self.alias_fixture['skill_canonical']
        store = LootStore(self.root)
        session = store.ensure_session('fixture:skill-crystals', 'universal')
        for index, example in enumerate(self.alias_fixture['screenshots']):
            path = FIXTURES / example['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), example['sha256'])
            expected = {(57+162*i, 411, 134, 139): canonical for i in range(example['icons'])}
            entries = self.assert_rewards(path, expected)
            self.assertTrue(all(r['confidence']['threshold'] == .75 for r in entries))
            result = self.recognizer.analyze_screenshot(path)
            event = dict(session_id=session, completion_id=f'skill-{index}', run_id=f'skill-{index}',
                         run_sequence=index+1, completed_at=index+100)
            for _ in range(2):
                store.finish(event, 'recognized', result, None, [])
        counts = store.dashboard(session)
        self.assertEqual(counts['drop_counts'], {canonical: 5})
        self.assertEqual(counts['total_drops'], 5)
        self.assertEqual(counts['unidentified_drops'], 0)

    def test_full_library_alias_review_does_not_promote_unseen_crystals(self):
        for group in self.alias_fixture['groups']:
            canonical = group['canonical_item_id']
            self.assertTrue(all(self.recognizer.identity_map[mid] == canonical for mid in group['member_ids']))
            record = next(r for r in self.alias_fixture['records'] if r['id'] == canonical)
            result = self.recognizer.match_icon(FIXTURES / record['assets'][0]['file'])
            self.assertEqual(result['item_id'], canonical)
            self.assertEqual(result['identity_status'], 'matched' if canonical == self.alias_fixture['skill_canonical'] else 'candidate')

    def test_independent_six_drop_capture_keeps_lucky_drop_and_excludes_conditions(self):
        expected = {(57 + 162 * i, 411, 134, 139): rid
                    for i, rid in enumerate(self.manifest['expected_top_row'])}
        expected[(57, 573, 134, 139)] = self.manifest['expected_top_row'][1]
        self.assert_rewards(ROOT / 'tests/fixtures/universal/unconfigured_results.png', expected)

    def test_lucky_card_back_is_unrevealed_and_never_an_extra_item_icon(self):
        from loot_tracking import LootStore
        from item_recognition.reward_layout import has_unrevealed_rewards
        import cv2
        pending = FIXTURES / 'bravery_lucky_unrevealed.png'
        revealed = FIXTURES / 'bravery_lucky_revealed.png'
        result = self.recognizer.analyze_screenshot(pending)
        self.assertEqual(len(result['rewards']), 2)
        self.assertEqual(len(result['unknown_rewards']), 1)
        back = result['unknown_rewards'][0]
        self.assertEqual(back['bounding_box'], [381, 411, 134, 139])
        self.assertEqual(back['visual_state'], 'unrevealed')
        self.assertIsNone(back['item_id'])
        self.assertTrue(has_unrevealed_rewards(cv2.imread(str(pending)), ROOT / 'assets/item_recognition'))
        self.assertFalse(has_unrevealed_rewards(cv2.imread(str(revealed)), ROOT / 'assets/item_recognition'))
        store = LootStore(self.root)
        session = store.ensure_session('fixture:lucky-back', 'universal')
        event = dict(session_id=session, completion_id='lucky-back', run_id='lucky-back',
                     run_sequence=1, completed_at=100)
        store.finish(event, 'uncertain', result, None, [])
        data = store.dashboard(session)
        self.assertEqual(data['total_drops'], 2)
        self.assertEqual(data['unidentified_drops'], 0)

    def test_unreviewed_ramen_size_remains_a_candidate(self):
        rid = self.manifest['uncalibrated_control']
        record = next(r for r in self.manifest['records'] if r['id'] == rid)
        result = self.recognizer.match_icon(FIXTURES / record['assets'][0]['file'])
        self.assertEqual(result['item_id'], rid)
        self.assertEqual(result['identity_status'], 'candidate')

    def test_xl_variants_keep_distinct_ids_and_count_once_per_saved_icon(self):
        from loot_tracking import LootStore
        store = LootStore(self.root)
        session = store.ensure_session('fixture:xl-count', 'universal')
        expected_counts = Counter()
        xl_ids = set(self.manifest['xl_item_ids'].values())
        for sequence, example in enumerate(self.manifest['xl_examples'], 1):
            path = FIXTURES / example['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), example['sha256'])
            expected = {tuple(r['bounding_box']): r['item_id'] for r in example['expected_rewards']}
            entries = self.assert_rewards(path, expected)
            for entry in entries:
                self.assertEqual(entry['rarity'], '3' if entry['item_id'] in xl_ids else '4')
            result = self.recognizer.analyze_screenshot(path)
            event = dict(session_id=session, completion_id=f'xl-{sequence}', run_id=f'xl-{sequence}',
                         run_sequence=sequence, completed_at=sequence)
            for _ in range(2):
                store.finish(event, 'recognized', result, None, [])
            expected_counts.update(expected.values())
            dashboard = store.dashboard(session)
            self.assertEqual(dashboard['drop_counts'], dict(expected_counts))
            self.assertEqual(dashboard['unidentified_drops'], 0)
            self.assertEqual(dashboard['total_drops'], sum(expected_counts.values()))
        self.assertEqual(dashboard['total_drops'], 20)
        self.assertEqual(dashboard['completed_runs'], 3)

    def test_xl_identities_require_their_own_review(self):
        baseline = copy.deepcopy(self.calibration)
        for rid in self.manifest['xl_item_ids'].values():
            baseline['supported_item_ids'].remove(rid)
            baseline['per_identity_thresholds'].pop(rid)
        path = self.root / 'without-xl-review.json'
        path.write_text(json.dumps(baseline), encoding='utf-8')
        recognizer = ItemRecognizer(library_path=self.root, calibration_path=path,
                                    include_characters=False, reviewed_path=None)
        for rid in self.manifest['xl_item_ids'].values():
            record = next(r for r in self.manifest['records'] if r['id'] == rid)
            result = recognizer.match_icon(FIXTURES / record['assets'][0]['file'])
            self.assertEqual(result['item_id'], rid)
            self.assertEqual(result['identity_status'], 'candidate')

    def test_blank_and_obscured_icons_do_not_gain_an_identity(self):
        for color in ('black', 'white', '#808080'):
            result = self.recognizer.match_icon(Image.new('RGB', (134, 139), color))
            self.assertEqual(result['identity_status'], 'unknown')
            self.assertIsNone(result['item_id'])

    def test_missing_review_reproduces_failure_without_promoting_candidates(self):
        baseline = copy.deepcopy(self.calibration)
        for rid in self.manifest['expected_top_row']:
            baseline['supported_item_ids'].remove(rid)
            baseline['per_identity_thresholds'].pop(rid)
        path = self.root / 'baseline.json'
        path.write_text(json.dumps(baseline), encoding='utf-8')
        recognizer = ItemRecognizer(library_path=self.root, calibration_path=path,
                                    include_characters=False, reviewed_path=None)
        result = recognizer.analyze_screenshot(FIXTURES / 'all_cups_ramen_results.png')
        entries = result['rewards'] + result['unknown_rewards']
        self.assertEqual(sum(r['identity_status'] == 'matched' for r in entries), 0)
        self.assertTrue(any(r['identity_status'] == 'ambiguous' for r in entries))

    def test_existing_review_supplement_stays_bound_to_calibration(self):
        path = ROOT / 'assets/item_recognition/calibration.json'
        reviewed = json.loads((ROOT / 'assets/item_recognition/reviewed_rewards.json').read_text('utf-8'))
        self.assertEqual(reviewed['base_calibration_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_each_saved_icon_including_lucky_drop_counts_once(self):
        from loot_tracking import LootStore
        result = self.recognizer.analyze_screenshot(ROOT / 'tests/fixtures/universal/unconfigured_results.png')
        store = LootStore(self.root)
        session = store.ensure_session('fixture:ramen-count', 'universal')
        event = {'session_id': session, 'completion_id': 'ramen-count', 'run_id': 'ramen-run',
                 'run_sequence': 1, 'completed_at': 100}
        for _ in range(2):
            store.finish(event, 'recognized', result, None, [])
        counts = store.dashboard(session)
        self.assertEqual(counts['completed_runs'], 1)
        self.assertEqual(counts['total_drops'], 6)
        self.assertEqual(counts['unidentified_drops'], 0)
        self.assertEqual(counts['drop_counts'], {rid: 2 if i == 1 else 1
                                               for i, rid in enumerate(self.manifest['expected_top_row'])})
        self.assertEqual(counts['drop_counts'], store.dashboard(session)['drop_counts'])

        # A second real results image adds one to each of its five item counters.
        next_result = self.recognizer.analyze_screenshot(FIXTURES / 'all_cups_ramen_results.png')
        next_event = {**event, 'completion_id': 'ramen-count-2', 'run_id': 'ramen-run-2',
                      'run_sequence': 2, 'completed_at': 200}
        for _ in range(2):
            store.finish(next_event, 'recognized', next_result, None, [])
        accumulated = LootStore(self.root).dashboard(session)
        self.assertEqual(accumulated['completed_runs'], 2)
        self.assertEqual(accumulated['total_drops'], 11)
        self.assertEqual(accumulated['unidentified_drops'], 0)
        self.assertEqual([run['drop_count'] for run in accumulated['runs']], [5, 6])
        for rid, previous in counts['drop_counts'].items():
            self.assertEqual(accumulated['drop_counts'][rid], previous + 1)
        self.assertEqual(accumulated['drop_counts'], store.dashboard(session)['drop_counts'])


if __name__ == '__main__':
    unittest.main()
