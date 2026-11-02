"""Recording recovery at the Universal input boundary; all devices are fake."""
import copy
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import controller as farm
from app.backend import loot
from test_stages import FakeDevice


class RecordingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-recording-recovery-')
        self.runtime = Path(self.temp.name)
        self.now = 1000.0
        self.patches = [
            patch.object(farm, 'RUNTIME', self.runtime),
            patch.object(farm, 'STOP', self.runtime / 'STOP'),
            patch.object(farm, 'event'),
            patch.object(farm.time, 'sleep'),
            patch.object(farm.time, 'time', side_effect=lambda: self.now),
            patch.object(farm, 'process_claim', return_value={
                'worker_pid': 123, 'worker_pid_identity': 'offline-fixture'}),
            patch.object(farm, 'ocr_text', return_value=''),
            patch('item_recognition.reward_layout.has_unrevealed_rewards', return_value=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def frame(self, name):
        files = {
            'start': 'tests/fixtures/rich_b/ready_to_start.png',
            'battle': 'tests/fixtures/battle.png',
            'complete': 'tests/fixtures/mission_complete.png',
            'results': 'tests/fixtures/rich_b/ready_to_retry.png',
        }
        image = farm.cv2.imread(str(farm.ROOT / files[name]))
        self.assertIsNotNone(image)
        return image

    def engine(self, screen='start', collector=None):
        device = FakeDevice(self.frame(screen))
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine('universal')
        if collector is not None:
            engine.loot_collector = collector
        return engine

    def start_run(self, collector):
        engine = self.engine(collector=collector)
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission'])
        engine.device.image = self.frame('battle')
        engine.tick()
        self.assertEqual(engine.data['counts']['started'], 1)
        return engine

    def complete(self, engine):
        engine.device.image = self.frame('complete')
        engine.tick()
        self.assertEqual(engine.data['counts']['completed'], 1)

    def next_attempt(self):
        self.now += 60

    def persisted_run(self):
        return farm.read_json(self.runtime / 'universal.json')['run']

    def test_completion_registration_retries_before_any_completion_input(self):
        collector = Mock()
        collector.completed.side_effect = [OSError('temporary registration failure'), 'durable-completion-id']
        engine = self.start_run(collector)
        self.complete(engine)
        run_id = engine.data['run']['id']
        completed_at = engine.data['run']['recording']['completed_at']
        self.assertEqual(engine.device.actions, ['start_mission'])
        self.assertEqual(self.persisted_run()['id'], run_id)
        self.assertEqual(self.persisted_run()['recording']['completed_at'], completed_at)
        self.assertTrue(engine.data['blocked'])
        engine.tick()
        self.assertEqual(collector.completed.call_count, 1)
        self.assertEqual(engine.device.actions, ['start_mission'])

        self.next_attempt()
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission', 'advance_completion'])
        self.assertEqual(engine.data['counts']['completed'], 1)
        self.assertEqual(collector.completed.call_count, 2)
        self.assertEqual([call.args[0] for call in collector.completed.call_args_list], [run_id] * 2)
        self.assertEqual([call.kwargs['completed_at'] for call in collector.completed.call_args_list],
                         [completed_at] * 2)

    def test_screenshot_failure_retries_without_sending_retry(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.side_effect = [False, True]
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('results')
        engine.tick()
        self.assertNotIn('retry', engine.device.actions)
        self.assertTrue(engine.data['blocked'])
        engine.tick()
        self.assertEqual(collector.evidence.call_count, 1)

        self.next_attempt()
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission', 'advance_completion', 'retry'])
        self.assertEqual(collector.evidence.call_count, 2)
        for call in collector.evidence.call_args_list:
            self.assertEqual(call.args[0], engine.data['run']['id'])
            np.testing.assert_array_equal(call.args[1], engine.device.image)
        collector.completed.assert_called_once()

    def test_repeated_capture_failure_escalates_and_keeps_retrying_automatically(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = False
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('results')
        for _ in range(farm.LOOT_MAX_ATTEMPTS):
            self.next_attempt()
            engine.tick()
        self.assertNotIn('retry', engine.device.actions)
        recording = self.persisted_run()['recording']
        self.assertEqual(recording['state'], 'retryable_failure')
        self.assertEqual(recording['recovery_tier'], 'broader')
        self.assertGreaterEqual(recording['capture_attempts'], farm.LOOT_MAX_ATTEMPTS)
        self.assertTrue(recording['error'])
        self.assertRegex(engine.data['blocked'].lower(), 'record|screenshot|capture')
        self.assertFalse(farm.STOP.exists())

        collector.evidence.return_value = True
        self.next_attempt()
        engine.tick()
        self.assertEqual(engine.device.actions.count('retry'), 1)
        self.assertEqual(engine.data['counts']['completed'], 1)

    def test_departed_results_are_closed_durably_and_farming_resumes(self):
        for departure in ('start', 'battle'):
            with self.subTest(departure=departure):
                collector = Mock()
                collector.completed.return_value = 'durable-completion-id'
                collector.evidence.return_value = False
                collector.missing.return_value = True
                engine = self.start_run(collector)
                self.complete(engine)
                engine.device.image = self.frame('results')
                engine.tick()
                original_id = engine.data['run']['id']
                engine.device.image = self.frame(departure)
                engine.tick()
                collector.missing.assert_called_once()
                self.assertEqual(collector.missing.call_args.args[0], original_id)
                self.assertEqual(collector.missing.call_args.kwargs['completion_id'],
                                 'durable-completion-id')
                recovered = engine.data['last_recording_recovery']
                self.assertEqual(recovered['run_id'], original_id)
                self.assertTrue(recovered['original_run']['recording']['capture_revoked'])
                self.assertEqual(collector.evidence.call_count, 1)
                self.assertFalse(farm.STOP.exists())
                if departure == 'start':
                    self.assertEqual(engine.data['run']['id'], original_id)
                    self.assertEqual(engine.data['run']['recording']['state'],
                                     'missing_screenshot')
                    self.assertEqual(engine.device.actions[-1], 'start_mission')
                else:
                    self.assertNotEqual(engine.data['run']['id'], original_id)
                    self.assertIsNone(engine.data['run']['outcome'])
                    self.assertEqual(engine.data['run']['origin'],
                                     'observed_after_recording_recovery')
                (self.runtime / 'universal.json').unlink()
                (self.runtime / 'session.json').unlink()

    def test_normal_results_and_uncertain_observations_keep_capture_eligible(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = True
        engine = self.start_run(collector)
        self.complete(engine)
        for name in ('RANK_UP_STAMINA_ACK', 'RANK_UP_RESULTS', 'TRIAL_COINS_NEXT',
                     'RESULTS_NEXT', 'REWARD_REVEAL', 'MISSION_COMPLETE', 'UNKNOWN'):
            with patch.object(engine, 'observe', return_value=(self.frame('complete'), name, {}, {})):
                engine.tick()
            self.assertFalse(engine.data['run']['recording'].get('capture_revoked'))
        with patch.object(engine, 'observe', side_effect=[
                (self.frame('start'), 'READY_TO_START', {}, {}),
                (self.frame('battle'), 'IN_BATTLE', {}, {})]):
            engine.tick()
        self.assertFalse(engine.data['run']['recording'].get('capture_revoked'))
        engine.device.image = self.frame('results')
        engine.tick()
        self.assertEqual(self.persisted_run()['recording']['state'], 'screenshot_saved')

    def test_departed_results_restore_retained_evidence_before_missing_closure(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = False
        collector.recover_evidence.return_value = True
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('start')
        engine.tick()
        recording = engine.data['run']['recording']
        self.assertEqual(recording['state'], 'screenshot_saved')
        self.assertEqual(recording['resolution_mode'], 'automatic_retained_evidence')
        collector.missing.assert_not_called()
        collector.recover_evidence.assert_called_once()
        self.assertEqual(engine.device.actions[-1], 'start_mission')
        self.assertEqual(engine.data['counts']['completed'], 1)

    def test_missing_resolution_crash_state_retries_durable_ack_automatically(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.recover_evidence.return_value = False
        collector.missing.return_value = True
        engine = self.start_run(collector)
        self.complete(engine)
        recording = engine.data['run']['recording']
        recording.update(state='missing_screenshot', missing_evidence_acknowledged=False)
        engine.device.image = self.frame('start')
        engine.tick()
        self.assertTrue(recording['missing_evidence_acknowledged'])
        collector.missing.assert_called_once()
        self.assertEqual(engine.device.actions[-1], 'start_mission')

    def test_capture_failure_reports_this_jobs_operation_error_over_health_warning(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = False
        collector.health.return_value = {'error': 'unrelated health-file sharing violation',
            'operation_errors': {'durable-completion-id': {'operation': 'capture',
                'message': 'Loot evidence storage limit reached; no eligible diagnostics remain'}}}
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('results')
        engine.tick()
        self.assertEqual(self.persisted_run()['recording']['error'],
                         'Loot evidence storage limit reached; no eligible diagnostics remain')

    def test_revoked_recording_still_registers_original_completion(self):
        collector = Mock()
        collector.completed.side_effect = [None, 'durable-completion-id']
        engine = self.start_run(collector)
        self.complete(engine)
        self.next_attempt()
        engine.device.image = self.frame('battle')
        engine.tick()
        recording = self.persisted_run()['recording']
        self.assertTrue(recording['capture_revoked'])
        self.assertEqual(recording['completion_id'], 'durable-completion-id')
        collector.evidence.assert_not_called()
        self.assertEqual(engine.device.actions, ['start_mission'])

    def test_restart_after_completion_keeps_job_identity_and_original_time(self):
        unavailable = Mock()
        unavailable.completed.return_value = None
        engine = self.start_run(unavailable)
        self.complete(engine)
        original = self.persisted_run()
        recovered = Mock()
        recovered.completed.return_value = 'durable-recovered-id'
        recovered.evidence.return_value = True

        self.next_attempt()
        restarted = self.engine('results', recovered)
        restarted.tick()
        self.assertEqual(restarted.data['run']['id'], original['id'])
        self.assertEqual(restarted.data['counts']['completed'], 1)
        recovered.completed.assert_called_once()
        self.assertEqual(recovered.completed.call_args.args[0], original['id'])
        self.assertEqual(recovered.completed.call_args.kwargs['completed_at'],
                         original['recording']['completed_at'])
        recovered.evidence.assert_called_once()
        self.assertEqual(recovered.evidence.call_args.args[0], original['id'])
        self.assertEqual(restarted.device.actions, ['retry'])

    def test_restart_after_registration_recaptures_only_saved_run(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        original = self.persisted_run()
        resumed_collector = Mock()
        resumed_collector.completed.return_value = 'durable-completion-id'
        resumed_collector.evidence.return_value = True
        self.next_attempt()
        resumed = self.engine('results', resumed_collector)
        resumed.tick()
        self.assertEqual(resumed.data['run']['id'], original['id'])
        self.assertEqual(resumed.data['counts']['completed'], 1)
        resumed_collector.evidence.assert_called_once()
        self.assertEqual(resumed_collector.evidence.call_args.args[0], original['id'])
        self.assertEqual(resumed_collector.evidence.call_args.kwargs['completion_id'],
                         original['recording']['completion_id'])
        self.assertEqual(resumed.device.actions, ['retry'])

    def test_restart_with_reset_session_verification_resumes_saved_results_only(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        original_id = engine.data['run']['id']
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': False})
        recovered = Mock()
        recovered.evidence.return_value = True
        resumed = self.engine('results', recovered)
        self.assertFalse(resumed.mission_verified)
        resumed.tick()
        self.assertEqual(resumed.data['run']['id'], original_id)
        self.assertEqual(resumed.data['counts']['completed'], 1)
        self.assertEqual(resumed.device.actions, ['retry'])
        recovered.evidence.assert_called_once()
        self.assertTrue(resumed.mission_verified)
        self.assertTrue(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        resumed.device.image = self.frame('battle')
        resumed.tick()
        self.assertEqual(resumed.data['screen'], 'IN_BATTLE')
        self.assertNotEqual(resumed.data['run']['id'], original_id)
        self.assertIsNone(resumed.data['run']['outcome'])
        self.assertEqual(resumed.data['counts']['completed'], 1)
        self.assertEqual(resumed.device.actions, ['retry'])

    def test_saved_screenshot_does_not_bypass_mismatched_device_guard(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = True
        engine = self.start_run(collector)
        self.complete(engine)
        engine.update(self.frame('results'), 'READY_TO_RETRY', {})
        saved = farm.read_json(self.runtime / 'universal.json')
        self.assertEqual(saved['run']['recording']['state'], 'screenshot_saved')
        saved['run']['recording']['device_id'] = 'unrelated-android-id'
        farm.write_json(self.runtime / 'universal.json', saved)
        recovered = Mock()
        resumed = self.engine('results', recovered)
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        recovered.completed.assert_not_called()
        recovered.evidence.assert_not_called()
        self.assertIn('does not match', resumed.data['blocked'])

    def test_matching_legacy_pending_job_restores_original_recording_identity(self):
        collector = Mock()
        collector.completed.return_value = 'original-v123-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        saved = farm.read_json(self.runtime / 'universal.json')
        original = saved['run'].pop('recording')
        saved['pending'] = None
        farm.write_json(self.runtime / 'universal.json', saved)
        legacy_job = dict(completion_id='original-v123-completion-id', session_id='legacy-session',
                          account='acc1', device_id='', stage='universal', run_id=saved['run']['id'],
                          completed_at=original['completed_at'], state='awaiting_evidence', screenshot=None)
        recovered = Mock()
        recovered.resume_pending.return_value = [legacy_job]
        recovered.evidence.return_value = True
        resumed = self.engine('results', recovered)
        resumed.tick()
        recording = self.persisted_run()['recording']
        self.assertEqual(recording['completion_id'], legacy_job['completion_id'])
        self.assertEqual(recording['completed_at'], legacy_job['completed_at'])
        self.assertEqual(recording['state'], 'screenshot_saved')
        self.assertEqual(resumed.data['counts']['completed'], 1)
        recovered.completed.assert_not_called()
        self.assertEqual(recovered.evidence.call_args.args[0], legacy_job['run_id'])
        self.assertEqual(recovered.evidence.call_args.kwargs['completion_id'], legacy_job['completion_id'])
        self.assertEqual(resumed.device.actions, ['retry'])

    def test_unrelated_legacy_pending_job_does_not_create_a_completion(self):
        collector = Mock()
        collector.completed.return_value = 'original-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        saved = farm.read_json(self.runtime / 'universal.json')
        saved['run'].pop('recording')
        saved['pending'] = None
        farm.write_json(self.runtime / 'universal.json', saved)
        recovered = Mock()
        recovered.resume_pending.return_value = [dict(
            completion_id='unrelated-completion-id', session_id='legacy-session', account='acc1',
            device_id='', stage='universal', run_id='unrelated-run', completed_at=900,
            state='awaiting_evidence', screenshot=None)]
        recovered.evidence.return_value = False
        resumed = self.engine('results', recovered)
        resumed.tick()
        self.assertNotIn('recording', resumed.data['run'])
        self.assertEqual(resumed.data['counts']['completed'], 1)
        recovered.completed.assert_not_called()
        self.assertTrue(all(call.kwargs.get('completion_id') != 'unrelated-completion-id'
                            for call in recovered.evidence.call_args_list))

    def test_legacy_results_wait_for_collector_initialization_before_job_recovery(self):
        collector = Mock()
        collector.completed.return_value = 'legacy-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        saved = farm.read_json(self.runtime / 'universal.json')
        original = saved['run'].pop('recording')
        saved['pending'] = None
        farm.write_json(self.runtime / 'universal.json', saved)
        recovered = Mock()
        recovered.health.return_value = {'status': 'collector_initialization_retry',
                                         'error': 'temporary initialization failure'}
        recovered.resume_pending.return_value = []
        recovered.evidence.return_value = False
        resumed = self.engine('results', recovered)
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertTrue(resumed.data['blocked'])
        self.assertNotIn('recording', resumed.data['run'])

        recovered.health.return_value = {'status': 'Tracking Active'}
        recovered.resume_pending.return_value = [dict(
            completion_id='legacy-completion-id', session_id='legacy-session', account='acc1',
            device_id='', stage='universal', run_id=saved['run']['id'],
            completed_at=original['completed_at'], state='awaiting_evidence', screenshot=None)]
        recovered.evidence.return_value = True
        resumed.tick()
        self.assertEqual(resumed.device.actions, ['retry'])
        self.assertEqual(resumed.data['counts']['completed'], 1)
        self.assertEqual(self.persisted_run()['recording']['completion_id'], 'legacy-completion-id')
        recovered.completed.assert_not_called()

    def test_progress_save_failure_keeps_original_completion_until_recovery(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        engine = self.start_run(collector)
        real_save = engine.save
        saves = []

        def fail_once():
            saves.append(None)
            if len(saves) == 1:
                raise OSError('temporary progress-file sharing failure')
            return real_save()

        with patch.object(engine, 'save', side_effect=fail_once):
            engine.device.image = self.frame('complete')
            with self.assertRaisesRegex(farm.RecordingPersistenceError, 'disk space and permissions'):
                engine.tick()
            collector.completed.assert_not_called()
            self.assertEqual(engine.device.actions, ['start_mission'])
            run_id = engine.data['run']['id']
            completed_at = engine.data['run']['recording']['completed_at']
            self.assertEqual(engine.data['counts']['completed'], 1)
            self.next_attempt()
            engine.tick()

        self.assertEqual(engine.device.actions, ['start_mission', 'advance_completion'])
        self.assertEqual(self.persisted_run()['id'], run_id)
        self.assertEqual(self.persisted_run()['recording']['completed_at'], completed_at)
        self.assertEqual(engine.data['counts']['completed'], 1)
        collector.completed.assert_called_once()

    def test_screenshot_ack_progress_failure_prevents_retry_until_saved(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = True
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('results')
        real_save = engine.save
        failed = []

        def fail_ack_once():
            if engine.data['run']['recording']['state'] == 'screenshot_saved' and not failed:
                failed.append(None)
                raise OSError('temporary screenshot acknowledgement sharing failure')
            return real_save()

        with patch.object(engine, 'save', side_effect=fail_ack_once):
            with self.assertRaises(farm.RecordingPersistenceError):
                engine.tick()
            self.assertNotIn('retry', engine.device.actions)
            self.assertEqual(self.persisted_run()['recording']['state'], 'screenshot_capture_pending')
            engine.tick()
        self.assertEqual(engine.device.actions.count('retry'), 1)
        self.assertEqual(self.persisted_run()['recording']['state'], 'screenshot_saved')
        collector.evidence.assert_called_once()

    def test_mismatched_saved_account_device_or_stage_blocks_recovery(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        engine = self.start_run(collector)
        self.complete(engine)
        original = farm.read_json(self.runtime / 'universal.json')
        for key, value in [('account', 'unrelated-account'),
                           ('device_id', 'unrelated-android-id'), ('stage', 'unrelated-stage')]:
            with self.subTest(identity=key):
                saved = copy.deepcopy(original)
                saved['run']['recording'][key] = value
                farm.write_json(self.runtime / 'universal.json', saved)
                recovered = Mock()
                resumed = self.engine('results', recovered)
                resumed.tick()
                self.assertEqual(resumed.device.actions, [])
                recovered.completed.assert_not_called()
                recovered.evidence.assert_not_called()
                self.assertIn('does not match', resumed.data['blocked'])
                self.assertEqual(resumed.data['counts']['completed'], 1)

    def test_initial_unrelated_results_do_not_register_or_capture_a_completion(self):
        collector = Mock()
        engine = self.engine('results', collector)
        engine.tick()
        collector.completed.assert_not_called()
        collector.evidence.assert_not_called()
        self.assertEqual(engine.data['counts']['completed'], 0)
        self.assertEqual(engine.device.actions, [])

    def test_saved_evidence_allows_retry_without_waiting_for_recognition(self):
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = True
        collector.wait_idle.side_effect = AssertionError('Farming must not wait for recognition')
        engine = self.start_run(collector)
        self.complete(engine)
        engine.device.image = self.frame('results')
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission', 'advance_completion', 'retry'])
        collector.wait_idle.assert_not_called()

    def test_stop_blocks_recording_retries_and_device_input(self):
        collector = Mock()
        collector.completed.return_value = None
        engine = self.start_run(collector)
        self.complete(engine)
        original_run = self.persisted_run()
        farm.STOP.write_text('Intentional offline fixture stop', encoding='utf-8')
        self.next_attempt()
        collector.completed.reset_mock()
        self.assertEqual(engine.tick(), 0)
        self.assertEqual(engine.device.actions, ['start_mission'])
        collector.completed.assert_not_called()
        collector.evidence.assert_not_called()
        self.assertEqual(self.persisted_run(), original_run)

    def test_collector_initialization_failure_retries_in_same_session(self):
        recovered = Mock()
        recovered.completed.return_value = 'durable-completion-id'
        with patch.object(loot, 'process_identity', return_value=('offline:collector', 100)), \
             patch.object(loot, 'Collector', side_effect=[OSError('temporary SQLite startup failure'), recovered]) as factory:
            collector = farm._loot_start('universal')
            self.assertIsNotNone(collector)
            collector.completed('offline-run', completed_at=self.now)
            self.next_attempt()
            self.assertEqual(collector.completed('offline-run', completed_at=1000), 'durable-completion-id')
            self.assertEqual(factory.call_count, 2)
            recovered.completed.assert_called_with('offline-run', completed_at=1000)
            collector.close()

    def test_cli_step_attaches_and_closes_the_recording_collector(self):
        engine = Mock(data={'offline': True})
        collector = Mock()
        target = 'offline-target'
        with patch.object(farm, 'Engine', return_value=engine), \
             patch.object(farm, '_loot_start', return_value=collector) as startup, \
             patch.object(farm, 'process_target', return_value=target), \
             patch.object(farm, 'state_path', side_effect=lambda name, instance=None: self.runtime / name), \
             patch.object(farm, 'controller_active', return_value=False), \
             patch.object(farm, 'lock_busy', return_value=False), \
             patch.object(farm, 'lock', side_effect=lambda *args, **kwargs: contextlib.nullcontext()), \
             patch.object(farm.sys, 'argv', ['farm.py', 'step']), patch('builtins.print'):
            farm.main()
        startup.assert_called_once_with('universal', instance=target)
        self.assertIs(engine.loot_collector, collector)
        engine.tick.assert_called_once_with()
        collector.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
