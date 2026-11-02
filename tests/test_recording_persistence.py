"""Capture failures retain their cause independently of status publication."""
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.backend import loot
from test_loot_tracking import FakeRecognizer, result, reward


class RecordingPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nbr-recording-persistence-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.collector = loot.Collector(self.root, 'universal', 'fixture:persistence',
            account='acc1', device_id='fixture-device', recognizer_factory=lambda: FakeRecognizer())
        self.addCleanup(self.collector.close)
        self.collector._stop.set()
        self.collector._thread.join(2)
        self.frame = np.full((32, 32, 3), 100, np.uint8)
        self.cid = self.collector.completed('original', completed_at=12345)

    def test_capacity_cause_survives_failed_health_publication(self):
        self.collector.max_storage_bytes = 1
        original = loot._atomic_json
        def write(path, value):
            if Path(path).name == 'health_acc1.json':
                raise PermissionError('fixture status destination busy')
            original(path, value)
        with patch.object(loot, '_atomic_json', side_effect=write):
            self.assertFalse(self.collector.evidence('original', self.frame, self.cid))
            try:
                self.collector._write_health()
            except OSError as exc:
                self.collector._telemetry_failure(exc)
        health = self.collector.health()
        capture = health['operation_errors'][self.cid]
        self.assertEqual(capture['operation'], 'capture')
        self.assertEqual(capture['limit_bytes'], 1)
        self.assertGreater(capture['required_bytes'], 1)
        self.assertIn('storage', health['error'].lower())
        self.assertIn('busy', health['telemetry_error']['message'])
        failure = json.loads((self.collector.directory / 'persistence_error.json').read_text())
        self.assertEqual(failure['completion_id'], self.cid)
        self.assertEqual(failure['operation'], 'capture')
        self.assertEqual(self.collector.resume_pending()[0]['state'], 'awaiting_evidence')

    def test_health_failure_does_not_invalidate_durable_capture(self):
        original = loot._atomic_json
        def write(path, value):
            if Path(path).name == 'health_acc1.json':
                raise PermissionError('fixture status destination busy')
            original(path, value)
        with patch.object(loot, '_atomic_json', side_effect=write):
            self.assertTrue(self.collector.evidence('original', self.frame, self.cid))
            with self.assertRaises(PermissionError):
                self.collector._write_health()
            self.assertTrue(self.collector.evidence('original', self.frame, self.cid))
        value = json.loads((self.collector.spool / (self.cid + '.json')).read_text())
        self.assertEqual(value['state'], 'recognition_pending')
        self.assertEqual(value['attempts'], 0)

    def test_repaired_capacity_clears_capture_error_under_same_identity(self):
        self.collector.max_storage_bytes = 1
        self.assertFalse(self.collector.evidence('original', self.frame, self.cid))
        self.collector.max_storage_bytes = 1024**2
        self.assertTrue(self.collector.evidence('original', self.frame, self.cid))
        self.assertNotIn(self.cid, self.collector.health()['operation_errors'])
        value = json.loads((self.collector.spool / (self.cid + '.json')).read_text())
        self.assertEqual(value['completed_at'], 12345)
        self.assertEqual(value['run_id'], 'original')

    def test_background_error_does_not_replace_capture_cause(self):
        self.collector.max_storage_bytes = 1
        self.assertFalse(self.collector.evidence('original', self.frame, self.cid))
        self.collector._error(OSError('fixture database heartbeat unavailable'))
        self.assertIn('storage', self.collector.health()['error'].lower())
        failure = json.loads((self.collector.directory / 'persistence_error.json').read_text())
        self.assertEqual(failure['operation'], 'capture')
        self.assertEqual(failure['completion_id'], self.cid)

    def test_collector_records_across_full_diagnostic_budget_without_losing_drops(self):
        # Use distinct, poorly compressible images so actual PNG admission and
        # diagnostic copies exercise rollover rather than mocked byte counts.
        rng = np.random.default_rng(731)
        frame = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
        encoded_size = cv2.imencode('.png', frame)[1].size
        self.collector.max_storage_bytes = int(encoded_size) * 8
        self.collector.factory = lambda: FakeRecognizer(result([reward(status='candidate')]))
        first_path = None
        for index in range(8):
            run = 'original' if index == 0 else f'rollover-{index}'
            cid = self.cid if index == 0 else self.collector.completed(run, completed_at=12345 + index)
            image = np.roll(frame, index, axis=0)
            self.assertTrue(self.collector.evidence(run, image, cid))
            value = json.loads((self.collector.spool / (cid + '.json')).read_text())
            self.collector._process(value)
            if index == 0:
                first_path = Path(self.collector.store.dashboard(self.collector.session_id)['runs'][0]['screenshot_path'])
        data = self.collector.store.dashboard(self.collector.session_id)
        self.assertEqual(data['completed_runs'], 8)
        self.assertEqual(data['total_drops'], 8)
        self.assertEqual(data['unidentified_drops'], 8)
        self.assertEqual(data['outstanding_jobs'], 0)
        self.assertEqual(len(list((self.collector.directory / 'receipts').glob('*.json'))), 8)
        self.assertFalse(first_path.exists())
        self.assertLessEqual(self.collector._storage_used(), self.collector.max_storage_bytes)
        self.assertTrue(any('pruned' in ' '.join(run['warnings']) for run in data['runs']))
        journal = json.loads(self.collector.retention.journal_path.read_text())
        self.assertTrue(journal['entries'])
        self.assertTrue(all(entry['phase'] == 'committed' for entry in journal['entries']))

    def test_empty_heartbeat_cannot_erase_concurrent_new_capture_failure(self):
        self.assertTrue(self.collector.missing('original', completion_id=self.cid))
        value = json.loads((self.collector.spool / (self.cid + '.json')).read_text())
        self.collector._process(value)
        self.collector.max_storage_bytes = 1
        snapshot_taken, publish, failed = threading.Event(), threading.Event(), threading.Event()
        health = self.collector.health
        failures = []
        identity = []
        def pause_snapshot():
            snapshot = health()
            snapshot_taken.set()
            if not publish.wait(2):
                raise TimeoutError('fixture publication was not released')
            return snapshot
        def fail_capture():
            try:
                cid = self.collector.completed('new', completed_at=12346)
                identity.append(cid)
                self.assertFalse(self.collector.evidence('new', self.frame, cid))
            except BaseException as exc:
                failures.append(exc)
            finally:
                failed.set()
        with patch.object(self.collector, 'health', side_effect=pause_snapshot):
            heartbeat = threading.Thread(target=self.collector._write_health)
            heartbeat.start()
            self.assertTrue(snapshot_taken.wait(2))
            capture = threading.Thread(target=fail_capture)
            capture.start()
            # Old unlocked publication lets the failure complete here and then
            # erases it when the stale empty snapshot is finally published.
            failed.wait(.1)
            publish.set()
            heartbeat.join(2)
            capture.join(2)
        self.assertFalse(heartbeat.is_alive())
        self.assertFalse(capture.is_alive())
        self.assertEqual(failures, [])
        self.assertIn(identity[0], health()['operation_errors'])
        self.assertIn('storage', health()['error'].lower())
        saved = json.loads((self.collector.directory / 'health_acc1.json').read_text())
        self.assertEqual(saved['status'], 'Screenshot Capture Pending')

    def test_unreadable_spool_is_not_an_empty_backlog(self):
        self.collector.max_storage_bytes = 1
        self.assertFalse(self.collector.evidence('original', self.frame, self.cid))
        original_read = Path.read_text
        path = self.collector.spool / (self.cid + '.json')
        def read(target, *args, **kwargs):
            if target == path:
                raise PermissionError('fixture pending metadata temporarily unreadable')
            return original_read(target, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assertEqual(self.collector.health()['unreadable_jobs'], 1)
            self.collector._write_health()
        self.assertIn(self.cid, self.collector.health()['operation_errors'])
        failure = json.loads((self.collector.directory / 'persistence_error.json').read_text())
        self.assertEqual(failure['completion_id'], self.cid)
        self.assertIn('storage', failure['message'].lower())


if __name__ == '__main__':
    unittest.main()
