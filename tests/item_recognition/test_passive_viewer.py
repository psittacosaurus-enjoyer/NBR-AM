"""Small stdlib-only viewer checks use disposable saved state, never a device."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from item_recognition.passive_viewer import read_snapshot


class PassiveViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-passive-viewer-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def prepare(self, screenshot=None):
        runtime = self.root / 'runtime'
        (runtime / 'loot/diagnostics').mkdir(parents=True)
        (runtime / 'universal.json').write_text(json.dumps({'screen': 'IN_BATTLE'}), encoding='utf-8')
        with closing(sqlite3.connect(runtime / 'loot/loot.sqlite3')) as db, db:
            db.executescript('CREATE TABLE sessions(session_id TEXT, tracking_started REAL);'
                             'CREATE TABLE completions(session_id TEXT, completion_id TEXT, '
                             'run_sequence INTEGER, completed_at REAL, screenshot_path TEXT, '
                             'result_json TEXT, warnings_json TEXT);')
            db.execute('INSERT INTO sessions VALUES(?,?)', ('session-test', 1))
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       ('session-test', 'run-test', 1, 2, str(screenshot) if screenshot else None, '{}', '[]'))

    def test_missing_state_does_not_create_files(self):
        before = list(self.root.rglob('*'))
        result, image = read_snapshot(self.root)
        self.assertEqual(result['status'], 'Unavailable')
        self.assertIsNone(image)
        self.assertEqual(list(self.root.rglob('*')), before)

    def test_saved_result_is_read_without_changing_state(self):
        path = self.root / 'runtime/loot/diagnostics/result.png'
        self.prepare(path)
        path.write_bytes(b'fake-saved-png')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result, image = read_snapshot(self.root)
        self.assertEqual(result['status'], 'Available')
        self.assertEqual(result['farmer']['screen'], 'IN_BATTLE')
        self.assertEqual(result['image_id'], 'run-test')
        self.assertEqual(image, path)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_database_cannot_serve_an_external_image(self):
        path = self.root / 'outside.png'
        self.prepare(path)
        path.write_bytes(b'not-diagnostic')
        result, image = read_snapshot(self.root)
        self.assertEqual(result['status'], 'Available')
        self.assertIsNone(result['image_id'])
        self.assertIsNone(image)

    def test_missing_database_is_not_created(self):
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/universal.json').write_text('{}', encoding='utf-8')
        result, _ = read_snapshot(self.root)
        self.assertEqual(result['status'], 'Unavailable')
        self.assertFalse((self.root / 'runtime/loot').exists())


if __name__ == '__main__':
    unittest.main()
