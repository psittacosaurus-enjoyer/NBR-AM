"""Verified maintenance rejection recovery using saved frames and fake devices."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import farm
from test_stages import FakeDevice


class MaintenanceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value='')]
        for item in self.patches:
            item.start()
        self.detector = farm.Detector('universal')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def image(self, name):
        paths = {'start': 'tests/fixtures/rich_b/ready_to_start.png',
                 'retry': 'tests/fixtures/rich_b/ready_to_retry.png',
                 'maintenance': 'tests/fixtures/universal/maintenance_prompt.png',
                 'battle': 'tests/fixtures/battle.png',
                 'auto_off': 'tests/fixtures/universal/battle_auto_off.png'}
        frame = farm.cv2.imread(str(farm.ROOT / paths[name]))
        self.assertIsNotNone(frame)
        return frame

    def engine(self, name):
        device = FakeDevice(self.image(name))
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine('universal')

    def pending(self, action='start_mission', source='READY_TO_START'):
        return dict(id=action, source=source,
                    expected=['IN_BATTLE', 'MISSION_COMPLETE', 'MAINTENANCE_PROMPT'],
                    time=0, run_id='rejected-attempt')

    def test_start_rejected_once_acknowledges_and_waits_before_new_attempt(self):
        engine = self.engine('start')
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
            original = copy.deepcopy(engine.data['pending'])
            engine.device.image = self.image('maintenance')
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission', 'dismiss_known_popup'])
        self.assertEqual(engine.data['pending']['source'], 'MAINTENANCE_PROMPT')
        self.assertEqual(engine.data['maintenance_rejection']['run_id'], original['run_id'])
        self.assertEqual(engine.data['counts']['started'], 0)
        self.assertEqual(engine.data['counts']['completed'], 0)
        self.assertNotIn('run', engine.data)
        engine.device.image = self.image('start')
        with patch.object(farm.time, 'time', return_value=1002):
            engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['maintenance_until'], 1062)
        self.assertIn('server maintenance', engine.data['blocked'])
        with patch.object(farm.time, 'time', return_value=1061.99):
            engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission', 'dismiss_known_popup'])
        with patch.object(farm.time, 'time', return_value=1062):
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions,
                         ['start_mission', 'dismiss_known_popup', 'start_mission'])
        self.assertEqual(engine.data['counts']['started'], 0)
        engine.device.image = self.image('battle')
        with patch.object(farm.time, 'time', return_value=1064):
            engine.tick()
        self.assertEqual(engine.data['counts']['started'], 1)
        self.assertEqual(engine.data['counts']['completed'], 0)
        self.assertNotEqual(engine.data['run']['id'], original['run_id'])

    def test_restart_with_maintenance_preserves_verification_until_start_returns(self):
        farm.write_json(self.runtime / 'universal.json', {'stage': 'universal',
                        'pending': self.pending()})
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': False})
        engine = self.engine('maintenance')
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
        self.assertFalse(engine.mission_verified)
        self.assertFalse(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        resumed = self.engine('maintenance')
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending']['source'], 'MAINTENANCE_PROMPT')
        resumed.device.image = self.image('start')
        with patch.object(farm.time, 'time', return_value=2000):
            resumed.tick()
        self.assertTrue(resumed.mission_verified)
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['maintenance_until'], 2060)
        self.assertEqual(resumed.data['counts']['started'], 0)

    def test_retry_rejection_preserves_completed_run_and_counts(self):
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': True})
        engine = self.engine('maintenance')
        engine.data['pending'] = self.pending('retry', 'READY_TO_RETRY')
        completed = dict(id='already-completed', start=0, outcome='completed')
        engine.data['run'] = copy.deepcopy(completed)
        engine.data['counts'].update(started=10, completed=10)
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
        self.assertEqual(engine.data['run'], completed)
        self.assertEqual(engine.data['counts']['started'], 10)
        self.assertEqual(engine.data['counts']['completed'], 10)

    def test_restart_acknowledgement_return_to_retry_clears_without_mission_authorization(self):
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': False})
        engine = self.engine('retry')
        engine.data['pending'] = dict(id='dismiss_known_popup', source='MAINTENANCE_PROMPT',
            expected=['READY_TO_START', 'READY_TO_RETRY'], time=0, run_id='notice')
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['maintenance_until'], 1060)
        self.assertEqual(engine.data['screen'], 'READY_TO_RETRY')
        self.assertFalse(engine.mission_verified)
        self.assertEqual(engine.device.actions, [])
        self.assertEqual(engine.data['counts']['started'], 0)
        with patch.object(farm.time, 'time', return_value=1061):
            engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertFalse(engine.mission_verified)
        self.assertEqual(engine.data['blocked'], 'Prepare any mission Start Mission screen, then Start')

    def test_auto_off_battle_confirms_mission_entry_and_reports_without_input(self):
        engine = self.engine('start')
        engine.tick()
        engine.device.image = self.image('auto_off')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.data['screen'], 'IN_BATTLE')
        self.assertFalse(engine.data['battle_auto_enabled'])
        self.assertEqual(engine.data['blocked'], 'Auto-play is off; enable Auto to continue')
        self.assertEqual(engine.data['counts']['started'], 1)
        self.assertEqual(engine.data['counts']['completed'], 0)
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.device.actions, ['start_mission'])
        engine.device.image = self.image('battle')
        engine.tick()
        self.assertTrue(engine.data['battle_auto_enabled'])
        self.assertIsNone(engine.data['blocked'])
        self.assertEqual(engine.data['counts']['started'], 1)
        self.assertEqual(engine.device.actions, ['start_mission'])

    def test_auto_off_battle_requires_both_exact_guards_and_has_no_action(self):
        frame = self.image('auto_off')
        name, spec, _ = self.detector.classify(frame)
        self.assertEqual(name, 'IN_BATTLE')
        self.assertFalse(spec['battle_auto_enabled'])
        self.assertIsNone(spec.get('action'))
        altered = [(frame.astype(float) * .5).astype(frame.dtype)]
        for anchor in spec['anchors']:
            changed = frame.copy()
            x1, y1, x2, y2 = anchor['region']
            changed[y1:y2, x1:x2] = 0
            altered.append(changed)
        for index, changed in enumerate(altered):
            with self.subTest(guard=index):
                self.assertEqual(self.detector.classify(changed)[0], 'UNKNOWN')

    def test_unrelated_pending_cannot_be_cleared_even_if_expected_lists_maintenance(self):
        farm.write_json(self.runtime / 'session.json',
                        {'stage': 'universal', 'mission_verified': True})
        for action, source in [('advance_completion', 'MISSION_COMPLETE'),
                               ('next', 'RESULTS_NEXT'), ('start_mission', 'HOME_MENU'),
                               ('retry', 'READY_TO_START')]:
            with self.subTest(action=action, source=source):
                engine = self.engine('maintenance')
                pending = self.pending(action, source)
                engine.data['pending'] = copy.deepcopy(pending)
                engine.tick()
                self.assertEqual(engine.data['pending'], pending)
                self.assertEqual(engine.device.actions, [])
                self.assertEqual(engine.data['counts']['started'], 0)
                self.assertNotIn('maintenance_rejection', engine.data)

    def test_same_source_old_pending_stays_latched(self):
        engine = self.engine('start')
        pending = self.pending()
        engine.data['pending'] = copy.deepcopy(pending)
        engine.tick()
        engine.tick()
        self.assertEqual(engine.data['pending'], pending)
        self.assertEqual(engine.device.actions, [])

    def test_maintenance_requires_each_exact_guard_and_undimmed_dialog(self):
        frame = self.image('maintenance')
        name, spec, _ = self.detector.classify(frame)
        self.assertEqual(name, 'MAINTENANCE_PROMPT')
        self.assertEqual(spec['action']['point'], [448, 996])
        altered = [(frame.astype(float) * .5).astype(frame.dtype)]
        for anchor in spec['anchors']:
            changed = frame.copy()
            x1, y1, x2, y2 = anchor['region']
            changed[y1:y2, x1:x2] = 0
            altered.append(changed)
        for index, changed in enumerate(altered):
            with self.subTest(guard=index):
                self.assertEqual(self.detector.classify(changed)[0], 'UNKNOWN')

    def test_unverified_dialog_without_matching_saved_entry_does_not_authorize_input(self):
        engine = self.engine('maintenance')
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        self.assertFalse(engine.mission_verified)
        self.assertNotIn('maintenance_rejection', engine.data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
