"""v1.24 disk outbox regressions; isolated files and no device inputs."""
from pathlib import Path
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.backend import loot
from test_loot_tracking import FakeRecognizer


class DurableLootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-durable-')
        self.root = Path(self.temp.name)
        self.collectors = []
        self.frame = np.full((24, 32, 3), 100, np.uint8)

    def tearDown(self):
        for collector in self.collectors:
            collector.close()
        self.temp.cleanup()

    def collector(self, factory=None, process='fixture:original', **kwargs):
        collector = loot.Collector(self.root, 'universal', process, account='acc1', device_id='fixture-device',
                                   recognizer_factory=factory or (lambda: FakeRecognizer()),
                                   retry_base=.02, retry_max=.05, **kwargs)
        self.collectors.append(collector)
        return collector

    @staticmethod
    def wait_for(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.02)
        return False

    def test_registration_write_failure_retries_same_identity_and_original_time(self):
        collector = self.collector()
        original = loot._atomic_json
        failed = False
        def save(path, value):
            nonlocal failed
            if Path(path).parent == collector.spool and not failed:
                failed = True
                raise OSError('fixture temporarily unavailable')
            return original(path, value)
        with patch.object(loot, '_atomic_json', side_effect=save):
            self.assertIsNone(collector.completed('run-one', completed_at=1234))
            cid = collector.completed('run-one', completed_at=1234)
        self.assertTrue(cid)
        self.assertEqual(collector.completed('run-one', completed_at=9999), cid)
        self.assertEqual(json.loads((collector.spool / (cid + '.json')).read_text())['completed_at'], 1234)
        self.assertEqual(len(list(collector.spool.glob('*.json'))), 1)

    def test_partial_png_failure_leaves_capture_pending_and_second_attempt_succeeds(self):
        collector = self.collector()
        cid = collector.completed('capture')
        original = cv2.imwrite
        calls = 0
        def save(path, frame):
            nonlocal calls
            calls += 1
            if calls == 1:
                Path(path).write_bytes(b'incomplete png')
                return False
            return original(path, frame)
        with patch.object(cv2, 'imwrite', side_effect=save):
            self.assertFalse(collector.evidence('capture', self.frame))
            job = json.loads((collector.spool / (cid + '.json')).read_text())
            self.assertEqual(job['state'], 'awaiting_evidence')
            self.assertFalse((collector.spool / (cid + '.png')).exists())
            self.assertTrue(collector.evidence('capture', self.frame))
        self.assertTrue(collector.wait_idle(4))
        self.assertEqual(collector.store.dashboard(collector.session_id)['recognized_icons'], 1)

    def test_backlog_memory_limit_never_discards_recognition(self):
        collector = self.collector(max_pending=1)
        collector._stop.set()
        collector._thread.join(2)
        for run in range(12):
            self.assertTrue(collector.completed(run))
            self.assertTrue(collector.evidence(run, self.frame))
            self.assertLessEqual(len(collector._events), 3)
        self.assertEqual(len(list(collector.spool.glob('*.json'))), 12)
        restarted = self.collector(process='fixture:restarted', max_pending=1)
        self.assertTrue(restarted.wait_idle(5))
        data = collector.store.dashboard(collector.session_id)
        self.assertEqual(data['recognized_icons'], 12)
        self.assertEqual(data['completed_runs'], 12)

    def test_database_and_temporary_recognition_retry_without_a_new_session(self):
        calls = 0
        good = FakeRecognizer()
        def factory():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise sqlite3.OperationalError('references locked')
            return good
        collector = self.collector(factory=factory)
        cid = collector.completed('db-and-recognition')
        original = collector.store.accept
        failures = 0
        def accept(value):
            nonlocal failures
            failures += 1
            if failures < 6:
                raise sqlite3.OperationalError('database unavailable')
            return original(value)
        with patch.object(collector.store, 'accept', side_effect=accept):
            self.assertTrue(collector.evidence('db-and-recognition', self.frame))
            self.assertTrue(collector.wait_idle(5))
        self.assertGreater(failures, 5)
        self.assertGreaterEqual(calls, 3)
        self.assertEqual(collector.store.dashboard(collector.session_id)['recognized_icons'], 1)
        self.assertTrue(collector.evidence('db-and-recognition', self.frame, completion_id=cid))

    def test_restart_before_capture_retains_original_session_run_and_timestamp(self):
        first = self.collector()
        cid = first.completed('persisted-active-run', completed_at=12345)
        first.close()
        restarted = self.collector(process='fixture:next-process')
        self.assertEqual(restarted.completed('persisted-active-run', completed_at=99999), cid)
        pending = restarted.resume_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['session_id'], first.session_id)
        self.assertEqual(pending[0]['completed_at'], 12345)
        self.assertTrue(restarted.evidence('persisted-active-run', self.frame, completion_id=cid))
        self.assertTrue(restarted.wait_idle(4))
        self.assertEqual(first.store.dashboard(first.session_id)['recognized_icons'], 1)
        self.assertEqual(first.store.dashboard(restarted.session_id)['recognized_icons'], 0)
        self.assertFalse(restarted.evidence('unrelated-results-screen', self.frame))

    def test_committed_ack_survives_restart_and_database_outage_without_recredit(self):
        first = self.collector()
        cid = first.completed('ack-crash-window')
        self.assertTrue(first.evidence('ack-crash-window', self.frame))
        self.assertTrue(first.wait_idle(4))
        first.close()
        restarted = self.collector(process='fixture:ack-restart')
        with patch.object(loot.sqlite3, 'connect', side_effect=sqlite3.OperationalError('offline')):
            self.assertEqual(restarted.completed('ack-crash-window'), cid)
            self.assertTrue(restarted.evidence('ack-crash-window', self.frame))
        self.assertEqual(first.store.dashboard(first.session_id)['recognized_icons'], 1)
        self.assertFalse(list(first.spool.glob('*.json')))

    def test_storage_limit_preserves_job_and_corrupt_saved_image_can_be_recaptured(self):
        collector = self.collector(max_storage_bytes=1)
        cid = collector.completed('storage')
        self.assertFalse(collector.evidence('storage', self.frame))
        self.assertTrue((collector.spool / (cid + '.json')).exists())
        collector.max_storage_bytes = 1024**2
        collector._stop.set()
        collector._thread.join(2)
        self.assertTrue(collector.evidence('storage', self.frame))
        (collector.spool / (cid + '.png')).write_bytes(b'corrupted')
        self.assertTrue(collector.evidence('storage', self.frame))
        loot.Collector._verify_image(collector.spool / (cid + '.png'))

    def test_operator_attention_evidence_retries_under_the_same_id(self):
        fake = FakeRecognizer(error=ValueError('invalid reference configuration'))
        collector = self.collector(factory=lambda: fake)
        cid = collector.completed('repairable')
        self.assertTrue(collector.evidence('repairable', self.frame))
        self.assertTrue(self.wait_for(lambda: collector.health()['attention_required'] == 1))
        self.assertTrue((collector.spool / (cid + '.png')).exists())
        fake.error = None
        self.assertTrue(collector.retry(cid))
        self.assertTrue(collector.wait_idle(4))
        data = collector.store.dashboard(collector.session_id)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recognized_icons'], 1)

    def test_partially_loaded_transient_reference_snapshot_is_never_installed(self):
        partial = FakeRecognizer()
        partial.reference_errors = [{'error': 'sharing violation', 'retryable': True}]
        complete = FakeRecognizer()
        candidates = iter((partial, complete))
        collector = self.collector(factory=lambda: next(candidates))
        collector.completed('partial-reference')
        self.assertTrue(collector.evidence('partial-reference', self.frame))
        self.assertTrue(collector.wait_idle(4))
        self.assertIs(collector.recognizer, complete)
        self.assertFalse(partial.calls)
        self.assertEqual(collector.store.dashboard(collector.session_id)['recognized_icons'], 1)


class RecognitionFailureClassificationTests(unittest.TestCase):
    def test_optional_reviewed_supplement_distinguishes_missing_from_access_failure(self):
        from item_recognition.recognizer import ItemRecognizer
        recognizer = ItemRecognizer.__new__(ItemRecognizer)
        recognizer.calibration = {'schema_version': 2}
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('sharing violation')):
            with self.assertRaises(PermissionError):
                recognizer._reviewed_rewards('reviewed.json', 'fixture-calibration')
        with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError('optional file absent')):
            reviewed, digest = recognizer._reviewed_rewards('reviewed.json', 'fixture-calibration')
        self.assertEqual(reviewed['canonical_groups'], [])
        self.assertIsNone(digest)

    def test_evidence_read_outage_is_retryable_but_invalid_image_is_not(self):
        from item_recognition.recognizer import ItemRecognizer
        recognizer = ItemRecognizer.__new__(ItemRecognizer)
        recognizer.reference_snapshot = {}
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('sharing violation')):
            result = recognizer.analyze_screenshot('fixture.png')
        self.assertTrue(result['retryable'])
        with patch.object(Path, 'read_bytes', return_value=b'not an image'):
            result = recognizer.analyze_screenshot('fixture.png')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result.get('retryable', False))

    def test_reference_read_outage_is_retryable_but_invalid_image_is_not(self):
        from item_recognition.recognizer import ItemRecognizer
        from PIL import UnidentifiedImageError
        recognizer = ItemRecognizer.__new__(ItemRecognizer)
        recognizer.library_root = Path('.')
        recognizer.calibration = None
        recognizer.reference_errors = []
        recognizer._images = []
        record = {'id': 'fixture', 'assets': [{'status': 'ok', 'local_image_path': 'reference.png'}]}
        with patch('item_recognition.recognizer.Image.open', side_effect=PermissionError('sharing violation')):
            recognizer._load_reference(record)
        self.assertTrue(recognizer.reference_errors[-1]['retryable'])
        with patch('item_recognition.recognizer.Image.open', side_effect=UnidentifiedImageError('invalid image')):
            recognizer._load_reference(record)
        self.assertFalse(recognizer.reference_errors[-1]['retryable'])


if __name__ == '__main__':
    unittest.main()
