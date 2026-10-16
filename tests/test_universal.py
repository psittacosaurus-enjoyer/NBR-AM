"""Universal loop replay checks with saved frames and fake devices only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import farm
from test_stages import FakeDevice
from instance_fixtures import legacy_target


LAYOUTS = {
    'scrolls-a': 'tests/fixtures/',
    'rich-b': 'tests/fixtures/rich_b/',
    'night-of-tragedy-a': 'tests/fixtures/night_of_tragedy_a/',
}
SCREENS = {'ready_to_start': 'READY_TO_START', 'results_next': 'RESULTS_NEXT',
           'reward_reveal': 'REWARD_REVEAL', 'ready_to_retry': 'READY_TO_RETRY'}


class UniversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'selected_instance', return_value=legacy_target()),
                        patch.object(farm, 'validate_mapping'),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value='')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def image(self, path):
        frame = farm.cv2.imread(str(farm.ROOT / path))
        self.assertIsNotNone(frame, path)
        return frame

    def frame(self, stage, filename, unfamiliar=False):
        path = LAYOUTS[stage] + filename + '.png'
        if stage == 'scrolls-a' and filename == 'ready_to_retry':
            path = 'tests/fixtures/retry_live.png'
        frame = self.image(path)
        if unfamiliar:
            screen = SCREENS[filename]
            catalogs = {'scrolls-a': 'catalog.json', 'rich-b': 'rich_b.json',
                        'night-of-tragedy-a': 'night_of_tragedy_a.json'}
            source = farm.read_json(farm.ROOT / 'assets/patterns' / catalogs[stage])
            state = next(s for s in source['states'] if s['name'] == screen)
            index = 0 if screen == 'REWARD_REVEAL' else 1
            x1, y1, x2, y2 = state['anchors'][index]['region']
            frame[y1:y2, x1:x2] = 0
        return frame

    def engine(self, frame):
        device = FakeDevice(frame)
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine('universal')

    def test_shared_layouts_recognize_known_and_unfamiliar_titles(self):
        detector = farm.Detector('universal')
        for stage in LAYOUTS:
            for filename, screen in SCREENS.items():
                for unfamiliar in (False, True):
                    with self.subTest(stage=stage, screen=screen, unfamiliar=unfamiliar):
                        name, spec, _ = detector.classify(self.frame(stage, filename, unfamiliar))
                        self.assertEqual(name, screen)
                        self.assertEqual(spec['action']['id'],
                                         farm.load_stage('universal')['state_actions'][screen])
                        self.assertNotIn('mission_id', spec)
                        self.assertNotIn('mission_name', spec)

    def test_actual_unconfigured_mission_frames_need_no_stage_configuration(self):
        universal = farm.Detector('universal')
        for filename, expected in [('unconfigured_start.png', 'READY_TO_START'),
                                   ('unconfigured_results.png', 'READY_TO_RETRY')]:
            with self.subTest(filename=filename):
                frame = self.image('tests/fixtures/universal/' + filename)
                name, spec, _ = universal.classify(frame)
                self.assertEqual(name, expected)
                self.assertNotIn('mission_id', spec)
                self.assertNotIn('mission_name', spec)

    def test_trial_coin_summary_is_guarded_without_title_or_reward_totals(self):
        detector = farm.Detector('universal')
        frame = self.image('tests/fixtures/universal/trial_coins_next.png')
        name, spec, _ = detector.classify(frame)
        self.assertEqual(name, 'TRIAL_COINS_NEXT')
        self.assertEqual(spec['action']['point'], [457, 1526])
        self.assertNotIn('mission_name', spec)
        # Mission identity, coin icons and variable amounts are not input guards.
        frame[150:273, 170:740] = 0
        frame[420:607, 100:650] = 0
        frame[850:1100, 735:825] = 0
        frame[1310:1390, 515:795] = 0
        self.assertEqual(detector.classify(frame)[0], 'TRIAL_COINS_NEXT')

    def test_trial_coin_summary_requires_each_guard_and_undimmed_button(self):
        detector = farm.Detector('universal')
        frame = self.image('tests/fixtures/universal/trial_coins_next.png')
        _, spec, _ = detector.classify(frame)
        changed = [(frame.astype(float) * .5).astype(frame.dtype)]
        for anchor in spec['anchors']:
            copy = frame.copy()
            x1, y1, x2, y2 = anchor['region']
            copy[y1:y2, x1:x2] = 0
            changed.append(copy)
        for index, copy in enumerate(changed):
            with self.subTest(guard=index):
                self.assertEqual(detector.classify(copy)[0], 'UNKNOWN')

    def test_trial_extra_next_resolves_prior_latch_and_retries_once(self):
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})
        engine = self.engine(self.image('tests/fixtures/universal/trial_coins_next.png'))
        engine.data['pending'] = {'id': 'next', 'source': 'RESULTS_NEXT',
                                  'expected': ['READY_TO_RETRY', 'REWARD_REVEAL'], 'time': 0}
        engine.data['run'] = {'id': 'finished', 'start': 0, 'outcome': 'completed'}
        engine.data['counts']['completed'] = 1
        engine.tick()
        self.assertEqual(engine.device.actions, ['next'])
        self.assertEqual(engine.data['pending']['source'], 'TRIAL_COINS_NEXT')
        engine.tick()
        self.assertEqual(engine.device.actions, ['next'])
        engine.device.image = self.image('tests/fixtures/universal/unconfigured_results.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['next', 'retry'])
        self.assertEqual(engine.data['counts']['completed'], 1)

    def test_dim_modal_and_erased_controls_never_authorize_input(self):
        detector = farm.Detector('universal')
        for stage in LAYOUTS:
            for filename, screen in SCREENS.items():
                frame = self.frame(stage, filename)
                name, spec, _ = detector.classify(frame)
                self.assertEqual(name, screen)
                altered = [(frame.astype(float) * .5).astype(frame.dtype)]
                # Layout/footer guards are shared across animation variants.
                # The reward tap animation itself has an existing alternative.
                anchors = spec['anchors'][:3] if screen == 'REWARD_REVEAL' else spec['anchors']
                for anchor in anchors:
                    changed = frame.copy()
                    x1, y1, x2, y2 = anchor['region']
                    changed[y1:y2, x1:x2] = 0
                    altered.append(changed)
                for index, changed in enumerate(altered):
                    with self.subTest(stage=stage, screen=screen, alteration=index):
                        self.assertEqual(detector.classify(changed)[0], 'UNKNOWN')
                        engine = self.engine(changed)
                        engine.tick()
                        self.assertEqual(engine.device.actions, [])


    def test_saved_pending_input_blocks_start_and_preserves_stop(self):
        saved = {'stage': 'retired-choice', 'pending': {'id': 'retry', 'source': 'READY_TO_RETRY',
                  'expected': ['READY_TO_START'], 'time': 0}}
        farm.write_json(self.runtime / 'selection.json', {'stage': 'retired-choice'})
        farm.write_json(self.runtime / 'retired_choice.json', saved)
        farm.STOP.write_text('Intentional stop', encoding='utf-8')
        with patch.object(farm, 'lock_busy', return_value=False), \
             patch.object(farm, 'live_controller_pids', return_value=[]), \
             patch.object(farm.subprocess, 'run') as launch:
            with self.assertRaisesRegex(RuntimeError, 'existing saved input'):
                farm.start()
        launch.assert_not_called()
        self.assertEqual(farm.read_json(self.runtime / 'retired_choice.json'), saved)
        self.assertEqual(farm.STOP.read_text(encoding='utf-8'), 'Intentional stop')

    def test_unrelated_saved_progress_remains_untouched(self):
        saved = {'stage': 'unrelated', 'pending': {'id': 'retry'}, 'counts': {'started': 8}}
        farm.write_json(self.runtime / 'unrelated.json', saved)
        farm.write_json(self.runtime / 'farm.json', saved)
        farm.write_json(self.runtime / 'selection.json', {'stage': 'universal'})
        farm.verify_saved_input()
        self.assertEqual(farm.read_json(self.runtime / 'unrelated.json'), saved)
        self.assertEqual(farm.read_json(self.runtime / 'farm.json'), saved)

    def test_two_full_loops_count_once_and_reuse_loot_collection(self):
        for unfamiliar in (False, True):
            with self.subTest(unfamiliar=unfamiliar):
                (self.runtime / 'universal.json').unlink(missing_ok=True)
                (self.runtime / 'session.json').unlink(missing_ok=True)
                engine = self.engine(self.frame('rich-b', 'ready_to_start', unfamiliar))
                collector = Mock()
                engine.loot_collector = collector
                engine.tick()
                engine.tick()
                self.assertEqual(engine.device.actions, ['start_mission'])
                self.assertNotIn('mission_id', engine.data)
                expected = ['start_mission']
                run_ids = []
                for cycle in (1, 2):
                    for path, screen, action in [
                        ('tests/fixtures/battle.png', 'IN_BATTLE', None),
                        ('tests/fixtures/mission_complete.png', 'MISSION_COMPLETE', 'advance_completion'),
                        ('results_next', 'RESULTS_NEXT', 'next'),
                        ('reward_reveal', 'REWARD_REVEAL', 'reveal_rewards'),
                        ('ready_to_retry', 'READY_TO_RETRY', 'retry'),
                        ('ready_to_start', 'READY_TO_START', 'start_mission'),
                    ]:
                        engine.device.image = (self.image(path) if path.endswith('.png')
                                               else self.frame('rich-b', path, unfamiliar))
                        engine.tick()
                        engine.tick()
                        self.assertEqual(engine.data['screen'], screen)
                        if action:
                            expected.append(action)
                        self.assertEqual(engine.device.actions, expected)
                        if screen == 'MISSION_COMPLETE':
                            run_ids.append(engine.data['run']['id'])
                    self.assertEqual(engine.data['counts']['started'], cycle)
                    self.assertEqual(engine.data['counts']['completed'], cycle)
                    self.assertEqual(engine.data['counts']['failed'], 0)
                    self.assertEqual(collector.completed.call_count, cycle)
                    self.assertEqual(collector.evidence.call_count, cycle)
                self.assertEqual(len(set(run_ids)), 2)
                self.assertEqual([c.args[0] for c in collector.completed.call_args_list], run_ids)
                self.assertEqual([c.args[0] for c in collector.evidence.call_args_list], run_ids)
                self.assertTrue(all(call.kwargs == {} for call in collector.evidence.call_args_list))
                self.assertEqual(engine.device.commands, [])

    def test_stamina_no_latch_persists_and_wait_ends_at_deadline(self):
        engine = self.engine(self.frame('rich-b', 'ready_to_start'))
        engine.tick()
        engine.device.image = self.image('tests/fixtures/rich_b/not_enough_stamina.png')
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
            engine.tick()
            self.assertEqual(engine.device.actions, ['start_mission', 'dismiss_stamina'])
            self.assertEqual(engine.data['pending']['id'], 'dismiss_stamina')
            resumed = self.engine(engine.device.image)
            resumed.tick()
            self.assertEqual(resumed.device.actions, [])
        resumed.device.image = self.frame('rich-b', 'ready_to_retry')
        with patch.object(farm.time, 'time', return_value=1004):
            resumed.tick()
            self.assertIsNone(resumed.data['pending'])
            deadline = 1004 + farm.STAMINA_WAIT_SECONDS
            self.assertEqual(resumed.data['stamina_until'], deadline)
            self.assertEqual(resumed.device.actions, [])
        with patch.object(farm.time, 'time', return_value=deadline - .01):
            resumed.tick()
            self.assertEqual(resumed.device.actions, [])
        with patch.object(farm.time, 'time', return_value=deadline):
            resumed.tick()
            resumed.tick()
            self.assertEqual(resumed.device.actions, ['retry'])
        persisted = farm.read_json(self.runtime / 'universal.json')
        self.assertEqual(persisted['pending']['id'], 'retry')

    def test_saved_account_stamina_wait_survives_upgrade(self):
        farm.write_json(self.runtime / 'rich_b.json', {'stamina_until': 2000})
        engine = self.engine(self.frame('scrolls-a', 'ready_to_start'))
        with patch.object(farm.time, 'time', return_value=1999):
            engine.tick()
            self.assertEqual(engine.device.actions, [])
            self.assertEqual(engine.data['stamina_until'], 2000)
        with patch.object(farm.time, 'time', return_value=2000):
            engine.tick()
            self.assertEqual(engine.device.actions, ['start_mission'])


    def test_initial_results_and_battle_require_start_screen(self):
        paths = ['tests/fixtures/battle.png', 'tests/fixtures/mission_complete.png',
                 'tests/fixtures/rich_b/results_next.png',
                 'tests/fixtures/rich_b/ready_to_retry.png']
        for path in paths:
            with self.subTest(path=path):
                engine = self.engine(self.image(path))
                engine.tick()
                self.assertEqual(engine.device.actions, [])
                self.assertEqual(engine.data['screen'], 'UNKNOWN')
                self.assertFalse(engine.mission_verified)
                self.assertEqual(engine.data['counts']['completed'], 0)

    def test_stop_blocks_all_input_and_no_menu_navigation_is_available(self):
        engine = self.engine(self.frame('rich-b', 'ready_to_start'))
        farm.STOP.write_text('Intentional stop', encoding='utf-8')
        self.assertEqual(engine.tick(), 0)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.device.commands, [])
        menu_states = {'TITLE_START', 'HOME_MENU', 'SCROLLS_BANNER', 'RANK_SELECTION',
                       'SOLO_SELECTION', 'HELPER_SELECTION', 'TEAM_MENU'}
        self.assertFalse(menu_states.intersection(s['name'] for s in engine.detector.states))
        farm.STOP.unlink()
        engine.device.image = self.image('assets/reference_images/starting_point.png')
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        with patch.object(engine.device, 'foreground', return_value='com.uncube.launcher3'):
            engine.tick()
        self.assertEqual(engine.device.commands, [])
        self.assertEqual(engine.data['counts']['launches'], 0)


if __name__ == '__main__':
    unittest.main()
