"""Manager 1.1 controls exercised offline, with no real processes or ADB calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import farm
from test_stages import FakeDevice


class StaminaControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-manager-controls-')
        self.runtime = Path(self.temp.name)
        self.now = 100.0
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'),
                        patch.object(farm, 'alive', side_effect=lambda pid: pid == os.getpid()),
                        patch.object(farm.time, 'time', side_effect=lambda: self.now),
                        patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value='')]
        for item in self.patches:
            item.start()
        self.frame = farm.np.zeros((1600, 900, 3), dtype=farm.np.uint8)
        self.screen = 'STAMINA_PROMPT'
        self.specs = {
            'STAMINA_PROMPT': {'action': {'id': 'dismiss_stamina', 'kind': 'tap',
                                        'point': [299, 1018], 'expected': ['READY_TO_RETRY']}},
            'READY_TO_RETRY': {'action': {'id': 'retry', 'kind': 'tap',
                                        'point': [450, 1524],
                                        'expected': ['READY_TO_START', 'STAMINA_PROMPT']}},
        }
        farm.write_json(self.runtime / 'selection.json', {'stage': 'rich-b'})
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def engine(self):
        with patch.object(farm, 'Device', return_value=FakeDevice(self.frame)):
            engine = farm.Engine('rich-b')
        engine.observe = lambda: (self.frame, self.screen, copy.deepcopy(self.specs[self.screen]), {})
        return engine

    def waiting(self):
        engine = self.engine()
        engine.tick()
        self.now += 1
        self.screen = 'READY_TO_RETRY'
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(farm.stamina_status(engine.data), 'WAITING')
        return engine

    def test_all_durations_persist_and_are_used_by_new_engines(self):
        for minutes in (5, 10, 15, 20, 25):
            with self.subTest(minutes=minutes):
                (self.runtime / 'rich_b.json').unlink(missing_ok=True)
                self.screen = 'STAMINA_PROMPT'
                farm.set_stamina_duration(minutes)
                self.assertEqual(farm.stamina_wait_minutes(), minutes)
                self.assertEqual(farm.read_json(self.runtime / 'manager_settings.json')['stamina_wait_minutes'], minutes)
                engine = self.engine()
                engine.tick()
                self.assertEqual(engine.data['pending']['stamina_wait_seconds'], minutes * 60)
                self.assertEqual(engine.data['stamina_until'], self.now + minutes * 60)
                self.now += 1
                self.screen = 'READY_TO_RETRY'
                engine.tick()
                self.assertEqual(engine.data['stamina_until'], self.now + minutes * 60)

    def test_default_and_invalid_setting_fallback(self):
        self.assertEqual(farm.stamina_wait_minutes(), 10)
        farm.write_json(self.runtime / 'manager_settings.json', {'stamina_wait_minutes': 7})
        self.assertEqual(farm.stamina_wait_minutes(), 5)
        for invalid in (0, 7, '5', True, 5.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                farm.set_stamina_duration(invalid)

    def test_setting_change_preserves_current_and_pending_wait_deadlines(self):
        engine = self.engine()
        engine.tick()
        farm.set_stamina_duration(25)
        self.now += 1
        self.screen = 'READY_TO_RETRY'
        engine.tick()
        self.assertEqual(engine.data['stamina_until'], self.now + 600)
        deadline = engine.data['stamina_until']
        farm.set_stamina_duration(5)
        resumed = self.engine()
        resumed.tick()
        self.assertEqual(resumed.data['stamina_until'], deadline)
        self.assertEqual(resumed.device.actions, [])

    def test_end_wait_is_consumed_by_tick_and_insufficient_stamina_waits_again(self):
        engine = self.waiting()
        before = engine.data['stamina_until']
        farm.end_stamina_wait()
        self.assertEqual(engine.data['stamina_until'], before)
        self.assertTrue((self.runtime / 'stamina_request.json').exists())
        engine.tick()
        self.assertEqual(engine.data['stamina_until'], 0)
        self.assertEqual(engine.device.actions, ['dismiss_stamina', 'retry'])
        self.assertFalse((self.runtime / 'stamina_request.json').exists())
        self.screen = 'STAMINA_PROMPT'
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina', 'retry', 'dismiss_stamina'])
        self.now += 1
        self.screen = 'READY_TO_RETRY'
        engine.tick()
        deadline = engine.data['stamina_until']
        self.assertEqual(deadline, self.now + 600)
        for _ in range(3):
            engine.tick()
        self.assertEqual(engine.device.actions.count('retry'), 1)
        self.assertEqual(engine.data['stamina_until'], deadline)

    def test_skip_requires_two_fresh_agreeing_observations(self):
        engine = self.waiting()
        farm.end_stamina_wait()
        spec = copy.deepcopy(self.specs['READY_TO_RETRY'])
        with patch.object(engine, 'observe', side_effect=[
                (self.frame, 'READY_TO_RETRY', spec, {}),
                (self.frame, 'UNKNOWN', None, {})]):
            engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.assertEqual(engine.data['screen'], 'UNKNOWN')

    def test_stale_request_and_unresolved_pending_are_rejected_without_input(self):
        engine = self.waiting()
        farm.end_stamina_wait()
        request = farm.read_json(self.runtime / 'stamina_request.json')
        request['worker_pid'] += 1
        farm.write_json(self.runtime / 'stamina_request.json', request)
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        farm.end_stamina_wait()
        pending = {'id': 'retry', 'source': 'READY_TO_RETRY', 'expected': ['READY_TO_START'],
                   'time': self.now, 'run_id': 'uncertain'}
        engine.data['pending'] = copy.deepcopy(pending)
        deadline = engine.data['stamina_until']
        engine.tick()
        self.assertEqual(engine.data['pending'], pending)
        self.assertEqual(engine.data['stamina_until'], deadline)
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.assertFalse((self.runtime / 'stamina_request.json').exists())

    def test_old_worker_unknown_and_stopped_states_cannot_skip(self):
        engine = self.waiting()
        progress = copy.deepcopy(engine.data)
        for changes in ({'manager_controls_version': None}, {'heartbeat': -1000},
                        {'screen': 'UNKNOWN'}, {'stamina_until': 0},
                        {'pending': {'id': 'retry'}}):
            with self.subTest(changes=changes):
                farm.write_json(engine.progress, dict(progress, **changes))
                with self.assertRaises(RuntimeError):
                    farm.end_stamina_wait()
                self.assertFalse((self.runtime / 'stamina_request.json').exists())
        farm.write_json(engine.progress, progress)
        farm.STOP.write_text('Intentional user stop', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            farm.end_stamina_wait()


class FullRestartControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-manager-restart-')
        self.runtime = Path(self.temp.name)
        self.device = Mock(spec=FakeDevice)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'),
                        patch.object(farm, 'Device', return_value=self.device),
                        patch.object(farm, 'alive', return_value=False),
                        patch.object(farm.subprocess, 'run')]
        for item in self.patches:
            item.start()
        farm.write_json(self.runtime / 'selection.json', {'stage': 'rich-b'})
        farm.write_json(self.runtime / 'supervisor.json', {'pid': 111, 'worker_pid': 222, 'stage': 'rich-b'})
        self.progress = {'pending': {'id': 'retry', 'source': 'READY_TO_RETRY',
                                    'expected': ['READY_TO_START'], 'time': 1},
                         'stamina_until': 12345, 'counts': {'started': 9},
                         'run': {'id': 'preserved'}, 'worker_pid': 222,
                         'heartbeat': 1, 'screen': 'UNKNOWN', 'blocked': 'error'}
        farm.write_json(self.runtime / 'rich_b.json', self.progress)
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})
        farm.write_json(self.runtime / 'stamina_request.json', {'id': 'obsolete'})
        (self.runtime / 'events.jsonl').write_text('preserve log', encoding='utf-8')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_shutdown_failure_never_launches_or_clears_progress(self):
        before = (self.runtime / 'rich_b.json').read_bytes()
        session = (self.runtime / 'session.json').read_bytes()
        with patch.object(farm, 'live_controller_pids', return_value=[111, 222]), \
             patch.object(farm, 'start') as launch:
            with self.assertRaisesRegex(RuntimeError, 'old controller still active'):
                farm.full_restart('scrolls-a', shutdown_timeout=0)
        launch.assert_not_called()
        self.assertEqual((self.runtime / 'rich_b.json').read_bytes(), before)
        self.assertEqual((self.runtime / 'session.json').read_bytes(), session)
        self.device.connect.assert_not_called()
        self.assertTrue((self.runtime / 'stamina_request.json').exists())
        self.assertEqual(farm.event.call_args.args[0], 'full_restart_failed')

    def test_success_preserves_active_stage_pending_wait_and_history(self):
        with patch.object(farm, 'live_controller_pids',
                          side_effect=lambda: [] if farm.STOP.exists() else [111, 222]), \
             patch.object(farm, 'start') as launch:
            farm.full_restart('scrolls-a')
        launch.assert_called_once()
        self.device.connect.assert_called_once_with()
        self.assertEqual(launch.call_args.args, ('rich-b',))
        self.assertTrue(launch.call_args.kwargs['restarting'])
        self.assertEqual(launch.call_args.kwargs['expected_stop'], farm.STOP.read_text('utf-8'))
        reset = farm.read_json(self.runtime / 'rich_b.json')
        self.assertEqual(reset, {key: value for key, value in self.progress.items()
                                 if key not in ('worker_pid', 'heartbeat', 'screen', 'blocked')})
        self.assertEqual(farm.read_json(self.runtime / 'session.json'), {'stage': 'rich-b', 'mission_verified': False})
        self.assertFalse((self.runtime / 'stamina_request.json').exists())
        self.assertEqual((self.runtime / 'events.jsonl').read_text('utf-8'), 'preserve log')
        self.assertEqual([call.args[0] for call in farm.event.call_args_list], [
            'full_restart_stop_requested', 'full_restart_exit_confirmed',
            'full_restart_temporary_state_reset', 'full_restart_connection_check',
            'full_restart_identity_verified',
            'full_restart_relaunch', 'full_restart_completed'])

    def test_concurrent_stop_cancels_restart_without_reset_or_launch(self):
        before = (self.runtime / 'rich_b.json').read_bytes()
        def stop_after_exit(name, **kwargs):
            if name == 'full_restart_exit_confirmed':
                farm.STOP.write_text('Intentional user stop\n', encoding='utf-8')
        farm.event.side_effect = stop_after_exit
        with patch.object(farm, 'live_controller_pids', return_value=[]), \
             patch.object(farm, 'start') as launch:
            with self.assertRaisesRegex(InterruptedError, 'Full Restart cancelled'):
                farm.full_restart()
        launch.assert_not_called()
        self.assertEqual((self.runtime / 'rich_b.json').read_bytes(), before)
        self.assertEqual(farm.STOP.read_text('utf-8'), 'Intentional user stop\n')

    def test_restart_lock_blocks_duplicate_restart_start_and_stage_switch(self):
        with farm.lock('restart'):
            with self.assertRaises(RuntimeError):
                farm.full_restart()
            with self.assertRaisesRegex(RuntimeError, 'Full Restart is underway'):
                farm.start('rich-b')
            with self.assertRaisesRegex(RuntimeError, 'Full Restart is underway'):
                farm.select_stage('scrolls-a')
        farm.subprocess.run.assert_not_called()
        self.assertEqual(farm.selected_stage(), 'rich-b')

    def test_relaunch_failure_is_logged_and_worker_guards_remain_in_force(self):
        with patch.object(farm, 'live_controller_pids', return_value=[]), \
             patch.object(farm, 'start', side_effect=RuntimeError('identity validation failed')):
            with self.assertRaisesRegex(RuntimeError, 'identity validation failed'):
                farm.full_restart()
        self.assertEqual(farm.event.call_args.args[0], 'full_restart_failed')
        self.assertIn('identity validation failed', farm.event.call_args.kwargs['error'])
        with patch.object(farm, 'live_controller_pids', return_value=[999]):
            with self.assertRaisesRegex(RuntimeError, 'Existing account controller alive'):
                farm.start('rich-b')
        farm.subprocess.run.assert_not_called()

    def test_connection_failure_never_relaunches(self):
        self.device.connect.side_effect = RuntimeError('Android device identity mismatch')
        with patch.object(farm, 'live_controller_pids', return_value=[]), \
             patch.object(farm, 'start') as launch:
            with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                farm.full_restart()
        launch.assert_not_called()
        self.assertTrue(farm.STOP.exists())
        self.assertEqual(farm.event.call_args.args[0], 'full_restart_failed')

    def test_old_pid_still_alive_blocks_launch_even_if_runtime_record_disappears(self):
        def controllers():
            if farm.STOP.exists():
                (self.runtime / 'supervisor.json').unlink(missing_ok=True)
                return []
            return [111, 222]
        before = (self.runtime / 'rich_b.json').read_bytes()
        with patch.object(farm, 'live_controller_pids', side_effect=controllers), \
             patch.object(farm, 'alive', return_value=True), \
             patch.object(farm, 'start') as launch:
            with self.assertRaisesRegex(RuntimeError, 'old controller still active'):
                farm.full_restart(shutdown_timeout=0)
        launch.assert_not_called()
        self.assertEqual((self.runtime / 'rich_b.json').read_bytes(), before)

    def test_restart_runs_existing_start_path_once_then_duplicate_start_is_noop(self):
        def launch(*args, **kwargs):
            self.assertFalse(farm.STOP.exists())
            self.device.connect.assert_called_once_with()
            farm.write_json(self.runtime / 'supervisor.json',
                            {'pid': 9001, 'worker_pid': 9002, 'stage': 'rich-b'})
            return subprocess.CompletedProcess(args, 0,
                json.dumps({'ReturnValue': 0, 'ProcessId': 9001}), '')
        farm.subprocess.run.side_effect = launch
        with patch.object(farm, 'live_controller_pids',
                          side_effect=lambda: [] if farm.STOP.exists() else [111, 222]), \
             patch.object(farm, 'alive', side_effect=lambda pid: pid in (9001, 9002)):
            farm.full_restart('scrolls-a')
            farm.start('rich-b')
        farm.subprocess.run.assert_called_once()
        self.assertFalse(farm.STOP.exists())
        self.assertEqual(farm.selected_stage(), 'rich-b')
        self.assertEqual(farm.read_json(self.runtime / 'supervisor.json')['worker_pid'], 9002)
        self.assertEqual(farm.read_json(self.runtime / 'rich_b.json')['pending'], self.progress['pending'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
