"""Pinned Universal start recovery across Full Restart; fake devices only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from automation import controller as farm
from instance_fixtures import legacy_target
from test_stages import FakeDevice


class StartRestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.target = legacy_target()
        self.patches = [
            patch.object(farm, 'RUNTIME', self.runtime),
            patch.object(farm, 'STOP', self.runtime / 'STOP'),
            patch.object(farm, 'selected_instance', return_value=self.target),
            patch.object(farm, 'validate_mapping'),
            patch.object(farm, 'event'),
            patch.object(farm.time, 'sleep'),
            patch.object(farm, 'ocr_text', return_value=''),
            patch.object(farm, 'recorded_processes', return_value=[]),
            patch.object(farm, 'live_controller_pids', return_value=[]),
            patch.object(farm, 'controller_active', return_value=False),
            patch.object(farm, 'lock_busy', return_value=False),
            patch('item_recognition.reward_layout.has_unrevealed_rewards', return_value=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def frame(self, screen):
        paths = {'start': 'tests/fixtures/rich_b/ready_to_start.png',
                 'battle': 'tests/fixtures/battle.png',
                 'complete': 'tests/fixtures/mission_complete.png',
                 'results': 'tests/fixtures/rich_b/ready_to_retry.png',
                 'stamina': 'tests/fixtures/rich_b/not_enough_stamina.png'}
        frame = farm.cv2.imread(str(farm.ROOT / paths[screen]))
        self.assertIsNotNone(frame)
        return frame

    def engine(self, screen):
        device = FakeDevice(self.frame(screen))
        device.target = self.target
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine('universal')

    def saved_start(self):
        engine = self.engine('start')
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission'])
        return copy.deepcopy(engine.data['pending'])

    def test_full_restart_recovers_pinned_start_without_duplicate_input_or_recording(self):
        pending = self.saved_start()
        device = FakeDevice(self.frame('battle'))
        with patch.object(farm, 'effective_instance', return_value=self.target), \
             patch.object(farm, 'Device', return_value=device), \
             patch.object(farm, 'start') as launch:
            farm.full_restart('universal', shutdown_timeout=0)
        launch.assert_called_once()
        self.assertEqual(farm.read_json(self.runtime / 'universal.json')['pending'], pending)
        self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        farm.STOP.unlink()  # Simulate the mocked launch consuming its restart stop.

        resumed = self.engine('battle')
        collector = Mock()
        collector.completed.return_value = 'durable-completion-id'
        collector.evidence.return_value = True
        resumed.loot_collector = collector
        self.assertFalse(resumed.mission_verified)
        resumed.tick()
        resumed.tick()
        self.assertTrue(resumed.mission_verified)
        self.assertIsNone(resumed.data['pending'])
        self.assertEqual(resumed.data['run']['id'], pending['run_id'])
        self.assertEqual(resumed.data['counts']['started'], 1)
        self.assertEqual(resumed.device.actions, [])

        resumed.device.image = self.frame('complete')
        resumed.tick()
        resumed.tick()
        self.assertEqual(resumed.data['counts']['completed'], 1)
        self.assertEqual(resumed.device.actions, ['advance_completion'])
        collector.completed.assert_called_once_with(pending['run_id'],
            completed_at=resumed.data['run']['recording']['completed_at'])
        resumed.device.image = self.frame('results')
        resumed.tick()
        resumed.tick()
        self.assertEqual(resumed.device.actions, ['advance_completion', 'retry'])
        self.assertEqual(collector.evidence.call_count, 1)
        self.assertEqual(collector.evidence.call_args.args[0], pending['run_id'])
        self.assertEqual(resumed.data['run']['recording']['state'], 'screenshot_saved')

    def test_completion_or_stamina_can_be_first_fresh_observation(self):
        for screen, expected_action, started, completed in [
                ('complete', 'advance_completion', 1, 1),
                ('stamina', 'dismiss_stamina', 0, 0)]:
            with self.subTest(screen=screen):
                self.saved_start()
                farm.write_json(self.runtime / 'session.json',
                    {'stage': 'universal', 'mission_verified': False})
                engine = self.engine(screen)
                engine.tick()
                engine.tick()
                self.assertTrue(engine.mission_verified)
                self.assertEqual(engine.device.actions, [expected_action])
                self.assertEqual(engine.data['counts']['started'], started)
                self.assertEqual(engine.data['counts']['completed'], completed)
                (self.runtime / 'universal.json').unlink()
                (self.runtime / 'session.json').unlink()

    def test_full_restart_recovers_active_run_without_incrementing_started_again(self):
        for first_screen in ('battle', 'complete'):
            with self.subTest(first_screen=first_screen):
                engine = self.engine('start')
                engine.tick()
                engine.device.image = self.frame('battle')
                engine.tick()
                run = copy.deepcopy(engine.data['run'])
                self.assertIsNone(engine.data['pending'])
                with patch.object(farm, 'effective_instance', return_value=self.target), \
                     patch.object(farm, 'Device', return_value=engine.device), \
                     patch.object(farm, 'start'):
                    farm.full_restart('universal', shutdown_timeout=0)
                farm.STOP.unlink()
                resumed = self.engine(first_screen)
                self.assertFalse(resumed.mission_verified)
                resumed.tick()
                resumed.tick()
                self.assertTrue(resumed.mission_verified)
                self.assertEqual(resumed.data['run']['id'], run['id'])
                self.assertEqual(resumed.data['counts']['started'], 1)
                self.assertEqual(resumed.data['counts']['completed'], int(first_screen == 'complete'))
                self.assertEqual(resumed.device.actions,
                                 ['advance_completion'] if first_screen == 'complete' else [])
                (self.runtime / 'universal.json').unlink()
                (self.runtime / 'session.json').unlink()

    def test_recovery_requires_original_matching_target_and_start_latch(self):
        for invalid in ('missing_target', 'different_device', 'different_endpoint', 'different_stage',
                        'different_source', 'different_action', 'missing_pending', 'unexpected_battle'):
            with self.subTest(invalid=invalid):
                self.saved_start()
                path = self.runtime / 'universal.json'
                progress = farm.read_json(path)
                if invalid == 'missing_target':
                    progress.pop('instance')
                elif invalid == 'different_device':
                    progress['instance']['android_id'] = 'another-device'
                elif invalid == 'different_endpoint':
                    progress['instance']['endpoint'] = '127.0.0.1:5557'
                elif invalid == 'different_stage':
                    progress['stage'] = 'ninja_road'
                elif invalid == 'different_source':
                    progress['pending']['source'] = 'RESULTS_NEXT'
                elif invalid == 'different_action':
                    progress['pending']['id'] = 'retry'
                elif invalid == 'missing_pending':
                    progress.pop('pending')
                else:
                    progress['pending']['expected'] = ['STAMINA_PROMPT']
                farm.write_json(path, progress)
                farm.write_json(self.runtime / 'session.json',
                    {'stage': 'universal', 'mission_verified': False})
                engine = self.engine('battle')
                engine.tick()
                engine.tick()  # A save refreshing the target must not grant recovery.
                engine = self.engine('battle')
                engine.tick()  # Reconstructing from blocked saves must still reject it.
                self.assertFalse(engine.mission_verified)
                self.assertEqual(engine.data['screen'], 'UNKNOWN')
                self.assertEqual(engine.data['counts']['started'], 0)
                self.assertEqual(engine.device.actions, [])
                path.unlink()
                (self.runtime / 'session.json').unlink()

    def test_active_run_recovery_requires_original_unfinished_run_for_pinned_target(self):
        for invalid in ('missing_run', 'completed_run', 'different_device', 'different_stage',
                        'unrelated_pending'):
            with self.subTest(invalid=invalid):
                engine = self.engine('start')
                engine.tick()
                engine.device.image = self.frame('battle')
                engine.tick()
                progress = farm.read_json(self.runtime / 'universal.json')
                if invalid == 'missing_run':
                    progress.pop('run')
                elif invalid == 'completed_run':
                    progress['run']['outcome'] = 'completed'
                elif invalid == 'different_device':
                    progress['instance']['android_id'] = 'another-device'
                elif invalid == 'different_stage':
                    progress['stage'] = 'ninja_road'
                else:
                    progress['pending'] = {'id': 'next', 'source': 'RESULTS_NEXT',
                        'expected': ['IN_BATTLE'], 'time': 1, 'run_id': 'unrelated-input'}
                farm.write_json(self.runtime / 'universal.json', progress)
                farm.write_json(self.runtime / 'session.json',
                    {'stage': 'universal', 'mission_verified': False})
                resumed = self.engine('battle')
                resumed.tick()
                resumed.tick()
                resumed = self.engine('battle')
                resumed.tick()
                self.assertFalse(resumed.mission_verified)
                self.assertEqual(resumed.data['screen'], 'UNKNOWN')
                self.assertEqual(resumed.device.actions, [])
                (self.runtime / 'universal.json').unlink()
                (self.runtime / 'session.json').unlink()

    def test_disagreeing_fresh_observations_keep_start_latched(self):
        pending = self.saved_start()
        farm.write_json(self.runtime / 'session.json',
            {'stage': 'universal', 'mission_verified': False})
        engine = self.engine('battle')
        engine.device.capture = Mock(side_effect=[self.frame('battle'), self.frame('start')])
        engine.tick()
        self.assertFalse(engine.mission_verified)
        self.assertEqual(engine.data['pending'], pending)
        self.assertEqual(engine.data['counts']['started'], 0)
        self.assertEqual(engine.device.actions, [])


if __name__ == '__main__':
    unittest.main()
