"""Isolated Manager 1.1 loot tests. Never contacts ADB or live farming processes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import loot_tracking as loot


ROOT = Path(__file__).resolve().parent


def reward(item_id="fixture-scroll", status="matched", quantity=None,
           quantity_status="unknown", scope="acquired"):
    return {"item_id": item_id, "item_name": "Fixture Scroll", "identity_status": status,
            "quantity": quantity, "quantity_status": quantity_status, "reward_scope": scope,
            "visual_state": "icon", "bounding_box": [0, 0, 16, 16], "candidates": []}


def result(rewards=None, unknown=None, status="partial", screen_type="acquired"):
    return {"schema_version": 1, "stage": "scrolls-a", "status": status,
            "screen_type": screen_type, "reward_region": [0, 0, 32, 32],
            "rewards": list(rewards or []), "unknown_rewards": list(unknown or []),
            "warnings": [], "totals": {}, "summary_fields": [], "diagnostics": {}}


class FakeRecognizer:
    def __init__(self, output=None, error=None, entered=None, release=None):
        self.output = output if output is not None else result([reward()])
        self.error = error
        self.entered = entered
        self.release = release
        self.calls = []

    def analyze_screenshot(self, screenshot_path, stage="unknown", **kwargs):
        self.calls.append((str(screenshot_path), stage))
        if self.entered:
            self.entered.set()
        if self.release and not self.release.wait(5):
            raise TimeoutError("Test fixture was not released")
        if self.error:
            raise self.error
        output = copy.deepcopy(self.output)
        output["screenshot"] = str(screenshot_path)
        output["screenshot_sha256"] = hashlib.sha256(Path(screenshot_path).read_bytes()).hexdigest()
        return output


class LootTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nbr-loot-test-")
        self.root = Path(self.temp.name)
        self.collectors = []
        self.frame = np.full((32, 32, 3), (40, 70, 110), dtype=np.uint8)

    def tearDown(self):
        for collector in self.collectors:
            collector.close()
        self.temp.cleanup()

    def collector(self, output=None, process="fixture:100:200", stage="scrolls-a", **kwargs):
        fake = FakeRecognizer(output)
        collector = loot.Collector(self.root, stage, process, process_started=200,
                                   recognizer_factory=lambda: fake, **kwargs)
        self.collectors.append(collector)
        return collector, fake

    def observe(self, collector, sequence=1, frame=None):
        completion_id = collector.completed(sequence)
        self.assertIsNotNone(completion_id)
        self.assertTrue(collector.evidence(sequence, self.frame if frame is None else frame))
        self.assertTrue(collector.wait_idle(10))
        return completion_id

    def dashboard(self, collector):
        return loot.LootStore(self.root).dashboard(collector.session_id)

    def test_identical_images_in_distinct_runs_have_unique_durable_ids(self):
        collector, fake = self.collector()
        first = self.observe(collector, 1)
        second = self.observe(collector, 2)
        self.assertNotEqual(first, second)
        data = self.dashboard(collector)
        self.assertEqual(data["completed_runs"], 2)
        self.assertEqual(data["recognized_icons"], 2)
        self.assertEqual(len(data["runs"]), 2)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len({run["completion_id"] for run in data["runs"]}), 2)

    def test_duplicate_completion_evidence_and_collector_reopen_do_not_inflate(self):
        collector, fake = self.collector()
        completion_id = self.observe(collector)
        self.assertIn(collector.completed(1), (None, completion_id))
        collector.evidence(1, self.frame)
        self.assertTrue(collector.wait_idle(10))
        session_id = collector.session_id
        collector.close()
        reopened, other_fake = self.collector()
        self.assertEqual(reopened.session_id, session_id)
        self.assertIn(reopened.completed(1), (None, completion_id))
        reopened.evidence(1, self.frame)
        self.assertTrue(reopened.wait_idle(10))
        data = self.dashboard(reopened)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["recognized_icons"], 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(other_fake.calls), 0)

    def test_new_process_gets_fresh_session_without_historical_backfill(self):
        first, _ = self.collector()
        self.assertEqual(self.dashboard(first)["completed_runs"], 0)
        self.observe(first, 57)
        first.close()
        next_process, _ = self.collector(process="fixture:100:300", stage="rich-b")
        self.assertNotEqual(first.session_id, next_process.session_id)
        self.assertEqual(self.dashboard(next_process)["completed_runs"], 0)
        self.observe(next_process, 1)
        self.assertEqual(self.dashboard(first)["completed_runs"], 1)
        self.assertEqual(self.dashboard(next_process)["completed_runs"], 1)
        self.assertEqual(len(loot.LootStore(self.root).sessions()), 2)

    def test_confirmed_quantity_requires_verified_acquired_evidence(self):
        observations = [reward(quantity=7, quantity_status="verified"), reward(),
                        reward(quantity=90, quantity_status="candidate"),
                        reward(quantity=500, quantity_status="verified", scope="condition"),
                        reward("fixture-review", status="candidate", quantity=33, quantity_status="verified")]
        output = result(observations)
        output["totals"] = {"fixture-scroll": 99999}
        output["summary_fields"] = [{"scope": "ryo_earned", "quantity": 98765,
                                      "quantity_status": "verified"}]
        collector, _ = self.collector(output)
        self.observe(collector)
        data = self.dashboard(collector)
        self.assertEqual(data["recognized_icons"], 3)
        item = next(row for row in data["items"] if row["item_id"] == "fixture-scroll")
        self.assertEqual(item["observed_icons"], 3)
        self.assertEqual(item["confirmed_acquired"], 7)
        self.assertEqual(item["unknown_quantities"], 2)
        self.assertFalse(any(row["item_id"] == "fixture-review" for row in data["items"]))

    def test_quantity_unknown_is_normal_and_successful_temp_copy_is_removed(self):
        collector, _ = self.collector()
        self.observe(collector)
        data = self.dashboard(collector)
        self.assertEqual(data["failures"], 0)
        self.assertIsNone(data["items"][0]["confirmed_acquired"])
        self.assertFalse(data["runs"][0].get("screenshot_path"))
        self.assertEqual(list((self.root / "runtime" / "loot").rglob("*.png")), [])

    def test_unknown_candidate_and_ambiguous_are_retained_without_guessed_identity(self):
        for identity in ("unknown", "candidate", "ambiguous"):
            with self.subTest(identity=identity):
                entry = reward("fixture-review", status=identity, quantity=9, quantity_status="verified")
                output = result([entry] if identity == "candidate" else [],
                                [entry] if identity != "candidate" else [])
                collector, _ = self.collector(output, process="fixture:" + identity)
                self.observe(collector)
                data = self.dashboard(collector)
                self.assertEqual(data["recognized_icons"], 0)
                self.assertEqual(data["unknown_icons"] + data["ambiguous_icons"], 1)
                self.assertEqual(data["items"], [])
                evidence = Path(data["runs"][0]["screenshot_path"])
                self.assertTrue(evidence.is_file())
                collector.evidence(1, self.frame)
                self.assertTrue(collector.wait_idle(10))
                self.assertEqual(len(list(evidence.parent.glob("*" + data["runs"][0]["completion_id"] + "*.png"))), 1)

    def test_recognition_exception_preserves_evidence_and_farming_call_returns(self):
        fake = FakeRecognizer(error=RuntimeError("fixture recognition failure"))
        collector = loot.Collector(self.root, "rich-b", "fixture:error", recognizer_factory=lambda: fake)
        self.collectors.append(collector)
        self.observe(collector)
        data = self.dashboard(collector)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["failures"], 1)
        self.assertEqual(data["recognized_icons"], 0)
        self.assertTrue(Path(data["runs"][0]["screenshot_path"]).is_file())
        self.assertIn("fixture recognition failure", str(data["runs"][0]))

    def test_missing_evidence_is_recorded_and_does_not_invent_rewards(self):
        collector, fake = self.collector()
        collector.completed(1)
        collector.missing(1)
        self.assertTrue(collector.wait_idle(10))
        data = self.dashboard(collector)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["recognized_icons"], 0)
        self.assertEqual(data["failures"], 1)
        self.assertIn("missing_screenshot", str(data["runs"][0]))
        self.assertEqual(fake.calls, [])

    def test_unsupported_or_unreliable_parsing_retains_screenshot(self):
        for status in ("unsupported_screen", "no_visible_rewards", "error"):
            with self.subTest(status=status):
                collector, _ = self.collector(result(status=status), process="fixture:" + status)
                self.observe(collector)
                data = self.dashboard(collector)
                self.assertEqual(data["failures"], 1)
                self.assertTrue(Path(data["runs"][0]["screenshot_path"]).is_file())

    def test_incomplete_results_preserve_evidence_even_when_visible_icons_are_known(self):
        output = result([reward()])
        output["diagnostics"]["parsing_complete"] = False
        collector, _ = self.collector(output)
        self.observe(collector)
        data = self.dashboard(collector)
        self.assertEqual(data["recognized_icons"], 1)
        self.assertEqual(data["failures"], 1)
        self.assertEqual(data["runs"][0]["status"], "incomplete_results")
        self.assertTrue(Path(data["runs"][0]["screenshot_path"]).is_file())

    def test_bounded_queue_does_not_block_completion_and_records_overflow(self):
        entered, release = threading.Event(), threading.Event()
        fake = FakeRecognizer(entered=entered, release=release)
        collector = loot.Collector(self.root, "scrolls-a", "fixture:slow", max_pending=1,
                                   recognizer_factory=lambda: fake)
        self.collectors.append(collector)
        collector.completed(1)
        collector.evidence(1, self.frame)
        self.assertTrue(entered.wait(3))
        started = time.monotonic()
        try:
            for sequence in range(2, 7):
                collector.completed(sequence)
                collector.evidence(sequence, self.frame)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            release.set()
        self.assertTrue(collector.wait_idle(10))
        data = self.dashboard(collector)
        self.assertEqual(data["completed_runs"], 6)
        self.assertGreater(data["failures"], 0)
        self.assertLessEqual(len(fake.calls), 2)

    def test_persistence_failure_does_not_escape_farmer_calls(self):
        collector, _ = self.collector()
        with patch.object(loot.sqlite3, "connect", side_effect=sqlite3.OperationalError("fixture disk failure")):
            self.assertIsNotNone(collector.completed(1))
            self.assertTrue(collector.evidence(1, self.frame))
        self.assertTrue(collector.wait_idle(10))
        self.assertEqual(self.dashboard(collector)["completed_runs"], 1)

    def test_database_outage_keeps_memory_bounded_and_all_completions_durable(self):
        collector, _ = self.collector(max_pending=1)
        with patch.object(loot.sqlite3, "connect", side_effect=sqlite3.OperationalError("fixture prolonged outage")):
            for sequence in range(1, 21):
                self.assertIsNotNone(collector.completed(sequence))
                collector.evidence(sequence, self.frame)
                self.assertLessEqual(len(collector._events), collector.max_pending + 2)
            spool = self.root / "runtime" / "loot" / "pending"
            self.assertEqual(len(list(spool.glob("*.json"))), 20)
            self.assertEqual(len(list(spool.glob("*.png"))), 20)
            queued = [json.loads(path.read_text('utf-8')) for path in spool.glob('*.json')]
            self.assertLessEqual(sum(event['state'] == 'ready' for event in queued), collector.max_pending)
        self.assertTrue(collector.wait_idle(10))
        data = self.dashboard(collector)
        self.assertEqual(data["completed_runs"], 20)
        self.assertEqual(len(data["runs"]), 20)
        self.assertEqual(list(spool.glob("*.json")), [])

    def test_paged_history_keeps_all_session_totals_and_preserves_run_order(self):
        collector, _ = self.collector()
        for sequence in range(1, 6):
            self.observe(collector, sequence)
        store = loot.LootStore(self.root)
        first = store.dashboard(collector.session_id, limit=2, offset=0)
        second = store.dashboard(collector.session_id, limit=2, offset=2)
        last = loot.read_dashboard(self.root, {"running": False}, collector.session_id, limit=2, offset=4)
        for page in (first, second, last):
            self.assertEqual(page["completed_runs"], 5)
            self.assertEqual(page["recognized_icons"], 5)
            self.assertEqual(page["items"][0]["observed_icons"], 5)
        self.assertEqual([run["run_sequence"] for run in first["runs"]], [5, 4])
        self.assertEqual([run["run_sequence"] for run in second["runs"]], [3, 2])
        self.assertEqual([run["run_sequence"] for run in last["runs"]], [1])

    def test_database_finish_failure_preserves_evidence_until_durable_replay(self):
        collector, fake = self.collector(result(unknown=[reward(None, "unknown")]))
        with patch.object(collector.store, "finish", side_effect=sqlite3.OperationalError("fixture finish failure")):
            collector.completed("finish-replay")
            collector.evidence("finish-replay", self.frame)
            deadline = time.monotonic() + 3
            while not fake.calls and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(fake.calls)
            source = Path(fake.calls[0][0])
            self.assertTrue(source.is_file())
            self.assertTrue(list((self.root / "runtime" / "loot" / "pending").glob("*.json")))
            collector.close()
        reopened, _ = self.collector(result(unknown=[reward(None, "unknown")]))
        self.assertTrue(reopened.wait_idle(10))
        data = self.dashboard(reopened)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["unknown_icons"], 1)
        self.assertTrue(Path(data["runs"][0]["screenshot_path"]).is_file())
        self.assertFalse(source.exists())

    def test_pending_durable_event_survives_restart_without_quantity_recredit(self):
        collector, _ = self.collector(result([reward(quantity=4, quantity_status="verified")], status="complete"))
        # Simulate a process crash after the small durable event/screenshot writes,
        # before a recognizer can read them. Never stop any real process.
        collector._stop.set()
        collector._thread.join(3)
        self.assertFalse(collector._thread.is_alive())
        completion_id = collector.completed("restart-replay")
        self.assertIsNotNone(completion_id)
        self.assertTrue(collector.evidence("restart-replay", self.frame))
        reopened, _ = self.collector(result([reward(quantity=4, quantity_status="verified")], status="complete"))
        self.assertTrue(reopened.wait_idle(10))
        data = self.dashboard(reopened)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["items"][0]["confirmed_acquired"], 4)
        self.assertIn(reopened.completed("restart-replay"), (None, completion_id))
        self.assertFalse(reopened.evidence("restart-replay", self.frame))
        self.assertEqual(self.dashboard(reopened)["items"][0]["confirmed_acquired"], 4)

    def test_new_process_replays_saved_prior_session_evidence_into_prior_session_only(self):
        output = result([reward(quantity=2, quantity_status="verified")], status="complete")
        prior, _ = self.collector(output, process="fixture:prior")
        prior._stop.set()
        prior._thread.join(3)
        prior.completed("prior-completion")
        self.assertTrue(prior.evidence("prior-completion", self.frame))
        current, _ = self.collector(output, process="fixture:current", stage="rich-b")
        self.assertTrue(current.wait_idle(10))
        old_data, new_data = self.dashboard(prior), self.dashboard(current)
        self.assertEqual(old_data["completed_runs"], 1)
        self.assertEqual(old_data["items"][0]["confirmed_acquired"], 2)
        self.assertEqual(new_data["completed_runs"], 0)
        self.assertEqual(new_data["items"], [])

    def test_store_records_survive_close_and_session_end_is_explicit(self):
        collector, _ = self.collector()
        self.observe(collector)
        session_id = collector.session_id
        collector.close()
        store = loot.LootStore(self.root)
        self.assertEqual(store.dashboard(session_id)["completed_runs"], 1)
        store.end_session(session_id)
        self.assertEqual(store.dashboard(session_id)["status"], "Session Ended")

    def test_disposable_process_exit_persists_ended_session_without_farming_access(self):
        fake = FakeRecognizer()
        with subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(1.25)"],
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            identity, started = loot.process_identity(process.pid)
            collector = loot.Collector(self.root, "rich-b", identity, process_started=started,
                                       recognizer_factory=lambda: fake)
            self.collectors.append(collector)
            self.observe(collector)
            self.assertEqual(self.dashboard(collector)["session"]["process_identity"], identity)
            self.assertEqual(process.wait(timeout=5), 0)
        collector.close()
        data = loot.read_dashboard(self.root, {"running": False, "supervisor": {}, "farm": {}})
        self.assertEqual(data["status"], "Session Ended")
        self.assertIsNotNone(data["session"]["ended_at"])
        self.assertEqual(loot.LootStore(self.root).dashboard(collector.session_id)["status"], "Session Ended")

    def test_passive_attach_to_uninstrumented_process_does_not_backfill_or_write(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        progress = runtime / "session.json"
        progress.write_text('{"counts":{"completed":70},"run":{"id":"old","outcome":"completed"}}')
        before = progress.read_bytes()
        snapshot = {"running": True, "supervisor": {"pid": 901}, "farm": {"worker_pid": 902}}
        data = loot.read_dashboard(self.root, snapshot)
        self.assertEqual(data["status"], "Tracking Unavailable")
        self.assertEqual(data["completed_runs"], 0)
        self.assertEqual(progress.read_bytes(), before)
        self.assertFalse((runtime / "loot").exists())

    def test_passive_dashboard_updates_from_persisted_records_and_process_identity(self):
        collector, _ = self.collector(process="fixture:attached")
        snapshot = {"running": True, "supervisor": {"pid": 901}, "farm": {}}
        with patch.object(loot, "process_identity", return_value=("fixture:attached", 200)):
            self.assertEqual(loot.read_dashboard(self.root, snapshot)["completed_runs"], 0)
            self.observe(collector)
            data = loot.read_dashboard(self.root, snapshot)
        self.assertEqual(data["completed_runs"], 1)
        self.assertEqual(data["status"], "Tracking Active")
        with patch.object(loot, "process_identity", return_value=("fixture:different", 300)):
            unrelated = loot.read_dashboard(self.root, snapshot)
        self.assertEqual(unrelated["status"], "Tracking Unavailable")
        self.assertEqual(unrelated["completed_runs"], 0)

    def test_existing_completed_progress_cannot_supply_historical_evidence(self):
        collector, fake = self.collector()
        self.assertFalse(collector.evidence("historical-run", self.frame))
        collector.missing("historical-run")
        self.assertTrue(collector.wait_idle(3))
        self.assertEqual(self.dashboard(collector)["completed_runs"], 0)
        self.assertEqual(fake.calls, [])

    def test_real_recognizer_integration_is_readonly_and_uses_saved_evidence(self):
        screenshot = ROOT / "reference_images" / "starting_point.png"
        library = ROOT / "game_library" / "data" / "library.sqlite3"
        before_source = hashlib.sha256(screenshot.read_bytes()).hexdigest()
        before_library = hashlib.sha256(library.read_bytes()).hexdigest()
        from item_recognition.recognizer import ItemRecognizer
        recognizer = ItemRecognizer(library_path=ROOT / "game_library")
        collector = loot.Collector(self.root, "scrolls-a", "fixture:real",
                                   recognizer_factory=lambda: recognizer)
        self.collectors.append(collector)
        self.observe(collector, frame=cv2.imread(str(screenshot)))
        data = self.dashboard(collector)
        self.assertGreater(data["recognized_icons"], 0)
        self.assertEqual(data["failures"], 0)
        self.assertTrue(all(row["confirmed_acquired"] is None for row in data["items"]))
        self.assertEqual(hashlib.sha256(screenshot.read_bytes()).hexdigest(), before_source)
        self.assertEqual(hashlib.sha256(library.read_bytes()).hexdigest(), before_library)


class FarmerLootHookTests(unittest.TestCase):
    def test_verified_completion_generates_one_event_and_reuses_results_frame_both_stages(self):
        import farm
        for stage in ("scrolls-a", "rich-b"):
            with self.subTest(stage=stage):
                engine = farm.Engine.__new__(farm.Engine)
                engine.stage = {"id": stage}
                engine.data = {"screen": "IN_BATTLE", "pending": None, "stamina_until": 0,
                               "run": {"id": "fixture-completed", "start": time.time(), "outcome": None},
                               "counts": {"completed": 0, "failed": 0}}
                engine.save = Mock()
                engine.loot_collector = Mock()
                frame = np.zeros((32, 32, 3), dtype=np.uint8)
                with patch.object(farm, "event"), patch.object(farm, "Device", side_effect=AssertionError("No device access")):
                    engine.update(frame, "MISSION_COMPLETE", {})
                    engine.update(frame, "MISSION_COMPLETE", {})
                    engine.update(frame, "READY_TO_RETRY", {})
                    engine.update(frame, "READY_TO_RETRY", {})
                self.assertEqual(engine.data["counts"]["completed"], 1)
                engine.loot_collector.completed.assert_called_once()
                self.assertEqual(engine.loot_collector.completed.call_args.args, ("fixture-completed",))
                engine.loot_collector.evidence.assert_called_once_with("fixture-completed", frame)

    def test_loot_exception_does_not_change_successful_farming_update(self):
        import farm
        engine = farm.Engine.__new__(farm.Engine)
        engine.stage = {"id": "rich-b"}
        engine.data = {"screen": "IN_BATTLE", "pending": None, "stamina_until": 0,
                       "run": {"id": "fixture-error", "start": time.time(), "outcome": None},
                       "counts": {"completed": 0, "failed": 0}}
        engine.save = Mock()
        engine.loot_collector = Mock()
        engine.loot_collector.completed.side_effect = OSError("fixture persistence failure")
        with patch.object(farm, "event"):
            engine.update(np.zeros((32, 32, 3), dtype=np.uint8), "MISSION_COMPLETE", {})
        self.assertEqual(engine.data["run"]["outcome"], "completed")
        self.assertEqual(engine.data["counts"]["completed"], 1)
        engine.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
