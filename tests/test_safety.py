"""Small offline safety checks. Never connects to or sends input to BlueStacks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import farm


class FakeDevice:
    def __init__(self, image):
        self.image = image
        self.actions = []
        self.commands = []

    def foreground(self): return farm.PACKAGE
    def capture(self): return self.image.copy()
    def perform(self, action): self.actions.append(action["id"])
    def verify(self): pass
    def command(self, args): self.commands.append(args)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.patches = [patch.object(farm, "RUNTIME", self.runtime),
                        patch.object(farm, "STOP", self.runtime / "STOP"),
                        patch.object(farm, "event"), patch.object(farm.time, "sleep")]
        for p in self.patches: p.start()
        self.detector = farm.Detector()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def image(self, path): return cv2.imread(str(farm.ROOT / path))

    def engine(self, image):
        engine = object.__new__(farm.Engine)
        engine.device = FakeDevice(image)
        engine.detector = self.detector
        engine.data = {"counts": {"started": 0, "completed": 0, "failed": 0, "launches": 0}}
        engine.last_unknown = 0
        engine.unknown_hash = None
        return engine

    def test_both_results_variants_and_wrong_mission(self):
        for path in ["assets/reference_images/starting_point.png", "assets/reference_images/starting_point_lucky_drop.png"]:
            image = self.image(path)
            self.assertEqual(self.detector.classify(image)[0], "READY_TO_RETRY")
            image[182:247, 300:649] = 0
            self.assertEqual(self.detector.classify(image)[0], "UNKNOWN")

    def test_dark_modal_does_not_click_underlying_retry(self):
        image = self.image("assets/reference_images/starting_point.png")
        engine = self.engine((image.astype(float) * .5).astype(np.uint8))
        engine.tick()
        self.assertEqual(engine.device.actions, [])

    def test_rank_up_ack_closes_once_and_does_not_click_results_beneath(self):
        image = self.image('tests/fixtures/rank_up_stamina_ack.png')
        self.assertEqual(self.detector.classify(image)[0],'RANK_UP_STAMINA_ACK')
        engine = self.engine(image)
        engine.data['pending'] = {'id':'advance_completion','source':'MISSION_COMPLETE',
                                  'expected':['RESULTS_NEXT','READY_TO_RETRY'],'time':0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions,['dismiss_known_popup'])
        self.assertEqual(engine.data['pending']['source'],'RANK_UP_STAMINA_ACK')
        # Similar framed dialogs cannot be closed without the exact message and OK.
        changed = image.copy()
        changed[695:779,182:722] = 0
        name,spec,_ = self.detector.classify(changed)
        self.assertEqual(name,'UNKNOWN')
        self.assertIsNone(spec)

    def test_completion_animation_frames_and_no_results_confusion(self):
        for path in ["tests/fixtures/mission_complete.png", *[f"tests/fixtures/completion_{i}.png" for i in range(3)]]:
            self.assertEqual(self.detector.classify(self.image(path))[0], "MISSION_COMPLETE")
        self.assertNotEqual(self.detector.classify(self.image("assets/reference_images/starting_point.png"))[0], "MISSION_COMPLETE")

    def test_unchanged_screen_never_repeats_and_restart_keeps_latch(self):
        image = self.image("assets/reference_images/starting_point.png")
        engine = self.engine(image)
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions, ["retry"])
        resumed = self.engine(image)
        resumed.data = farm.read_json(self.runtime / "farm.json")
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])

    def test_only_confirmed_transition_allows_next_input(self):
        engine = self.engine(self.image("assets/reference_images/starting_point.png"))
        engine.tick()
        engine.device.image = self.image("tests/fixtures/ready_to_start.png")
        engine.tick()
        self.assertEqual(engine.device.actions, ["retry", "start_mission"])
        engine.tick()
        self.assertEqual(engine.device.actions, ["retry", "start_mission"])

    def test_unknown_waits_and_recognized_state_resumes(self):
        engine = self.engine(np.zeros((1600,900,3),np.uint8))
        engine.tick()
        self.assertEqual(engine.device.actions, [])
        engine.device.image = self.image("assets/reference_images/starting_point.png")
        engine.tick()
        self.assertEqual(engine.device.actions, ["retry"])

    def test_battle_never_clicks_even_when_long(self):
        engine = self.engine(self.image("tests/fixtures/battle.png"))
        engine.data["run"] = {"id": "test", "start": 0, "outcome": None}
        engine.tick()
        self.assertEqual(engine.device.actions, [])

    def test_title_latch_resolves_on_resumed_battle_without_battle_input(self):
        engine = self.engine(self.image('tests/fixtures/battle.png'))
        engine.data['pending'] = {'id':'title_start','source':'TITLE_START','expected':['HOME_MENU'],'time':0}
        engine.data['run'] = {'id':'old','start':0,'outcome':'completed'}
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.device.actions,[])
        self.assertIsNone(engine.data['run']['outcome'])
        self.assertEqual(engine.data['run']['origin'],'observed_after_title')
        self.assertEqual(engine.data['counts']['recovered_missions'],1)

    def test_title_latch_resolves_on_completion_and_advances_once(self):
        engine = self.engine(self.image('tests/fixtures/mission_complete.png'))
        engine.data['pending'] = {'id':'title_start','source':'TITLE_START','expected':['HOME_MENU'],'time':0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions,['advance_completion'])
        self.assertEqual(engine.data['counts']['completed'],1)

    def test_battle_cannot_clear_completion_action_latch(self):
        engine = self.engine(self.image('tests/fixtures/battle.png'))
        engine.data['pending'] = {'id':'advance_completion','source':'MISSION_COMPLETE','expected':['RESULTS_NEXT'],'time':0}
        engine.tick()
        self.assertEqual(engine.data['pending']['id'],'advance_completion')
        self.assertEqual(engine.device.actions,[])

    def test_retry_latch_resolves_when_mission_already_resumed(self):
        engine = self.engine(self.image('tests/fixtures/battle.png'))
        engine.data['pending'] = {'id':'retry','source':'READY_TO_RETRY','expected':['READY_TO_START'],'time':0}
        engine.data['run'] = {'id':'old','start':0,'outcome':'completed'}
        engine.tick()
        self.assertIsNone(engine.data['pending'])
        self.assertEqual(engine.device.actions,[])
        self.assertIsNone(engine.data['run']['outcome'])
        self.assertEqual(engine.data['run']['origin'],'observed_after_retry')

    def test_completion_can_advance_directly_to_rewards_with_old_persisted_latch(self):
        engine = self.engine(self.image('tests/fixtures/reward_reveal.png'))
        engine.data['pending'] = {'id':'advance_completion','source':'MISSION_COMPLETE',
                                  'expected':['RESULTS_NEXT','READY_TO_RETRY'],'time':0}
        engine.tick()
        engine.tick()
        self.assertEqual(engine.device.actions,['reveal_rewards'])
        self.assertEqual(engine.data['pending']['source'],'REWARD_REVEAL')

    def test_loss_stops_persistently_without_clicking_and_notifies_once(self):
        engine = self.engine(np.zeros((1600,900,3),np.uint8))
        engine.data['run'] = {'id':'loss-test','start':0,'outcome':None}
        with patch.object(farm,'ocr_text',return_value='Mission Failed'), patch.object(farm,'notify_loss') as notify:
            engine.tick()
            engine.tick()
            self.assertTrue(farm.STOP.exists())
            self.assertEqual(engine.device.actions,[])
            self.assertEqual(engine.device.commands,[])
            self.assertEqual(engine.data['counts']['failed'],1)
            self.assertEqual(engine.data['run']['outcome'],'failed')
            self.assertEqual(farm.read_json(self.runtime/'loss.json')['screen'],'LOSS_DETECTED')
            notify.assert_called_once()

    def test_unconfirmed_loss_does_not_stop_or_click(self):
        engine = self.engine(np.zeros((1600,900,3),np.uint8))
        engine.data['run'] = {'id':'test','start':0,'outcome':None}
        with patch.object(farm,'ocr_text',side_effect=['Mission Failed','Now loading']), patch.object(farm,'notify_loss') as notify:
            engine.tick()
            self.assertFalse(farm.STOP.exists())
            self.assertEqual(engine.device.actions,[])
            notify.assert_not_called()

    def test_revival_screen_stops_without_declining_or_spending(self):
        engine = self.engine(np.zeros((1600,900,3),np.uint8))
        spec={'action':{'id':'decline_revive'}}
        with patch.object(engine,'observe',return_value=(engine.device.image,'REVIVAL_PROMPT',spec,{})), patch.object(farm,'notify_loss') as notify:
            engine.tick()
            self.assertTrue(farm.STOP.exists())
            self.assertEqual(engine.device.actions,[])
            notify.assert_called_once()

    def test_loss_text_does_not_treat_rewards_or_shop_as_loss(self):
        for text in ['Mission Complete Ninja Pearls','Ninja Pearls 359','Enemy defeated','Now loading','Restore stamina using Ninja Pearls?']:
            self.assertFalse(farm.loss_text(text),text)
        for text in ['Mission Failed','GAME OVER','Use a Ninja Pearl to continue?','Revive with Ninja Pearls?']:
            self.assertTrue(farm.loss_text(text),text)

    def test_target_banner_moves_and_tap_follows_artwork(self):
        image = self.image("tests/fixtures/scrolls_banner.png")
        moved = image.copy()
        moved[330:1380] = 0
        moved[798:992,80:812] = image[648:842,80:812]
        name,spec,_ = self.detector.classify(moved)
        self.assertEqual(name,"SCROLLS_BANNER")
        self.assertEqual(spec['action']['point'],[451,921])

    def test_stalled_menu_preserves_app_without_another_click(self):
        engine = self.engine(self.image("tests/fixtures/home.png"))
        engine.data['pending'] = {'id':'nav_right','source':'HOME_MENU','expected':['HOME_EMERGENCY'],'time':0}
        engine.tick()
        self.assertEqual(engine.device.actions,[])
        self.assertEqual(engine.device.commands,[])

    def test_stalled_target_banner_holds_without_relaunch(self):
        engine = self.engine(self.image("tests/fixtures/scrolls_banner.png"))
        engine.data['pending'] = {'id':'select_scrolls','source':'SCROLLS_BANNER','expected':['RANK_SELECTION'],'time':0}
        engine.tick()
        self.assertEqual(engine.device.actions,[])
        self.assertEqual(engine.device.commands,[])
        self.assertIn('Mission banner did not advance',engine.data['blocked'])

    def test_unknown_with_old_pending_action_never_resets(self):
        engine = self.engine(np.zeros((1600,900,3),np.uint8))
        engine.data['pending'] = {'id':'select_scrolls','source':'SCROLLS_BANNER','expected':['RANK_SELECTION'],'time':0}
        engine.tick()
        self.assertEqual(engine.device.actions,[])
        self.assertEqual(engine.device.commands,[])

    def test_stamina_deadline_survives_reload(self):
        engine = self.engine(self.image("tests/fixtures/ready_to_start.png"))
        engine.data["stamina_until"] = farm.time.time() + 899
        engine.save()
        resumed = self.engine(engine.device.image)
        resumed.data = farm.read_json(self.runtime / "farm.json")
        resumed.tick()
        self.assertEqual(resumed.device.actions, [])
        self.assertGreater(resumed.data["stamina_until"], farm.time.time() + 895)

    def test_stop_prevents_input(self):
        engine = self.engine(self.image("assets/reference_images/starting_point.png"))
        farm.STOP.write_text("stop")
        engine.tick()
        self.assertEqual(engine.device.actions, [])

    def test_wrong_android_identity_rejected(self):
        conf = self.runtime / "bluestacks.conf"
        conf.write_text('bst.instance.Pie64.display_name="acc1"\nbst.instance.Pie64.adb_port="5555"\nbst.instance.Pie64.android_id="expected"\n')
        with patch.object(farm, "CONF", conf), patch.object(farm.Device, "command", return_value=b"wrong\n"):
            with self.assertRaisesRegex(RuntimeError, "not acc1"):
                farm.Device().verify()

    def test_fixed_tap_script_is_one_deterministic_touch(self):
        device = farm.Device()
        calls = []
        def fake_command(args, timeout=15):
            calls.append(args)
            return b'Events injected: 3\n'
        with patch.object(device,'verify'),patch.object(device,'foreground',return_value=farm.PACKAGE),patch.object(device,'command',side_effect=fake_command):
            device.perform({'id':'select_scrolls','kind':'tap','point':[450,700]})
        self.assertEqual(calls[-1],['shell','monkey','-p',farm.PACKAGE,'-f','/data/local/tmp/nbr_fixed_tap.script','1'])
        text=(self.runtime/'fixed_tap.script').read_text()
        self.assertEqual(text.split('start data >>\n')[1],'Tap(450,700,350)\n')


if __name__ == "__main__": unittest.main(verbosity=2)
