"""Isolated saved-evidence audits; never import a controller or open live state."""
from contextlib import closing, redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_loot_evidence.py'
SPEC = importlib.util.spec_from_file_location('verify_loot_evidence', SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def png(image_data=None):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 900, 1600, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' * 901) * 1600) if image_data is None else image_data)
            + chunk(b'IEND', b''))


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-evidence-audit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fixture repo'
        self.loot = self.root / 'runtime' / 'loot'
        self.evidence = self.root / 'saved evidence'
        for folder in (self.evidence, self.loot / 'receipts', self.loot / 'pending'):
            folder.mkdir(parents=True)
        self.database = self.loot / 'loot.sqlite3'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('''
                PRAGMA foreign_keys=ON;
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY, account TEXT, stage TEXT,
                    process_identity TEXT, process_started REAL, tracking_started REAL);
                CREATE TABLE completions (
                    completion_id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(session_id),
                    run_id TEXT, run_sequence INTEGER, completed_at REAL, status TEXT,
                    result_json TEXT);
                INSERT INTO sessions VALUES ('session_fixture','acc1','universal','123:456',90,91);
            ''')

    def json(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def add_run(self, sequence=1, identity='matched', quantity=None, quantity_status='unknown', *,
                session_id='session_fixture', device_id='device_fixture', run_id=None, process_identity='123:456'):
        run_id = 'run_' + str(sequence) if run_id is None else run_id
        owner = f'acc1:{device_id}:universal' if device_id else session_id
        cid = 'job_' + hashlib.sha256(f'{owner}:{run_id}'.encode()).hexdigest()[:48]
        image = self.evidence / (cid + '.png')
        image.write_bytes(png())
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        screenshot = str(self.loot / 'pending' / image.name)
        entry = dict(item_id='item_fixture', item_name='Fixture reward', identity_status=identity,
                     quantity=quantity, quantity_status=quantity_status, reward_scope='acquired',
                     visual_state='icon', bounding_box=[50, 410, 130, 140], quantity_bounding_box=None)
        result = dict(screenshot=screenshot, screenshot_sha256=digest, status='partial',
                      screen_type='acquired', rewards=[entry], unknown_rewards=[],
                      totals={'item_fixture': quantity} if identity == 'matched' and quantity_status == 'verified' else {})
        job = dict(completion_id=cid, session_id=session_id, run_id=run_id, run_sequence=sequence,
                   completed_at=100 + sequence, stage='universal', account='acc1', device_id=device_id,
                   process_identity=process_identity, process_started=90, tracking_started=92,
                   state='recognition_pending', screenshot=screenshot, screenshot_sha256=digest,
                   validation_screenshot=str(image), recognition_status='recognized' if identity == 'matched' else 'uncertain',
                   recognition_result=result)
        self.save_job(job)
        receipt = {key: value for key, value in job.items()
                   if key not in ('validation_screenshot', 'recognition_status', 'recognition_result')}
        receipt.update(recorded=True, recorded_at=110 + sequence)
        self.json(self.loot / 'receipts' / (cid + '.json'), receipt)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?)',
                       (session_id, 'acc1', 'universal', process_identity, 90, 91))
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       (cid, job['session_id'], run_id, sequence, job['completed_at'],
                        job['recognition_status'], json.dumps(result)))
        return job

    def replace_image(self, job, image_data):
        (self.evidence / (job['completion_id'] + '.png')).write_bytes(image_data)
        digest = hashlib.sha256(image_data).hexdigest()
        job['screenshot_sha256'] = digest
        job['recognition_result']['screenshot_sha256'] = digest
        self.save_job(job)
        path = self.loot / 'receipts' / (job['completion_id'] + '.json')
        receipt = json.loads(path.read_text('utf-8'))
        receipt['screenshot_sha256'] = digest
        self.json(path, receipt)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE completions SET result_json=? WHERE completion_id=?',
                       (json.dumps(job['recognition_result']), job['completion_id']))

    def save_job(self, job):
        self.json(self.evidence / (job['completion_id'] + '.json'), job)

    def cli(self, *arguments):
        output = io.StringIO()
        with patch.object(checker, 'ROOT', self.root), redirect_stdout(output):
            code = checker.main(['--evidence-dir', str(self.evidence), *arguments])
        return code, json.loads(output.getvalue())

    def snapshot(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob('*') if path.is_file()}

    def test_valid_three_runs_match_database_receipts_and_verified_totals(self):
        for sequence in range(1, 4):
            self.add_run(sequence, quantity=2, quantity_status='verified')
        report = checker.audit(self.root, self.evidence, 3)
        self.assertTrue(report['integrity_ok'], report['errors'])
        self.assertTrue(report['expected_count_met'])
        self.assertTrue(report['all_entries_identified'])
        self.assertTrue(report['backlog_empty'])
        self.assertEqual(report['database_quick_check'], ['ok'])
        self.assertEqual(report['database_foreign_key_issues'], [])
        self.assertEqual(report['quantity_status_counts'], {'verified': 3})

    def test_default_cli_prints_report_and_leaves_every_input_unchanged(self):
        self.add_run()
        before = self.snapshot()
        code, report = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(report['integrity_ok'])
        self.assertFalse(report['expected_count_met'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.evidence / 'integrity_report.json').exists())

    def test_reports_are_created_only_in_explicit_destination(self):
        self.add_run()
        before = self.snapshot()
        destination = self.root / 'new report directory' / 'nested'
        code, report = self.cli('--report-dir', str(destination))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((destination / 'integrity_report.json').read_text('utf-8')), report)
        self.assertIn('Integrity: PASS', (destination / 'integrity_report.txt').read_text('utf-8'))
        after = self.snapshot()
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(len(after) - len(before), 2)

    def test_candidate_and_ambiguous_quantity_are_not_integrity_failures(self):
        self.add_run(identity='candidate', quantity_status='ambiguous')
        code, report = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(report['integrity_ok'], report['errors'])
        self.assertFalse(report['all_entries_identified'])
        self.assertEqual(report['identity_status_counts'], {'candidate': 1})
        self.assertEqual(report['quantity_status_counts'], {'ambiguous': 1})
        self.assertIsNone(report['runs'][0]['entries'][0]['quantity'])
        required_code, required_report = self.cli('--require-count')
        self.assertEqual(required_code, 1)
        self.assertTrue(required_report['integrity_ok'])
        self.assertFalse(required_report['expected_count_met'])
        self.assertEqual(self.cli('--expected', '1', '--require-count')[0], 0)

    def test_report_replacement_cannot_modify_hard_linked_source_evidence(self):
        job = self.add_run()
        image = self.evidence / (job['completion_id'] + '.png')
        original = image.read_bytes()
        destination = self.root / 'reports'
        destination.mkdir()
        os.link(image, destination / 'integrity_report.json')
        code, report = self.cli('--report-dir', str(destination))
        self.assertEqual(code, 0)
        self.assertTrue(report['integrity_ok'])
        self.assertEqual(image.read_bytes(), original)
        self.assertEqual(len(list(destination.iterdir())), 2)

    def test_corrupt_png_is_reported_with_nonzero_exit(self):
        job = self.add_run()
        image = self.evidence / (job['completion_id'] + '.png')
        data = bytearray(image.read_bytes())
        data[45] ^= 1
        image.write_bytes(data)
        code, report = self.cli()
        self.assertEqual(code, 1)
        self.assertFalse(report['integrity_ok'])
        self.assertTrue(any('invalid screenshot' in error for error in report['errors']))
        self.assertTrue(any('SHA mismatch' in error for error in report['errors']))

    def test_valid_crc_and_matching_hashes_do_not_accept_invalid_image_pixels(self):
        job = self.add_run()
        for data in (b'not a zlib stream', zlib.compress(b'too few pixel bytes')):
            with self.subTest(data=data):
                self.replace_image(job, png(data))
                code, report = self.cli()
                self.assertEqual(code, 1)
                self.assertTrue(any('invalid screenshot' in error for error in report['errors']))
                self.assertFalse(any('SHA mismatch' in error for error in report['errors']))

    def test_non_ascii_report_survives_cp1252_stdout(self):
        job = self.add_run()
        name = 'Sai \u201cMask-like Smile\u201d \u5c71\u4e2d'
        job['recognition_result']['rewards'][0]['item_name'] = name
        self.save_job(job)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE completions SET result_json=?', (json.dumps(job['recognition_result']),))
        data = io.BytesIO()
        output = io.TextIOWrapper(data, encoding='cp1252', errors='strict')
        try:
            with patch.object(checker, 'ROOT', self.root), redirect_stdout(output):
                code = checker.main(['--evidence-dir', str(self.evidence)])
            output.flush()
            report = json.loads(data.getvalue().decode('cp1252'))
        finally:
            output.detach()
        self.assertEqual(code, 0)
        self.assertEqual(report['runs'][0]['entries'][0]['item_name'], name)

    def test_receipt_and_database_mismatches_are_reported_together(self):
        job = self.add_run()
        path = self.loot / 'receipts' / (job['completion_id'] + '.json')
        receipt = json.loads(path.read_text())
        receipt.update(recorded=False, device_id='wrong-device', screenshot_sha256='wrong-sha')
        self.json(path, receipt)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE completions SET run_id='wrong-run',status='uncertain',result_json='{}'")
        code, report = self.cli()
        self.assertEqual(code, 1)
        for text in ('receipt is not recorded', 'receipt mismatch: device_id', 'receipt mismatch: screenshot_sha256',
                     'database identity mismatch: run_id', 'database status mismatch', 'database result differs'):
            self.assertTrue(any(text in error for error in report['errors']), text)

    def test_malformed_database_result_and_receipt_json_are_report_errors(self):
        job = self.add_run()
        (self.loot / 'receipts' / (job['completion_id'] + '.json')).write_text('[]', encoding='utf-8')
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE completions SET result_json='not-json'")
        code, report = self.cli()
        self.assertEqual(code, 1)
        self.assertTrue(any('Cannot read global receipt' in error for error in report['errors']))
        self.assertTrue(any('invalid database recognition JSON' in error for error in report['errors']))

    def test_invalid_job_shape_is_reported_without_traceback(self):
        self.json(self.evidence / 'job_invalid.json', {'recognition_result': []})
        code, report = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(report['captured_count'], 0)
        self.assertTrue(any('Cannot read validation job' in error for error in report['errors']))

    def test_duplicate_out_of_bounds_and_unrevealed_boxes_fail_integrity(self):
        job = self.add_run()
        first = job['recognition_result']['rewards'][0]
        first.update(bounding_box=[895, 1595, 20, 20], visual_state='unrevealed')
        job['recognition_result']['rewards'].append(copy.deepcopy(first))
        self.save_job(job)
        code, report = self.cli()
        self.assertEqual(code, 1)
        for text in ('bounding box is outside', 'duplicate bounding box', 'entry is not revealed'):
            self.assertTrue(any(text in error for error in report['errors']), text)

    def test_repeated_raw_run_ids_are_informational_and_foreign_key_damage_fails(self):
        job = self.add_run()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       ('job_duplicate', 'missing-session', job['run_id'], 2, 103, 'uncertain', '{}'))
        report = checker.audit(self.root, self.evidence, 1)
        self.assertFalse(report['integrity_ok'])
        self.assertTrue(report['database_foreign_key_issues'])
        self.assertEqual(report['global_run_id_repetitions'], [{'run_id': job['run_id'], 'count': 2}])
        self.assertFalse(any(error.endswith('global_run_id') for error in report['errors']))

    def test_legacy_sessions_can_reuse_integer_run_ids(self):
        self.add_run(device_id='', run_id=1)
        self.add_run(device_id='', run_id=1, session_id='session_second', process_identity='789:456')
        report = checker.audit(self.root, self.evidence, 2)
        self.assertTrue(report['integrity_ok'], report['errors'])
        self.assertTrue(report['expected_count_met'])
        self.assertEqual(report['global_run_id_repetitions'], [{'run_id': '1', 'count': 2}])
        self.assertEqual(report['global_receipt_duplicates'], [])
        self.assertEqual(report['global_database_duplicates']['canonical_identity'], [])

    def test_true_device_identity_duplicates_across_sessions_fail(self):
        job = self.add_run(run_id='same-device-run')
        original_cid = job['completion_id']
        receipt = json.loads((self.loot / 'receipts' / (original_cid + '.json')).read_text('utf-8'))
        duplicate_cid = 'job_duplicate_device_identity'
        receipt.update(completion_id=duplicate_cid, session_id='session_second', process_identity='789:456')
        self.json(self.loot / 'receipts' / (duplicate_cid + '.json'), receipt)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO sessions VALUES('session_second','acc1','universal','789:456',90,91)")
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?,?)',
                       (duplicate_cid, 'session_second', job['run_id'], 1, 103, 'recognized', '{}'))
        report = checker.audit(self.root, self.evidence, 1)
        self.assertFalse(report['integrity_ok'])
        self.assertTrue(report['global_receipt_duplicates'])
        self.assertTrue(report['global_database_duplicates']['canonical_identity'])
        self.assertTrue(any('Global database duplicate: canonical_identity' in error for error in report['errors']))

    def test_backlog_is_reported_and_audited_pending_job_fails(self):
        job = self.add_run()
        self.json(self.loot / 'pending' / (job['completion_id'] + '.json'), job)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE completions SET status='recognition_pending'")
        report = checker.audit(self.root, self.evidence, 1)
        self.assertFalse(report['integrity_ok'])
        self.assertFalse(report['backlog_empty'])
        self.assertEqual(len(report['pending_database']), 1)
        self.assertEqual(len(report['pending_spool']), 1)

    def test_missing_database_is_reported_without_creating_it(self):
        self.database.unlink()
        before = self.snapshot()
        code, report = self.cli()
        self.assertEqual(code, 1)
        self.assertTrue(any('Cannot audit loot database' in error for error in report['errors']))
        self.assertEqual(self.snapshot(), before)

    def test_database_is_opened_read_only_and_query_only(self):
        self.add_run()
        connect = sqlite3.connect
        calls, statements = [], []

        def record(*args, **kwargs):
            calls.append((args, kwargs))
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch.object(checker.sqlite3, 'connect', side_effect=record):
            report = checker.audit(self.root, self.evidence, 1)
        self.assertTrue(report['integrity_ok'], report['errors'])
        self.assertEqual(calls[0][0][0], self.database.as_uri() + '?mode=ro')
        self.assertTrue(calls[0][1]['uri'])
        self.assertIn('PRAGMA query_only=ON', statements)
        self.assertFalse(any(statement.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE')) for statement in statements))

    def test_foreign_cwd_cli_resolves_root_from_script_and_explicit_relative_evidence(self):
        self.add_run()
        script = self.root / 'scripts' / SCRIPT.name
        script.parent.mkdir()
        shutil.copyfile(SCRIPT, script)
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(script), '--evidence-dir', '../saved evidence',
                                 '--expected', '1', '--require-count'], cwd=script.parent,
                                capture_output=True, text=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report['repository_root'], str(self.root))
        self.assertEqual(report['evidence_directory'], str(self.evidence))
        self.assertTrue(report['integrity_ok'])
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
