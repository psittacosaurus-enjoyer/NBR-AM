"""Explicit missing-evidence recovery never creates reward evidence or credits."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import loot_tracking as loot
from test_loot_tracking import FakeRecognizer


class MissingEvidenceDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-missing-evidence-')
        self.root = Path(self.temp.name)
        self.collectors = []
        self.recognizer = FakeRecognizer()
        self.frame = np.full((24, 32, 3), 100, np.uint8)

    def tearDown(self):
        for collector in self.collectors:
            collector.close()
        self.temp.cleanup()

    def collector(self, process='fixture:original', **kwargs):
        collector = loot.Collector(self.root, 'universal', process, account='acc1',
                                   device_id='fixture-device', recognizer_factory=lambda: self.recognizer,
                                   retry_base=.02, retry_max=.05, **kwargs)
        self.collectors.append(collector)
        return collector

    def test_explicit_resolution_retains_completion_once_with_no_item_credits(self):
        first = self.collector()
        cid = first.completed('lost-rewards', completed_at=12345)
        self.assertTrue(first.missing('lost-rewards', 'Operator verified results already left', cid))
        self.assertTrue(first.wait_idle(4))
        data = first.store.dashboard(first.session_id)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recognized_icons'], 0)
        self.assertEqual(data['runs'][0]['status'], 'missing_screenshot')
        self.assertIn('Operator verified results already left', data['runs'][0]['warnings'])
        self.assertEqual(data['runs'][0]['completed_at'], 12345)
        self.assertEqual(self.recognizer.calls, [])
        self.assertFalse(first.evidence('lost-rewards', self.frame, cid))
        first.close()
        restarted = self.collector(process='fixture:restart')
        with patch.object(loot.sqlite3, 'connect', side_effect=sqlite3.OperationalError('offline')):
            self.assertTrue(restarted.missing('lost-rewards', 'Repeated recovery', cid))
            self.assertFalse(restarted.evidence('lost-rewards', self.frame, cid))
            self.assertEqual(restarted.completed('lost-rewards'), cid)
        self.assertEqual(first.store.dashboard(first.session_id)['completed_runs'], 1)
        self.assertFalse(list(first.spool.glob('*.json')))

    def test_persistence_failure_has_no_ack_and_keeps_original_job(self):
        collector = self.collector()
        collector._stop.set()
        collector._thread.join(2)
        cid = collector.completed('lost-rewards')
        with patch.object(collector, '_save', side_effect=OSError('disk unavailable')):
            self.assertFalse(collector.missing('lost-rewards', completion_id=cid))
        job = json.loads((collector.spool / (cid + '.json')).read_text())
        self.assertEqual(job['state'], 'awaiting_evidence')

    def test_resolution_rejects_saved_and_orphaned_pngs_and_unrelated_jobs(self):
        collector = self.collector()
        collector._stop.set()
        collector._thread.join(2)
        cid = collector.completed('capture')
        png = collector.spool / (cid + '.png')
        png.write_bytes(b'orphaned evidence must be preserved')
        self.assertFalse(collector.missing('capture', completion_id=cid))
        png.unlink()
        self.assertFalse(collector.missing('different-run', completion_id=cid))
        self.assertFalse(collector.missing('unknown-run'))
        self.assertTrue(collector.evidence('capture', self.frame, cid))
        self.assertFalse(collector.missing('capture', completion_id=cid))

    def test_job_ack_survives_crash_before_farmer_save_and_database_commit(self):
        collector = self.collector()
        collector._stop.set()
        collector._thread.join(2)
        cid = collector.completed('lost-rewards')
        with patch.object(loot.sqlite3, 'connect', side_effect=sqlite3.OperationalError('offline')):
            self.assertTrue(collector.missing('lost-rewards', 'Verified missing rewards', cid))
            self.assertTrue(collector.missing('lost-rewards', 'Repeated recovery', cid))
        self.assertFalse(collector.evidence('lost-rewards', self.frame, cid))
        restarted = self.collector(process='fixture:restart')
        self.assertTrue(restarted.wait_idle(4))
        self.assertTrue(restarted.missing('lost-rewards', completion_id=cid))
        self.assertEqual(collector.store.dashboard(collector.session_id)['completed_runs'], 1)


if __name__ == '__main__':
    unittest.main()
