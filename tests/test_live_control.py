"""Offline development-control safeguards. All processes, devices and runtime are isolated."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

import farm
import loot_tracking as loot
from test_loot_tracking import FakeRecognizer, result, reward
from test_stages import FakeDevice
from instance_fixtures import legacy_target


class DevelopmentRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-control-test-')
        self.runtime = Path(self.temp.name)
        self.stop = self.runtime / 'STOP'
        self.latch = self.runtime / 'session.json'
        self.latch.write_text('{"stage":"universal","pending":{"id":"retry"},"mission_verified":true}')
        self.before_latch = self.latch.read_bytes()
        self.patches = [patch.object(farm, 'RUNTIME', self.runtime),
                        patch.object(farm, 'selected_instance', return_value=legacy_target()),
                        patch.object(farm, 'validate_mapping'),
                        patch.object(farm, 'Device', return_value=FakeDevice()),
                        patch.object(farm, 'STOP', self.stop),
                        patch.object(farm, 'event'),
                        patch.object(farm, 'record_alive', side_effect=lambda record, key='pid': farm.alive(record.get(key))),
                        patch.object(farm, 'live_controller_pids', return_value=[]),
                        patch.object(farm, 'lock_busy', return_value=False),
                        patch.object(farm, 'selected_stage', return_value='universal')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_changed_stop_rejects_restart_before_launch_or_latch_mutation(self):
        self.stop.write_text('Intentional user stop\n', encoding='utf-8')
        before_stop = self.stop.read_bytes()
        with patch.object(farm.subprocess, 'run') as launch, \
             patch.object(farm, 'write_json') as write:
            with self.assertRaises(InterruptedError):
                farm.start('universal', expected_stop='development-owned-stop')
        launch.assert_not_called()
        write.assert_not_called()
        self.assertEqual(self.stop.read_bytes(), before_stop)
        self.assertEqual(self.latch.read_bytes(), self.before_latch)

    def test_missing_stop_rejects_restart_without_creating_stop_or_mutating_latch(self):
        with patch.object(farm.subprocess, 'run') as launch, \
             patch.object(farm, 'write_json') as write:
            with self.assertRaises(InterruptedError):
                farm.start('universal', expected_stop='development-owned-stop')
        launch.assert_not_called()
        write.assert_not_called()
        self.assertFalse(self.stop.exists())
        self.assertEqual(self.latch.read_bytes(), self.before_latch)

    def test_stop_token_is_checked_after_control_lock_is_acquired(self):
        self.stop.write_text('development-owned-stop', encoding='utf-8')
        locks = []

        @contextmanager
        def changed_during_lock(name):
            locks.append(name)
            self.stop.write_text('New user Stop request', encoding='utf-8')
            yield

        with patch.object(farm, 'lock', side_effect=changed_during_lock), \
             patch.object(farm.subprocess, 'run') as launch, \
             patch.object(farm, 'write_json') as write:
            with self.assertRaises(InterruptedError):
                farm.start('universal', expected_stop='development-owned-stop')
        self.assertEqual(locks, ['control'])
        launch.assert_not_called()
        write.assert_not_called()
        self.assertEqual(self.stop.read_text('utf-8'), 'New user Stop request')
        self.assertEqual(self.latch.read_bytes(), self.before_latch)

    def test_normal_start_default_still_clears_stop_and_launches_once(self):
        self.stop.write_text('Ordinary prior Stop request', encoding='utf-8')

        def launch(*args, **kwargs):
            farm.write_json(self.runtime / 'supervisor.json',
                {'pid': 9001, 'worker_pid': 9002, 'stage': 'universal'})
            return subprocess.CompletedProcess(args, 0,
                json.dumps({'ReturnValue': 0, 'ProcessId': 9001}), '')

        with patch.object(farm, 'alive', side_effect=lambda pid: pid in (9001, 9002)), \
             patch.object(farm.subprocess, 'run', side_effect=launch) as mocked_launch:
            farm.start('universal')
        mocked_launch.assert_called_once()
        self.assertFalse(self.stop.exists())
        self.assertEqual(self.latch.read_bytes(), self.before_latch)
        self.assertEqual(farm.read_json(self.runtime / 'selection.json')['stage'], 'universal')


class ValidationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-validation-test-')
        self.root = Path(self.temp.name)
        self.collectors = []
        self.frame = np.full((32, 32, 3), (40, 70, 110), dtype=np.uint8)

    def tearDown(self):
        for collector in self.collectors:
            collector.close()
        self.temp.cleanup()

    def collector(self, directory, limit=1):
        recognizer = FakeRecognizer(result([reward()], status='complete'))
        with patch.dict(os.environ, {'NBR_LOOT_VALIDATION_DIR': str(directory),
                                    'NBR_LOOT_VALIDATION_MAX_RUNS': str(limit)}):
            collector = loot.Collector(self.root, 'universal', 'fixture:validation',
                recognizer_factory=lambda: recognizer)
        self.collectors.append(collector)
        return collector

    def observe(self, collector, run):
        completion_id = collector.completed(run)
        self.assertIsNotNone(completion_id)
        self.assertTrue(collector.evidence(run, self.frame))
        self.assertTrue(collector.wait_idle(10))
        return completion_id

    def test_bounded_development_copies_do_not_change_history_or_production_cleanup(self):
        directory = self.root / 'runtime' / 'validation' / 'live-recognition'
        collector = self.collector(directory)
        first = self.observe(collector, 'first')
        second = self.observe(collector, 'second')
        self.assertEqual([p.name for p in directory.glob('*.png')], [first + '.png'])
        self.assertEqual([p.name for p in directory.glob('*.json')], [first + '.json'])
        self.assertFalse((directory / (second + '.png')).exists())
        saved = json.loads((directory / (first + '.json')).read_text('utf-8'))
        self.assertEqual(saved['completion_id'], first)
        self.assertEqual(saved['recognition_status'], 'recognized')
        self.assertEqual(saved['recognition_result']['rewards'][0]['item_id'], 'fixture-scroll')
        data = loot.LootStore(self.root).dashboard(collector.session_id)
        self.assertEqual(data['completed_runs'], 2)
        self.assertEqual(data['recognized_icons'], 2)
        self.assertEqual(len(data['runs']), 2)
        self.assertTrue(all(run['screenshot_path'] is None for run in data['runs']))
        self.assertEqual(list((self.root / 'runtime' / 'loot').rglob('*.png')), [])
        self.assertEqual(data['items'][0]['confirmed_acquired'], 2)
        self.assertEqual(data['total_drops'], 2)

    def test_non_development_path_is_ignored_without_losing_session_record(self):
        # Prefix resemblance must not pass the resolved-parent ownership check.
        directory = self.root / 'runtime' / 'validation-neighbor' / 'live-recognition'
        collector = self.collector(directory)
        self.assertIsNone(collector.validation_directory)
        self.observe(collector, 'first')
        self.assertFalse(directory.exists())
        data = loot.LootStore(self.root).dashboard(collector.session_id)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recognized_icons'], 1)
        self.assertEqual(list((self.root / 'runtime' / 'loot').rglob('*.png')), [])

    def marker(self, directory, expires_at):
        marker = self.root / 'runtime' / 'validation' / 'validation_capture.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'enabled': True, 'directory': str(directory),
            'max_runs': 1, 'expires_at': expires_at}), encoding='utf-8')
        return marker

    def test_project_local_marker_captures_one_run_and_preserves_both_records(self):
        directory = self.root / 'runtime' / 'validation' / 'marker-evidence'
        marker = self.marker(directory, time.time() + 600)
        before = marker.read_bytes()
        # Empty environment forces the independent-launch marker path.
        collector = self.collector('')
        first = self.observe(collector, 'first')
        self.observe(collector, 'second')
        self.assertEqual(collector.validation_directory, directory.resolve())
        self.assertEqual([p.name for p in directory.glob('*.png')], [first + '.png'])
        data = loot.LootStore(self.root).dashboard(collector.session_id)
        self.assertEqual(data['completed_runs'], 2)
        self.assertEqual(data['recognized_icons'], 2)
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(list((self.root / 'runtime' / 'loot').rglob('*.png')), [])

    def test_expired_project_local_marker_does_not_capture_or_lose_record(self):
        directory = self.root / 'runtime' / 'validation' / 'expired-evidence'
        marker = self.marker(directory, time.time() - 1)
        before = marker.read_bytes()
        collector = self.collector('')
        self.assertIsNone(collector.validation_directory)
        self.observe(collector, 'first')
        self.assertFalse(directory.exists())
        self.assertEqual(marker.read_bytes(), before)
        data = loot.LootStore(self.root).dashboard(collector.session_id)
        self.assertEqual(data['completed_runs'], 1)
        self.assertEqual(data['recognized_icons'], 1)


if __name__ == '__main__':
    unittest.main()
