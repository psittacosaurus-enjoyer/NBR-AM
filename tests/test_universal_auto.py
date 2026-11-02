"""Universal Auto enabling uses saved frames and fake devices only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation import controller as farm
from instance_fixtures import legacy_target
from test_stages import FakeDevice


class UniversalAutoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.target = legacy_target()
        self.patches = [
            patch.object(farm, 'RUNTIME', self.runtime),
            patch.object(farm, 'STOP', self.runtime / 'STOP'),
            patch.object(farm, 'event'),
            patch.object(farm.time, 'sleep'),
            patch.object(farm, 'ocr_text', return_value=''),
        ]
        for item in self.patches:
            item.start()
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': True})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def image(self, screen):
        paths = {'off': 'tests/fixtures/universal/battle_auto_off.png',
                 'on': 'tests/fixtures/battle.png',
                 'complete': 'tests/fixtures/mission_complete.png'}
        frame = farm.cv2.imread(str(farm.ROOT / paths[screen]))
        self.assertIsNotNone(frame)
        return frame

    def engine(self, screen='off'):
        device = FakeDevice(self.image(screen))
        device.target = self.target
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine('universal')

    def test_off_enables_once_until_on_is_confirmed_and_can_enable_again(self):
        engine = self.engine()
        engine.tick()
        pending = copy.deepcopy(engine.data['pending'])
        self.assertEqual(pending['id'], 'enable_auto')
        self.assertEqual(pending['source'], 'IN_BATTLE')
        self.assertEqual(pending['expected'], ['IN_BATTLE', 'MISSION_COMPLETE'])
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['enable_auto'])
        self.assertEqual(engine.data['pending'], pending)
        engine.device.image = self.image('on')
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertTrue(engine.data['battle_auto_enabled'])
        self.assertIsNone(engine.data['blocked'])
        self.assertEqual(engine.device.actions, ['enable_auto'])
        engine.device.image = self.image('off')
        engine.tick()
        self.assertEqual(engine.device.actions, ['enable_auto', 'enable_auto'])
        self.assertEqual(engine.data['counts']['started'], 0)
        self.assertEqual(engine.data['counts']['completed'], 0)

    def test_on_battle_never_toggles_auto_off(self):
        engine = self.engine('on')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertTrue(engine.data['battle_auto_enabled'])
        self.assertIsNone(engine.data.get('pending'))

    def test_both_observations_must_agree_on_auto_state(self):
        for first, second in [('off', 'on'), ('on', 'off')]:
            with self.subTest(first=first, second=second):
                engine = self.engine()
                observations = []
                for screen in (first, second):
                    frame = self.image(screen)
                    name, spec, scores = engine.detector.classify(frame)
                    observations.append((frame, name, spec, scores))
                with patch.object(engine, 'observe', side_effect=observations):
                    engine.tick()
                self.assertEqual(engine.device.actions, [])
                self.assertIsNone(engine.data.get('pending'))

    def test_dimmed_or_missing_battle_guards_never_enable(self):
        engine = self.engine()
        frame = self.image('off')
        _, spec, _ = engine.detector.classify(frame)
        altered = [(frame.astype(float) * .5).astype(frame.dtype)]
        for anchor in spec['anchors']:
            changed = frame.copy()
            x1, y1, x2, y2 = anchor['region']
            changed[y1:y2, x1:x2] = 0
            altered.append(changed)
        for index, changed in enumerate(altered):
            with self.subTest(guard=index):
                self.assertEqual(engine.detector.classify(changed)[0], 'UNKNOWN')
                engine.device.image = changed
                engine.tick()
                self.assertEqual(engine.device.actions, [])
                self.assertIsNone(engine.data.get('pending'))

    def test_observe_only_and_stop_do_not_enable(self):
        engine = self.engine()
        engine.tick(act=False)
        self.assertEqual(engine.device.actions, [])
        self.assertIsNone(engine.data.get('pending'))
        farm.STOP.write_text('stop', encoding='utf-8')
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertIsNone(engine.data.get('pending'))

    def test_unverified_session_does_not_enable(self):
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': False})
        engine = self.engine()
        engine.tick()
        self.assertFalse(engine.mission_verified)
        self.assertEqual(engine.device.actions, [])
        self.assertIsNone(engine.data.get('pending'))

    def test_universal_auto_action_is_refused_for_other_stage(self):
        engine = self.engine()
        engine.stage = farm.load_stage('ninja_road', engine.target)
        # Even a mismatched catalog cannot authorize Universal battle input.
        with self.assertRaisesRegex(RuntimeError, 'Battle input prohibited'):
            engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertIsNone(engine.data.get('pending'))

    def test_pending_enable_survives_restart_and_unknown_without_replay(self):
        engine = self.engine()
        engine.tick()
        pending = copy.deepcopy(engine.data['pending'])
        resumed = self.engine()
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending'], pending)
        resumed.device.image[:] = 0
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending'], pending)
        resumed.device.image = self.image('on')
        resumed.tick()
        self.assertIsNone(resumed.data['pending'])
        self.assertEqual(resumed.device.actions, [])

    def test_completion_confirms_enable_and_advances_once(self):
        engine = self.engine()
        engine.data['run'] = {'id': 'active-run', 'start': 0, 'outcome': None}
        engine.tick()
        engine.device.image = self.image('complete')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['enable_auto', 'advance_completion'])
        self.assertEqual(engine.data['counts']['completed'], 1)
        self.assertEqual(engine.data['pending']['id'], 'advance_completion')

    def test_full_restart_session_recovers_matching_active_run_and_auto_latch(self):
        engine = self.engine()
        active_run = {'id': 'active-run', 'start': 100, 'outcome': None}
        engine.data['run'] = copy.deepcopy(active_run)
        engine.tick()
        pending = copy.deepcopy(engine.data['pending'])
        # Full Restart preserves work and clears only the session verification.
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': False})
        resumed = self.engine()
        self.assertFalse(resumed.mission_verified)
        resumed.tick()
        self.assertTrue(resumed.mission_verified)
        self.assertEqual(resumed.data['pending'], pending)
        self.assertEqual(resumed.data['run'], active_run)
        self.assertEqual(resumed.device.actions, [])
        resumed.device.image = self.image('on')
        resumed.tick()
        self.assertIsNone(resumed.data['pending'])
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['counts']['started'], 0)

    def test_full_restart_does_not_recover_unrelated_pending_in_battle(self):
        engine = self.engine()
        engine.data['run'] = {'id': 'active-run', 'start': 100, 'outcome': None}
        engine.tick()
        pending = copy.deepcopy(engine.data['pending'])
        pending['id'] = 'next'
        engine.data['pending'] = copy.deepcopy(pending)
        engine.save()
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': False})
        resumed = self.engine()
        resumed.tick()
        self.assertFalse(resumed.mission_verified)
        self.assertEqual(resumed.data['pending'], pending)
        self.assertEqual(resumed.device.actions, [])

    def test_uncertain_input_keeps_latch_and_is_not_replayed(self):
        engine = self.engine()
        with patch.object(engine.device, 'perform', side_effect=RuntimeError('ADB timeout')):
            with self.assertRaisesRegex(RuntimeError, 'ADB timeout'):
                engine.tick()
        pending = copy.deepcopy(engine.data['pending'])
        self.assertEqual(pending['id'], 'enable_auto')
        resumed = self.engine()
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending'], pending)

    def test_not_dispatched_input_can_retry_after_fresh_off_observations(self):
        engine = self.engine()
        with patch.object(engine.device, 'perform', side_effect=farm.ActionNotDispatched('not sent')):
            with self.assertRaisesRegex(farm.ActionNotDispatched, 'not sent'):
                engine.tick()
        self.assertIsNone(engine.data['pending'])
        engine.tick()
        self.assertEqual(engine.device.actions, ['enable_auto'])


if __name__ == '__main__':
    unittest.main()
