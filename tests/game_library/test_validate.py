"""Offline source validation accepts canonical images with retained URL provenance."""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from game_library import library as L
from game_library.deduplicate import consolidate
from game_library.validate import validate


class CanonicalAssetValidationTests(unittest.TestCase):
    def test_source_associations_survive_exact_image_row_normalization(self):
        with tempfile.TemporaryDirectory(prefix='nbr-library-validation-') as folder:
            root = Path(folder)
            (root / 'data/raw').mkdir(parents=True)
            (root / 'assets').mkdir()
            original = root / 'assets/shared.png'
            thumbnail = root / 'assets/shared.webp'
            Image.new('RGBA', (12, 12), 'red').save(original)
            Image.new('RGB', (12, 12), 'red').save(thumbnail)
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            form = {'cardNo': 1, 'stars': 3, 'element': 'Body',
                    'card': '/naruto-blazing/ninjas/shared.png'}
            (root / 'data/raw/ninjas.json').write_text(json.dumps({'ninjas': [
                {'id': 'ninja', 'forms': [form]}]}), encoding='utf-8')
            with closing(L.connect(root / 'data/library.sqlite3')) as db:
                L.initialize(db)
                char, _ = L.upsert_record(db, record_type='character', source_key='ninja:1',
                    display_name='Ninja', source_url='https://gachawiki.net/naruto-blazing/ninjas/ninja',
                    game_id='1', element='Body', rarity='3', metadata={'original': form})
                item, _ = L.upsert_record(db, record_type='item', source_key='item',
                    display_name='Material', source_url='https://gachawiki.net/naruto-blazing/materials',
                    metadata={'occurrences': [{'record': {'icon': '/naruto-blazing/items/shared.png'}}]})
                for aid, url, rid, role in (
                        ('asset-a', '/naruto-blazing/abilities/shared.png', char, 'ability'),
                        ('asset-b', form['card'], char, 'card'),
                        ('asset-c', '/naruto-blazing/items/shared.png', item, 'icon')):
                    db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (aid, 'https://gachawiki.net' + url, 'shared.png',
                                'assets/shared.png', 'assets/shared.webp', digest,
                                12, 12, None, 'ok', L.now()))
                    db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, aid, role))
                with db:
                    result = consolidate(db)
                self.assertEqual(result['removed_asset_rows'], 2)
                db.execute('INSERT INTO assets(id,source_image_url,original_filename,sha256,status,collected_at) '
                           'VALUES(?,?,?,?,?,?)', ('failed', 'https://example.test/missing',
                                                  'missing.png', digest, 'failed', L.now()))
                db.commit()
                L.export(db, root / 'catalogs')
            with redirect_stdout(io.StringIO()):
                report = validate(root)
            self.assertTrue(report['passed'], report['failures'])
            self.assertEqual(report['checks']['character_forms_source_checked'], 1)
            self.assertEqual(report['checks']['items_source_checked'], 1)
            self.assertEqual(report['exact_duplicate_image_urls'], 0)


if __name__ == '__main__':
    unittest.main()
