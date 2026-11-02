"""Recover existing same-job loot evidence without any current game frame."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.backend import loot
from test_loot_tracking import FakeRecognizer


class RetainedEvidenceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nbr-retained-evidence-')
        self.root = Path(self.temporary.name)
        self.collector = loot.Collector(self.root, 'universal', 'fixture:retained-evidence',
            account='acc1', device_id='fixture-device', recognizer_factory=FakeRecognizer)
        self.collector._stop.set()
        self.collector._thread.join(2)
        self.frame = np.full((24, 32, 3), 100, np.uint8)
        self.run_id = 'original-run'
        self.cid = self.collector.completed(self.run_id, completed_at=12345)
        self.path = self.collector.spool / (self.cid + '.json')
        self.image = self.collector.spool / (self.cid + '.png')

    def tearDown(self):
        self.collector.close()
        self.temporary.cleanup()

    def job(self):
        return json.loads(self.path.read_text('utf-8'))

    def test_orphaned_same_job_png_is_verified_and_durably_reattached(self):
        original = self.job()
        self.assertTrue(cv2.imwrite(str(self.image), self.frame))
        with patch.object(cv2, 'imwrite', side_effect=AssertionError('Recovery must not capture')):
            self.assertTrue(self.collector.recover_evidence(self.run_id, self.cid))
        recovered = self.job()
        self.assertEqual(recovered['state'], 'recognition_pending')
        self.assertEqual(recovered['screenshot'], str(self.image.resolve()))
        self.assertEqual(recovered['screenshot_sha256'], self.collector._verify_image(self.image))
        for key in ('completion_id', 'session_id', 'run_id', 'account', 'device_id',
                    'stage', 'completed_at'):
            self.assertEqual(recovered[key], original[key])

    def test_corrupt_png_and_hash_mismatch_never_acknowledge_evidence(self):
        original = self.job()
        self.image.write_bytes(b'not a PNG')
        self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertEqual(self.job(), original)
        self.assertTrue(cv2.imwrite(str(self.image), self.frame))
        changed = dict(original, screenshot_sha256='0' * 64)
        loot._atomic_json(self.path, changed)
        self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertEqual(self.job(), changed)

    def test_metadata_ack_write_failure_retries_original_image(self):
        self.assertTrue(cv2.imwrite(str(self.image), self.frame))
        original = self.job()
        with patch.object(self.collector, '_save', side_effect=OSError('temporary sharing failure')):
            self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertEqual(self.job(), original)
        self.assertTrue(self.image.exists())
        self.assertTrue(self.collector.recover_evidence(self.run_id, self.cid))

    def test_other_run_account_device_or_stage_cannot_supply_evidence(self):
        self.assertTrue(cv2.imwrite(str(self.image), self.frame))
        original = self.job()
        for field, value in [('run_id', 'another-run'), ('account', 'another-account'),
                             ('device_id', 'another-device'), ('device_id', ''),
                             ('stage', 'ninja_road')]:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(original)
                changed[field] = value
                loot._atomic_json(self.path, changed)
                self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
                self.assertEqual(self.job(), changed)
        loot._atomic_json(self.path, original)
        self.assertFalse(self.collector.recover_evidence('unrelated-run', self.cid))

    def test_unrelated_png_is_never_attached(self):
        unrelated = self.collector.spool / 'another-completion.png'
        self.assertTrue(cv2.imwrite(str(unrelated), self.frame))
        original = self.job()
        self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertEqual(self.job(), original)

    def test_finalized_receipt_ack_survives_cleanup_and_database_outage(self):
        self.assertTrue(self.collector.evidence(self.run_id, self.frame, self.cid))
        self.collector._process(self.job())
        self.assertFalse(self.image.exists())
        self.assertFalse(self.path.exists())
        with patch.object(loot.sqlite3, 'connect', side_effect=sqlite3.OperationalError('offline')):
            self.assertTrue(self.collector.recover_evidence(self.run_id, self.cid))
        data = self.collector.store.dashboard(self.collector.session_id)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recognized_icons'], 1)

    def test_finalized_database_ack_requires_device_bound_completion_identity(self):
        self.assertTrue(self.collector.evidence(self.run_id, self.frame, self.cid))
        self.collector._process(self.job())
        receipt = self.collector.directory / 'receipts' / (self.cid + '.json')
        receipt.unlink()
        self.assertTrue(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertFalse(self.collector.recover_evidence('different-run', self.cid))
        original = self.collector.device_id
        self.collector.device_id = 'another-device'
        try:
            self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        finally:
            self.collector.device_id = original

    def test_missing_evidence_resolution_cannot_be_revived_by_a_later_png(self):
        self.assertTrue(self.collector.missing(self.run_id, 'verified original evidence unavailable', self.cid))
        original = self.job()
        self.assertTrue(cv2.imwrite(str(self.image), self.frame))
        self.assertFalse(self.collector.recover_evidence(self.run_id, self.cid))
        self.assertEqual(self.job(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
