"""Deduplication works only on disposable library copies, without device access."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from game_library import library as L
from game_library.deduplicate import deduplicate_copy, consolidate


class DeduplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-deduplicate-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/data/library.sqlite3'
        self.output = self.root / 'candidate/library.sqlite3'
        self.db = L.connect(self.source)
        L.initialize(self.db)
        self.addCleanup(self.db.close)

    def record(self, key, name='Test Material', *, card=None, size=64, rarity='3',
               game_id=None, scope=None, record_type='item', sha=None):
        rid, _ = L.upsert_record(self.db, record_type=record_type, source_key=key,
            display_name=name, source_url='https://example.test/' + key, game_id=game_id,
            category='crystal', element='Body', rarity=rarity,
            metadata={'game_id_namespace': scope} if scope else {})
        aid = 'asset-' + key
        url = ('https://gachawiki.net/' + ('lb' if key.endswith('b') else 'missions') +
               '/card-' + str(card) + '.png') if card else 'https://example.test/images/' + key + '.png'
        self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (aid, url, key + '.png', 'assets/' + key + '.png', None,
                         sha or key, size, size, None, 'ok', L.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, aid, 'icon'))
        return rid, aid

    def run_copy(self, calibration=None, preferred=()):
        L.index(self.db)
        self.db.commit()
        # Close the fixture writer so source-byte invariance includes WAL checkpoint behavior.
        self.db.close()
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        path = None
        if calibration:
            path = self.root / 'calibration.json'
            path.write_text(json.dumps(calibration), encoding='utf-8')
        result = deduplicate_copy(self.source, self.output, path, preferred)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        return result

    def test_source_card_duplicates_physically_removed_with_fidelity_and_provenance(self):
        first, _ = self.record('first-a', card='0949', size=64)
        second, best_asset = self.record('second-b', name='Alternate Material Name', card=949, size=256,
                                         game_id='949', scope='card_no')
        character, _ = self.record('character', record_type='character')
        self.db.execute('INSERT INTO relationships VALUES(?,?,?)', (second, character, 'used_by'))
        report = self.run_copy(preferred=[first])
        self.assertEqual(report['removed_item_records'], 1)
        self.assertEqual(report['mapping'][second], first)
        self.assertEqual(report['preferred_assets'][first]['id'], best_asset)
        with closing(L.connect(self.output, readonly=True)) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM records WHERE id=?', (second,)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM records WHERE record_type="character"').fetchone()[0], 1)
            self.assertEqual(tuple(db.execute('SELECT * FROM relationships').fetchone()), (first, character, 'used_by'))
            self.assertEqual(db.execute('SELECT canonical_id FROM identity_aliases WHERE alias_id=?', (second,)).fetchone()[0], first)
            self.assertEqual(L.get_record(db, second)['id'], first)
            self.assertEqual(db.execute('SELECT count(*) FROM record_assets WHERE record_id=?', (first,)).fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM record_search WHERE record_id=?', (second,)).fetchone()[0], 0)
            self.assertEqual(L.search(db, q='Alternate Material')['records'][0]['id'], first)
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])

    def test_artwork_and_name_alone_do_not_merge_different_items(self):
        self.record('first', sha='identical')
        self.record('second', sha='identical')
        report = self.run_copy()
        self.assertEqual(report['removed_item_records'], 0)
        self.assertEqual({entry['kind'] for entry in report['unproven_collisions']}, {'same_name', 'identical_artwork'})

    def test_conflicting_rarity_blocks_shared_card_merge(self):
        self.record('first-a', card=949, rarity='3')
        self.record('second-b', card=949, rarity='4')
        report = self.run_copy()
        self.assertEqual(report['removed_item_records'], 0)
        self.assertTrue(report['rejected_merges'])
        self.assertEqual(report['collision_counts']['confirmed_distinct'], 1)

    def test_material_and_fandom_card_proof_merges_special_awakening_tool_taxonomy(self):
        first, _ = self.record('first', name='Sand Gourd "Special Awakening Tool"', game_id='957', scope='card_no')
        second, _ = self.record('second', name='Sand Gourd "Special Awakening Tool"')
        self.db.execute('UPDATE records SET category=?,metadata=? WHERE id=?',
            ('scroll', json.dumps({'game_id_namespace': 'card_no', 'source': 'Fandom',
                                  'source_data': {'source_type': 'Awakening'}}), first))
        self.db.execute('UPDATE records SET category=? WHERE id=?', ('special', second))
        self.db.execute('UPDATE assets SET source_image_url=? WHERE id=?',
                        ('https://gachawiki.net/materials/card-0957.webp', 'asset-second'))
        self.db.execute('UPDATE assets SET source_image_url=? WHERE id=?',
                        ('https://example.test/Card-0957.png/revision/latest', 'asset-first'))
        report = self.run_copy()
        self.assertEqual(report['removed_item_records'], 1)
        self.assertEqual(report['remaining_proven_duplicate_groups'], [])

    def test_equal_scoped_game_id_and_explicit_alias_are_merged(self):
        first, _ = self.record('first', game_id='55', scope='unit')
        second, _ = self.record('second', game_id='55', scope='unit')
        third, _ = self.record('third')
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)', (third, first, 'Reviewed source identity'))
        report = self.run_copy(preferred=[first])
        self.assertEqual(report['removed_item_records'], 2)
        self.assertEqual({report['mapping'][rid] for rid in (first, second, third)}, {first})

    def test_preferred_calibration_id_and_stricter_member_gates_survive(self):
        first, _ = self.record('first-a', card=949)
        second, _ = self.record('second-b', card=949)
        calibration = {'threshold': .88, 'margin': .04, 'supported_item_ids': [first, second],
                       'canonical_groups': [{'canonical_item_id': second, 'member_ids': [first, second], 'evidence': ['reviewed']}],
                       'per_identity_thresholds': {first: {'threshold': .97, 'margin': .08}}}
        report = self.run_copy(calibration)
        self.assertEqual(report['mapping'][first], second)
        self.assertEqual(report['calibration_gates_remapped'][second]['threshold'], .97)
        self.assertEqual(report['calibration_gates_remapped'][second]['margin'], .08)

    def test_unsupported_member_limits_and_derived_namespace_conflicts_are_preserved(self):
        first, _ = self.record('first-a', card=949)
        second, _ = self.record('second-b', card=949)
        third, _ = self.record('third', game_id='1')
        fourth, _ = self.record('fourth', game_id='2')
        for rid in (third, fourth):
            self.db.execute('UPDATE records SET metadata=? WHERE id=?',
                            (json.dumps({'game_id_kind': 'unit'}), rid))
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)', (fourth, third, 'Old conflicting alias'))
        report = self.run_copy({'threshold': .88, 'margin': .04, 'supported_item_ids': [first],
                                'per_identity_thresholds': {second: {'threshold': .98, 'margin': .12}}}, [first])
        self.assertEqual(report['removed_item_records'], 1)
        self.assertTrue(report['rejected_merges'])
        self.assertEqual(report['calibration_gates_remapped'][first]['threshold'], .98)
        self.assertEqual(report['calibration_gates_remapped'][first]['margin'], .12)

    def test_source_and_existing_output_refused(self):
        with self.assertRaises(ValueError):
            deduplicate_copy(self.source, self.source)
        self.output.parent.mkdir()
        self.output.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            deduplicate_copy(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep')

    def test_transactional_consolidation_preserves_alias_writes_sources_and_rolls_back(self):
        first, _ = self.record('first-a', card=949)
        second, _ = self.record('second-b', card=949)
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError, 'abort'):
            with self.db:
                consolidate(self.db, preferred_ids=[first])
                self.assertEqual(L.resolve_id(self.db, second), first)
                raise RuntimeError('abort')
        self.assertEqual(self.db.execute('SELECT count(*) FROM records').fetchone()[0], 2)
        with self.db:
            report = consolidate(self.db, preferred_ids=[first])
        self.assertEqual(report['mapping'][second], first)
        L.annotate(self.db, second, {'notes': 'Historical alias remains writable', 'tags': ['reviewed']})
        canonical, _ = L.upsert_record(self.db, record_type='item', source_key='second-b',
            display_name='New source spelling', source_url='https://example.test/second-b',
            category='crystal', element='Body', rarity='3')
        self.assertEqual(canonical, first)
        self.assertEqual(self.db.execute('SELECT count(*) FROM records').fetchone()[0], 1)
        detail = L.get_record(self.db, second)
        self.assertEqual(detail['id'], first)
        self.assertEqual(detail['display_name'], 'Test Material')
        self.assertEqual(detail['notes'], 'Historical alias remains writable')
        self.assertIn('New source spelling', json.dumps(detail['sources']))
        with self.db:
            repeated = consolidate(self.db, preferred_ids=[first])
        self.assertEqual(repeated['merged_groups'], [])
        self.assertEqual(repeated['removed_asset_rows'], 0)
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')), [])

    def test_legacy_alias_schema_migrates_in_callers_transaction(self):
        first, _ = self.record('first')
        second, _ = self.record('second')
        self.db.execute('DROP TABLE identity_aliases')
        self.db.execute('CREATE TABLE identity_aliases (alias_id TEXT PRIMARY KEY REFERENCES records(id),'
                        'canonical_id TEXT NOT NULL REFERENCES records(id),evidence TEXT NOT NULL)')
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)', (second, first, 'Reviewed identity'))
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError, 'abort migration'):
            with self.db:
                consolidate(self.db, preferred_ids=[first])
                raise RuntimeError('abort migration')
        self.assertIn('alias_id', {row[3] for row in self.db.execute('PRAGMA foreign_key_list(identity_aliases)')})
        self.assertFalse(L.has_table(self.db, 'identity_aliases_new'))
        with self.db:
            consolidate(self.db, preferred_ids=[first])
        self.assertEqual(L.get_record(self.db, second)['id'], first)
        self.assertEqual(self.db.execute('SELECT count(*) FROM records').fetchone()[0], 1)
        self.assertNotIn('alias_id', {row[3] for row in self.db.execute('PRAGMA foreign_key_list(identity_aliases)')})
        self.assertIn('canonical_id', {row[3] for row in self.db.execute('PRAGMA foreign_key_list(identity_aliases)')})
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')), [])

    def test_exact_asset_merge_retains_record_specific_urls_roles_and_source_rebuilds(self):
        first, first_asset = self.record('first', record_type='character', sha='same-pixels')
        second, second_asset = self.record('second', record_type='character', sha='same-pixels')
        self.db.execute('UPDATE assets SET source_image_url=? WHERE id=?',
                        ('https://example.test/abilities/shared.png', first_asset))
        self.db.execute('UPDATE assets SET source_image_url=? WHERE id=?',
                        ('https://example.test/ninjas/shared.png', second_asset))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (second, second_asset, 'card'))
        with self.db:
            report = consolidate(self.db)
        self.assertEqual(report['removed_asset_rows'], 1)
        canonical_asset = report['asset_mapping'][second_asset]
        detail = L.get_record(self.db, second)
        self.assertEqual({a['role'] for a in detail['assets']}, {'icon', 'card'})
        self.assertTrue(all(a['source_image_urls'] == ['https://example.test/ninjas/shared.png'] for a in detail['assets']))
        self.assertEqual(L.get_record(self.db, first)['assets'][0]['source_image_urls'],
                         ['https://example.test/abilities/shared.png'])
        original = dict(self.db.execute('SELECT * FROM assets WHERE id=?', (canonical_asset,)).fetchone())
        original.update(id=second_asset, source_image_url='https://example.test/ninjas/shared.png')
        self.assertEqual(L.upsert_asset(self.db, original), canonical_asset)
        self.assertEqual(self.db.execute('SELECT count(*) FROM assets').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM asset_sources').fetchone()[0], 2)

    def test_changed_shared_source_splits_only_its_links_and_preserves_other_bytes(self):
        for changing in ('a', 'b'):
            with self.subTest(changing=changing):
                db=sqlite3.connect(':memory:')
                db.row_factory=sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                L.initialize(db)
                def asset(key, digest='old'):
                    url='https://example.test/'+key
                    return dict(id=L.stable_id('asset',url),source_image_url=url,
                        original_filename=key+'.png',local_image_path='assets/'+digest+'.png',
                        thumbnail_path=None,sha256=digest,width=1,height=1,
                        perceptual_hash=None,status='ok',collected_at=L.now())
                records={}
                for key in ('a','b'):
                    rid,_=L.upsert_record(db,record_type='item',source_key=key,
                        display_name=key,source_url='https://example.test/record/'+key)
                    records[key]=rid
                    aid=L.upsert_asset(db,asset(key))
                    L.link_asset(db,rid,aid,'icon',asset(key)['source_image_url'])
                    db.execute('INSERT INTO asset_preferences VALUES(?,?,?,?)',(rid,aid,10,'Reviewed'))
                old=aid
                new=L.upsert_asset(db,asset(changing,'new'))
                self.assertNotEqual(new,old)
                other='b' if changing=='a' else 'a'
                for key,digest in ((changing,'new'),(other,'old')):
                    rows=db.execute('SELECT a.sha256,a.local_image_path FROM assets a JOIN record_assets ra '
                                    'ON a.id=ra.asset_id WHERE ra.record_id=?',(records[key],)).fetchall()
                    self.assertEqual([tuple(row) for row in rows],[(digest,'assets/'+digest+'.png')])
                    self.assertEqual(db.execute('SELECT count(*) FROM asset_preferences WHERE record_id=?',
                                                (records[key],)).fetchone()[0],1)
                self.assertEqual(L.upsert_asset(db,asset(changing,'new')),new)
                self.assertEqual(db.execute('SELECT count(*) FROM assets').fetchone()[0],2)
                # A subsequent update converges both sources onto existing bytes.
                self.assertEqual(L.upsert_asset(db,asset(other,'new')),new)
                self.assertEqual(db.execute('SELECT count(*) FROM assets').fetchone()[0],1)
                self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
                db.close()

    def test_repeated_source_versions_allocate_an_unused_id_after_rejoining_shared_bytes(self):
        def asset(key,digest):
            url='https://example.test/'+key
            return dict(id=L.stable_id('asset',url),source_image_url=url,original_filename=key+'.png',
                        local_image_path='assets/'+digest+'.png',thumbnail_path=None,sha256=digest,
                        width=1,height=1,perceptual_hash=None,status='ok',collected_at=L.now())
        records={}
        for key in ('a','b','c'):
            records[key],_=L.upsert_record(self.db,record_type='item',source_key=key,
                display_name=key,source_url='https://example.test/record/'+key)
            aid=L.upsert_asset(self.db,asset(key,'0'))
            L.link_asset(self.db,records[key],aid,'icon',asset(key,'0')['source_image_url'])
        L.upsert_asset(self.db,asset('a','1'))
        L.upsert_asset(self.db,asset('a','2'))
        L.upsert_asset(self.db,asset('c','2'))
        restored=L.upsert_asset(self.db,asset('a','1'))
        self.assertEqual(L.upsert_asset(self.db,asset('a','1')),restored)
        for key,digest in (('a','1'),('b','0'),('c','2')):
            self.assertEqual({a['sha256'] for a in L.get_record(self.db,records[key])['assets']},{digest})
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])


if __name__ == '__main__':
    unittest.main()
