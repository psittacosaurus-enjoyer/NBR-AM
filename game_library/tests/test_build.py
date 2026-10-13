"""Representative structured catalogs validate provenance and asset associations."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build
import library as L


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-build-test-')
        self.root = Path(self.temp.name)
        (self.root / 'data/raw').mkdir(parents=True)
        self.resources = []
        for filename in ('card_a.webp', 'card_b.webp', 'card_unknown.webp', 'scroll_9999.webp', 'crystal.webp', 'mission.webp', 'unused_12345.webp'):
            relative = 'assets/fixtures/' + filename
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(filename.encode())
            self.resources.append(dict(url=build.HOME + '/fixtures/' + filename,
                kind='image', status='ok', path=relative, sha256=filename, width=64, height=64))
        self.ninjas = {'ninjas': [None, {'name': 'Missing ID'}, {'id': 'naruto', 'name': 'Naruto Uzumaki',
            'title': 'Hero of the Leaf', 'affiliations': ['Hidden Leaf'],
            'forms': [{'cardNo': 1001, 'stars': 5, 'element': 'Heart', 'card': self.image('card_a.webp'),
                       'awakening': {'materials': [self.scroll(2)]}},
                      {'cardNo': 1002, 'stars': 6, 'element': 'Heart', 'card': self.image('card_b.webp')}]},
            {'id': 'unknown-card', 'name': 'Unknown Card Number', 'forms': [
                {'stars': 3, 'card': self.image('card_unknown.webp')}, 'bad-form']} ]}
        self.missions = {'missions': [None, {'id': 'missing-title'}, {
            'id': 'm1', 'title': 'Leaf Training', 'type': 'emergency', 'area': 50,
            'banner': self.image('mission.webp'), 'stages': [{'rank': 'A', 'id': 51,
                'title': 'Awakening Trial', 'rewards': [self.scroll(2), self.scroll(3),
                    {'kind': 'crystal', 'name': 'Heart Crystal', 'unit': 77, 'stars': 2,
                     'element': 1, 'icon': self.image('crystal.webp')}]}]}]}
        self.story = {'story': [{'no': 1, 'title': 'Chapter One', 'normal': {'missions': [
            {'id': 81, 'title': 'First Steps', 'rewards': [self.scroll(2)]}, None]}}]}

    def tearDown(self):
        self.temp.cleanup()

    def image(self, filename):
        return '/naruto-blazing/fixtures/' + filename

    def scroll(self, stars):
        return {'kind': 'scroll', 'name': 'Heart Awakening Scroll', 'stars': stars,
                'element': 1, 'icon': self.image('scroll_9999.webp')}

    def fixtures(self):
        resources = list(self.resources)
        for filename, data in (('ninjas.json', self.ninjas), ('missions.json', self.missions), ('story.json', self.story)):
            path = 'data/raw/' + filename
            (self.root / path).write_text(json.dumps(data), encoding='utf-8')
            resources.append(dict(kind='json', status='ok', url=build.BASE + '/data/naruto-blazing/' + filename, path=path))
        bad = self.root / 'data/raw/malformed.json'
        bad.write_text('{broken', encoding='utf-8')
        resources.append(dict(kind='json', status='ok', url=build.BASE + '/data/naruto-blazing/malformed.json', path='data/raw/malformed.json'))
        (self.root / 'data/manifest.json').write_text(json.dumps({'resources': resources}), encoding='utf-8')

    def run_build(self):
        self.fixtures()
        builder = build.Builder(root=self.root)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                summary = builder.run()
        finally:
            builder.db.close()
        return summary

    def test_source_fields_correct_cards_rewards_and_unknown_ids(self):
        summary = self.run_build()
        self.assertGreaterEqual(len(summary['extraction_failures']), 5)
        with contextlib.closing(L.connect(self.root / 'data/library.sqlite3', readonly=True)) as db:
            characters = L.search(db, record_type='character')['records']
            self.assertEqual(len(characters), 3)
            by_id = {r['game_id']: r for r in characters}
            self.assertEqual(by_id['1001']['local_image_path'], 'assets/fixtures/card_a.webp')
            self.assertEqual(by_id['1002']['local_image_path'], 'assets/fixtures/card_b.webp')
            self.assertEqual(by_id[None]['local_image_path'], 'assets/fixtures/card_unknown.webp')
            self.assertEqual(by_id['1001']['display_name'], 'Naruto Uzumaki "Hero of the Leaf"')
            items = L.search(db, record_type='item')['records']
            scrolls = [r for r in items if r['display_name'] == 'Heart Awakening Scroll']
            self.assertEqual({r['rarity'] for r in scrolls}, {'2', '3'})
            self.assertTrue(all(r['game_id'] is None for r in scrolls))
            self.assertTrue(all(r['element'] == 'Heart' for r in scrolls))
            self.assertTrue(all(r['local_image_path'] == 'assets/fixtures/scroll_9999.webp' for r in scrolls))
            crystal = next(r for r in items if r['display_name'] == 'Heart Crystal')
            self.assertEqual(crystal['game_id'], '77')
            self.assertEqual(crystal['metadata']['element_code'], 1)
            self.assertTrue(any(r['kind'] == 'awakening_material' for r in L.get_record(db, by_id['1001']['id'])['related']))
            stage = next(r for r in L.search(db, record_type='stage')['records'] if r['game_id'] == '51')
            self.assertEqual(stage['metadata']['difficulty'], 'A')
            self.assertTrue(any(r['kind'] == 'reward' for r in L.get_record(db, stage['id'])['related']))
            unknown = L.search(db, status='unidentified')['records']
            self.assertEqual(len(unknown), 1)
            self.assertIsNone(unknown[0]['game_id'])
            self.assertEqual(unknown[0]['display_name'], 'unused_12345.webp')

    def test_rebuild_preserves_manual_edits_ids_and_exports(self):
        first = self.run_build()
        path = self.root / 'data/library.sqlite3'
        with contextlib.closing(L.connect(path)) as db:
            before = {r['id'] for r in L.search(db)['records']}
            rid = L.search(db, record_type='item', rarity='2', q='Scroll')['records'][0]['id']
            L.annotate(db, rid, {'display_name': 'Verified Scroll', 'notes': 'Manual sample',
                                'verification_status': 'manually_verified'})
        second = self.run_build()
        self.assertEqual(first['total_records'], second['total_records'])
        with contextlib.closing(L.connect(path, readonly=True)) as db:
            self.assertEqual(before, {r['id'] for r in L.search(db)['records']})
            self.assertEqual(L.get_record(db, rid)['display_name'], 'Verified Scroll')
            self.assertEqual(L.search(db, q='Manual sample')['total'], 1)
        exported = json.loads((self.root / 'catalogs/items.json').read_text('utf-8'))
        saved = next(r for r in exported if r['id'] == rid)
        self.assertEqual(saved['verification_status'], 'manually_verified')
        self.assertEqual(saved['notes'], 'Manual sample')

    def test_malformed_nested_form_does_not_abort_other_characters(self):
        self.ninjas['ninjas'].insert(0, {'id': 'bad', 'name': 'Malformed Source',
            'forms': [{'cardNo': 9001, 'stars': 4, 'awakening': 'invalid'}]})
        summary = self.run_build()
        self.assertTrue(summary['extraction_failures'])
        with contextlib.closing(L.connect(self.root / 'data/library.sqlite3', readonly=True)) as db:
            self.assertEqual(L.search(db, q='Uzumaki', record_type='character')['total'], 2)


if __name__ == '__main__':
    unittest.main()
