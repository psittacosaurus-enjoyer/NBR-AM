"""Offline library identity, artwork and acquired-counter checks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from game_library import library
from manager_loot import LootGrid, acquired_count, enrich_loot_items, sort_loot_items


class ManagerLootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library_root = self.root / 'runtime' / 'library'
        self.database = self.library_root / 'data' / 'library.sqlite3'
        self.db = library.connect(self.database)
        self.addCleanup(self.db.close)
        library.initialize(self.db)

    def record(self, key, name='Scroll', record_type='item'):
        rid, _ = library.upsert_record(self.db, record_type=record_type, source_key=key,
                                       display_name=name, source_url='https://example.test/' + key)
        self.db.commit()
        return rid

    def asset(self, rid, key, *, role='icon', score=None, color='red', path=None,
              thumbnail=None, missing=False, corrupt=False, source_url=None):
        relative = path or f'assets/{key}.png'
        target = self.library_root / relative
        if not missing:
            target.parent.mkdir(parents=True, exist_ok=True)
            if corrupt:
                target.write_bytes(b'not an image')
            else:
                image = QImage(20, 20, QImage.Format.Format_ARGB32)
                image.fill(QColor(color))
                self.assertTrue(image.save(str(target)))
        self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (key, source_url or 'https://example.test/' + key, key + '.png', relative,
                         thumbnail, key, 20, 20, None, 'ok', library.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)', (rid, key, role))
        if score is not None:
            self.db.execute('INSERT INTO asset_preferences VALUES(?,?,?,?)', (rid, key, score, 'Reviewed'))
        self.db.commit()
        return target.resolve()

    def grid(self, items):
        grid = LootGrid()
        self.addCleanup(grid.close)
        grid.resize(380, 180)
        grid.set_items(items)
        grid.show()
        self.app.processEvents()
        return grid

    def test_exact_record_id_wins_over_identical_name_and_recorded_counters_are_preserved(self):
        first = self.record('first')
        second = self.record('second')
        correct = self.asset(first, 'correct', color='red')
        self.asset(second, 'wrong', score=99999, color='blue')
        item = {'item_id': first, 'item_name': 'Scroll', 'observed_icons': 99,
                'confirmed_acquired': 7, 'unknown_quantities': 0}
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        resolved = enrich_loot_items(self.root, [item])
        self.assertEqual(resolved[0]['icon_path'], str(correct))
        self.assertEqual(resolved[0]['icon_asset_id'], 'correct')
        self.assertEqual(resolved[0]['library_record_id'], first)
        self.assertNotIn('icon_path', item)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        grid = self.grid(resolved)
        tile = grid.tiles[0]
        self.assertEqual(tile.count_label.text(), '99')
        self.assertEqual(tile.icon_asset_id, 'correct')
        self.assertEqual(tile.icon_label.pixmap().toImage().pixelColor(35, 35), QColor('red'))

    def test_explicit_alias_and_canonical_preferences_choose_valid_card_over_artwork(self):
        canonical = self.record('canonical')
        alias = self.record('alias')
        self.db.execute('INSERT INTO identity_aliases VALUES(?,?,?)', (alias, canonical, 'Reviewed identity'))
        self.db.commit()
        self.asset(canonical, 'base', role='icon', score=10)
        preferred = self.asset(alias, 'preferred', role='card', color='green')
        self.db.execute('INSERT INTO asset_preferences VALUES(?,?,?,?)', (canonical, 'preferred', 50, 'Reviewed'))
        self.db.commit()
        self.asset(canonical, 'artwork', role='artwork', score=100000)
        item = enrich_loot_items(self.root, [{'item_id': alias}])[0]
        self.assertEqual(item['library_record_id'], canonical)
        self.assertEqual(item['icon_path'], str(preferred))
        self.assertEqual(item['icon_asset_id'], 'preferred')

    def test_character_ability_icon_cannot_replace_a_unit_portrait(self):
        unit = self.record('unit', 'Unit', 'character')
        card = self.asset(unit, 'card', role='card', score=1)
        self.asset(unit, 'ability', role='icon', score=100000,
                   source_url='https://example.test/abilities/unit.png')
        self.asset(unit, 'cutin', role='cutin', score=100001)
        self.assertEqual(enrich_loot_items(self.root, [{'item_id': unit}])[0]['icon_path'], str(card))

    def test_badges_count_one_drop_per_icon_instead_of_text_quantities(self):
        grid = self.grid([
            {'item_id': 'unknown', 'item_name': 'Unknown', 'observed_icons': 100,
             'confirmed_acquired': None, 'unknown_quantities': 3},
            {'item_id': 'partial', 'item_name': 'Partial', 'observed_icons': 2,
             'confirmed_acquired': 7, 'unknown_quantities': 1},
            {'item_id': 'known', 'item_name': 'Known', 'observed_icons': 99,
             'confirmed_acquired': 0, 'unknown_quantities': 0},
        ])
        self.assertEqual([tile.count_label.text() for tile in grid.tiles], ['100', '2', '99'])
        self.assertIn('100 drops', grid.tiles[0].count_label.toolTip())
        self.assertIn('2 drops', grid.tiles[1].toolTip())
        self.assertIn('one drop', grid.tiles[1].toolTip())
        self.assertEqual(acquired_count({'confirmed_acquired': True, 'observed_icons': 8})[0], '8')
        self.assertEqual(acquired_count({'drop_count': 4, 'observed_icons': 99})[0], '4')
        self.assertEqual(acquired_count({'drop_count': True})[0], '?')
        self.assertEqual(acquired_count({'confirmed_acquired': -1})[0], '?')

    def test_drop_sorting_keeps_counts_and_unidentified_drops_separate(self):
        items = [
            dict(item_id='body3', item_name='Body XL', element='Body', rarity='3',
                 last_drop_sequence=1, last_drop_at=100, drop_count=7),
            dict(item_id='wisdom4', item_name='Wisdom XXL', element='Wisdom', rarity='4',
                 last_drop_sequence=3, last_drop_at=110, drop_count=2),
            dict(item_id='body4', item_name='Body XXL', element='Body', rarity='4',
                 last_drop_sequence=2, last_drop_at=120, drop_count=4),
            dict(item_name='Unidentified', rarity=999, last_drop_sequence=99, drop_count=3),
        ]
        before = [dict(item) for item in items]
        for order, expected in (
            ('type', ['body4', 'body3', 'wisdom4', None]),
            ('rarity', ['body4', 'wisdom4', 'body3', None]),
            ('recent', ['wisdom4', 'body4', 'body3', None]),
        ):
            sorted_items = sort_loot_items(items, order)
            self.assertEqual([item.get('item_id') for item in sorted_items], expected)
            self.assertEqual(sum(item['drop_count'] for item in sorted_items), 16)
        self.assertEqual(items, before)

    def test_missing_art_unknown_identity_and_unsafe_paths_use_placeholder(self):
        missing = self.record('missing')
        unsafe = self.record('unsafe')
        self.asset(missing, 'missing', missing=True)
        self.asset(unsafe, 'escape', path='../outside.png')
        resolved = enrich_loot_items(self.root, [{'item_id': missing}, {'item_id': unsafe},
                                                {'item_id': 'not-in-library', 'item_name': 'Scroll'}])
        self.assertEqual([item['icon_path'] for item in resolved], [None, None, None])
        grid = self.grid(resolved)
        self.assertEqual([tile.icon_label.text() for tile in grid.tiles], ['?', '?', '?'])
        self.assertTrue(all(tile.icon_path is None for tile in grid.tiles))

    def test_corrupt_preferred_art_uses_next_exact_record_candidate(self):
        rid = self.record('fallback')
        self.asset(rid, 'corrupt', score=100, corrupt=True)
        valid = self.asset(rid, 'valid', score=1, color='blue')
        grid = self.grid(enrich_loot_items(self.root, [{'item_id': rid, 'observed_icons': 2}]))
        self.assertEqual(grid.tiles[0].icon_path, str(valid))
        self.assertEqual(grid.tiles[0].icon_asset_id, 'valid')
        self.assertEqual(grid.tiles[0].count_label.text(), '2')

    def test_absent_library_is_not_created_and_empty_grid_remains_visible(self):
        root = self.root / 'absent'
        items = [{'item_id': 'unknown', 'confirmed_acquired': None}]
        self.assertIsNone(enrich_loot_items(root, items)[0]['icon_path'])
        self.assertFalse(root.exists())
        grid = self.grid(items)
        grid.set_items([])
        self.app.processEvents()
        self.assertTrue(grid.isVisible())
        self.assertTrue(grid.empty_label.isVisible())
        self.assertEqual(grid.tiles, [])

    def test_alias_cycles_and_older_schema_do_not_hide_recorded_loot(self):
        first, second = self.record('cycle-first'), self.record('cycle-second')
        self.db.executemany('INSERT INTO identity_aliases VALUES(?,?,?)',
                            [(first, second, 'invalid'), (second, first, 'invalid')])
        self.db.commit()
        self.assertIsNone(enrich_loot_items(self.root, [{'item_id': first}])[0]['icon_path'])
        self.db.execute('DROP TABLE identity_aliases')
        self.db.execute('DROP TABLE asset_preferences')
        self.db.commit()
        valid = self.asset(first, 'older-schema')
        self.assertEqual(enrich_loot_items(self.root, [{'item_id': first}])[0]['icon_path'], str(valid))


if __name__ == '__main__':
    unittest.main()
