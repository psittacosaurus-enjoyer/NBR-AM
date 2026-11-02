"""Rank-up acknowledgement layouts replayed offline with fake devices."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation import controller as farm
from test_stages import FakeDevice


class RankUpNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value='')]
        for item in self.patches:
            item.start()
        self.detector = farm.Detector()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def image(self, path):
        frame = farm.cv2.imread(str(farm.ROOT / path))
        self.assertIsNotNone(frame, path)
        return frame

    def tall(self, notice=True):
        frame = self.image('tests/fixtures/universal/rank_up_phantom_castle_ack.png')
        if not notice:
            # Remove only the optional paragraph, leaving the same tall layout.
            frame[735:830, 115:785] = frame[900, 450]
        return frame

    def layouts(self):
        return [('normal', self.image('tests/fixtures/rank_up_stamina_ack.png'), [446, 896]),
                ('phantom_notice', self.tall(), [446, 987]),
                ('without_notice', self.tall(False), [446, 987])]

    def engine(self, frame):
        device = FakeDevice(frame)
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine()
        engine.mission_verified = True
        return engine

    def test_normal_and_tall_layouts_recognize_with_or_without_notice(self):
        frames = self.layouts() + [
            ('rich_normal', self.image('tests/fixtures/rich_b/rank_up_stamina_ack.png'), [446, 896]),
            ('night_normal', self.image('tests/fixtures/night_of_tragedy_a/rank_up_stamina_ack.png'), [446, 896])]
        for label, frame, point in frames:
            with self.subTest(layout=label):
                name, spec, _ = self.detector.classify(frame)
                self.assertEqual(name, 'RANK_UP_STAMINA_ACK')
                self.assertEqual(spec['action']['id'], 'dismiss_known_popup')
                self.assertEqual(spec['action']['point'], point)

    def test_optional_paragraph_and_underlying_results_are_not_required(self):
        frame = self.tall()
        _, spec, _ = self.detector.classify(frame)
        # Retain only the two acknowledgement guards; notices, mission title,
        # rank values and all background results can vary independently.
        isolated = farm.np.zeros_like(frame)
        for anchor in spec['anchors']:
            x1, y1, x2, y2 = anchor['region']
            isolated[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
        self.assertEqual(self.detector.classify(isolated)[0], 'RANK_UP_STAMINA_ACK')

    def test_missing_message_button_or_dimmed_dialog_remains_unknown(self):
        for label, frame, _ in self.layouts():
            _, spec, _ = self.detector.classify(frame)
            altered = [('dimmed', (frame.astype(float) * .5).astype(frame.dtype))]
            for index, anchor in enumerate(spec['anchors']):
                changed = frame.copy()
                x1, y1, x2, y2 = anchor['region']
                changed[y1:y2, x1:x2] = 0
                altered.append((str(index), changed))
            for guard, changed in altered:
                with self.subTest(layout=label, missing=guard):
                    self.assertEqual(self.detector.classify(changed)[0], 'UNKNOWN')

    def test_each_layout_acknowledges_once_then_resumes_results(self):
        for label, frame, point in self.layouts():
            with self.subTest(layout=label):
                engine = self.engine(frame)
                engine.data['pending'] = dict(id='advance_completion', source='MISSION_COMPLETE',
                    expected=['RESULTS_NEXT', 'READY_TO_RETRY'], time=0)
                counts = copy.deepcopy(engine.data['counts'])
                with patch.object(engine.device, 'perform', wraps=engine.device.perform) as perform:
                    engine.tick()
                    engine.tick()
                    self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
                    self.assertEqual(perform.call_args.args[0]['point'], point)
                    self.assertEqual(engine.data['pending']['source'], 'RANK_UP_STAMINA_ACK')
                    engine.device.image = self.image('tests/fixtures/night_of_tragedy_a/rank_up_results.png')
                    engine.tick()
                    engine.tick()
                self.assertEqual(engine.device.actions, ['dismiss_known_popup', 'next'])
                self.assertEqual(engine.data['pending']['id'], 'next')
                self.assertEqual(engine.data['counts'], counts)
                self.assertEqual(engine.device.commands, [])

    def test_notice_layout_change_cannot_repeat_a_pending_acknowledgement(self):
        engine = self.engine(self.tall())
        engine.tick()
        engine.device.image = self.tall(False)
        engine.tick()
        engine.device.image = self.image('tests/fixtures/rank_up_stamina_ack.png')
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
        self.assertEqual(engine.data['pending']['source'], 'RANK_UP_STAMINA_ACK')


if __name__ == '__main__':
    unittest.main()
