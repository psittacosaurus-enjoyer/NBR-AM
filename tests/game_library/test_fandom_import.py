"""Second-source import regressions use isolated SQLite and image fixtures only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/game_library'))
import library as L
import build
from fandom_import import import_records
from fandom_source import extract_encyclopedia, enrich_material


class FandomExtractionTests(unittest.TestCase):
    def fixture(self):
        return '''{| class="wikitable"
! No. !! Picture !! Name !! Rarity !! Element !! Type
|-
|3001
|[[File:Material_9999.png|thumb]]
|[[Heart Awakening Scroll]]
|★★
|[[File:Heart.png|link=Category:Heart]]
|Awakening
|-
|3002
|[[File:Ninja_42.png]]
|[[Naruto Uzumaki]]
|★★★★★★
|[[File:Heart.png|link=Category:Heart]]
|Ninja
|-
|unknown
|[[File:Material_5555.png]]
|[[Missing Verified Number]]
|★
|
|Awakening
|}'''

    def test_explicit_item_number_name_rarity_and_element_are_extracted_without_filename_guessing(self):
        records, excluded = extract_encyclopedia(self.fixture())
        self.assertEqual(len(records), 1)
        item = records[0]
        self.assertEqual(item['game_id'], '3001')
        self.assertEqual(item['title'], 'Heart Awakening Scroll')
        self.assertEqual(item['rarity'], 2)
        self.assertEqual(item['element'], 'Heart')
        self.assertEqual(item['category'], 'Awakening')
        self.assertEqual(item['metadata']['image_filenames'], ['Material_9999.png'])
        self.assertIn('explicit No.', item['metadata']['game_id_evidence'])
        self.assertEqual(excluded.get('Ninja'), 1)
        self.assertEqual(item['source_url'], 'https://naruto-blazing.fandom.com/wiki/Heart_Awakening_Scroll')

    def test_material_enrichment_retains_conflicting_source_values_and_excludes_adjacent_card_art(self):
        record = extract_encyclopedia(self.fixture())[0][0]
        wiki = '''{{Material
|card-no=4001
|rarity=★★★
|mat-image=[[File:Own_Card.png]]
|mat-artwork=[[File:Own_Artwork.png]]
|next-card=[[File:Different_Item.png]]
|additional-information=Awakens [[Heart]] units.<br/>Preserves rarity.
|how-obtain=[[Limit Break]] missions
}}
{{Skill Materials}}'''
        page = {'pageid': 123, 'title': 'Heart Awakening Scroll',
                'revisions': [{'slots': {'main': {'*': wiki}}}]}
        result = enrich_material(record, page)
        self.assertEqual((result['game_id'], result['rarity'], result['element']), ('3001', 2, 'Heart'))
        conflicts = {d['field'] for d in result['metadata']['source_discrepancies']}
        self.assertEqual(conflicts, {'card_no', 'rarity', 'element'})
        self.assertIn('4001', result['metadata']['material_parameters']['card-no'])
        self.assertIn('Awakens Heart units.', result['description'])
        self.assertEqual(result['metadata']['image_roles']['Own_Card.png'], 'card')
        self.assertEqual(result['metadata']['image_roles']['Own_Artwork.png'], 'artwork')
        self.assertNotIn('Different_Item.png', result['metadata']['image_roles'])
        self.assertIn('https://naruto-blazing.fandom.com/wiki/Limit_Break', result['related_links'])


class FandomImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-second-source-test-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/library.sqlite3'
        self.db = L.connect(self.path)
        L.initialize(self.db)
        self.gacha_url = 'https://gachawiki.net/naruto-blazing/items/heart-scroll'
        self.fandom_url = 'https://naruto-blazing.fandom.com/wiki/Heart_Awakening_Scroll'
        self.original_id, _ = L.upsert_record(self.db, record_type='item',
            source_key='heart-scroll:2', display_name='Heart Awakening Scroll',
            source_url=self.gacha_url, game_id='3001', category='scroll', element='Heart', rarity='2',
            metadata={'original': {'kind': 'scroll', 'name': 'Heart Awakening Scroll',
                                  'stars': 2, 'element': 'Heart', 'unit': 3001},
                      'description': 'Used to awaken Heart units.'})
        self.original_image = self.image('existing.png')
        self.asset_id = L.stable_id('asset', 'gacha-heart-scroll')
        self.db.execute('''INSERT INTO assets(id,source_image_url,original_filename,
          local_image_path,sha256,width,height,perceptual_hash,status,collected_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',
          (self.asset_id, 'https://gachawiki.net/naruto-blazing/icons/scroll_3001.png',
           'scroll_3001.png', str(self.original_image.relative_to(self.root)),
           hashlib.sha256(self.original_image.read_bytes()).hexdigest(), 48, 48,
           self.perceptual_hash(self.original_image), 'ok', L.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (self.original_id, self.asset_id, 'icon'))
        self.db.commit()
        L.annotate(self.db, self.original_id, {'display_name': 'Reviewed Heart Scroll',
            'notes': 'Existing verified reward label', 'verification_status': 'manually_verified'})
        self.db.close()

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, size=48, alternate=False):
        image = Image.new('RGBA', (size, size), (24, 28, 40, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((size//8, size//8, size*7//8, size*7//8),
                       fill=(170, 48, 25, 255) if not alternate else (25, 120, 180, 255),
                       outline=(248, 210, 80, 255), width=max(2, size//12))
        draw.line((size//4, size*3//4, size//2, size//4, size*3//4, size*3//4),
                  fill=(250, 245, 210, 255), width=max(2, size//8))
        target = self.root / 'assets/items' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return target

    def incoming(self, **changes):
        record = {'source_key': 'Heart_Awakening_Scroll:2',
            'title': 'Heart Awakening Scroll', 'name': 'Heart Awakening Scroll',
            'source_url': self.fandom_url, 'game_id': '3001', 'category': 'Awakening',
            'element': 'Heart', 'rarity': '2', 'description': 'Used to awaken Heart units.',
            'metadata': {'source': 'Fandom', 'source_type': 'Awakening',
                         'game_id_namespace': 'card_no', 'game_id_evidence': 'Encyclopedia explicit No. column'},
            'images': [self.image_record(self.original_image)]}
        record.update(changes)
        return record

    def image_record(self, path, **changes):
        with Image.open(path) as image:
            width, height = image.size
        record = {'url': 'https://static.wikia.nocookie.net/naruto-blazing/images/a/ab/' + path.name,
            'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'width': width, 'height': height, 'perceptual_hash': self.perceptual_hash(path),
            'format': 'PNG', 'has_alpha': True}
        record.update(changes)
        return record

    @staticmethod
    def perceptual_hash(path):
        with Image.open(path) as image:
            gray = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.get_flattened_data() if hasattr(gray, 'get_flattened_data') else gray.getdata())
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | int(pixels[y*9+x] > pixels[y*9+x+1])
        return f'{bits:016x}'

    def importer(self, records, **kwargs):
        return import_records(records, db_path=self.path, root=self.root, **kwargs)

    def counts(self):
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            return {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                    for table in ('records', 'assets', 'record_assets')}

    def references(self):
        with patch.object(L, 'ROOT', self.root), L.ReferenceLibrary(self.path) as lib:
            return list(lib.item_references())

    def test_exact_image_reuses_asset_and_preserves_manual_identity_and_both_sources(self):
        before = self.counts()
        original_bytes = self.original_image.read_bytes()
        report = self.importer([self.incoming()])
        self.assertIsInstance(report, dict)
        self.assertEqual(report['new_identities'], 0)
        self.assertEqual(report['existing_identities_enriched'], 1)
        self.assertEqual(report['exact_duplicates_avoided'], 1)
        self.assertEqual(report['usable_reference_images_added'], 0)
        after = self.counts()
        self.assertEqual(after['records'], before['records'])
        self.assertEqual(after['assets'], before['assets'])
        self.assertEqual(self.original_image.read_bytes(), original_bytes)
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            detail = L.get_record(db, self.original_id)
        self.assertEqual(detail['id'], self.original_id)
        self.assertEqual(detail['display_name'], 'Reviewed Heart Scroll')
        self.assertEqual(detail['notes'], 'Existing verified reward label')
        self.assertEqual(detail['verification_status'], 'manually_verified')
        serialized = json.dumps(detail)
        self.assertIn(self.gacha_url, serialized)
        self.assertIn(self.fandom_url, serialized)
        self.assertIn(self.incoming()['images'][0]['url'], serialized)
        refs = self.references()
        self.assertEqual([r['id'] for r in refs], [self.original_id])
        self.assertEqual(len(refs[0]['reference_paths']), 1)
        self.assertEqual(list((self.root / 'assets/items').glob('*')), [self.original_image])
        with contextlib.closing(L.connect(report['backup'], readonly=True)) as backup:
            saved = L.get_record(backup, self.original_id)
        self.assertEqual(saved['display_name'], 'Reviewed Heart Scroll')
        self.assertEqual(saved['notes'], 'Existing verified reward label')
        self.assertNotIn(self.fandom_url, json.dumps(saved))

    def test_repeated_import_does_not_multiply_records_assets_or_provenance(self):
        self.importer([self.incoming()])
        first = self.counts()
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            detail = L.get_record(db, self.original_id)
        repeated_report = self.importer([self.incoming()])
        self.assertEqual(self.counts(), first)
        self.assertEqual(repeated_report['unchanged_source_records'], 1)
        self.assertEqual(repeated_report['new_identities'], 0)
        self.assertEqual(repeated_report['usable_reference_images_added'], 0)
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            repeated = L.get_record(db, self.original_id)
        self.assertEqual(repeated['id'], detail['id'])
        self.assertEqual(repeated['display_name'], detail['display_name'])
        self.assertEqual(len(repeated['assets']), len(detail['assets']))
        if 'sources' in detail:
            self.assertEqual(len(repeated['sources']), len(detail['sources']))

    def test_dry_run_preserves_database_and_original_images(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        assets = {p.relative_to(self.root): p.read_bytes() for p in (self.root/'assets').rglob('*') if p.is_file()}
        counts = self.counts()
        report = self.importer([self.incoming()], dry_run=True)
        self.assertIsInstance(report, dict)
        self.assertTrue(report['dry_run'])
        self.assertNotIn('backup', report)
        self.assertFalse((self.root / 'backups').exists())
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(assets, {p.relative_to(self.root): p.read_bytes() for p in (self.root/'assets').rglob('*') if p.is_file()})

    def test_shared_art_without_identity_metadata_does_not_force_merge(self):
        unknown = self.incoming(source_key='Unknown_Crystal', title='Unidentified Crystal',
            name='Unidentified Crystal', source_url='https://naruto-blazing.fandom.com/wiki/Unknown_Crystal',
            game_id=None, rarity=None, element=None, category='crystal', description=None)
        self.importer([unknown])
        self.assertEqual(self.counts()['records'], 2)
        self.assertEqual(self.counts()['assets'], 1)
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            original = L.get_record(db, self.original_id)
            all_rows = L.search(db, record_type='item')['records']
        self.assertEqual(original['display_name'], 'Reviewed Heart Scroll')
        self.assertTrue(any(r['id'] != self.original_id for r in all_rows))

    def test_conflicting_verified_rarity_or_element_is_not_forced_into_original_item(self):
        for field, value in (('rarity', '3'), ('element', 'Skill')):
            with self.subTest(field=field):
                # Each source key names a distinct contradictory source claim.
                incoming = self.incoming(source_key='conflict-' + field,
                    source_url='https://naruto-blazing.fandom.com/wiki/Conflict_' + field,
                    **{field: value})
                before = self.counts()['records']
                report = self.importer([incoming])
                self.assertEqual(self.counts()['records'], before + 1)
                self.assertGreaterEqual(report['unresolved_identity_conflicts'], 1)
                self.assertTrue(report['conflicts'])
                with contextlib.closing(L.connect(self.path, readonly=True)) as db:
                    original = L.get_record(db, self.original_id)
                self.assertEqual(original['rarity'], '2')
                self.assertEqual(original['element'], 'Heart')
                self.assertEqual(original['display_name'], 'Reviewed Heart Scroll')

    def test_same_name_and_art_with_different_known_ids_in_one_namespace_stay_separate(self):
        with contextlib.closing(L.connect(self.path)) as db:
            row = db.execute('SELECT metadata FROM records WHERE id=?', (self.original_id,)).fetchone()
            metadata = json.loads(row[0])
            metadata['game_id_namespace'] = 'card_no'
            db.execute('UPDATE records SET metadata=? WHERE id=?', (json.dumps(metadata), self.original_id))
            db.commit()
        incoming = self.incoming(source_key='different-card-number', game_id='3002',
            title='  HEART   AWAKENING SCROLL  ', name='  HEART   AWAKENING SCROLL  ')
        report = self.importer([incoming])
        self.assertEqual(report['new_identities'], 1)
        self.assertGreaterEqual(report['unresolved_identity_conflicts'], 1)
        self.assertEqual(self.counts()['records'], 2)
        self.assertEqual(self.counts()['assets'], 1)
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            original = L.get_record(db, self.original_id)
            item_ids = {r['game_id'] for r in L.search(db, record_type='item')['records']}
            self.assertEqual(db.execute('SELECT count(*) FROM identity_aliases').fetchone()[0], 0)
        self.assertEqual(original['game_id'], '3001')
        self.assertEqual(original['display_name'], 'Reviewed Heart Scroll')
        self.assertEqual(item_ids, {'3001', '3002'})
        refs = self.references()
        self.assertEqual(len({r['id'] for r in refs}), 2)

    def test_gacha_build_preserves_imported_sources_assets_aliases_and_reference_links(self):
        with contextlib.closing(L.connect(self.path)) as db:
            alias, _ = L.upsert_record(db, record_type='item', source_key='preexisting-equivalent-scroll',
                display_name='Heart Awakening Scroll', source_url=self.gacha_url + '?alias=1',
                game_id='3001', category='scroll', element='Heart', rarity='2',
                metadata={'original': {'name': 'Heart Awakening Scroll', 'kind': 'scroll',
                                      'stars': 2, 'element': 'Heart', 'unit': 3001}})
            db.execute('INSERT INTO record_assets VALUES(?,?,?)', (alias, self.asset_id, 'icon'))
            db.commit()
        added_image = self.image('new-skill-crystal.png', alternate=True)
        genuinely_new = self.incoming(source_key='Skill_Crystal:4', title='Skill Crystal', name='Skill Crystal',
            source_url='https://naruto-blazing.fandom.com/wiki/Skill_Crystal', game_id='8001',
            category='Limit Break', element='Skill', rarity='4', images=[self.image_record(added_image)])
        self.importer([self.incoming(), genuinely_new])
        with contextlib.closing(L.connect(self.path)) as db:
            db.execute('INSERT INTO relationships VALUES(?,?,?)',
                       (alias, self.original_id, 'source_equivalence'))
            db.commit()
        preserved_tables = ('record_sources', 'asset_sources', 'identity_aliases',
                            'asset_preferences', 'assets', 'record_assets', 'relationships')
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            before = {table: {tuple(row) for row in db.execute('SELECT * FROM ' + table)}
                      for table in preserved_tables}
            stable_ids = {row[0] for row in db.execute('SELECT id FROM records')}
        self.assertTrue(before['identity_aliases'])
        self.assertEqual(len(stable_ids), 3)
        expected_reference_ids = {record['id'] for record in self.references()}
        original_files = {path: path.read_bytes() for path in (self.root/'assets').rglob('*') if path.is_file()}
        (self.root / 'data/manifest.json').write_text(json.dumps({'resources': []}), encoding='utf-8')
        builder = build.Builder(root=self.root, db_path=self.path)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                builder.run()
        finally:
            builder.db.close()
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT id FROM records')}, stable_ids)
            for table, expected in before.items():
                with self.subTest(table=table):
                    self.assertEqual({tuple(row) for row in db.execute('SELECT * FROM ' + table)}, expected)
            self.assertEqual(L.get_record(db, self.original_id)['display_name'], 'Reviewed Heart Scroll')
        self.assertEqual({record['id'] for record in self.references()}, expected_reference_ids)
        self.assertTrue(all(path.read_bytes() == data for path, data in original_files.items()))

    def test_confirmed_old_alias_keeps_both_stable_rows_but_one_recognition_identity(self):
        with contextlib.closing(L.connect(self.path)) as db:
            alias, _ = L.upsert_record(db, record_type='item', source_key='duplicate-source-scroll',
                display_name='Heart Awakening Scroll', source_url=self.gacha_url + '?variant=duplicate',
                game_id='3001', category='scroll', element='Heart', rarity='2',
                metadata={'original': {'kind': 'scroll', 'name': 'Heart Awakening Scroll',
                                      'stars': 2, 'element': 'Heart', 'unit': 3001}})
            db.execute('INSERT INTO record_assets VALUES(?,?,?)', (alias, self.asset_id, 'icon'))
            db.commit()
        self.importer([self.incoming()])
        self.assertEqual(self.counts()['records'], 2)
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            self.assertIsNotNone(L.get_record(db, alias))
            self.assertIsNotNone(L.get_record(db, self.original_id))
        refs = self.references()
        self.assertEqual([r['id'] for r in refs], [self.original_id])
        self.assertEqual(len(refs[0]['reference_paths']), 1)

    def test_higher_resolution_equivalent_art_is_one_item_with_original_variant_retained(self):
        high_resolution = self.image('fandom-high-resolution.png', size=96)
        incoming = self.incoming(images=[self.image_record(high_resolution)])
        original_bytes = self.original_image.read_bytes()
        report = self.importer([incoming])
        self.assertEqual(self.counts()['records'], 1)
        self.assertEqual(report['new_identities'], 0)
        self.assertEqual(report['usable_reference_images_added'], 1)
        self.assertGreaterEqual(report['visually_similar_images_flagged'], 1)
        self.assertIn(self.asset_id, {r['existing_asset_id'] for r in report['similarities']})
        refs = self.references()
        self.assertEqual([r['id'] for r in refs], [self.original_id])
        self.assertEqual(self.original_image.read_bytes(), original_bytes)
        self.assertTrue(any(Path(p).read_bytes() == high_resolution.read_bytes()
                            for p in refs[0]['reference_paths']))
        self.assertTrue(any(Path(p).read_bytes() == original_bytes
                            for p in refs[0]['reference_paths']))
        with contextlib.closing(L.connect(self.path, readonly=True)) as db:
            preferred = L.get_record(db, self.original_id)['local_image_path']
        with Image.open(self.root / preferred) as image:
            self.assertEqual(image.size, (96, 96))


if __name__ == '__main__':
    unittest.main()
