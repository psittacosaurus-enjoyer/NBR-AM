"""Offline regression fixtures for live reward matching; never connects to a device."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageEnhance

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/item_recognition'))
sys.path.insert(0, str(ROOT / 'src/game_library'))
import library as L
from recognizer import ItemRecognizer
from reward_identity import character_references


def reference_icon(color=(184, 49, 35)):
    image = Image.new('RGB', (72, 72), (20, 26, 38))
    draw = ImageDraw.Draw(image)
    draw.rectangle((7, 8, 65, 65), fill=color, outline=(250, 228, 153), width=4)
    draw.polygon((36, 12, 57, 37, 36, 60, 15, 37), fill=(238, 218, 181))
    draw.line((20, 46, 52, 25), fill=(26, 45, 61), width=6)
    return image


class LiveUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-live-upgrade-test-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/library.sqlite3'
        self.db = L.connect(self.path)
        L.initialize(self.db)

    def tearDown(self):
        if self.db is not None:
            self.db.close()
        self.temp.cleanup()

    def reference(self, key, name='Library Material', *, element='Heart', rarity='4',
                  record_type='item', status='source_verified', role='icon',
                  image=None, asset_status='ok', source_path=None, game_id=None):
        rid, _ = L.upsert_record(self.db, record_type=record_type, source_key=key,
            display_name=name, source_url='https://example.test/records/' + key,
            category='material' if record_type == 'item' else 'character',
            element=element, rarity=rarity, verification_status=status, game_id=game_id)
        relative = 'assets/' + key + '.png'
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image = image or reference_icon()
        image.save(target)
        aid = L.stable_id('asset', relative)
        self.db.execute('''INSERT INTO assets(id,source_image_url,original_filename,
            local_image_path,sha256,width,height,status,collected_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (aid, source_path or 'https://example.test/images/' + key + '.png',
             target.name, relative, hashlib.sha256(target.read_bytes()).hexdigest(),
             image.width, image.height, asset_status, L.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, aid, role))
        return rid

    def recognizer(self, supported=(), *, character_supported=(), groups=(),
                   identity_limits=None, threshold=.88, **kwargs):
        self.db.commit()
        calibration = None
        if supported or character_supported or groups or identity_limits:
            calibration = self.root / 'calibration.json'
            calibration.write_text(json.dumps({'schema_version': 2, 'threshold': threshold, 'margin': .04,
                'supported_item_ids': list(supported),
                'supported_character_ids': list(character_supported),
                'canonical_groups': list(groups),
                'per_identity_thresholds': identity_limits or {}}), encoding='utf-8')
        return ItemRecognizer(library_path=self.path, calibration_path=calibration, **kwargs)

    def test_same_unit_id_with_consistent_metadata_is_one_identity(self):
        first = self.reference('first', game_id='material-101')
        second = self.reference('second', game_id='material-101')
        recognizer = self.recognizer()
        result = recognizer.match_icon(reference_icon())
        self.assertEqual(result['identity_status'], 'candidate')
        self.assertIn(result['item_id'], (first, second))
        self.assertEqual(set(result['member_ids']), {first, second})
        self.assertEqual(result['item_name'], 'Library Material')
        self.assertEqual(len(result['candidates']), 1)
        self.assertIn(result['reference_item_id'], (first, second))
        self.assertEqual(len(recognizer.references), 2)

    def test_identical_name_metadata_and_art_without_identity_evidence_stay_ambiguous(self):
        first = self.reference('first')
        second = self.reference('second')
        result = self.recognizer().match_icon(reference_icon())
        self.assertEqual(result['identity_status'], 'ambiguous')
        self.assertIsNone(result['item_id'])
        self.assertEqual({r['item_id'] for r in result['candidates']}, {first, second})

    def test_group_keeps_strongest_asset_owner_and_actual_library_record(self):
        first = self.reference('first', game_id='material-101')
        second = self.reference('second', game_id='material-101',
            image=reference_icon((48, 159, 108)))
        result = self.recognizer().match_icon(reference_icon((48, 159, 108)))
        self.assertEqual(result['identity_status'], 'candidate')
        self.assertEqual(set(result['member_ids']), {first, second})
        self.assertEqual(result['reference_item_id'], second)
        with L.ReferenceLibrary(self.path) as library:
            canonical = library.get(result['item_id'])
            owner = library.get(result['reference_item_id'])
        self.assertEqual(result['item_name'], canonical['display_name'])
        reference_path = result['candidates'][0]['reference_path']
        self.assertIn(reference_path, [str((self.root / a['local_image_path']).resolve())
                                      for a in owner['assets']])

    def test_effective_library_annotation_is_used_for_character_name(self):
        character = self.reference('character', 'Original Source Name',
            record_type='character', role='card')
        self.db.commit()
        L.annotate(self.db, character, {'display_name': 'Reviewed Library Name'})
        result = self.recognizer().match_icon(reference_icon())
        self.assertEqual(result['item_id'], character)
        self.assertEqual(result['item_name'], 'Reviewed Library Name')

    def test_conflicting_element_rarity_or_semantic_label_never_merge(self):
        for field, value, color in (('element', 'Body', (50, 155, 96)),
                                   ('rarity', '5', (145, 50, 190)),
                                   ('name', 'Different Library Material', (50, 100, 190))):
            with self.subTest(field=field):
                # Each pair gets a separate source identity so earlier fixtures cannot mask a conflict.
                key = 'conflict-' + field
                first = self.reference(key + '-first', game_id=key,
                    image=reference_icon(color))
                second = self.reference(key + '-second', game_id=key,
                    image=reference_icon(color), **{field: value})
                result = self.recognizer().match_icon(reference_icon(color))
                self.assertEqual(result['identity_status'], 'ambiguous')
                self.assertIsNone(result['item_id'])
                candidates = result['candidates']
                self.assertTrue(any(first in r['member_ids'] for r in candidates))
                self.assertTrue(any(second in r['member_ids'] for r in candidates))
                self.assertFalse(any({first, second} <= set(r['member_ids']) for r in candidates))

    def test_calibrated_alias_group_does_not_promote_unrelated_exact_match(self):
        first = self.reference('first', game_id='material-101')
        second = self.reference('second', game_id='material-101')
        other = self.reference('other', 'Other Library Material',
            image=reference_icon((48, 159, 108)))
        recognizer = self.recognizer(supported=[second])
        matched = recognizer.match_icon(reference_icon())
        candidate = recognizer.match_icon(reference_icon((48, 159, 108)))
        self.assertEqual(matched['identity_status'], 'matched')
        self.assertEqual(set(matched['member_ids']), {first, second})
        self.assertEqual(candidate['identity_status'], 'candidate')
        self.assertEqual(candidate['item_id'], other)
        self.assertFalse(candidate['confidence']['calibrated'])

    def test_reviewed_group_requires_evidence_and_compatible_library_metadata(self):
        first = self.reference('first')
        second = self.reference('second')
        group = {'canonical_item_id': first, 'member_ids': [first, second]}
        no_evidence = self.recognizer(groups=[group]).match_icon(reference_icon())
        self.assertEqual(no_evidence['identity_status'], 'ambiguous')
        group['evidence'] = [{'kind': 'inspected_test_fixture', 'reason': 'same source unit'}]
        merged = self.recognizer(groups=[group], supported=[first]).match_icon(reference_icon())
        self.assertEqual(merged['identity_status'], 'matched')
        self.assertEqual(merged['item_id'], first)
        self.assertEqual(set(merged['member_ids']), {first, second})
        self.db.execute('UPDATE records SET element=? WHERE id=?', ('Body', second))
        conflict = self.recognizer(groups=[group], supported=[first]).match_icon(reference_icon())
        self.assertEqual(conflict['identity_status'], 'ambiguous')
        self.assertIsNone(conflict['item_id'])

    def test_hidden_library_alias_retains_its_calibrated_acceptance_gates(self):
        canonical = self.reference('canonical')
        alias = self.reference('hidden-alias')
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)',
            (alias, canonical, 'Reviewed explicit source identity'))
        recognizer = self.recognizer(supported=[alias],
            identity_limits={alias: {'threshold': .99, 'margin': .12}})
        result = recognizer.match_icon(reference_icon())
        self.assertEqual(result['identity_status'], 'matched')
        self.assertEqual(result['item_id'], canonical)
        self.assertEqual(len(recognizer.references), 1)
        self.assertEqual(result['confidence']['threshold'], .99)
        self.assertEqual(result['confidence']['required_margin'], .12)
        degraded = recognizer.match_icon(ImageEnhance.Brightness(reference_icon()).enhance(.85))
        self.assertEqual(degraded['identity_status'], 'unknown')
        self.assertLess(degraded['match_score'], .99)

    def test_canonical_member_gate_conflicts_use_each_stricter_limit(self):
        canonical = self.reference('canonical')
        alias = self.reference('hidden-alias')
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)',
            (alias, canonical, 'Reviewed explicit source identity'))
        recognizer = self.recognizer(supported=[canonical], identity_limits={
            canonical: {'threshold': .90, 'margin': .15},
            alias: {'threshold': .99, 'margin': .06}})
        result = recognizer.match_icon(reference_icon())
        self.assertEqual(result['identity_status'], 'matched')
        self.assertEqual(result['confidence']['threshold'], .99)
        self.assertEqual(result['confidence']['required_margin'], .15)

    def test_character_lookup_can_be_explicitly_disabled_without_hiding_items(self):
        item = self.reference('item')
        self.reference('character', record_type='character', role='card',
            image=reference_icon((48, 159, 108)))
        recognizer = self.recognizer(include_characters=False)
        self.assertEqual(recognizer.character_references, [])
        self.assertEqual(recognizer.match_icon(reference_icon())['item_id'], item)

    def test_character_identity_uses_library_name_and_separate_calibration_gate(self):
        character = self.reference('character', 'Actual Library Character Title',
            record_type='character', role='card')
        candidate = self.recognizer().match_icon(reference_icon())
        self.assertEqual(candidate['identity_status'], 'candidate')
        self.assertEqual(candidate['item_id'], character)
        self.assertEqual(candidate['item_name'], 'Actual Library Character Title')
        self.assertEqual(candidate['record_type'], 'character')
        # An item gate cannot accidentally authorize an unvalidated character.
        item_gate = self.recognizer(supported=[character]).match_icon(reference_icon())
        self.assertEqual(item_gate['identity_status'], 'candidate')
        character_gate = self.recognizer(character_supported=[character]).match_icon(reference_icon())
        self.assertEqual(character_gate['identity_status'], 'matched')

    def test_character_api_rejects_inferred_ability_and_unavailable_assets(self):
        verified = self.reference('verified', 'Verified Character', record_type='character', role='card')
        manual = self.reference('manual', 'Manual Character', record_type='character',
            role='card', status='manually_verified')
        self.reference('inferred', record_type='character', role='card', status='inferred')
        self.reference('unidentified', record_type='character', role='card', status='unidentified')
        self.reference('ability', record_type='character', role='icon',
            source_path='https://example.test/abilities/special.png')
        self.reference('full-art', record_type='character', role='art')
        self.reference('unavailable', record_type='character', role='card', asset_status='error')
        self.db.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with L.ReferenceLibrary(self.path) as library:
            records = list(character_references(library))
        self.assertEqual({r['id'] for r in records}, {verified, manual})
        recognizer = self.recognizer()
        self.assertEqual({r['id'] for r in recognizer.character_references}, {verified, manual})
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_verified_character_does_not_default_quantity_to_one(self):
        character = self.reference('character', 'Actual Library Character Title',
            record_type='character', role='card')
        screen = Image.new('RGB', (240, 160), (19, 26, 38))
        screen.paste(reference_icon(), (20, 40))
        path = self.root / 'results.png'
        screen.save(path)
        recognizer = self.recognizer(character_supported=[character])
        first = recognizer.analyze_screenshot(path, icon_boxes=[[20, 40, 72, 72]])
        second = recognizer.analyze_screenshot(path, icon_boxes=[[20, 40, 72, 72]])
        self.assertEqual(first, second)
        reward = first['rewards'][0]
        self.assertEqual(reward['identity_status'], 'matched')
        self.assertEqual(reward['item_id'], character)
        self.assertIsNone(reward['quantity'])
        self.assertEqual(reward['quantity_status'], 'unknown')
        self.assertEqual(first['totals'], {})

    def test_per_identity_limits_do_not_relax_other_supported_identity(self):
        target = self.reference('target')
        other = self.reference('other', 'Other Library Material',
            image=reference_icon((48, 159, 108)))
        recognizer = self.recognizer(supported=[target, other], threshold=.98,
            identity_limits={target: {'threshold': .70, 'margin': .055}})
        target_result = recognizer.match_icon(ImageEnhance.Brightness(reference_icon()).enhance(.6))
        other_result = recognizer.match_icon(ImageEnhance.Brightness(reference_icon((48, 159, 108))).enhance(.6))
        self.assertEqual(target_result['identity_status'], 'matched')
        self.assertEqual(target_result['item_id'], target)
        self.assertEqual(target_result['confidence']['threshold'], .70)
        self.assertEqual(target_result['confidence']['required_margin'], .055)
        self.assertEqual(other_result['identity_status'], 'unknown')
        self.assertIsNone(other_result['item_id'])
        self.assertEqual(other_result['confidence']['threshold'], .98)
        self.assertEqual(other_result['confidence']['required_margin'], .04)

    def test_per_identity_override_does_not_validate_unsupported_identity(self):
        target = self.reference('target')
        recognizer = self.recognizer(identity_limits={target: {'threshold': .70, 'margin': .055}})
        matched = recognizer.match_icon(reference_icon())
        self.assertEqual(matched['identity_status'], 'candidate')
        self.assertEqual(matched['item_id'], target)
        self.assertFalse(matched['confidence']['calibrated'])

    def test_invalid_per_identity_limits_are_rejected(self):
        target = self.reference('target')
        for limits in ({'threshold': -.01}, {'threshold': 1.01},
                       {'margin': -.01}, {'margin': 1.01}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.recognizer(identity_limits={target: limits})

    @unittest.skipUnless((ROOT / 'runtime/library/data/library.sqlite3').is_file(),
                         'Requires the locally downloaded reference library')
    def test_real_nearest_sakura_five_star_form_is_not_accepted_as_four_star(self):
        # Read actual records and artwork through the public API; use disposable calibration only.
        with L.ReferenceLibrary(ROOT / 'runtime/library/data/library.sqlite3') as library:
            records = list(character_references(library))
        four = next(r for r in records if r['game_id'] == '379')
        five = next(r for r in records if r['game_id'] == '380')
        self.assertEqual(four['rarity'], '4')
        self.assertEqual(five['rarity'], '5')
        calibration = self.root / 'sakura-only.json'
        calibration.write_text(json.dumps({'schema_version': 2,
            'threshold': .75, 'margin': .019265, 'supported_character_ids': [four['id']],
            'per_identity_thresholds': {four['id']: {'threshold': .70, 'margin': .055}}}),
            encoding='utf-8')
        recognizer = ItemRecognizer(library_path=ROOT / 'runtime/library', calibration_path=calibration)
        loaded = next(r for r in recognizer.character_references if r['id'] == five['id'])
        self.assertTrue(loaded['reference_paths'])
        for path in loaded['reference_paths']:
            with self.subTest(reference=path):
                actual = recognizer.match_icon(path)
                self.assertFalse(actual['identity_status'] == 'matched' and actual['item_id'] == four['id'])
                self.assertEqual(actual['candidates'][0]['item_id'], five['id'])
                self.assertEqual(actual['candidates'][0]['rarity'], '5')


if __name__ == '__main__':
    unittest.main()
