"""Offline stamina timing checks; fake screens/devices never send ADB input."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


class StaminaTimerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.now = 100.0
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm.time, 'time', side_effect=lambda: self.now),
                        patch.object(farm, 'ocr_text', return_value='')]
        for item in self.patches:
            item.start()
        self.frame = farm.np.zeros((1600, 900, 3), dtype=farm.np.uint8)
        self.screen = 'STAMINA_PROMPT'
        self.specs = {
            'STAMINA_PROMPT': {'action': {'id': 'dismiss_stamina', 'kind': 'tap',
                                        'point': [300, 1017], 'expected': ['READY_TO_RETRY']}},
            'READY_TO_RETRY': {'action': {'id': 'retry', 'kind': 'tap',
                                        'point': [450, 1524], 'expected': ['READY_TO_START']}},
        }
        farm.write_json(self.runtime / 'session.json', {'stage': 'universal', 'mission_verified': True})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def engine(self):
        device = FakeDevice(self.frame)
        with patch.object(farm, 'Device', return_value=device):
            engine = farm.Engine('universal')
        engine.observe = lambda: (self.frame, self.screen, copy.deepcopy(self.specs.get(self.screen)), {})
        return engine

    def confirmed_no(self):
        engine = self.engine()
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.now = 105.0
        self.screen = 'READY_TO_RETRY'
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['stamina_until'], self.now + 600)
        return engine

    def test_no_retry_at_599_and_one_retry_at_600_after_confirmed_return(self):
        engine = self.confirmed_no()
        deadline = engine.data['stamina_until']
        self.now = deadline - 1
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.now = deadline
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina', 'retry'])
        self.assertEqual(engine.data['pending']['id'], 'retry')

    def test_deadline_survives_stop_restart_without_being_extended_each_observation(self):
        engine = self.confirmed_no()
        deadline = engine.data['stamina_until']
        farm.STOP.write_text('stop')
        self.now = deadline - 1
        resumed = self.engine()
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['stamina_until'], deadline)
        farm.STOP.unlink()
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['stamina_until'], deadline)
        self.now = deadline
        resumed.tick()
        self.assertEqual(resumed.device.actions, ['retry'])

    def test_provisional_deadline_and_latch_are_saved_before_no_input(self):
        engine = self.engine()
        before_input = []
        def inspect_saved(action):
            before_input.append(farm.read_json(self.runtime / 'universal.json'))
            engine.device.actions.append(action['id'])
        engine.device.perform = inspect_saved
        engine.tick()
        self.assertEqual(before_input[0]['pending']['id'], 'dismiss_stamina')
        self.assertEqual(before_input[0]['stamina_until'], 700)
        self.now = 1000
        resumed = self.engine()
        resumed.tick()
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending']['id'], 'dismiss_stamina')
        self.screen = 'READY_TO_RETRY'
        resumed.tick()
        self.assertEqual(resumed.data['stamina_until'], 1600)
        self.assertEqual(resumed.device.actions, [])

    def test_new_no_is_permitted_during_an_existing_wait(self):
        engine = self.engine()
        engine.data['stamina_until'] = 500
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.assertEqual(engine.data['pending']['source'], 'STAMINA_PROMPT')
        self.assertEqual(engine.data['stamina_until'], 700)

    def test_old_pending_input_is_preserved_when_stamina_dialog_appears(self):
        engine = self.engine()
        original = {'id': 'retry', 'source': 'READY_TO_RETRY', 'expected': ['READY_TO_START'],
                    'time': 1, 'run_id': 'existing-input'}
        engine.data['pending'] = copy.deepcopy(original)
        engine.data['stamina_until'] = 500
        engine.tick()
        engine.tick()
        self.assertEqual(engine.data['pending'], original)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data['stamina_until'], 500)


if __name__ == '__main__':
    unittest.main(verbosity=2)
