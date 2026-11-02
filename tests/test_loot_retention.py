"""Finalized evidence retention uses only isolated files and SQLite fixtures."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.backend.retention import EvidenceCapacityError, EvidenceRetention, PRUNED_WARNING


class EvidenceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-retention-')
        self.root = Path(self.temp.name)
        self.retention = EvidenceRetention(self.root)
        self.directory = self.root / 'runtime' / 'loot'
        self.diagnostics = self.directory / 'diagnostics'
        self.diagnostics.mkdir(parents=True)
        (self.directory / 'pending').mkdir()
        with closing(sqlite3.connect(self.retention.database)) as db:
            db.executescript('''
                CREATE TABLE sessions(session_id TEXT PRIMARY KEY,account TEXT);
                INSERT INTO sessions VALUES('s1','acc1'),('s2','acc2');
                CREATE TABLE completions(completion_id TEXT PRIMARY KEY,session_id TEXT,
                    completed_at REAL,status TEXT,screenshot_path TEXT,result_json TEXT,warnings_json TEXT);
            ''')
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def add(self, number, *, account='acc1', state='uncertain', path=None, size=100):
        cid = f'job_{account}_{number}'
        path = path or self.diagnostics / (cid + '_uncertain.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([number % 255]) * size)
        with closing(sqlite3.connect(self.retention.database)) as db:
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       (cid, 's1' if account == 'acc1' else 's2', number, state, str(path),
                        json.dumps({'rewards': [{'item_id': 'preserved', 'quantity': 1}]}), '[]'))
            db.commit()
        return cid, path

    def row(self, cid):
        with closing(sqlite3.connect(self.retention.database)) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM completions WHERE completion_id=?', (cid,)).fetchone())

    def journal(self):
        return json.loads(self.retention.journal_path.read_text())

    def test_no_pressure_preserves_all_finalized_screenshots(self):
        files = [self.add(i)[1] for i in range(6)]
        info = self.retention.ensure_capacity(100, 1000)
        self.assertEqual(info['pruned_count'], 0)
        self.assertTrue(all(path.exists() for path in files))
        self.assertFalse(self.retention.journal_path.exists())

    def test_no_pressure_does_not_open_database_or_scan_references(self):
        with patch.object(self.retention, '_protections', side_effect=AssertionError('expensive scan')):
            with patch('app.backend.retention.sqlite3.connect', side_effect=AssertionError('database lock')):
                self.assertEqual(self.retention.ensure_capacity(100, 1000)['used_bytes'], 0)
                self.assertEqual(self.retention.reconcile()['pruned_count'], 0)

    def test_pressure_prunes_oldest_to_eighty_percent_and_preserves_account_records(self):
        rows = [self.add(i) for i in range(10)]
        receipt = self.directory / 'receipts' / (rows[0][0] + '.json')
        receipt.parent.mkdir()
        receipt.write_text('{"recorded":true,"screenshot":"original"}')
        result = self.row(rows[0][0])['result_json']
        info = self.retention.ensure_capacity(100, 1000)
        self.assertEqual(info['used_bytes'], 800)
        self.assertEqual(info['pruned_count'], 2)
        self.assertFalse(rows[0][1].exists())
        self.assertFalse(rows[1][1].exists())
        self.assertTrue(all(path.exists() for _, path in rows[2:]))
        row = self.row(rows[0][0])
        self.assertIsNone(row['screenshot_path'])
        self.assertEqual(row['result_json'], result)
        self.assertEqual(row['status'], 'uncertain')
        self.assertIn(PRUNED_WARNING, json.loads(row['warnings_json']))
        self.assertEqual(receipt.read_text(), '{"recorded":true,"screenshot":"original"}')
        entry = self.journal()['entries'][0]
        self.assertEqual(entry['phase'], 'committed')
        self.assertEqual(entry['size'], 100)
        self.assertEqual(entry['sha256'], hashlib.sha256(bytes([0]) * 100).hexdigest())

    def test_pending_explicit_and_newest_three_per_account_are_protected(self):
        rows1 = [self.add(i) for i in range(7)]
        rows2 = [self.add(i, account='acc2') for i in range(4)]
        pending = self.directory / 'pending' / (rows1[0][0] + '.json')
        pending.write_text(json.dumps({'completion_id': rows1[0][0]}))
        self.retention.ensure_capacity(100, 1000, protected_completion_ids=[rows1[1][0]])
        self.assertTrue(rows1[0][1].exists())
        self.assertTrue(rows1[1][1].exists())
        self.assertTrue(all(path.exists() for _, path in rows1[-3:] + rows2[-3:]))
        self.assertFalse(rows1[2][1].exists())
        self.assertFalse(rows1[3][1].exists())

    def test_reference_paths_hashes_progress_and_extra_paths_are_protected(self):
        rows = [self.add(i) for i in range(10)]
        calibration = self.root / 'assets' / 'item_recognition' / 'calibration.json'
        calibration.parent.mkdir(parents=True)
        calibration.write_text(json.dumps({'calibration_screenshots': [str(rows[0][1])]}))
        fixture = self.root / 'tests' / 'fixtures' / 'evidence.json'
        fixture.parent.mkdir(parents=True)
        fixture.write_text(json.dumps({'screenshot_sha256': hashlib.sha256(rows[1][1].read_bytes()).hexdigest()}))
        progress = self.root / 'runtime' / 'universal.json'
        progress.write_text(json.dumps({'run': {'completion_id': rows[2][0]}}))
        for number, folder in [(3, 'validation'), (4, 'recovery')]:
            document = self.root / 'runtime' / folder / 'evidence.json'
            document.parent.mkdir()
            document.write_text(json.dumps({'image': str(rows[number][1].relative_to(self.root))}))
        with self.assertRaises(EvidenceCapacityError):
            self.retention.ensure_capacity(100, 900, extra_protected_paths=[rows[5][1]])
        self.assertTrue(all(path.exists() for _, path in rows[:6] + rows[-3:]))

    def test_filename_only_markdown_and_python_references_are_protected(self):
        rows = [self.add(i) for i in range(7)]
        document = self.root / 'docs' / 'REVIEW.md'
        document.parent.mkdir()
        document.write_text(f'[evidence]({rows[0][1].name})\n')
        script = self.root / 'tests' / 'saved.py'
        script.parent.mkdir()
        script.write_text(f'images=["{rows[1][1].name}"]\n')
        self.retention.ensure_capacity(100, 700)
        self.assertTrue(rows[0][1].exists())
        self.assertTrue(rows[1][1].exists())
        self.assertFalse(rows[2][1].exists())

    def test_disappearing_pending_files_do_not_prevent_capacity_checks(self):
        rows = [self.add(i) for i in range(5)]
        pending = self.directory / 'pending' / 'finished.json'
        pending.write_text('{}')
        vanished_png = self.directory / 'pending' / 'finished.png'
        vanished_png.write_bytes(b'x' * 100)
        original_read = Path.read_text
        original_stat = Path.stat
        def read(path, *args, **kwargs):
            if path == pending:
                raise FileNotFoundError(str(path))
            return original_read(path, *args, **kwargs)
        def size(path, *args, **kwargs):
            if path == vanished_png:
                raise FileNotFoundError(str(path))
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), patch.object(Path, 'stat', size):
            info = self.retention.ensure_capacity(100, 500)
        self.assertEqual(info['pruned_count'], 1)
        self.assertFalse(rows[0][1].exists())

    def test_pending_access_failure_still_prevents_pruning(self):
        rows = [self.add(i) for i in range(5)]
        pending = self.directory / 'pending' / 'inaccessible.json'
        pending.write_text('{}')
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == pending:
                raise PermissionError(str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            with self.assertRaises(PermissionError):
                self.retention.ensure_capacity(100, 500)
        self.assertTrue(all(path.exists() for _, path in rows))

    def test_unfinished_rows_or_unknown_files_never_pruned(self):
        pending = self.add(0, state='retryable_failure')
        latest = [self.add(i) for i in range(1, 4)]
        orphan = self.diagnostics / 'unlinked.png'
        orphan.write_bytes(b'x' * 100)
        temp = self.directory / 'pending' / 'capture.tmp.png'
        temp.write_bytes(b'x' * 5000)
        with self.assertRaises(EvidenceCapacityError) as caught:
            self.retention.ensure_capacity(100, 500)
        self.assertEqual(caught.exception.used_bytes, 500)
        self.assertEqual(caught.exception.required_bytes, 100)
        self.assertTrue(pending[1].exists())
        self.assertTrue(orphan.exists())
        self.assertTrue(all(path.exists() for _, path in latest))

    def test_noncanonical_nested_and_shared_paths_are_never_pruned(self):
        external = self.root / 'outside.png'
        nested = self.diagnostics / 'nested' / 'evidence.png'
        first = self.add(0, path=external)
        second = self.add(1, path=nested)
        shared = self.add(2)
        self.add(3, path=shared[1])
        for i in range(4, 7):
            self.add(i)
        with self.assertRaises(EvidenceCapacityError):
            self.retention.ensure_capacity(100, 400)
        self.assertTrue(first[1].exists())
        self.assertTrue(second[1].exists())
        self.assertTrue(shared[1].exists())

    def test_absolute_and_relative_database_aliases_are_both_protected(self):
        rows = [self.add(i) for i in range(5)]
        with closing(sqlite3.connect(self.retention.database)) as db:
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       ('job_pending_alias', 's1', 8, 'retryable_failure',
                        str(rows[0][1].relative_to(self.root)), '{}', '[]'))
            db.commit()
        self.retention.ensure_capacity(100, 500)
        self.assertTrue(rows[0][1].exists())
        self.assertFalse(rows[1][1].exists())

    def test_journal_write_failure_preserves_candidate_and_reference(self):
        rows = [self.add(i) for i in range(5)]
        with patch('app.backend.retention.atomic_json', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.retention.ensure_capacity(100, 500)
        self.assertTrue(all(path.exists() for _, path in rows))
        self.assertEqual(self.row(rows[0][0])['screenshot_path'], str(rows[0][1]))

    def test_crash_after_unlink_reconciles_database_without_losing_results(self):
        rows = [self.add(i) for i in range(5)]
        original = self.row(rows[0][0])['result_json']
        with patch.object(self.retention, '_clear_reference', side_effect=OSError('crash after unlink')):
            with self.assertRaises(OSError):
                self.retention.ensure_capacity(100, 500)
        self.assertFalse(rows[0][1].exists())
        self.assertEqual(self.journal()['entries'][0]['phase'], 'deleted')
        info = EvidenceRetention(self.root).reconcile()
        self.assertEqual(info['pruned_count'], 1)
        self.assertIsNone(self.row(rows[0][0])['screenshot_path'])
        self.assertEqual(self.row(rows[0][0])['result_json'], original)
        self.assertEqual(self.journal()['entries'][0]['phase'], 'committed')
        self.assertEqual(self.retention.reconcile()['pruned_count'], 0)

    def test_prepared_deletion_is_cancelled_if_hash_changes(self):
        rows = [self.add(i) for i in range(5)]
        original = self.retention._finish_entry
        def change_before_delete(db, journal, entry, protections):
            (self.root / entry['path']).write_bytes(b'z' * entry['size'])
            return original(db, journal, entry, protections)
        with patch.object(self.retention, '_finish_entry', side_effect=change_before_delete):
            with self.assertRaises(EvidenceCapacityError):
                self.retention.ensure_capacity(100, 500)
        self.assertTrue(all(path.exists() for _, path in rows))
        self.assertTrue(all(entry['phase'] == 'cancelled' for entry in self.journal()['entries']))

    def test_prepared_deletion_is_cancelled_when_job_becomes_protected(self):
        rows = [self.add(i) for i in range(5)]
        with patch.object(self.retention, '_finish_entry', side_effect=OSError('crash before unlink')):
            with self.assertRaises(OSError):
                self.retention.ensure_capacity(100, 500)
        (self.directory / 'pending' / (rows[0][0] + '.json')).write_text('{}')
        self.retention.reconcile()
        self.assertTrue(rows[0][1].exists())
        self.assertEqual(self.journal()['entries'][0]['phase'], 'cancelled')

    def test_admission_serializes_capacity_check_through_caller_commit(self):
        started = threading.Event()
        first_committed = threading.Event()
        outcomes = []
        def other_capture():
            started.set()
            try:
                with EvidenceRetention(self.root).admission(100, 100):
                    outcomes.append(first_committed.is_set())
            except EvidenceCapacityError:
                outcomes.append('full')
        with self.retention.admission(100, 100):
            worker = threading.Thread(target=other_capture)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(.04)
            self.assertEqual(outcomes, [])
            (self.directory / 'pending' / 'capture.png').write_bytes(b'x' * 100)
            first_committed.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ['full'])

    def test_admission_lock_is_shared_with_another_process(self):
        code = '''
import sys
from app.backend.retention import EvidenceRetention,EvidenceCapacityError
print('ready',flush=True)
try:
    EvidenceRetention(sys.argv[1]).ensure_capacity(100,100)
    print('admitted',flush=True)
except EvidenceCapacityError:
    print('full',flush=True)
'''
        with self.retention.admission(100, 100):
            environment = os.environ.copy()
            source = str(Path(__file__).resolve().parents[1] / 'src')
            environment['PYTHONPATH'] = os.pathsep.join(
                part for part in (source, environment.get('PYTHONPATH', '')) if part)
            child = subprocess.Popen([sys.executable, '-c', code, str(self.root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     env=environment)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                time.sleep(.04)
                self.assertIsNone(child.poll())
                (self.directory / 'pending' / 'capture.png').write_bytes(b'x' * 100)
            except BaseException:
                child.kill()
                child.communicate()
                raise
        output, errors = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, errors)
        self.assertEqual(output.strip(), 'full')

    def test_corrupt_journal_and_pending_metadata_fail_closed(self):
        rows = [self.add(i) for i in range(5)]
        self.retention.journal_path.write_text('{broken')
        with self.assertRaises((ValueError, OSError)):
            self.retention.ensure_capacity(100, 500)
        self.retention.journal_path.unlink()
        (self.directory / 'pending' / 'bad.json').write_text('[]')
        with self.assertRaises(OSError):
            self.retention.ensure_capacity(100, 500)
        self.assertTrue(all(path.exists() for _, path in rows))


if __name__ == '__main__':
    unittest.main()
