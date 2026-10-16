"""Offline controller ownership checks. All devices are fake; never sends ADB input."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import cv2

import farm
from instance_fixtures import legacy_target


class FakeDevice:
    def __init__(self, image=None):
        self.image = image
        self.actions = []
        self.commands = []

    def connect(self): pass
    def foreground(self): return farm.PACKAGE
    def capture(self): return self.image.copy()
    def perform(self, action): self.actions.append(action['id'])
    def verify(self): pass
    def command(self, args): self.commands.append(args)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'selected_instance', return_value=legacy_target()),
                        patch.object(farm, 'validate_mapping'),
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value=''),
                        patch.object(farm, 'record_alive', side_effect=lambda record, key='pid': farm.alive(record.get(key)))]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def image(self, path):
        image = cv2.imread(str(farm.ROOT / path))
        self.assertIsNotNone(image, path)
        return image

    def engine(self, stage, path):
        device = FakeDevice(self.image(path))
        with patch.object(farm, 'Device', return_value=device):
            return farm.Engine(stage)


    def test_only_shared_automation_is_supported(self):
        self.assertEqual(farm.STAGE_IDS, ('universal',))
        self.assertEqual(farm.load_stage()['progress'], self.runtime / 'universal.json')
        for mission in ('scrolls-a', 'rich-b', 'practice-part-1', 'night-of-tragedy-a', '../universal'):
            with self.subTest(mission=mission), self.assertRaises(ValueError):
                farm.load_stage(mission)
        farm.write_json(self.runtime / 'selection.json', {'stage': 'retired-choice'})
        self.assertEqual(farm.selected_stage(), 'universal')
        self.assertEqual(farm.read_json(self.runtime / 'selection.json')['stage'], 'retired-choice')

    def test_worker_lock_rejects_second_controller(self):
        for lock_name in ('worker', 'supervisor'):
            with self.subTest(lock=lock_name), farm.lock(lock_name):
                self.assertTrue(farm.lock_busy(lock_name))
                with self.assertRaises(RuntimeError):
                    with farm.lock(lock_name): pass
                with patch.object(farm.subprocess, 'run') as launch:
                    with self.assertRaises(RuntimeError):
                        farm.start('universal')
                    launch.assert_not_called()
            self.assertFalse(farm.lock_busy(lock_name))


    def test_concurrent_starts_launch_exactly_one_controller(self):
        barrier = threading.Barrier(2)
        launches = []
        read_json = farm.read_json

        def fake_read(path, default=None):
            value = read_json(path, default)
            if Path(path).name == 'supervisor.json' and value.get('launching'):
                value['worker_pid'] = 9002
            return value

        def fake_launch(*args, **kwargs):
            command = kwargs['env']['NBR_LAUNCH_COMMAND']
            stage = 'universal'
            launches.append(stage)
            farm.write_json(self.runtime / 'supervisor.json',
                            {'pid': 9001, 'worker_pid': 9002, 'stage': stage})
            return subprocess.CompletedProcess(args, 0,
                                               json.dumps({'ReturnValue': 0, 'ProcessId': 9001}), '')

        def attempt(stage):
            barrier.wait(timeout=5)
            try:
                farm.start(stage)
                return 'started'
            except RuntimeError:
                return 'blocked'

        with patch.object(farm, 'alive', side_effect=lambda pid: pid in (9001, 9002)), \
             patch.object(farm, 'Device', return_value=FakeDevice()), \
             patch.object(farm, 'read_json', side_effect=fake_read), \
             patch.object(farm.subprocess, 'run', side_effect=fake_launch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, ('universal', 'universal')))
        self.assertIn('started', results)
        self.assertTrue(set(results) <= {'started', 'blocked'})
        self.assertEqual(len(launches), 1)
        self.assertEqual(farm.read_json(self.runtime / 'supervisor.json')['stage'], launches[0])

    def test_supervisor_launch_excludes_only_own_pid_or_matching_venv_parent(self):
        cases = [({'pid': 100, 'stage': 'universal', 'launching': True}, set()),
                 ({'pid': 101, 'stage': 'universal', 'launching': True}, set()),
                 ({'pid': 102, 'stage': 'universal', 'launching': True}, {102}),
                 ({'pid': 101, 'stage': 'incompatible', 'launching': True}, {101}),
                 ({'pid': 101, 'stage': 'universal', 'launching': False}, {101})]
        with patch.object(farm.os, 'getpid', return_value=100), \
             patch.object(farm.os, 'getppid', return_value=101), \
             patch.object(farm, 'alive', return_value=True):
            for claim, blocked in cases:
                with self.subTest(claim=claim):
                    farm.write_json(self.runtime / 'supervisor.json', claim)
                    exclusions = farm.controller_exclusions('universal', launching_supervisor=True)
                    self.assertIn(100, exclusions)
                    self.assertEqual(set(farm.live_controller_pids(exclude=exclusions)), blocked)

    def test_worker_venv_parent_exclusion_requires_matching_supervisor_and_stage(self):
        cases = [({'pid': 200, 'worker_pid': 101, 'stage': 'universal'}, 200, set()),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'universal'}, 201, {200, 101}),
                 ({'pid': 200, 'worker_pid': 102, 'stage': 'universal'}, 200, {102}),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'incompatible'}, 200, {200, 101}),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'universal'}, None, {200, 101})]
        with patch.object(farm.os, 'getpid', return_value=100), \
             patch.object(farm.os, 'getppid', return_value=101), \
             patch.object(farm, 'alive', return_value=True):
            for claim, supervisor_pid, blocked in cases:
                with self.subTest(claim=claim, supervisor_pid=supervisor_pid):
                    farm.write_json(self.runtime / 'supervisor.json', claim)
                    exclusions = farm.controller_exclusions('universal', supervisor_pid)
                    self.assertEqual(set(farm.live_controller_pids(exclude=exclusions)), blocked)


if __name__ == '__main__': unittest.main(verbosity=2)
