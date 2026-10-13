"""Offline multi-stage checks. All devices are fake; never sends ADB input."""
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
                        patch.object(farm, 'STOP', self.runtime / 'STOP'),
                        patch.object(farm, 'event'), patch.object(farm.time, 'sleep'),
                        patch.object(farm, 'ocr_text', return_value='')]
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

    def test_stage_configs_keep_original_catalog_and_progress(self):
        scrolls = farm.load_stage('scrolls-a')
        rich = farm.load_stage('rich-b')
        self.assertEqual(scrolls['catalog'], farm.ROOT / 'assets/patterns/catalog.json')
        self.assertEqual(scrolls['progress'], self.runtime / 'farm.json')
        self.assertEqual(rich['catalog'], farm.ROOT / 'assets/patterns/rich_b.json')
        self.assertEqual(rich['progress'], self.runtime / 'rich_b.json')
        self.assertEqual(rich['state_actions']['RANK_SELECTION'], 'select_rank_b')
        with self.assertRaises((ValueError, RuntimeError)):
            farm.load_stage('../rich-b')

    def test_each_rank_catalog_rejects_other_mission(self):
        scrolls = farm.Detector('scrolls-a')
        rich = farm.Detector('rich-b')
        image = self.image('tests/fixtures/rich_b/rank_selection.png')
        self.assertEqual(rich.classify(image)[0], 'RANK_SELECTION')
        self.assertEqual(scrolls.classify(image)[0], 'UNKNOWN')
        for path in ('assets/reference_images/starting_point.png', 'tests/fixtures/ready_to_start.png',
                     'tests/fixtures/results_next.png', 'tests/fixtures/reward_reveal.png'):
            self.assertNotEqual(scrolls.classify(self.image(path))[0], 'UNKNOWN', path)
            self.assertEqual(rich.classify(self.image(path))[0], 'UNKNOWN', path)

    def test_rich_rank_action_is_latched_and_separate_from_scrolls_progress(self):
        original = {'counts': {'started': 42, 'completed': 41, 'failed': 0, 'launches': 1},
                    'pending': {'id': 'retry', 'source': 'READY_TO_RETRY',
                                'expected': ['READY_TO_START'], 'time': 0},
                    'stamina_until': farm.time.time() + 900}
        farm.write_json(self.runtime / 'farm.json', original)
        engine = self.engine('rich-b', 'tests/fixtures/rich_b/rank_selection.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['select_rank_b'])
        self.assertEqual(farm.read_json(self.runtime / 'farm.json'), original)
        self.assertEqual(farm.read_json(self.runtime / 'rich_b.json')['pending']['id'], 'select_rank_b')
        resumed = self.engine('rich-b', 'tests/fixtures/rich_b/rank_selection.png')
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertEqual(resumed.data['pending']['id'], 'select_rank_b')

    def test_rich_two_complete_replay_cycles_reuse_existing_engine(self):
        image = self.image('tests/fixtures/rich_b/ready_to_start.png')
        self.assertEqual(farm.Detector('rich-b').classify(image)[0], 'READY_TO_START')
        self.assertEqual(farm.Detector('scrolls-a').classify(image)[0], 'UNKNOWN')
        engine = self.engine('rich-b', 'tests/fixtures/rich_b/ready_to_start.png')
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['start_mission'])
        self.assertTrue(engine.mission_verified)
        self.assertTrue(farm.read_json(self.runtime / 'session.json')['mission_verified'])
        expected_actions = ['start_mission']
        cycle = [('tests/fixtures/battle.png', 'IN_BATTLE', None),
                 ('tests/fixtures/mission_complete.png', 'MISSION_COMPLETE', 'advance_completion'),
                 ('tests/fixtures/rich_b/results_next.png', 'RESULTS_NEXT', 'next'),
                 ('tests/fixtures/rich_b/reward_reveal.png', 'REWARD_REVEAL', 'reveal_rewards'),
                 ('tests/fixtures/rich_b/ready_to_retry.png', 'READY_TO_RETRY', 'retry'),
                 ('tests/fixtures/rich_b/ready_to_start.png', 'READY_TO_START', 'start_mission')]
        for cycle_number in (1, 2):
            for path, expected_screen, action in cycle:
                with self.subTest(cycle=cycle_number, screen=expected_screen):
                    engine.device.image = self.image(path)
                    engine.tick()
                    engine.tick()
                    self.assertEqual(engine.data['screen'], expected_screen)
                    if action: expected_actions.append(action)
                    self.assertEqual(engine.device.actions, expected_actions)
            self.assertEqual(engine.data['counts']['started'], cycle_number)
            self.assertEqual(engine.data['counts']['completed'], cycle_number)
            self.assertEqual(engine.data['counts']['failed'], 0)

    def test_rich_titled_cycle_screens_reject_wrong_mission_title_and_dim_dialog(self):
        rich = farm.Detector('rich-b')
        scrolls = farm.Detector('scrolls-a')
        fixtures = [('ready_to_start', (214, 396, 722, 459)),
                    ('results_next', (206, 182, 709, 247)),
                    ('reward_reveal', (206, 182, 709, 247)),
                    ('ready_to_retry', (206, 182, 709, 247))]
        for filename, title in fixtures:
            with self.subTest(screen=filename):
                image = self.image('tests/fixtures/rich_b/' + filename + '.png')
                self.assertEqual(rich.classify(image)[0], filename.upper())
                self.assertEqual(scrolls.classify(image)[0], 'UNKNOWN')
                changed = image.copy()
                x1, y1, x2, y2 = title
                changed[y1:y2, x1:x2] = 0
                self.assertEqual(rich.classify(changed)[0], 'UNKNOWN')
                dimmed = (image.astype(float) * .5).astype(image.dtype)
                self.assertEqual(rich.classify(dimmed)[0], 'UNKNOWN')
                engine = self.engine('rich-b', 'tests/fixtures/rich_b/' + filename + '.png')
                engine.device.image = dimmed
                engine.tick()
                self.assertEqual(engine.device.actions, [])

    def test_rank_b_emblem_is_required_before_rank_selection(self):
        image = self.image('tests/fixtures/rich_b/rank_selection.png')
        image[634:764, 146:238] = 0
        engine = self.engine('rich-b', 'tests/fixtures/rich_b/rank_selection.png')
        engine.device.image = image
        engine.tick()
        self.assertEqual(engine.data['screen'], 'UNKNOWN')
        self.assertEqual(engine.device.actions, [])

    def test_live_stamina_prompt_only_no_and_guarded_return_waits(self):
        path = 'tests/fixtures/rich_b/not_enough_stamina.png'
        engine = self.engine('rich-b', path)
        image = engine.device.image.copy()
        name,spec,_ = engine.detector.classify(image)
        self.assertEqual(name, 'STAMINA_PROMPT')
        self.assertEqual(spec['action']['id'], 'dismiss_stamina')
        self.assertEqual(spec['action']['point'], [299,1018])
        self.assertEqual(farm.Detector('scrolls-a').classify(image)[0], 'UNKNOWN')
        engine.data['pending'] = {'id':'retry', 'source':'READY_TO_RETRY',
                                  'expected':['READY_TO_START','STAMINA_PROMPT'], 'time':0}
        with patch.object(farm.time, 'time', return_value=1000):
            engine.tick()
            engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        self.assertEqual(engine.data['pending']['source'], 'STAMINA_PROMPT')
        engine.device.image = self.image('tests/fixtures/rich_b/ready_to_retry.png')
        with patch.object(farm.time, 'time', return_value=1004):
            engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.data['stamina_until'], 1604)
        self.assertEqual(engine.device.actions, ['dismiss_stamina'])
        for region in ([121,510,778,586],[114,630,795,899],[200,982,399,1054],[206,182,709,247]):
            changed = image.copy()
            x1,y1,x2,y2 = region
            changed[y1:y2,x1:x2] = 0
            self.assertEqual(engine.detector.classify(changed)[0], 'UNKNOWN')

    def test_rich_free_rank_up_stamina_ack_closes_once_and_confirms_results(self):
        path = 'tests/fixtures/rich_b/rank_up_stamina_ack.png'
        image = self.image(path)
        detector = farm.Detector('rich-b')
        self.assertEqual(detector.classify(image)[0], 'RANK_UP_STAMINA_ACK')
        self.assertEqual(farm.Detector('scrolls-a').classify(image)[0], 'UNKNOWN')
        engine = self.engine('rich-b', path)
        engine.data['pending'] = {'id': 'advance_completion', 'source': 'MISSION_COMPLETE',
                                  'expected': ['RESULTS_NEXT'], 'time': 0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup'])
        self.assertEqual(engine.data['pending']['source'], 'RANK_UP_STAMINA_ACK')
        self.assertFalse(farm.STOP.exists())
        for region in ((182,695,722,779), (343,856,548,935), (206,182,709,247)):
            changed = image.copy()
            x1,y1,x2,y2 = region
            changed[y1:y2,x1:x2] = 0
            self.assertEqual(detector.classify(changed)[0], 'UNKNOWN')
        engine.device.image = self.image('tests/fixtures/rich_b/results_next.png')
        engine.tick()
        self.assertEqual(engine.device.actions, ['dismiss_known_popup', 'next'])
        self.assertFalse(farm.STOP.exists())

    def test_reward_footer_brightness_variant_resumes_next_latch_once(self):
        path = 'tests/fixtures/rich_b/reward_reveal_variant.png'
        engine = self.engine('rich-b', path)
        self.assertEqual(engine.detector.classify(engine.device.image)[0], 'REWARD_REVEAL')
        engine.data['pending'] = {'id': 'next', 'source': 'RESULTS_NEXT',
                                  'expected': ['REWARD_REVEAL', 'READY_TO_RETRY'], 'time': 0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ['reveal_rewards'])
        self.assertEqual(engine.data['pending']['source'], 'REWARD_REVEAL')
        retry = self.image('tests/fixtures/rich_b/ready_to_retry.png')
        self.assertEqual(engine.detector.classify(retry)[0], 'READY_TO_RETRY')
        engine.device.image = (engine.device.image.astype(float) * .5).astype(engine.device.image.dtype)
        engine.tick()
        self.assertEqual(engine.device.actions, ['reveal_rewards'])

    def test_unknown_mission_generic_completion_never_advances(self):
        for path in ('tests/fixtures/battle.png', 'tests/fixtures/mission_complete.png'):
            for persisted in ({}, {'run': {'id': 'old-run', 'start': 0, 'outcome': None},
                                    'pending': {'id': 'start_mission', 'source': 'READY_TO_START',
                                                'expected': ['IN_BATTLE', 'MISSION_COMPLETE'],
                                                'time': 0, 'run_id': 'old-run'}}):
                with self.subTest(path=path, persisted=bool(persisted)):
                    farm.write_json(self.runtime / 'rich_b.json', persisted)
                    engine = self.engine('rich-b', path)
                    engine.tick()
                    self.assertEqual(engine.device.actions, [])
                    self.assertEqual(engine.data['screen'], 'UNKNOWN')
                    self.assertFalse(getattr(engine, 'mission_verified', False))
                    self.assertEqual(engine.data['counts']['completed'], 0)

    def test_verified_rank_latch_recovers_resumed_battle_and_completion_once(self):
        for path in ('tests/fixtures/battle.png', 'tests/fixtures/rich_b/stuck_rank_completion.png'):
            with self.subTest(path=path):
                farm.write_json(self.runtime / 'session.json', {'stage':'rich-b', 'mission_verified':True})
                farm.write_json(self.runtime / 'rich_b.json', {
                    'run': {'id':'previous', 'start':0, 'outcome':'completed'},
                    'pending': {'id':'select_rank_b', 'source':'RANK_SELECTION',
                                'expected':['SOLO_SELECTION','HELPER_SELECTION','READY_TO_START'], 'time':0}})
                engine = self.engine('rich-b', path)
                engine.tick()
                engine.tick()
                self.assertEqual(engine.data['counts']['recovered_missions'], 1)
                self.assertEqual(engine.data['run']['origin'], 'observed_after_rank_selection')
                self.assertNotIn('select_rank_b', engine.device.actions)
                if path.endswith('battle.png'):
                    self.assertIsNone(engine.data['pending'])
                    self.assertEqual(engine.device.actions, [])
                    self.assertIsNone(engine.data['run']['outcome'])
                else:
                    self.assertEqual(engine.device.actions, ['advance_completion'])
                    self.assertEqual(engine.data['counts']['completed'], 1)
                    self.assertEqual(engine.data['pending']['id'], 'advance_completion')

    def test_rank_recovery_requires_matching_verified_session_and_exact_source(self):
        for verified in (False, True):
            for source in ('RANK_SELECTION', 'SCROLLS_BANNER'):
                if verified and source == 'RANK_SELECTION':
                    continue
                with self.subTest(verified=verified, source=source):
                    farm.write_json(self.runtime / 'session.json', {'stage':'rich-b', 'mission_verified':verified})
                    pending = {'id':'select_rank_b', 'source':source, 'expected':['READY_TO_START'], 'time':0}
                    farm.write_json(self.runtime / 'rich_b.json', {'pending':pending})
                    engine = self.engine('rich-b', 'tests/fixtures/rich_b/stuck_rank_completion.png')
                    engine.tick()
                    self.assertEqual(engine.device.actions, [])
                    self.assertEqual(engine.data['pending'], pending)
                    self.assertEqual(engine.data['counts']['completed'], 0)

    def test_global_stop_blocks_both_stage_engines(self):
        rich = self.engine('rich-b', 'tests/fixtures/rich_b/rank_selection.png')
        scrolls = self.engine('scrolls-a', 'assets/reference_images/starting_point.png')
        farm.STOP.write_text('stop', encoding='utf-8')
        rich.tick()
        scrolls.tick()
        self.assertEqual(rich.device.actions, [])
        self.assertEqual(scrolls.device.actions, [])

    def test_verified_session_survives_worker_restart_only_for_same_stage(self):
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})
        farm.write_json(self.runtime / 'rich_b.json',
                        {'run': {'id': 'verified-run', 'start': 0, 'outcome': None}})
        battle = self.engine('rich-b', 'tests/fixtures/battle.png')
        battle.tick()
        self.assertEqual(battle.data['screen'], 'IN_BATTLE')
        self.assertEqual(battle.device.actions, [])
        resumed = self.engine('rich-b', 'tests/fixtures/mission_complete.png')
        resumed.tick()
        resumed.tick()
        self.assertEqual(resumed.device.actions, ['advance_completion'])
        self.assertEqual(resumed.data['counts']['completed'], 1)
        farm.write_json(self.runtime / 'session.json', {'stage': 'scrolls-a', 'mission_verified': True})
        wrong_stage = self.engine('rich-b', 'tests/fixtures/mission_complete.png')
        wrong_stage.tick()
        self.assertEqual(wrong_stage.data['screen'], 'UNKNOWN')
        self.assertEqual(wrong_stage.device.actions, [])

    def test_stage_switch_invalidates_previous_generic_screen_authorization(self):
        farm.select_stage('rich-b')
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})
        farm.select_stage('scrolls-a')
        session = farm.read_json(self.runtime / 'session.json')
        self.assertEqual(session['stage'], 'scrolls-a')
        self.assertFalse(session['mission_verified'])
        farm.select_stage('rich-b')
        rich = self.engine('rich-b', 'tests/fixtures/mission_complete.png')
        rich.tick()
        self.assertEqual(rich.data['screen'], 'UNKNOWN')
        self.assertEqual(rich.device.actions, [])

    def test_switching_both_directions_preserves_old_latches_on_generic_screens(self):
        farm.select_stage('rich-b')
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})
        old_pending = {'id': 'retry', 'source': 'READY_TO_RETRY',
                       'expected': ['READY_TO_START'], 'time': 0}
        counts = {'started': 11, 'completed': 10, 'failed': 0, 'launches': 0}
        # Stale saved worker PIDs represent the explicitly stopped previous worker.
        with patch.object(farm, 'alive', return_value=False):
            for stage in ('scrolls-a', 'rich-b'):
                farm.select_stage(stage)
                progress = farm.load_stage(stage)['progress']
                for path in ('tests/fixtures/battle.png', 'tests/fixtures/mission_complete.png'):
                    with self.subTest(stage=stage, screen=path):
                        farm.write_json(progress, {'pending': old_pending, 'counts': counts,
                                                  'run': {'id': 'old', 'start': 0, 'outcome': 'completed'}})
                        engine = self.engine(stage, path)
                        engine.tick()
                        self.assertEqual(engine.data['screen'], 'UNKNOWN')
                        self.assertEqual(engine.device.actions, [])
                        self.assertEqual(engine.data['pending'], old_pending)
                        self.assertEqual(engine.data['counts'], counts)
                        self.assertFalse(engine.mission_verified)

    def test_titled_scrolls_results_establishes_matching_persisted_identity(self):
        farm.write_json(self.runtime / 'session.json', {'stage': 'rich-b', 'mission_verified': True})
        scrolls = self.engine('scrolls-a', 'assets/reference_images/starting_point.png')
        self.assertFalse(scrolls.mission_verified)
        scrolls.tick()
        self.assertEqual(scrolls.device.actions, ['retry'])
        self.assertEqual(farm.read_json(self.runtime / 'session.json'),
                         {'stage': 'scrolls-a', 'mission_verified': True})
        resumed = self.engine('scrolls-a', 'tests/fixtures/battle.png')
        resumed.tick()
        self.assertTrue(resumed.mission_verified)
        self.assertEqual(resumed.data['screen'], 'IN_BATTLE')
        self.assertEqual(resumed.device.actions, [])

    def test_worker_lock_rejects_second_controller_and_stage_switch(self):
        for lock_name in ('worker', 'supervisor'):
            with self.subTest(lock=lock_name), farm.lock(lock_name):
                self.assertTrue(farm.lock_busy(lock_name))
                with self.assertRaises(RuntimeError):
                    with farm.lock(lock_name): pass
                with self.assertRaises(RuntimeError):
                    farm.select_stage('rich-b')
                with patch.object(farm.subprocess, 'run') as launch:
                    with self.assertRaises(RuntimeError):
                        farm.start('rich-b')
                    launch.assert_not_called()
            self.assertFalse(farm.lock_busy(lock_name))

    def test_select_preserves_progress_and_stop(self):
        for filename, value in (('farm.json', {'marker': 'scrolls'}),
                                ('rich_b.json', {'marker': 'rich'})):
            farm.write_json(self.runtime / filename, value)
        farm.STOP.write_text('manual stop', encoding='utf-8')
        before = {path.name: path.read_bytes() for path in self.runtime.glob('*')}
        farm.select_stage('rich-b')
        farm.select_stage('scrolls-a')
        for filename in ('farm.json', 'rich_b.json', 'STOP'):
            self.assertEqual((self.runtime / filename).read_bytes(), before[filename])

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
            stage = 'rich-b' if 'rich-b' in command else 'scrolls-a'
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
             patch.object(farm, 'read_json', side_effect=fake_read), \
             patch.object(farm.subprocess, 'run', side_effect=fake_launch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, ('scrolls-a', 'rich-b')))
        self.assertEqual(sorted(results), ['blocked', 'started'])
        self.assertEqual(len(launches), 1)
        self.assertEqual(farm.read_json(self.runtime / 'supervisor.json')['stage'], launches[0])

    def test_supervisor_launch_excludes_only_own_pid_or_matching_venv_parent(self):
        cases = [({'pid': 100, 'stage': 'rich-b', 'launching': True}, set()),
                 ({'pid': 101, 'stage': 'rich-b', 'launching': True}, set()),
                 ({'pid': 102, 'stage': 'rich-b', 'launching': True}, {102}),
                 ({'pid': 101, 'stage': 'scrolls-a', 'launching': True}, {101}),
                 ({'pid': 101, 'stage': 'rich-b', 'launching': False}, {101})]
        with patch.object(farm.os, 'getpid', return_value=100), \
             patch.object(farm.os, 'getppid', return_value=101), \
             patch.object(farm, 'alive', return_value=True):
            for claim, blocked in cases:
                with self.subTest(claim=claim):
                    farm.write_json(self.runtime / 'supervisor.json', claim)
                    exclusions = farm.controller_exclusions('rich-b', launching_supervisor=True)
                    self.assertIn(100, exclusions)
                    self.assertEqual(set(farm.live_controller_pids(exclude=exclusions)), blocked)

    def test_worker_venv_parent_exclusion_requires_matching_supervisor_and_stage(self):
        cases = [({'pid': 200, 'worker_pid': 101, 'stage': 'rich-b'}, 200, set()),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'rich-b'}, 201, {200, 101}),
                 ({'pid': 200, 'worker_pid': 102, 'stage': 'rich-b'}, 200, {102}),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'scrolls-a'}, 200, {200, 101}),
                 ({'pid': 200, 'worker_pid': 101, 'stage': 'rich-b'}, None, {200, 101})]
        with patch.object(farm.os, 'getpid', return_value=100), \
             patch.object(farm.os, 'getppid', return_value=101), \
             patch.object(farm, 'alive', return_value=True):
            for claim, supervisor_pid, blocked in cases:
                with self.subTest(claim=claim, supervisor_pid=supervisor_pid):
                    farm.write_json(self.runtime / 'supervisor.json', claim)
                    exclusions = farm.controller_exclusions('rich-b', supervisor_pid)
                    self.assertEqual(set(farm.live_controller_pids(exclude=exclusions)), blocked)


if __name__ == '__main__': unittest.main(verbosity=2)
