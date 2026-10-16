"""Offline practice-stage checks. Temporary state and fake devices only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


STAGE = 'universal'


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
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
        image = farm.cv2.imread(str(farm.ROOT / path))
        self.assertIsNotNone(image, path)
        return image

    def engine(self, path='tests/fixtures/battle.png'):
        device = FakeDevice(self.image(path))
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine(STAGE)
            engine.mission_verified = True
            return engine

    def practice_image(self, path, start=False):
        """Synthetic shared-control fixture with the captured practice title."""
        image = self.image(path)
        modal = path.endswith(('not_enough_stamina.png', 'rank_up_stamina_ack.png'))
        filename = 'start_title.png' if start else 'dialog_title.png' if modal else 'results_title.png'
        title = self.image('assets/patterns/practice_part_1/' + filename)
        y1, y2 = (395, 461) if start else (182, 247)
        self.assertEqual(title.shape[:2], (y2 - y1, 545))
        image[y1:y2, 190:735] = title
        return image


    def test_captured_practice_results_and_start_fixtures_are_recognized_without_ocr(self):
        detector = farm.Detector(STAGE)
        with patch.object(farm, 'ocr_text', side_effect=AssertionError('Routine OCR prohibited')) as ocr:
            self.assertEqual(detector.classify(self.image('tests/fixtures/practice_current.png'))[0], 'RESULTS_NEXT')
            self.assertEqual(detector.classify(self.image('tests/fixtures/practice_ready_to_start.png'))[0], 'READY_TO_START')
            ocr.assert_not_called()

    def test_each_retry_visual_guard_is_required(self):
        detector = farm.Detector(STAGE)
        image = self.practice_image('assets/reference_images/starting_point.png')
        self.assertEqual(detector.classify(image)[0], 'READY_TO_RETRY')
        retry = next(state for state in detector.states if state['name'] == 'READY_TO_RETRY')
        for anchor in retry['anchors']:
            with self.subTest(region=anchor['region']):
                changed = image.copy()
                x1, y1, x2, y2 = anchor['region']
                changed[y1:y2, x1:x2] = 0
                self.assertEqual(detector.classify(changed)[0], 'UNKNOWN')

    def test_menu_navigation_and_game_launch_are_disabled(self):
        engine = self.engine()
        engine.tick()
        names = {state['name'] for state in engine.detector.states}
        self.assertFalse(names & {'HOME_MENU', 'SCROLLS_BANNER', 'RANK_SELECTION', 'TITLE_START',
                                  'SOLO_SELECTION', 'HELPER_SELECTION', 'TEAM_MENU'})
        with patch.object(engine.detector, 'classify', return_value=('UNKNOWN', None, {})):
            engine.tick()
        with patch.object(engine.device, 'foreground', return_value='com.uncube.launcher3'), \
             patch.object(engine.device, 'launch', create=True) as launch:
            engine.tick()
            launch.assert_not_called()
        self.assertEqual(engine.device.actions, [])

    def test_stop_blocks_practice_input(self):
        engine = self.engine()
        engine.tick()
        engine.device.image = self.image('tests/fixtures/mission_complete.png')
        farm.STOP.write_text('manual stop', encoding='utf-8')
        engine.tick()
        self.assertEqual(engine.device.actions, [])

    def test_shared_stamina_prompt_dismisses_once_then_waits_for_natural_regeneration(self):
        engine = self.engine()
        engine.tick()
        engine.device.image = self.practice_image('tests/fixtures/rich_b/not_enough_stamina.png')
        engine.data['pending'] = {'id': 'retry', 'source': 'READY_TO_RETRY',
                                  'expected': ['READY_TO_START', 'STAMINA_PROMPT'], 'time': 0}
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        engine.device.image = self.practice_image('assets/reference_images/starting_point.png')
        with patch.object(farm.time, 'time', return_value=1004):
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['stamina_until'], 1604)


    def test_rank_up_dialog_acknowledges_once_and_confirms_results(self):
        engine = self.engine()
        engine.tick()
        engine.device.image = self.practice_image('tests/fixtures/rich_b/rank_up_stamina_ack.png')
        engine.data['pending'] = {'id': 'advance_completion', 'source': 'MISSION_COMPLETE',
                                  'expected': ['RESULTS_NEXT'], 'time': 0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
        engine.device.image = self.practice_image('tests/fixtures/results_next.png')
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup', 'next'])
        self.assertFalse(farm.STOP.exists())

    def test_atomic_state_write_retries_transient_reader_conflict(self):
        path = self.runtime / 'state.json'
        farm.write_json(path, {'marker': 'previous'})
        original_replace = farm.os.replace
        attempts = []

        def replace(source, destination):
            attempts.append(destination)
            if len(attempts) <= 2:
                self.assertEqual(farm.read_json(path), {'marker': 'previous'})
                raise PermissionError('reader temporarily owns destination')
            original_replace(source, destination)

        with patch.object(farm.os, 'replace', side_effect=replace), \
             patch.object(farm.time, 'sleep') as sleep:
            farm.write_json(path, {'marker': 'replacement'})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(farm.read_json(path), {'marker': 'replacement'})

    def test_atomic_state_write_exhaustion_preserves_previous_file(self):
        path = self.runtime / 'state.json'
        farm.write_json(path, {'marker': 'previous'})
        with patch.object(farm.os, 'replace', side_effect=PermissionError('persistent conflict')) as replace, \
             patch.object(farm.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                farm.write_json(path, {'marker': 'replacement'})
        self.assertEqual(replace.call_count, 20)
        self.assertEqual(sleep.call_count, 19)
        self.assertEqual(farm.read_json(path), {'marker': 'previous'})


if __name__ == '__main__':
    unittest.main()
