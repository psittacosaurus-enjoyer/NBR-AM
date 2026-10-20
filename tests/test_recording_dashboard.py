"""Passive recording visibility under storage outages; no collector or device."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import loot_tracking as loot


class RecordingDashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'runtime' / 'loot'

    def job(self, cid='job_fixture', account='acc1', device='fixture-device', **values):
        job = dict(completion_id=cid, session_id='session_fixture', account=account,
                   device_id=device, stage='universal', run_id='run_fixture', run_sequence=1,
                   completed_at=100, state='awaiting_evidence', screenshot=None,
                   process_identity='fixture:process', tracking_started=50)
        job.update(values)
        loot._atomic_json(self.directory / 'pending' / (cid + '.json'), job)
        return job

    def test_disk_backlog_remains_visible_without_creating_database(self):
        job = self.job()
        loot._atomic_json(self.directory / 'health_acc1.json', dict(account='acc1', device_id='fixture-device',
            session_id=job['session_id'], status='Screenshot Capture Pending', error='Screenshot write failed',
            next_retry_at=200))
        before = {str(path): path.read_bytes() for path in self.directory.rglob('*') if path.is_file()}
        data = loot.read_dashboard(self.root, dict(running=False, instance_account='acc1'))
        self.assertEqual(data['outstanding_jobs'], 1)
        self.assertEqual(data['capture_pending'], 1)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['runs'][0]['completion_id'], job['completion_id'])
        self.assertEqual(data['collector_health']['error'], 'Screenshot write failed')
        self.assertEqual(data['status'], 'Screenshot Capture Pending')
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.directory.rglob('*') if path.is_file()})
        self.assertFalse((self.directory / 'loot.sqlite3').exists())

    def test_temporary_sqlite_failure_preserves_passive_pending_visibility(self):
        loot.LootStore(self.root)
        self.job(state='recognition_pending')
        with patch.object(loot, 'LootStore', side_effect=sqlite3.OperationalError('database is locked')):
            data = loot.read_dashboard(self.root, dict(running=False, instance_account='acc1'))
        self.assertEqual(data['outstanding_jobs'], 1)
        self.assertEqual(data['capture_pending'], 0)
        self.assertEqual(data['collector_health']['error'], 'database is locked')
        self.assertIn('temporarily unavailable', data['detail'])
        self.assertTrue((self.directory / 'pending' / 'job_fixture.json').is_file())

    def test_database_and_spool_copy_are_one_outstanding_job(self):
        store = loot.LootStore(self.root)
        sid = store.ensure_session('fixture:process', 'universal')
        job = self.job(session_id=sid, state='recognition_pending', error='Temporary reference load failure',
                       next_retry_at=200)
        store.accept(job)
        data = loot.read_dashboard(self.root, dict(running=False, instance_account='acc1'))
        self.assertEqual(data['outstanding_jobs'], 1)
        self.assertEqual(data['capture_pending'], 0)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recording_states']['recognition_pending'], 1)
        self.assertEqual(data['runs'][0]['recording_state'], 'recognition_pending')
        self.assertEqual(data['runs'][0]['error'], 'Temporary reference load failure')

    def test_outage_backlog_is_account_and_device_scoped(self):
        self.job()
        self.job('job_other_account', account='other')
        self.job('job_other_device', device='other-device')
        state = dict(running=False, instance_account='acc1', instance={'android_id': 'fixture-device'})
        data = loot.read_dashboard(self.root, state)
        self.assertEqual(data['outstanding_jobs'], 1)
        self.assertEqual([run['completion_id'] for run in data['runs']], ['job_fixture'])
        rejected = loot.read_dashboard(self.root, state, session_id='unrelated_session')
        self.assertEqual(rejected['outstanding_jobs'], 0)
        self.assertIsNone(rejected['session'])

    def test_partial_or_malformed_metadata_cannot_hide_valid_pending_work(self):
        self.job()
        self.job('job_malformed', state={'invalid': 'object'})
        (self.directory / 'pending' / 'job_partial.json').write_text('{', encoding='utf-8')
        data = loot.read_dashboard(self.root, dict(running=False, instance_account='acc1'))
        self.assertEqual(data['outstanding_jobs'], 1)
        self.assertEqual(data['runs'][0]['completion_id'], 'job_fixture')


if __name__ == '__main__':
    unittest.main()
