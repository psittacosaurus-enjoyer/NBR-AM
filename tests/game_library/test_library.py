"""Regression tests use isolated fixtures; no farming or real crawl data is touched."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/game_library'))
import library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-library-test-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'library.sqlite3'
        self.db = library.connect(self.path)
        library.initialize(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def record(self, key='scroll', **changes):
        values = dict(record_type='item', source_key=key, display_name='Awakening Scroll',
                      source_url='https://gachawiki.net/naruto-blazing/items/' + key,
                      game_id='42', category='material', element='Heart', rarity='4',
                      metadata={'description': 'Awakens a character'}, tags=['awakening'])
        values.update(changes)
        return library.upsert_record(self.db, **values)

    def asset(self, rid, name='scroll_0042.png', status='ok', role='icon', key=None):
        key = key or name
        aid = library.stable_id('asset', key)
        self.db.execute('''INSERT INTO assets(id,source_image_url,original_filename,
          local_image_path,thumbnail_path,sha256,width,height,perceptual_hash,status,collected_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
          (aid, 'https://gachawiki.net/images/' + key, name, 'assets/items/' + key,
           'assets/thumbnails/' + key, 'a'*64, 64, 64, '0'*16, status, library.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, aid, role))
        return aid

    def test_source_record_retains_unknowns_and_metadata(self):
        rid, change = self.record(game_id=None, element=None, rarity=None,
                                  verification_status='inferred')
        row = library.get_record(self.db, rid)
        self.assertEqual(change, 'new')
        self.assertIsNone(row['game_id'])
        self.assertIsNone(row['element'])
        self.assertIsNone(row['rarity'])
        self.assertEqual(row['verification_status'], 'inferred')
        self.assertEqual(row['metadata']['description'], 'Awakens a character')
        self.assertEqual(row['source_url'], 'https://gachawiki.net/naruto-blazing/items/scroll')
        self.assertEqual(row['assets'], [])

    def test_ids_and_duplicate_upserts_are_stable(self):
        with patch.object(library, 'now', return_value='first'):
            rid, _ = self.record()
        with patch.object(library, 'now', return_value='second'):
            second, change = self.record()
        self.assertEqual(rid, second)
        self.assertEqual(change, 'unchanged')
        self.assertEqual(library.get_record(self.db, rid)['updated_at'], 'first')
        self.assertEqual(self.db.execute('SELECT count(*) FROM records').fetchone()[0], 1)
        with patch.object(library, 'now', return_value='third'):
            third, change = self.record(display_name='Updated Scroll')
        row = library.get_record(self.db, rid)
        self.assertEqual(third, rid)
        self.assertEqual(change, 'updated')
        self.assertEqual(row['collected_at'], 'first')
        self.assertEqual(row['updated_at'], 'third')
        self.assertNotEqual(library.stable_id('item', '42'), library.stable_id('character', '42'))

    def test_transaction_rolls_back_when_a_source_record_is_invalid(self):
        with self.assertRaises(ValueError):
            with self.db:
                self.record()
                self.record('bad', record_type='invented')
        self.assertEqual(self.db.execute('SELECT count(*) FROM records').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.record(verification_status='guessed_verified')

    def test_annotations_survive_source_updates_and_reindex(self):
        rid, _ = self.record()
        self.db.commit()
        library.annotate(self.db, rid, {'display_name': 'My Confirmed Scroll',
            'category': 'reward', 'tags': ['mytag'], 'notes': 'Blue border sample',
            'verification_status': 'manually_verified'})
        self.record(display_name='Uncertain Source Name', category='misc',
                    verification_status='inferred', tags=['crawled'])
        library.index(self.db)
        self.db.commit()
        row = library.get_record(self.db, rid)
        self.assertEqual(row['display_name'], 'My Confirmed Scroll')
        self.assertEqual(row['category'], 'reward')
        self.assertEqual(row['tags'], ['mytag'])
        self.assertEqual(row['notes'], 'Blue border sample')
        self.assertEqual(row['verification_status'], 'manually_verified')
        self.assertEqual(library.search(self.db, q='Blue border')['total'], 1)
        self.assertEqual(library.search(self.db, q='mytag')['total'], 1)
        self.assertEqual(library.search(self.db, q='Uncertain')['total'], 0)
        with library.ReferenceLibrary(self.path) as reopened:
            self.assertEqual(reopened.get(rid)['display_name'], 'My Confirmed Scroll')

    def test_annotation_validation_is_atomic(self):
        rid, _ = self.record()
        self.db.commit()
        for changes in ({'tags': 'not-list'}, {'tags': [42]}, {'verification_status': 'wrong'},
                        {'display_name': '  '}, {'notes': None}, {'game_id': '99'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                library.annotate(self.db, rid, changes)
        self.assertEqual(self.db.execute('SELECT count(*) FROM annotations').fetchone()[0], 0)
        with self.assertRaises(KeyError):
            library.annotate(self.db, 'missing-id', {'notes': 'missing'})

    def test_search_name_id_filename_tags_metadata_and_filters(self):
        rid, _ = self.record()
        self.asset(rid)
        self.record('other', display_name='Wisdom Crystal', game_id='75', element='Wisdom', rarity='3')
        self.record('character', record_type='character', display_name='Naruto Uzumaki', game_id='999')
        library.index(self.db)
        for query in ('Awakening', '42', 'scroll_0042', 'awakening', 'Awakens'):
            with self.subTest(query=query):
                rows = library.search(self.db, q=query)['records']
                self.assertIn(rid, [r['id'] for r in rows])
        found = library.search(self.db, record_type='item', category='material',
            element='Heart', rarity='4', status='source_verified')
        self.assertEqual([r['id'] for r in found['records']], [rid])
        self.assertEqual(library.search(self.db, element='Body')['total'], 0)
        self.assertEqual(library.search(self.db, q='scroll OR Naruto')['total'], 0)

    def test_pagination_is_bounded_deterministic_and_counts_all_results(self):
        for n in range(7):
            self.record(str(n), display_name=f'Scroll {n}')
        library.index(self.db)
        first = library.search(self.db, limit=3)
        second = library.search(self.db, page=2, limit=3)
        final = library.search(self.db, page=3, limit=3)
        self.assertEqual((first['total'], len(final['records'])), (7, 1))
        self.assertEqual(len({r['id'] for page in (first, second, final) for r in page['records']}), 7)
        bounded = library.search(self.db, page=-2, limit=1000)
        self.assertEqual((bounded['page'], bounded['limit']), (1, 120))

    def test_multiple_asset_references_related_records_and_image_priority(self):
        item, _ = self.record()
        character, _ = self.record('naruto', record_type='character', display_name='Naruto')
        self.asset(item, 'scroll_icon.png', role='icon')
        self.asset(item, 'scroll_card.png', role='card')
        self.db.execute('INSERT INTO relationships VALUES(?,?,?)', (character, item, 'uses'))
        detail = library.get_record(self.db, item)
        self.assertEqual(len(detail['assets']), 2)
        self.assertEqual(detail['local_image_path'], 'assets/items/scroll_card.png')
        self.assertEqual(detail['related'][0]['id'], character)
        self.assertEqual(library.get_record(self.db, character)['related'][0]['id'], item)
        self.assertIsNone(library.get_record(self.db, 'missing'))

    def test_readonly_item_api_excludes_uncertain_and_missing_images(self):
        verified, _ = self.record('verified')
        manual, _ = self.record('manual')
        inferred, _ = self.record('uncertain', verification_status='inferred')
        missing, _ = self.record('missing')
        for rid, name in ((verified, 'verified.png'), (manual, 'manual.png'), (inferred, 'uncertain.png')):
            self.asset(rid, name)
        self.asset(missing, 'missing.png', status='error')
        self.db.commit()
        library.annotate(self.db, manual, {'verification_status': 'manually_verified'})
        with library.ReferenceLibrary(self.path) as refs:
            result = list(refs.item_references())
            self.assertEqual({r['id'] for r in result}, {verified, manual})
            self.assertTrue(all(Path(p).is_absolute() for r in result for p in r['reference_paths']))
            self.assertEqual([r['id'] for r in refs.item_references(True)], [manual])
            with self.assertRaises(sqlite3.OperationalError):
                refs.db.execute("UPDATE records SET display_name='changed'")

    def test_json_export_contains_effective_labels_and_relative_asset_paths(self):
        rid, _ = self.record(display_name='Source 日本語')
        self.asset(rid)
        self.db.commit()
        library.annotate(self.db, rid, {'display_name': 'Verified 日本語', 'notes': 'kept'})
        target = self.root / 'catalogs'
        library.export(self.db, target)
        rows = json.loads((target / 'items.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[0]['id'], rid)
        self.assertEqual(rows[0]['display_name'], 'Verified 日本語')
        self.assertEqual(rows[0]['notes'], 'kept')
        self.assertFalse(Path(rows[0]['assets'][0]['local_image_path']).is_absolute())
        self.assertEqual(json.loads((target / 'characters.json').read_text()), [])
        self.assertEqual(len(json.loads((target / 'assets.json').read_text())), 1)
        self.assertEqual(list(target.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
