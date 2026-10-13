"""Offline Night checks using actual captures and fake devices; no ADB input."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


STAGE = 'night-of-tragedy-a'
FIXTURES = 'tests/fixtures/night_of_tragedy_a/'


class NightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', side_effect=AssertionError('Unexpected OCR'))]
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

    def engine(self, filename='in_battle.png'):
        device = FakeDevice(self.image(FIXTURES + filename))
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine(STAGE)

    def test_configuration_and_templates_are_stage_specific(self):
        stage = farm.load_stage(STAGE)
        self.assertTrue(stage['manual_first_battle'])
        self.assertEqual(stage['name'], 'Night of Tragedy [A]')
        self.assertEqual(stage['progress'], self.runtime / 'night_of_tragedy_a.json')
        self.assertEqual(stage['catalog'], farm.ROOT / 'assets/patterns/night_of_tragedy_a.json')
        detector = farm.Detector(STAGE)
        for template in detector.templates:
            with self.subTest(template=template):
                self.assertTrue((farm.ROOT / template).resolve().is_relative_to(
                    (farm.ROOT / 'assets/patterns/night_of_tragedy_a').resolve()))
        self.assertFalse({s['name'] for s in detector.states} &
                         {'HOME_MENU', 'TITLE_START', 'TEAM_MENU', 'HELPER_SELECTION'})

    def test_actual_captures_detect_without_ocr(self):
        detector = farm.Detector(STAGE)
        for filename, screen in [('ready_to_start.png', 'READY_TO_START'),
                                 ('results_next.png', 'RESULTS_NEXT'),
                                 ('reward_reveal.png', 'REWARD_REVEAL'),
                                 ('ready_to_retry.png', 'READY_TO_RETRY'),
                                 ('in_battle.png', 'IN_BATTLE'),
                                 ('mission_complete.png', 'MISSION_COMPLETE')]:
            with self.subTest(screen=screen):
                self.assertEqual(detector.classify(self.image(FIXTURES + filename))[0], screen)

    def test_other_stage_landing_and_results_are_rejected(self):
        detector = farm.Detector(STAGE)
        for path in ['tests/fixtures/ready_to_start.png', 'tests/fixtures/results_next.png',
                     'tests/fixtures/reward_reveal.png', 'assets/reference_images/starting_point.png',
                     'tests/fixtures/practice_ready_to_start.png', 'tests/fixtures/practice_current.png',
                     'tests/fixtures/rich_b/ready_to_start.png', 'tests/fixtures/rich_b/results_next.png',
                     'tests/fixtures/rich_b/reward_reveal.png', 'tests/fixtures/rich_b/ready_to_retry.png']:
            with self.subTest(path=path):
                self.assertEqual(detector.classify(self.image(path))[0], 'UNKNOWN')

    def test_fresh_completion_holdouts_and_dimmed_rejection(self):
        detector = farm.Detector(STAGE)
        for number in (*range(36, 42), *range(54, 60)):
            with self.subTest(capture=number):
                frame = self.image(FIXTURES + f'completion_{number:02}.png')
                self.assertEqual(detector.classify(frame)[0], 'MISSION_COMPLETE')
                dimmed = (frame.astype(float) * .5).astype(frame.dtype)
                self.assertNotEqual(detector.classify(dimmed)[0], 'MISSION_COMPLETE')

    def test_fresh_reward_holdouts_remain_distinct_and_require_every_guard(self):
        detector = farm.Detector(STAGE)
        for number in range(8):
            with self.subTest(capture=number):
                frame = self.image(FIXTURES + f'reward_stall_{number:02}.png')
                self.assertEqual(detector.classify(frame)[0], 'REWARD_REVEAL')
                dimmed = (frame.astype(float) * .5).astype(frame.dtype)
                self.assertNotEqual(detector.classify(dimmed)[0], 'REWARD_REVEAL')
        for filename in ('ready_to_retry.png', 'stamina_prompt.png', 'rank_up_stamina_ack.png'):
            with self.subTest(other_screen=filename):
                self.assertNotEqual(detector.classify(self.image(FIXTURES + filename))[0], 'REWARD_REVEAL')
        spec = next(s for s in detector.states if s['name'] == 'REWARD_REVEAL')
        frame = self.image(FIXTURES + 'reward_stall_00.png')
        for anchor in spec['anchors']:
            with self.subTest(region=anchor['region']):
                changed = frame.copy()
                x1, y1, x2, y2 = anchor['region']
                changed[y1:y2, x1:x2] = 0
                self.assertNotEqual(detector.classify(changed)[0], 'REWARD_REVEAL')

    def test_first_start_screen_waits_for_manual_battle(self):
        engine = self.engine('ready_to_start.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertFalse(engine.mission_verified)

    def test_captured_rank_up_dialog_and_fresh_holdout_are_stage_guarded(self):
        detector = farm.Detector(STAGE)
        for filename in ('rank_up_stamina_ack.png', 'rank_ack_holdout.png'):
            with self.subTest(filename=filename):
                self.assertEqual(detector.classify(self.image(FIXTURES + filename))[0],
                                 'RANK_UP_STAMINA_ACK')
        self.assertEqual(detector.classify(self.image(FIXTURES + 'rank_up_results.png'))[0],
                         'RESULTS_NEXT')
        self.assertEqual(detector.classify(self.image('tests/fixtures/rich_b/rank_up_stamina_ack.png'))[0],
                         'UNKNOWN')

    def test_rank_up_acknowledgement_resolves_completion_then_advances_results(self):
        engine = self.engine()
        engine.tick()
        engine.device.image = self.image(FIXTURES + 'mission_complete.png')
        engine.tick()
        self.assertEqual(engine.device.actions, ['advance_completion'])
        engine.device.image = self.image(FIXTURES + 'rank_up_stamina_ack.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['advance_completion', 'dismiss_known_popup'])
        self.assertEqual(engine.data['pending']['id'], 'dismiss_known_popup')
        engine.device.image = self.image(FIXTURES + 'rank_up_results.png')
        engine.tick()
        self.assertEqual(engine.device.actions, ['advance_completion', 'dismiss_known_popup', 'next'])
        self.assertEqual(engine.data['pending']['id'], 'next')

    def test_two_cycles_reuse_existing_macros(self):
        engine = self.engine()
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.device.commands, [])
        expected = []
        cycle = [('mission_complete.png', 'advance_completion'),
                 ('results_next.png', 'next'), ('reward_reveal.png', 'reveal_rewards'),
                 ('ready_to_retry.png', 'retry'), ('ready_to_start.png', 'start_mission'),
                 ('in_battle.png', None)]
        for number in (1, 2):
            for filename, action in cycle:
                with self.subTest(cycle=number, filename=filename):
                    engine.device.image = self.image(FIXTURES + filename)
                    engine.tick()
                    engine.tick()
                    if action:
                        expected.append(action)
                    self.assertEqual(engine.device.actions, expected)
            self.assertEqual(engine.data['counts']['completed'], number)
            self.assertEqual(engine.data['counts']['started'], number)

    def test_stop_and_existing_account_owner_block_actions(self):
        engine = self.engine('ready_to_retry.png')
        farm.STOP.write_text('manual stop', encoding='utf-8')
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        for name in ('worker', 'supervisor'):
            with self.subTest(lock=name), farm.lock(name):
                with self.assertRaises(RuntimeError):
                    farm.select_stage(STAGE)
                with patch.object(farm.subprocess, 'run') as launch:
                    with self.assertRaises(RuntimeError):
                        farm.start(STAGE)
                    launch.assert_not_called()

    def test_captured_stamina_prompt_and_holdout_require_every_guard(self):
        detector = farm.Detector(STAGE)
        frame = self.image(FIXTURES + 'stamina_prompt.png')
        for filename in ('stamina_prompt.png', 'stamina_holdout.png'):
            self.assertEqual(detector.classify(self.image(FIXTURES + filename))[0], 'STAMINA_PROMPT')
        self.assertEqual(detector.classify(self.image('tests/fixtures/rich_b/not_enough_stamina.png'))[0],
                         'UNKNOWN')
        spec = next(s for s in detector.states if s['name'] == 'STAMINA_PROMPT')
        for anchor in spec['anchors']:
            with self.subTest(region=anchor['region']):
                changed = frame.copy()
                x1, y1, x2, y2 = anchor['region']
                changed[y1:y2, x1:x2] = 0
                self.assertEqual(detector.classify(changed)[0], 'UNKNOWN')
        changed = frame.copy()
        changed[982:1054, 200:399] = (255, 0, 255)
        self.assertEqual(detector.classify(changed)[0], 'UNKNOWN')

    def test_real_stamina_prompt_reuses_600_second_timer_across_stop_restart(self):
        engine = self.engine()
        engine.tick()
        engine.device.image = self.image(FIXTURES + 'stamina_prompt.png')
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        engine.device.image = self.image(FIXTURES + 'ready_to_retry.png')
        with patch.object(farm.time, 'time', return_value=1004):
            engine.tick()
        self.assertEqual(engine.data['stamina_until'], 1604)
        farm.STOP.write_text('manual stop', encoding='utf-8')
        engine = self.engine('ready_to_retry.png')
        with patch.object(farm.time, 'time', return_value=1603):
            engine.tick()
        self.assertEqual(engine.device.actions, [])
        farm.STOP.unlink()
        with patch.object(farm.time, 'time', return_value=1603):
            engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data['stamina_until'], 1604)
        with patch.object(farm.time, 'time', return_value=1604):
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions, ['retry'])

    def test_persisted_retry_can_resume_verified_stamina_without_first_battle(self):
        engine = self.engine('stamina_prompt.png')
        engine.data['pending'] = {'id': 'retry', 'source': 'READY_TO_RETRY',
                                  'expected': ['READY_TO_START', 'STAMINA_PROMPT'], 'time': 0}
        engine.save()
        engine = self.engine('stamina_prompt.png')
        self.assertFalse(engine.mission_verified)
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])

    def test_initial_stamina_prompt_cannot_bypass_manual_first_battle(self):
        engine = self.engine('stamina_prompt.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertFalse(engine.mission_verified)


if __name__ == '__main__':
    unittest.main()
