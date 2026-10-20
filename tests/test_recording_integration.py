"""Real durable collector plus Universal engine, with a fake device/recognizer."""
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import farm
import loot_tracking as loot
from instance_fixtures import legacy_target
from test_loot_tracking import FakeRecognizer
from test_stages import FakeDevice


class RecordingIntegrationTests(unittest.TestCase):
    def test_restart_captures_original_device_run_and_continues_during_recognition(self):
        with tempfile.TemporaryDirectory(prefix='nbr-recording-integration-') as temporary:
            root = Path(temporary)
            runtime = root / 'runtime'
            target = legacy_target()
            entered, release = threading.Event(), threading.Event()
            fake = FakeRecognizer(entered=entered, release=release)
            first = loot.Collector(root, 'universal', 'fixture:original-process',
                                   device_id=target.android_id, recognizer_factory=lambda: fake)
            resumed_collector = None
            try:
                with patch.object(farm, 'RUNTIME', runtime), patch.object(farm, 'STOP', runtime / 'STOP'), \
                     patch.object(farm, 'event'), patch.object(farm, 'validate_mapping'), \
                     patch.object(farm, 'time', SimpleNamespace(time=time.time, monotonic=time.monotonic,
                                                               sleep=lambda seconds: None)), \
                     patch('item_recognition.reward_layout.has_unrevealed_rewards', return_value=False):
                    def engine(filename, collector):
                        frame = farm.cv2.imread(str(farm.ROOT / 'tests' / 'fixtures' / filename))
                        self.assertIsNotNone(frame)
                        device = FakeDevice(frame)
                        device.target = target
                        with patch.object(farm, 'Device', return_value=device):
                            value = farm.Engine('universal', target)
                        value.loot_collector = collector
                        return value

                    original = engine('rich_b/ready_to_start.png', first)
                    original.tick()
                    original.device.image = farm.cv2.imread(str(farm.ROOT / 'tests/fixtures/battle.png'))
                    original.tick()
                    original.device.image = farm.cv2.imread(str(farm.ROOT / 'tests/fixtures/mission_complete.png'))
                    original.tick()
                    run = farm.read_json(runtime / 'universal.json')['run']
                    self.assertEqual(run['recording']['device_id'], target.android_id)
                    self.assertEqual(run['recording']['state'], 'screenshot_capture_pending')
                    first.close()
                    farm.write_json(runtime / 'session.json', {'stage': 'universal', 'mission_verified': False})
                    resumed_collector = loot.Collector(root, 'universal', 'fixture:restarted-process',
                        device_id=target.android_id, recognizer_factory=lambda: fake)
                    resumed = engine('rich_b/ready_to_retry.png', resumed_collector)
                    resumed.tick()
                    self.assertEqual(resumed.device.actions, ['retry'])
                    self.assertEqual(resumed.data['run']['id'], run['id'])
                    self.assertEqual(resumed.data['run']['recording']['completed_at'], run['recording']['completed_at'])
                    self.assertEqual(resumed.data['run']['recording']['completion_id'], run['recording']['completion_id'])
                    self.assertEqual(resumed.data['run']['recording']['state'], 'screenshot_saved')
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(release.is_set())
                    self.assertEqual(resumed.data['counts']['completed'], 1)
                    release.set()
                    self.assertTrue(resumed_collector.wait_idle(10))
                    data = loot.LootStore(root).dashboard(first.session_id)
                    self.assertEqual(data['completed_runs'], 1)
                    self.assertEqual(data['recognized_icons'], 1)
                    self.assertEqual(loot.LootStore(root).dashboard(resumed_collector.session_id)['completed_runs'], 0)
            finally:
                release.set()
                first.close()
                if resumed_collector:
                    resumed_collector.close()


if __name__ == '__main__':
    unittest.main()
