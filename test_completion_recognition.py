"""Completion animation regressions. All input devices are fake."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


class CompletionRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep')]
        for item in self.patches:
            item.start()
        self.detector = farm.Detector('rich-b')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def image(self, path):
        return farm.cv2.imread(str(farm.ROOT / path))

    def test_all_live_glow_and_holdout_frames_and_dim_dialogs(self):
        paths = list((farm.ROOT / 'development/rich_b/completion_animation').glob('*.png'))
        self.assertEqual(len(paths), 25)
        for path in paths:
            with self.subTest(frame=path.name):
                frame = farm.cv2.imread(str(path))
                self.assertEqual(self.detector.classify(frame)[0], 'MISSION_COMPLETE')
                dim = (frame.astype(float) * .5).astype(frame.dtype)
                self.assertNotEqual(self.detector.classify(dim)[0], 'MISSION_COMPLETE')

    def test_both_words_and_undimmed_battle_hud_required(self):
        frame = self.image('development/rich_b/completion_animation/15.png')
        completion = next(s for s in self.detector.states if s['name'] == 'MISSION_COMPLETE')
        for anchor in completion['anchors']:
            changed = frame.copy()
            x1,y1,x2,y2 = anchor['region']
            changed[y1:y2,x1:x2] = 0
            self.assertNotEqual(self.detector.classify(changed)[0], 'MISSION_COMPLETE')
        for path in ('development/battle.png', 'development/rich_b/ready_to_start.png',
                     'development/rich_b/ready_to_retry.png', 'development/rich_b/results_next.png',
                     'development/rich_b/rank_up_stamina_ack.png', 'development/rich_b/reward_reveal.png',
                     'development/communication_error.png', 'development/rich_b/rank_selection.png'):
            self.assertNotEqual(self.detector.classify(self.image(path))[0], 'MISSION_COMPLETE', path)

    def test_quick_poll_and_two_different_glow_frames_advance_once(self):
        farm.write_json(self.runtime / 'session.json', {'stage':'rich-b', 'mission_verified':True})
        device = FakeDevice(self.image('development/battle.png'))
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine('rich-b')
        self.assertEqual(engine.tick(), 2)
        self.assertEqual(device.actions, [])
        frames = [self.image('development/rich_b/completion_animation/' + name + '.png')
                  for name in ('00', '15')]
        with patch.object(device, 'capture', side_effect=[frames[0],frames[1],frames[1],frames[0]]):
            engine.tick()
            engine.tick()
        self.assertEqual(device.actions, ['advance_completion'])
        self.assertEqual(engine.data['pending']['id'], 'advance_completion')

    def test_unknown_animation_in_verified_active_run_keeps_quick_poll_and_loss_probe(self):
        farm.write_json(self.runtime / 'session.json', {'stage':'rich-b', 'mission_verified':True})
        device = FakeDevice(farm.np.zeros((1600,900,3), dtype='uint8'))
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine('rich-b')
        engine.data['run'] = {'id':'active', 'start':0, 'outcome':None}
        with patch.object(farm, 'ocr_text', return_value='') as probe:
            self.assertEqual(engine.tick(), 2)
            probe.assert_called_once()
        self.assertEqual(device.actions, [])
        engine.data['run']['outcome'] = 'completed'
        self.assertEqual(engine.tick(), 30)


if __name__ == '__main__':
    unittest.main()
