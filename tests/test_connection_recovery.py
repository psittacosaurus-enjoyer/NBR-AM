"""Offline recovery checks; every device is fake and never sends ADB input."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


class ConnectionRecoveryTests(unittest.TestCase):
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
        self.assertIsNotNone(image)
        return image

    def engine(self, path, stage='universal', verified=True):
        farm.write_json(self.runtime / 'session.json',
                        {'stage': stage, 'mission_verified': verified})
        device = FakeDevice(self.image(path))
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine(stage)
        engine.data['pending'] = {'id': 'retry_connection', 'source': 'COMMUNICATION_ERROR',
                                  'expected': ['TITLE_START', 'HOME_MENU', 'LOADING'],
                                  'time': 0, 'run_id': 'connection-attempt'}
        engine.data['run'] = {'id': 'old-completed-run', 'start': 0, 'outcome': 'completed'}
        return engine

    def test_verified_completion_resolves_connection_once_and_tracks_recovered_run(self):
        for stage in farm.STAGE_IDS:
            with self.subTest(stage=stage):
                engine = self.engine('tests/fixtures/mission_complete.png', stage)
                engine.tick()
                engine.tick()
                self.assertEqual(engine.device.actions, ['advance_completion'])
                self.assertEqual(engine.device.commands, [])
                self.assertEqual(engine.data['counts']['recovered_missions'], 1)
                self.assertEqual(engine.data['counts']['completed'], 1)
                self.assertEqual(engine.data['run']['origin'], 'observed_after_connection')
                self.assertEqual(engine.data['run']['duration_basis'], 'since_first_observation')
                self.assertEqual(engine.data['pending']['id'], 'advance_completion')

    def test_verified_battle_resolves_without_input_then_completes_once(self):
        engine = self.engine('tests/fixtures/battle.png')
        engine.tick()
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data['counts']['recovered_missions'], 1)
        self.assertEqual(engine.data['run']['origin'], 'observed_after_connection')
        engine.device.image = self.image('tests/fixtures/mission_complete.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['advance_completion'])
        self.assertEqual(engine.data['counts']['completed'], 1)

    def test_incomplete_run_is_preserved_when_connection_returns_to_battle(self):
        engine = self.engine('tests/fixtures/battle.png')
        active_run = {'id': 'current-run', 'start': 42, 'outcome': None}
        engine.data['run'] = copy.deepcopy(active_run)
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['run'], active_run)
        self.assertEqual(engine.data['counts'].get('recovered_missions', 0), 0)
        self.assertEqual(engine.device.actions, [])

    def test_unverified_or_mismatched_session_keeps_connection_latched(self):
        for verified, session_stage in ((False, 'universal'), (True, 'different-session')):
            with self.subTest(verified=verified, session_stage=session_stage):
                engine = self.engine('tests/fixtures/mission_complete.png', verified=verified)
                farm.write_json(self.runtime / 'session.json',
                                {'stage': session_stage, 'mission_verified': verified})
                original = copy.deepcopy(engine.data['pending'])
                engine.tick()
                self.assertEqual(engine.data['pending'], original)
                self.assertEqual(engine.device.actions, [])
                self.assertEqual(engine.data['counts']['completed'], 0)

    def test_wrong_action_or_source_cannot_use_connection_recovery(self):
        for action, source in (('advance_completion', 'COMMUNICATION_ERROR'),
                               ('retry_connection', 'HOME_MENU')):
            with self.subTest(action=action, source=source):
                engine = self.engine('tests/fixtures/battle.png')
                engine.data['pending'].update(id=action, source=source)
                original = copy.deepcopy(engine.data['pending'])
                engine.tick()
                self.assertEqual(engine.data['pending'], original)
                self.assertEqual(engine.device.actions, [])

    def test_unchanged_connection_dialog_never_receives_repeated_retry(self):
        engine = self.engine('tests/fixtures/communication_error.png')
        original = copy.deepcopy(engine.data['pending'])
        engine.tick()
        engine.tick()
        self.assertEqual(engine.data['pending'], original)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.device.commands, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
