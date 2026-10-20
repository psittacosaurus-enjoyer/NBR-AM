"""Shutdown evidence capture is bounded, read-only and transaction preserving."""
import contextlib
import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

import farm
from instance_fixtures import legacy_target


class StoppedRecordingCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nbr-stop-capture-')
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.target = legacy_target()
        self.frame = np.full((24, 32, 3), 100, dtype=np.uint8)
        self.engine = farm.Engine.__new__(farm.Engine)
        self.engine.target = self.target
        self.engine.stage = {'id': 'universal'}
        self.engine.progress = self.runtime / 'universal.json'
        self.engine._rejected_recovery_origin = None
        self.device = self.engine.device = Mock()
        self.device.target = self.target
        self.device.foreground.return_value = farm.PACKAGE
        self.device.capture.return_value = self.frame
        self.detector = self.engine.detector = Mock()
        self.detector.classify.return_value = ('READY_TO_RETRY', {}, {})
        self.collector = self.engine.loot_collector = Mock()
        self.collector.evidence.return_value = True
        self.now = 1000.0
        self.original = {
            'stage': 'universal', 'instance': farm.instance_dict(self.target),
            'screen': 'REWARD_REVEAL', 'counts': {'started': 1, 'completed': 1, 'failed': 0},
            'recording_health': {'status': 'Screenshot Capture Pending', 'capture_pending': 1},
            'pending': {'id': 'reveal_rewards', 'source': 'REWARD_REVEAL',
                        'expected': ['READY_TO_RETRY'], 'time': 100, 'run_id': 'input-id'},
            'run': {'id': 'original-run', 'start': 90, 'outcome': 'completed',
                    'recording': {'version': 1, 'account': 'acc1', 'device_id': self.target.android_id,
                                  'stage': 'universal', 'completion_id': 'original-completion',
                                  'completed_at': 100, 'state': 'screenshot_capture_pending',
                                  'capture_attempts': 0, 'next_attempt_at': 500, 'error': None}},
        }
        self.engine.data = copy.deepcopy(self.original)
        self.reveal = Mock(return_value=False)
        for item in [patch.object(farm, 'RUNTIME', self.runtime),
                     patch.object(farm, 'STOP', self.runtime / 'STOP'),
                     patch.object(farm, 'event'),
                     patch.object(farm.time, 'monotonic', side_effect=lambda: self.now),
                     patch.object(farm.time, 'sleep', side_effect=self.advance),
                     patch.object(farm, 'process_claim', return_value={
                         'worker_pid': 123, 'worker_pid_identity': 'offline-fixture'}),
                     patch('item_recognition.reward_layout.has_unrevealed_rewards', self.reveal)]:
            item.start()
            self.addCleanup(item.stop)
        farm.write_json(self.engine.progress, self.original)
        farm.STOP.write_text('Intentional stop', encoding='utf-8')

    def advance(self, seconds):
        self.now += seconds

    def assert_no_input(self):
        self.device.perform.assert_not_called()
        self.device.connect.assert_not_called()
        self.device.command.assert_not_called()
        self.collector.completed.assert_not_called()
        self.collector.missing.assert_not_called()
        self.assertEqual(farm.STOP.read_text('utf-8'), 'Intentional stop')

    def assert_original_preserved(self):
        self.assertEqual(farm.read_json(self.engine.progress), self.original)
        self.assertEqual(self.engine.data['run'], self.original['run'])
        self.assertEqual(self.engine.data['pending'], self.original['pending'])
        self.assertEqual(self.engine.data['counts'], self.original['counts'])
        if 'recording_health' in self.original:
            self.assertEqual(self.engine.data['recording_health'], self.original['recording_health'])
        else:
            self.assertNotIn('recording_health', self.engine.data)
        self.assert_no_input()

    def test_final_capture_saves_ack_without_consuming_stop_or_pending_input(self):
        self.assertTrue(self.engine.capture_stopped_recording())
        saved = farm.read_json(self.engine.progress)
        self.assertEqual(saved['pending'], self.original['pending'])
        self.assertEqual(saved['counts'], self.original['counts'])
        self.assertEqual(saved['screen'], self.original['screen'])
        self.assertNotIn('recording_health', saved)
        recording = saved['run']['recording']
        self.assertEqual(recording['state'], 'screenshot_saved')
        self.assertEqual(recording['completion_id'], 'original-completion')
        self.assertEqual(recording['completed_at'], 100)
        self.assertEqual(recording['capture_attempts'], 0)
        self.collector.evidence.assert_called_once_with('original-run', self.frame,
                                                       completion_id='original-completion')
        self.assertEqual(self.device.verify.call_args_list, [call(timeout=1)] * 2)
        self.assertEqual(self.device.capture.call_args_list, [call(timeout=1)] * 2)
        self.assertEqual(self.device.foreground.call_args_list, [call(timeout=1)] * 3)
        farm.time.sleep.assert_called_once_with(.25)
        self.assert_no_input()

    def test_capture_or_ack_failure_preserves_original_and_does_not_retry(self):
        for result in (False, None, OSError('disk unavailable')):
            with self.subTest(result=result):
                self.collector.evidence.reset_mock()
                self.collector.evidence.side_effect = result if isinstance(result, Exception) else None
                self.collector.evidence.return_value = result
                self.assertFalse(self.engine.capture_stopped_recording())
                self.assertEqual(self.collector.evidence.call_count, 1)
                self.assert_original_preserved()

    def test_farmer_ack_save_failure_keeps_original_transaction(self):
        with patch.object(farm, 'write_json', side_effect=OSError('runtime unavailable')):
            self.assertFalse(self.engine.capture_stopped_recording())
        self.assert_original_preserved()
        self.assertTrue(self.engine.capture_stopped_recording())
        self.assertEqual(farm.read_json(self.engine.progress)['run']['recording']['state'], 'screenshot_saved')

    def test_farmer_ack_save_failure_preserves_absent_health_snapshot(self):
        self.original.pop('recording_health')
        self.engine.data.pop('recording_health')
        farm.write_json(self.engine.progress, self.original)
        with patch.object(farm, 'write_json', side_effect=OSError('runtime unavailable')):
            self.assertFalse(self.engine.capture_stopped_recording())
        self.assert_original_preserved()

    def test_mismatched_observations_unrevealed_rewards_and_capture_errors_skip(self):
        cases = [('READY_TO_RETRY', 'READY_TO_START'), ('IN_BATTLE', 'READY_TO_RETRY'),
                 ('READY_TO_START', 'READY_TO_START')]
        for names in cases:
            with self.subTest(names=names):
                self.detector.classify.side_effect = [(name, {}, {}) for name in names]
                self.assertFalse(self.engine.capture_stopped_recording())
                self.collector.evidence.assert_not_called()
                self.assert_original_preserved()
        self.detector.classify.side_effect = None
        self.reveal.return_value = True
        self.assertFalse(self.engine.capture_stopped_recording())
        self.reveal.return_value = False
        self.device.capture.side_effect = TimeoutError('read timed out')
        self.assertFalse(self.engine.capture_stopped_recording())
        self.collector.evidence.assert_not_called()
        self.assert_original_preserved()

    def test_reveal_animation_settles_within_one_deadline_before_final_capture(self):
        self.detector.classify.side_effect = [(name, {}, {}) for name in
            ['REWARD_REVEAL', 'UNKNOWN', 'LUCKY_REWARD', 'READY_TO_RETRY', 'READY_TO_RETRY']]
        self.assertTrue(self.engine.capture_stopped_recording())
        self.assertEqual(self.now, 1001.0)
        self.assertEqual(self.device.capture.call_count, 5)
        self.collector.evidence.assert_called_once()
        self.assertEqual(self.engine.data['pending'], self.original['pending'])
        self.assert_no_input()

    def test_unfinished_animation_or_placeholders_exhaust_budget_without_ack(self):
        for name, placeholders in [('REWARD_REVEAL', False), ('UNKNOWN', False), ('READY_TO_RETRY', True)]:
            with self.subTest(screen=name):
                self.now = 1000.0
                self.detector.classify.return_value = (name, {}, {})
                self.reveal.return_value = placeholders
                self.assertFalse(self.engine.capture_stopped_recording())
                self.assertEqual(self.now, 1004.0)
                self.collector.evidence.assert_not_called()
                self.assert_original_preserved()

    def test_unknown_without_saved_results_transition_does_not_poll(self):
        self.engine.data['pending'] = None
        self.detector.classify.return_value = ('UNKNOWN', {}, {})
        self.assertFalse(self.engine.capture_stopped_recording())
        self.assertEqual(self.device.capture.call_count, 1)
        self.assertEqual(self.now, 1000.0)
        self.collector.evidence.assert_not_called()
        self.assertEqual(self.engine.data['run'], self.original['run'])
        self.assert_no_input()

    def test_read_timeout_shrinks_with_remaining_shutdown_budget(self):
        self.device.verify.side_effect = lambda **kwargs: self.advance(min(.35, kwargs['timeout']))
        def foreground(**kwargs):
            self.advance(min(.35, kwargs['timeout']))
            return farm.PACKAGE
        def capture(**kwargs):
            self.advance(min(.75, kwargs['timeout']))
            return self.frame
        self.device.foreground.side_effect = foreground
        self.device.capture.side_effect = capture
        self.detector.classify.return_value = ('REWARD_REVEAL', {}, {})
        self.assertFalse(self.engine.capture_stopped_recording())
        self.assertEqual(self.device.capture.call_count, 2)
        self.assertLess(self.device.verify.call_args_list[-1].kwargs['timeout'], 1)
        self.assertLessEqual(self.now, 1004.0)
        self.assert_original_preserved()

    def test_foreground_or_fresh_android_identity_mismatch_skips(self):
        for foreground in (['other.app'], [farm.PACKAGE, farm.PACKAGE, 'other.app']):
            with self.subTest(foreground=foreground):
                self.device.foreground.side_effect = foreground
                self.assertFalse(self.engine.capture_stopped_recording())
                self.collector.evidence.assert_not_called()
                self.assert_original_preserved()
        self.device.foreground.side_effect = None
        self.device.verify.side_effect = RuntimeError('Android device identity mismatch')
        self.assertFalse(self.engine.capture_stopped_recording())
        self.collector.evidence.assert_not_called()
        self.assert_original_preserved()

    def test_ineligible_or_unrelated_transactions_skip_all_device_reads(self):
        changes = [lambda e: e.stage.update(id='ninja_road'),
                   lambda e: e.data['run'].update(outcome=None),
                   lambda e: e.data['run'].pop('recording'),
                   lambda e: e.data['run']['recording'].pop('completion_id'),
                   lambda e: e.data['run']['recording'].update(account='another-account'),
                   lambda e: e.data['run']['recording'].update(device_id='another-device'),
                   lambda e: e.data['run']['recording'].update(stage='another-stage'),
                   lambda e: e.data['run']['recording'].update(state='screenshot_saved'),
                   lambda e: e.data['run']['recording'].update(state='missing_screenshot'),
                   lambda e: setattr(e, 'target', replace(self.target, config_path='')),
                   lambda e: setattr(e.device, 'target', replace(self.target, endpoint='127.0.0.1:9999'))]
        for change in changes:
            with self.subTest(change=change):
                self.engine.data = copy.deepcopy(self.original)
                self.engine.stage = {'id': 'universal'}
                self.engine.target = self.target
                self.device.target = self.target
                change(self.engine)
                expected = copy.deepcopy(self.engine.data)
                self.assertFalse(self.engine.capture_stopped_recording())
                self.assertEqual(self.engine.data, expected)
                self.device.verify.assert_not_called()
                self.device.capture.assert_not_called()
                self.collector.evidence.assert_not_called()
                self.assert_no_input()

    def test_missing_results_block_points_to_explicit_resolution(self):
        self.assertTrue(self.engine._loot_input_blocked('READY_TO_START', 'start_mission'))
        self.assertIn('resolve-missing-evidence', self.engine.data['blocked'])
        self.assertIn('rewards screen is no longer visible', self.engine.data['blocked'])
        self.assertEqual(self.engine.data['run'], self.original['run'])
        self.assert_no_input()

    def test_shutdown_never_initializes_an_unavailable_collector(self):
        recovering = farm.RecoveringLootCollector.__new__(farm.RecoveringLootCollector)
        recovering.collector = None
        recovering._get = Mock(side_effect=AssertionError('No collector startup during stop'))
        self.engine.loot_collector = recovering
        self.assertFalse(self.engine.capture_stopped_recording())
        recovering._get.assert_not_called()
        self.device.verify.assert_not_called()
        self.assert_original_preserved()

    def run_worker(self, stage='universal', stopped_by_error=False):
        farm.STOP.unlink()
        order = []
        engine = Mock(data={})
        collector = Mock()
        def tick():
            if stopped_by_error:
                raise KeyboardInterrupt('original worker interruption')
            farm.STOP.write_text('Intentional stop', encoding='utf-8')
            return 0
        engine.tick.side_effect = tick
        engine.capture_stopped_recording.side_effect = lambda: order.append('capture')
        collector.close.side_effect = lambda **kwargs: order.append('close')
        with patch.object(farm, 'lock', side_effect=lambda *a, **k: contextlib.nullcontext()), \
             patch.object(farm, 'lock_busy', return_value=False), \
             patch.object(farm, 'live_controller_pids', return_value=[]), \
             patch.object(farm, 'process_target', return_value=self.target), \
             patch.object(farm, 'state_path', side_effect=lambda name, instance=None: self.runtime / name), \
             patch.object(farm, '_loot_start', return_value=collector), \
             patch.object(farm, 'create_engine', return_value=engine):
            if stopped_by_error:
                with self.assertRaisesRegex(KeyboardInterrupt, 'original worker interruption'):
                    farm.worker(stage, instance=self.target)
            else:
                farm.worker(stage, instance=self.target)
        return engine, collector, order

    def test_worker_captures_before_closing_collector_on_stop_and_interruption(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                farm.STOP.write_text('Intentional stop', encoding='utf-8')
                engine, collector, order = self.run_worker(stopped_by_error=interrupted)
                self.assertEqual(order, ['capture', 'close'])
                engine.capture_stopped_recording.assert_called_once_with()
                collector.close.assert_called_once_with(end_session=True)

    def test_worker_skips_capture_for_ninja_road(self):
        engine, _, order = self.run_worker(stage='ninja_road')
        self.assertEqual(order, ['close'])
        engine.capture_stopped_recording.assert_not_called()


if __name__ == '__main__':
    unittest.main()
