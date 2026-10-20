"""Explicit lost-evidence recovery uses isolated state and a fake device only."""
import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

import farm
from instance_fixtures import legacy_target
from test_stages import FakeDevice


class MissingEvidenceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='nbr-missing-evidence-')
        self.addCleanup(temp.cleanup)
        self.runtime = Path(temp.name)
        self.target = legacy_target()
        self.device = FakeDevice(np.zeros((8, 8, 3), dtype=np.uint8))
        self.device.target = self.target
        self.device.verify = Mock()
        self.device.connect = Mock()
        self.detector = Mock()
        self.detector.changed.return_value = False
        self.start_spec = {'action': {'id': 'start_mission', 'point': [450, 1305],
                                      'expected': ['IN_BATTLE']}}
        self.detector.classify.return_value = ('READY_TO_START', self.start_spec, {})
        self.collector = Mock()
        self.collector.missing.return_value = True
        self.collector.health.return_value = {}
        self.active = Mock(return_value=False)
        self.busy = Mock(return_value=False)
        self.reason = 'Results left after interrupted device capture; original rewards unavailable'
        self.progress_path = self.runtime / 'universal.json'
        self.original = {
            'stage': 'universal', 'instance': farm.instance_dict(self.target),
            'counts': {'started': 10, 'completed': 10, 'failed': 0},
            'pending': None, 'screen': 'READY_TO_START', 'stamina_until': 0,
            'evidence': 'retained-original-evidence.png',
            'blocked': 'Waiting for verified loot screenshot persistence',
            'recording_health': {'status': 'Screenshot Capture Pending', 'outstanding_jobs': 1,
                                 'recording_states': {'awaiting_evidence': 1}},
            'run': {'id': 'original-run', 'start': 90, 'outcome': 'completed',
                    'recording': {'version': 1, 'completed_at': 100, 'account': 'acc1',
                                  'device_id': self.target.android_id, 'stage': 'universal',
                                  'completion_id': 'original-completion',
                                  'state': 'screenshot_capture_pending', 'error': 'Original capture interrupted',
                                  'capture_attempts': 2, 'next_attempt_at': 500}},
        }
        for item in [
            patch.object(farm, 'RUNTIME', self.runtime),
            patch.object(farm, 'STOP', self.runtime / 'STOP'),
            patch.object(farm, 'process_target', return_value=self.target),
            patch.object(farm, 'is_legacy_instance', return_value=True),
            patch.object(farm, 'Device', return_value=self.device),
            patch.object(farm, 'Detector', return_value=self.detector),
            patch.object(farm, '_loot_start', return_value=self.collector),
            patch.object(farm, 'controller_active', self.active),
            patch.object(farm, 'lock_busy', self.busy),
            patch.object(farm, 'event'),
            patch.object(farm.time, 'sleep'),
            patch.object(farm, 'process_claim', return_value={
                'worker_pid': 123, 'worker_pid_identity': 'offline-fixture'}),
            patch('builtins.print'),
        ]:
            item.start()
            self.addCleanup(item.stop)
        farm.write_json(self.progress_path, self.original)
        farm.STOP.write_text('Intentional stop preserved', encoding='utf-8')

    def resolve(self):
        farm.resolve_missing_evidence('universal', reason=self.reason)

    def saved(self):
        return farm.read_json(self.progress_path)

    def assert_unchanged(self):
        self.assertEqual(self.saved(), self.original)
        self.collector.missing.assert_not_called()
        self.assertEqual(self.device.actions, [])

    def test_success_retains_original_transaction_and_error_after_durable_ack(self):
        self.collector.missing.side_effect = lambda *args, **kwargs: self.saved() == self.original
        self.resolve()
        saved = self.saved()
        self.assertEqual(saved['counts'], self.original['counts'])
        self.assertEqual(saved['evidence'], self.original['evidence'])
        self.assertEqual(saved['run']['id'], 'original-run')
        self.assertEqual(saved['run']['outcome'], 'completed')
        recording = saved['run']['recording']
        self.assertEqual(recording['completion_id'], 'original-completion')
        self.assertEqual(recording['completed_at'], 100)
        self.assertEqual(recording['error'], 'Original capture interrupted')
        self.assertEqual(recording['capture_attempts'], 2)
        self.assertEqual(recording['state'], 'missing_screenshot')
        self.assertIs(recording['missing_evidence_acknowledged'], True)
        self.assertEqual(recording['resolution_reason'], self.reason)
        self.assertIsNone(saved['blocked'])
        self.assertNotIn('recording_health', saved)
        self.assertEqual(farm.STOP.read_text('utf-8'), 'Intentional stop preserved')
        self.assertEqual(self.device.verify.call_count, 2)
        self.assertEqual(self.detector.classify.call_count, 2)
        self.assertEqual(self.device.actions, [])
        self.collector.missing.assert_called_once_with('original-run', reason=self.reason,
                                                       completion_id='original-completion')
        self.collector.close.assert_called_once_with(end_session=True)
        farm.event.assert_called_once_with('missing_evidence_resolved', stage='universal',
            account='acc1', device_id=self.target.android_id, run_id='original-run',
            completion_id='original-completion', reason=self.reason)

    def test_active_controller_or_restart_refuses_without_device_access(self):
        for active, restart in [(True, False), (False, True)]:
            with self.subTest(active=active, restart=restart):
                self.active.return_value = active
                self.busy.return_value = restart
                with self.assertRaisesRegex(RuntimeError, 'Stop the controller'):
                    self.resolve()
                self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_previously_selected_stage_pending_input_is_preserved_and_refused(self):
        farm.write_json(self.runtime / 'selection.json', {'stage': 'practice-part-1'})
        archive = self.runtime / 'practice_part_1.json'
        farm.write_json(archive, {'stage': 'practice-part-1', 'pending': {'id': 'retry'}})
        before = archive.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'saved input'):
            self.resolve()
        self.assert_unchanged()
        self.assertEqual(archive.read_bytes(), before)
        self.device.connect.assert_not_called()

    def test_unrelated_archived_pending_input_is_preserved_without_blocking_universal(self):
        farm.write_json(self.runtime / 'selection.json', {'stage': 'universal'})
        archive = self.runtime / 'practice_part_1.json'
        farm.write_json(archive, {'stage': 'practice-part-1', 'pending': {'id': 'reveal_rewards'}})
        before = archive.read_bytes()
        self.resolve()
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(self.saved()['run']['recording']['state'], 'missing_screenshot')
        self.collector.missing.assert_called_once()

    def test_current_pending_input_is_preserved(self):
        self.original['pending'] = {'id': 'advance_completion', 'source': 'MISSION_COMPLETE'}
        farm.write_json(self.progress_path, self.original)
        with self.assertRaisesRegex(RuntimeError, 'saved input'):
            self.resolve()
        self.assert_unchanged()

    def test_saved_instance_must_match_selected_device(self):
        self.original['instance'] = farm.instance_dict(replace(self.target, android_id='another-device'))
        farm.write_json(self.progress_path, self.original)
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.resolve()
        self.assert_unchanged()

    def test_recording_account_device_and_stage_must_match(self):
        for field, value in [('account', 'another-account'), ('device_id', 'another-device'),
                             ('stage', 'another-stage')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.original)
                changed['run']['recording'][field] = value
                farm.write_json(self.progress_path, changed)
                with self.assertRaisesRegex(RuntimeError, 'does not match'):
                    self.resolve()
                self.assertEqual(self.saved(), changed)
                self.collector.missing.assert_not_called()

    def test_only_saved_completed_capture_pending_transaction_is_eligible(self):
        changes = [lambda p: p['run'].update(outcome=None),
                   lambda p: p['run'].pop('recording'),
                   lambda p: p['run']['recording'].pop('completion_id'),
                   lambda p: p['run']['recording'].update(state='screenshot_saved')]
        for change in changes:
            with self.subTest(change=change):
                changed = copy.deepcopy(self.original)
                change(changed)
                farm.write_json(self.progress_path, changed)
                with self.assertRaisesRegex(RuntimeError, 'No completed transaction'):
                    self.resolve()
                self.assertEqual(self.saved(), changed)
                self.collector.missing.assert_not_called()

    def test_both_fresh_observations_must_be_start_mission(self):
        for names in [('READY_TO_RETRY', 'READY_TO_START'), ('READY_TO_START', 'UNKNOWN')]:
            with self.subTest(names=names):
                self.detector.classify.side_effect = [(name, {}, {}) for name in names]
                with self.assertRaisesRegex(RuntimeError, 'Two fresh Start Mission'):
                    self.resolve()
                self.assert_unchanged()

    def test_other_foreground_and_changed_android_identity_refuse_resolution(self):
        with patch.object(self.device, 'foreground', return_value='other.app'):
            with self.assertRaisesRegex(RuntimeError, 'verified Start Mission'):
                self.resolve()
        self.device.verify.side_effect = RuntimeError('Android device identity mismatch')
        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            self.resolve()
        self.assert_unchanged()

    def test_missing_ack_failure_or_exception_preserves_transaction(self):
        for result in [False, None, RuntimeError('temporary persistence failure')]:
            with self.subTest(result=result):
                self.collector.missing.reset_mock()
                self.collector.missing.side_effect = result if isinstance(result, Exception) else None
                self.collector.missing.return_value = result
                with self.assertRaisesRegex(RuntimeError, 'not durably acknowledged'):
                    self.resolve()
                self.assertEqual(self.saved(), self.original)
                self.assertEqual(self.device.actions, [])
                self.collector.close.assert_called_with(end_session=True)

    def test_save_failure_after_ack_can_retry_without_losing_original_transaction(self):
        with patch.object(farm, 'write_json', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                self.resolve()
        self.assertEqual(self.saved(), self.original)
        self.resolve()
        self.assertEqual(self.saved()['run']['recording']['state'], 'missing_screenshot')
        self.assertEqual(self.collector.missing.call_count, 2)

    def test_resolved_recording_does_not_capture_old_results_and_permits_next_start(self):
        self.resolve()
        engine = farm.Engine('universal', self.target)
        engine.loot_collector = self.collector
        engine.update(self.device.image, 'READY_TO_RETRY', {})
        self.collector.completed.assert_not_called()
        self.collector.evidence.assert_not_called()
        farm.STOP.unlink()
        engine.tick()
        self.assertEqual(self.device.actions, ['start_mission'])
        self.assertEqual(engine.data['counts'], self.original['counts'])
        self.detector.classify.return_value = ('IN_BATTLE', {}, {})
        engine.tick()
        self.assertNotEqual(engine.data['run']['id'], 'original-run')
        self.assertIsNone(engine.data['run']['outcome'])
        self.assertEqual(engine.data['counts']['started'], 11)
        self.assertEqual(engine.data['counts']['completed'], 10)

    def test_missing_state_without_explicit_ack_still_blocks(self):
        changed = copy.deepcopy(self.original)
        changed['run']['recording']['state'] = 'missing_screenshot'
        farm.write_json(self.progress_path, changed)
        engine = farm.Engine('universal', self.target)
        engine.loot_collector = self.collector
        farm.STOP.unlink()
        engine.tick()
        self.assertEqual(self.device.actions, [])
        self.assertIn('explicit durable resolution', engine.data['blocked'])
        self.collector.evidence.assert_not_called()

    def test_cli_requires_reason_and_dispatches_explicit_recovery(self):
        with patch.object(farm.sys, 'argv', ['farm.py', 'resolve-missing-evidence']), \
                patch.object(farm, 'resolve_missing_evidence', wraps=farm.resolve_missing_evidence):
            with self.assertRaisesRegex(ValueError, 'Provide a reason'):
                farm.main()
        self.assert_unchanged()
        with patch.object(farm.sys, 'argv', ['farm.py', 'resolve-missing-evidence', '--reason', self.reason]), \
                patch.object(farm, 'resolve_missing_evidence') as resolve:
            farm.main()
        resolve.assert_called_once_with('universal', reason=self.reason)


if __name__ == '__main__':
    unittest.main()
