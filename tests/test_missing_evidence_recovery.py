"""Explicit lost-evidence recovery uses isolated state and a fake device only."""
import copy
import contextlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import controller as farm
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
        self.home_detector = Mock()
        self.home_spec = {'name': 'NR_HOME', 'verified_safe': True,
                          'action': {'id': 'nr_available'}}
        self.home_detector.classify.return_value = ('UNKNOWN', None, {})
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
            patch.object(farm, 'Detector', side_effect=lambda stage: (
                self.home_detector if stage == 'ninja_road' else self.detector)),
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

    def save_results_pending(self):
        # Input tokens are distinct from the mission's run ID in production.
        self.original['pending'] = {
            'id': 'next', 'source': 'RESULTS_NEXT', 'time': 110, 'run_id': 'results-input-token',
            'expected': ['READY_TO_RETRY', 'REWARD_REVEAL', 'TRIAL_COINS_NEXT'],
        }
        farm.write_json(self.progress_path, self.original)

    def save_reveal_pending(self):
        self.original['pending'] = {
            'id': 'reveal_rewards', 'source': 'REWARD_REVEAL', 'time': 110,
            'run_id': 'reveal-input-token', 'expected': ['READY_TO_RETRY', 'LUCKY_REWARD'],
        }
        farm.write_json(self.progress_path, self.original)

    def observe_home(self):
        self.detector.classify.return_value = ('UNKNOWN', None, {})
        self.home_detector.classify.return_value = ('NR_HOME', self.home_spec, {})

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

    def test_departed_results_latch_is_audited_and_cleared_only_after_durable_ack(self):
        self.save_results_pending()
        previous_audit = {'reason': 'Earlier start recovery', 'original_pending': {'id': 'start_mission'}}
        self.original['pending_input_resolution'] = previous_audit
        farm.write_json(self.progress_path, self.original)
        self.collector.missing.side_effect = lambda *args, **kwargs: self.saved() == self.original
        self.resolve()
        saved = self.saved()
        self.assertIsNone(saved['pending'])
        self.assertEqual(saved['counts'], self.original['counts'])
        self.assertEqual(saved['pending_input_resolution'], previous_audit)
        recording = saved['run']['recording']
        self.assertEqual(recording['state'], 'missing_screenshot')
        audit = saved['results_input_resolution']
        self.assertEqual(audit['original_pending'], self.original['pending'])
        self.assertEqual(audit['reason'], self.reason)
        self.assertEqual(audit['resolved_at'], recording['resolved_at'])
        self.assertEqual((audit['run_id'], audit['completion_id']), ('original-run', 'original-completion'))
        self.assertEqual(len(audit['observations']), 2)
        for observation in audit['observations']:
            self.assertEqual(observation['screen'], 'READY_TO_START')
            self.assertEqual(observation['android_id'], self.target.android_id)
            self.assertIsInstance(observation['time'], (int, float))
        self.assertEqual(self.device.verify.call_count, 2)
        self.assertEqual(self.device.actions, [])
        self.collector.missing.assert_called_once_with('original-run', reason=self.reason,
                                                       completion_id='original-completion')

    def test_other_input_latches_and_malformed_or_older_results_latches_are_refused(self):
        self.save_results_pending()
        valid = copy.deepcopy(self.original)
        changes = [
            lambda p: p['pending'].update(id='start_mission', source='READY_TO_START'),
            lambda p: p['pending'].update(id='retry', source='READY_TO_RETRY'),
            lambda p: p['pending'].update(source='TRIAL_COINS_NEXT'),
            lambda p: p['pending'].update(expected=['READY_TO_START']),
            lambda p: p['pending'].update(expected=['READY_TO_RETRY', 'REWARD_REVEAL', {}]),
            lambda p: p['pending'].update(time=99),
            lambda p: p['pending'].update(time=True),
            lambda p: p['pending'].update(time=float('inf')),
            lambda p: p['pending'].pop('run_id'),
            lambda p: p.update(connection_pending={'id': 'retry_connection'}),
            lambda p: p.update(boss_swap={'pending': {'id': 'nr_boss_swap'}}),
        ]
        for change in changes:
            with self.subTest(change=change):
                self.original = copy.deepcopy(valid)
                change(self.original)
                # Legacy writers admitted Infinity; exercise validation of an
                # existing malformed file rather than the stricter new writer.
                self.progress_path.write_text(json.dumps(self.original), encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'saved input'):
                    self.resolve()
                self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_departed_results_latch_requires_both_fresh_start_observations(self):
        self.save_results_pending()
        for names in [('READY_TO_RETRY', 'READY_TO_START'), ('READY_TO_START', 'RESULTS_NEXT')]:
            with self.subTest(names=names):
                self.detector.classify.side_effect = [(name, {}, {}) for name in names]
                with self.assertRaisesRegex(RuntimeError, 'Two fresh Start Mission'):
                    self.resolve()
                self.assert_unchanged()

    def test_results_latch_and_original_transaction_survive_missing_ack_or_save_failure(self):
        self.save_results_pending()
        for result in [False, RuntimeError('temporary persistence failure')]:
            with self.subTest(result=result):
                self.collector.missing.side_effect = result if isinstance(result, Exception) else None
                self.collector.missing.return_value = result
                with self.assertRaisesRegex(RuntimeError, 'not durably acknowledged'):
                    self.resolve()
                self.assertEqual(self.saved(), self.original)
                self.assertEqual(self.device.actions, [])
        self.collector.missing.side_effect = None
        self.collector.missing.return_value = True
        with patch.object(farm, 'write_json', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                self.resolve()
        self.assertEqual(self.saved(), self.original)
        self.resolve()
        self.assertIsNone(self.saved()['pending'])
        self.assertEqual(self.saved()['results_input_resolution']['original_pending'],
                         self.original['pending'])

    def test_two_verified_home_observations_close_only_supported_results_latches_after_durable_ack(self):
        self.device.capture = Mock(wraps=self.device.capture)
        for save_pending in (self.save_results_pending, self.save_reveal_pending):
            with self.subTest(pending=save_pending.__name__):
                save_pending()
                self.offline_job()
                self.observe_home()
                farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})
                self.collector.missing.reset_mock()
                self.device.verify.reset_mock()
                self.device.capture.reset_mock()
                self.home_detector.classify.reset_mock()
                self.collector.missing.side_effect = lambda *args, **kwargs: (
                    self.saved() == self.original
                    and farm.read_json(self.runtime / 'session.json')['mission_verified'])
                original_write = farm.write_json
                def save(path, value):
                    if Path(path) == self.progress_path:
                        self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
                    return original_write(path, value)
                with patch.object(farm, 'write_json', side_effect=save):
                    self.resolve()
                saved = self.saved()
                self.assertIsNone(saved['pending'])
                self.assertEqual(saved['counts'], self.original['counts'])
                self.assertEqual(saved['run']['id'], self.original['run']['id'])
                self.assertEqual(saved['run']['outcome'], 'completed')
                recording = saved['run']['recording']
                self.assertEqual(recording['state'], 'missing_screenshot')
                self.assertEqual(recording['resolution_mode'], 'verified_home')
                self.assertEqual(recording['error'], 'Original capture interrupted')
                audit = saved['results_input_resolution']
                self.assertEqual(audit['original_pending'], self.original['pending'])
                self.assertEqual(audit['reason'], self.reason)
                self.assertEqual(audit['resolved_at'], recording['resolved_at'])
                self.assertEqual((audit['run_id'], audit['completion_id']),
                                 ('original-run', 'original-completion'))
                self.assertEqual([value['screen'] for value in audit['observations']], ['NR_HOME', 'NR_HOME'])
                for observation in audit['observations']:
                    self.assertEqual((observation['instance_key'], observation['android_id'],
                                      observation['endpoint'], observation['foreground']),
                                     (self.target.key, self.target.android_id,
                                      self.target.endpoint, farm.PACKAGE))
                self.assertEqual(self.device.verify.call_count, 2)
                self.assertEqual(self.device.capture.call_count, 2)
                self.assertEqual(self.home_detector.classify.call_count, 2)
                self.assertEqual(self.device.actions, [])
                self.assertEqual(farm.STOP.read_text('utf-8'), 'Intentional stop preserved')
                self.collector.missing.assert_called_once_with('original-run', reason=self.reason,
                                                               completion_id='original-completion')

    def test_home_recovery_requires_two_same_safe_positive_observations(self):
        self.save_reveal_pending()
        self.offline_job()
        self.observe_home()
        for observations in [
            [('NR_HOME', self.home_spec, {}), ('NR_AVAILABLE', self.home_spec, {})],
            [('NR_HOME', self.home_spec, {}), ('UNKNOWN', None, {})],
            [('UNKNOWN', None, {}), ('NR_HOME', self.home_spec, {})],
            [('NR_HOME', self.home_spec, {}), ('NR_HOME', {}, {})],
            [('NR_HOME', self.home_spec, {}), ('NR_HOME', {'verified_safe': False}, {})],
        ]:
            with self.subTest(observations=observations):
                self.home_detector.classify.side_effect = observations
                with self.assertRaisesRegex(RuntimeError, 'Two fresh Start Mission'):
                    self.resolve()
                self.assert_unchanged()

    def test_home_cannot_mix_with_start_or_recover_other_input_types(self):
        self.save_results_pending()
        self.offline_job()
        self.observe_home()
        for screens in [('READY_TO_START', 'UNKNOWN'), ('UNKNOWN', 'READY_TO_START')]:
            with self.subTest(screens=screens):
                self.detector.classify.side_effect = [(name, self.start_spec, {}) for name in screens]
                with self.assertRaisesRegex(RuntimeError, 'Two fresh Start Mission'):
                    self.resolve()
                self.assert_unchanged()
        self.detector.classify.side_effect = None
        self.save_reveal_pending()
        self.detector.classify.return_value = ('READY_TO_START', self.start_spec, {})
        with self.assertRaisesRegex(RuntimeError, 'Two fresh Start Mission'):
            self.resolve()
        self.assert_unchanged()
        self.observe_home()
        for pending in (None, {'id': 'start_mission', 'source': 'READY_TO_START'},
                        {'id': 'retry', 'source': 'READY_TO_RETRY'},
                        {'id': 'advance_completion', 'source': 'MISSION_COMPLETE'}):
            with self.subTest(pending=pending):
                self.original['pending'] = pending
                farm.write_json(self.progress_path, self.original)
                with self.assertRaises(RuntimeError):
                    self.resolve()
                self.assert_unchanged()

    def test_home_rejects_malformed_results_expectations_time_and_input_tokens_before_device_access(self):
        self.save_reveal_pending()
        valid = copy.deepcopy(self.original)
        self.offline_job()
        self.observe_home()
        changes = [
            lambda p: p['pending'].update(expected=['READY_TO_RETRY']),
            lambda p: p['pending'].update(expected=['READY_TO_RETRY', 'READY_TO_RETRY']),
            lambda p: p['pending'].update(expected=['READY_TO_RETRY', {}]),
            lambda p: p['pending'].update(expected='READY_TO_RETRY,LUCKY_REWARD'),
            lambda p: p['pending'].update(source='RESULTS_NEXT'),
            lambda p: p['pending'].update(time=99),
            lambda p: p['pending'].update(time=True),
            lambda p: p['pending'].update(time='110'),
            lambda p: p['pending'].update(time=float('inf')),
            lambda p: p['pending'].update(time=farm.time.time() + 3600),
            lambda p: p['run']['recording'].update(completed_at=-1),
            lambda p: p['pending'].update(run_id=''),
            lambda p: p['pending'].update(run_id=' '),
            lambda p: p['pending'].update(run_id='../different-token'),
            lambda p: p['pending'].update(run_id=True),
            lambda p: p['pending'].update(run_id='a' * 201),
        ]
        for change in changes:
            with self.subTest(change=change):
                self.original = copy.deepcopy(valid)
                change(self.original)
                self.progress_path.write_text(json.dumps(self.original), encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'saved input'):
                    self.resolve()
                self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_home_rechecks_foreground_and_identity_on_the_second_observation(self):
        self.save_reveal_pending()
        self.offline_job()
        self.observe_home()
        with patch.object(self.device, 'foreground', side_effect=[farm.PACKAGE, 'other.app']):
            with self.assertRaisesRegex(RuntimeError, 'verified Start Mission'):
                self.resolve()
        self.assert_unchanged()
        self.device.verify.side_effect = [None, RuntimeError('Android device identity mismatch')]
        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            self.resolve()
        self.assert_unchanged()

    def test_home_requires_original_durable_job_and_refuses_retained_or_mismatched_evidence(self):
        self.save_reveal_pending()
        self.observe_home()
        with self.assertRaisesRegex(RuntimeError, 'original durable recording'):
            self.resolve()
        self.assert_unchanged()
        path, value = self.offline_job()
        for field, replacement in [('run_id', 'different-run'), ('completion_id', 'different-completion'),
                                   ('device_id', 'different-device'), ('account', 'different-account'),
                                   ('stage', 'ninja_road'), ('completed_at', 101),
                                   ('screenshot', 'retained-results.png')]:
            with self.subTest(field=field):
                farm.write_json(path, dict(value, **{field: replacement}))
                with self.assertRaisesRegex(RuntimeError, 'identity|evidence'):
                    self.resolve()
                self.assert_unchanged()
        farm.write_json(path, value)
        farm.STOP.unlink()
        with self.assertRaisesRegex(RuntimeError, 'existing Stop'):
            self.resolve()
        self.assert_unchanged()

    def test_home_collector_or_progress_failure_preserves_original_latch_and_transaction(self):
        self.save_reveal_pending()
        self.offline_job()
        self.observe_home()
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})
        for result in [False, None, RuntimeError('temporary persistence failure')]:
            with self.subTest(result=result):
                self.collector.missing.side_effect = result if isinstance(result, Exception) else None
                self.collector.missing.return_value = result
                with self.assertRaisesRegex(RuntimeError, 'not durably acknowledged'):
                    self.resolve()
                self.assertEqual(self.saved(), self.original)
                self.assertTrue(farm.read_json(self.runtime / 'session.json')['mission_verified'])
                self.assertEqual(self.device.actions, [])
        self.collector.missing.side_effect = None
        self.collector.missing.return_value = True
        original_write = farm.write_json
        def save(path, value):
            if Path(path) == self.progress_path:
                raise OSError('fixture progress write unavailable')
            return original_write(path, value)
        with patch.object(farm, 'write_json', side_effect=save):
            with self.assertRaisesRegex(OSError, 'progress write unavailable'):
                self.resolve()
        self.assertEqual(self.saved(), self.original)
        self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        self.assertEqual(self.device.actions, [])
        self.resolve()
        self.assertIsNone(self.saved()['pending'])
        self.assertEqual(self.saved()['results_input_resolution']['original_pending'], self.original['pending'])

    def test_offline_recovery_still_refuses_reward_reveal_input_without_device_access(self):
        self.save_reveal_pending()
        self.offline_job()
        self.observe_home()
        with self.assertRaisesRegex(RuntimeError, 'saved input'):
            self.offline_resolve()
        self.assert_unchanged()
        self.device.connect.assert_not_called()

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
        self.save_results_pending()
        self.resolve()
        audit = copy.deepcopy(self.saved()['results_input_resolution'])
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
        self.assertEqual(self.saved()['results_input_resolution'], audit)

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

    def offline_job(self):
        recording = self.original['run']['recording']
        value = dict(completion_id=recording['completion_id'], run_id=self.original['run']['id'],
                     session_id='original-session', account=recording['account'],
                     device_id=recording['device_id'], stage='universal',
                     completed_at=recording['completed_at'], state='awaiting_evidence', screenshot=None)
        path = self.runtime / 'loot/pending/original-completion.json'
        farm.write_json(path, value)
        return path, value

    def offline_resolve(self, completion_id='original-completion'):
        farm.resolve_missing_evidence('universal', reason=self.reason, offline=True,
                                      completion_id=completion_id)

    def test_offline_resolution_preserves_stop_identity_and_resets_verification_before_ack_save(self):
        self.offline_job()
        self.device.capture = Mock(side_effect=AssertionError('Offline recovery must not capture a device'))
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})
        original_write = farm.write_json
        def save(path, value):
            if Path(path) == self.progress_path:
                self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
            return original_write(path, value)
        with patch.object(farm, 'write_json', side_effect=save):
            self.offline_resolve()
        saved = self.saved()
        self.assertEqual(saved['counts'], self.original['counts'])
        self.assertEqual(saved['run']['id'], 'original-run')
        self.assertEqual(saved['run']['recording']['error'], 'Original capture interrupted')
        self.assertEqual(saved['run']['recording']['resolution_mode'], 'offline')
        self.assertEqual(saved['run']['recording']['state'], 'missing_screenshot')
        self.assertEqual(farm.STOP.read_text('utf-8'), 'Intentional stop preserved')
        self.device.connect.assert_not_called()
        self.device.verify.assert_not_called()
        self.device.capture.assert_not_called()
        self.offline_resolve()
        self.assertEqual(self.collector.missing.call_count, 2)

    def test_offline_resolution_requires_exact_identity_and_existing_stop(self):
        self.offline_job()
        for completion_id in (None, 'another-completion', '../original-completion'):
            with self.subTest(completion_id=completion_id):
                with self.assertRaisesRegex(ValueError, 'exact saved'):
                    self.offline_resolve(completion_id)
                self.assert_unchanged()
        farm.STOP.unlink()
        with self.assertRaisesRegex(RuntimeError, 'existing Stop'):
            self.offline_resolve()
        self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_offline_rejects_all_pending_input_and_retained_or_mismatched_evidence(self):
        path, value = self.offline_job()
        self.save_results_pending()
        with self.assertRaisesRegex(RuntimeError, 'saved input'):
            self.offline_resolve()
        self.assert_unchanged()
        self.original['pending'] = None
        farm.write_json(self.progress_path, self.original)
        for key, replacement in (('device_id', 'different-device'), ('run_id', 'different-run'),
                                 ('stage', 'ninja_road'), ('completed_at', 101),
                                 ('screenshot', 'saved-results.png')):
            with self.subTest(field=key):
                farm.write_json(path, dict(value, **{key: replacement}))
                with self.assertRaisesRegex(RuntimeError, 'identity|evidence'):
                    self.offline_resolve()
                self.assert_unchanged()
        farm.write_json(path, value)
        png = self.runtime / 'validation/original-completion.png'
        png.parent.mkdir()
        png.write_bytes(b'orphaned PNG must be retained')
        with self.assertRaisesRegex(RuntimeError, 'same-job PNG'):
            self.offline_resolve()
        self.assertTrue(png.exists())
        self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_offline_progress_write_failure_leaves_unverified_session_and_original_transaction(self):
        self.offline_job()
        original_write = farm.write_json
        def save(path, value):
            if Path(path) == self.progress_path:
                raise OSError('fixture progress write unavailable')
            return original_write(path, value)
        with patch.object(farm, 'write_json', side_effect=save):
            with self.assertRaisesRegex(OSError, 'progress write unavailable'):
                self.offline_resolve()
        self.assertEqual(self.saved(), self.original)
        self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        self.assertTrue(farm.STOP.exists())
        self.offline_resolve()
        self.assertEqual(self.saved()['run']['recording']['state'], 'missing_screenshot')

    def test_offline_rejects_receipt_evidence_and_existing_database_results(self):
        _, value = self.offline_job()
        receipt = self.runtime / 'loot/receipts/original-completion.json'
        farm.write_json(receipt, dict(value, recorded=True, screenshot='retained-results.png'))
        with self.assertRaisesRegex(RuntimeError, 'Retained loot evidence'):
            self.offline_resolve()
        receipt.unlink()
        database = self.runtime / 'loot/loot.sqlite3'
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            db.executescript('CREATE TABLE sessions(session_id TEXT, account TEXT);'
                'CREATE TABLE completions(completion_id TEXT, session_id TEXT, run_id TEXT,'
                'completed_at REAL, status TEXT, screenshot_path TEXT);')
            db.execute('INSERT INTO sessions VALUES(?,?)', ('original-session', 'acc1'))
            db.execute('INSERT INTO completions VALUES(?,?,?,?,?,?)',
                       ('original-completion', 'original-session', 'original-run', 100, 'recognized', None))
        with self.assertRaisesRegex(RuntimeError, 'finalized result'):
            self.offline_resolve()
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE completions SET status='awaiting_evidence', screenshot_path='retained.png'")
        with self.assertRaisesRegex(RuntimeError, 'loot evidence'):
            self.offline_resolve()
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE completions SET screenshot_path=NULL, run_id='different-run'")
        with self.assertRaisesRegex(RuntimeError, 'Database completion identity'):
            self.offline_resolve()
        self.assert_unchanged()
        self.device.connect.assert_not_called()

    def test_offline_requires_original_durable_recording_and_durable_missing_ack(self):
        with self.assertRaisesRegex(RuntimeError, 'original durable recording'):
            self.offline_resolve()
        self.assert_unchanged()
        self.offline_job()
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})
        self.collector.missing.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'not durably acknowledged'):
            self.offline_resolve()
        self.assertEqual(self.saved(), self.original)
        self.assertTrue(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        self.assertTrue(farm.STOP.exists())

    def test_offline_closed_completion_cannot_verify_an_unrelated_results_screen(self):
        self.offline_job()
        self.offline_resolve()
        farm.STOP.unlink()
        engine = farm.Engine('universal', self.target)
        engine.loot_collector = self.collector
        self.detector.classify.return_value = ('READY_TO_RETRY', {}, {})
        engine.tick()
        self.assertFalse(engine.mission_verified)
        self.assertEqual(self.device.actions, [])
        self.collector.evidence.assert_not_called()

    def test_cli_dispatches_explicit_offline_completion(self):
        with patch.object(farm.sys, 'argv', ['farm.py', 'resolve-missing-evidence', '--offline',
                '--completion-id', 'original-completion', '--reason', self.reason]), \
                patch.object(farm, 'resolve_missing_evidence') as resolve:
            farm.main()
        resolve.assert_called_once_with('universal', reason=self.reason, offline=True,
                                        completion_id='original-completion')


if __name__ == '__main__':
    unittest.main()
