"""Disposable monitor windows: terminal identity, failures, resume and stalls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/monitor_ninja_runs.py"
SPEC = importlib.util.spec_from_file_location("monitor_ninja_runs", SCRIPT)
monitor_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor_module)


class NinjaRunMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.progress = self.runtime / "ninja_road.json"
        self.events = self.runtime / "events.jsonl"
        self.events.write_text("")
        self.data = {"heartbeat": 100, "screen": "NR_COMBAT", "counts": {},
                     "run": {"id": "resumed", "start": 95, "outcome": None, "exit_mode": "stage10"}}
        self.save()
        root_patch = patch.object(monitor_module, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.monitor = monitor_module.Monitor(self.root / "window", self.progress, self.events, 3, now=100)
        self.monitor.poll(now=100)

    def save(self):
        self.progress.write_text(json.dumps(self.data))

    def terminal(self, run_id, outcome="early_exit", ended=105, cleared_map=10, mode="stage10"):
        self.data.update(heartbeat=ended, exit_after_stage=cleared_map,
                         run={"id": run_id, "start": 101, "ended": ended,
                              "outcome": outcome, "exit_mode": mode})
        self.save()
        self.monitor.poll(now=ended)

    def test_only_three_distinct_successes_complete_and_resumed_run_is_labelled(self):
        self.terminal("resumed")
        self.terminal("resumed")
        self.terminal("second", ended=110)
        self.assertEqual(self.monitor.summary["successful_runs"], 2)
        self.terminal("third", ended=115)
        self.assertEqual(self.monitor.summary["status"], "completed")
        self.assertFalse(self.monitor.summary["runs"][0]["observed_from_start"])

    def test_defeat_abandon_wrong_map_and_changed_mode_do_not_count_as_success(self):
        for index, (outcome, cleared, mode) in enumerate([
                ("failed", None, "stage10"), ("abandoned", None, "stage10"),
                ("early_exit", 15, "stage10"), ("early_exit", 10, "stage15")]):
            self.terminal(str(index), outcome, 105 + index, cleared, mode)
        self.assertEqual(self.monitor.summary["successful_runs"], 0)
        self.assertEqual(self.monitor.summary["failed_runs"], 1)
        self.assertEqual(self.monitor.summary["terminal_runs"], 4)

    def test_resume_keeps_baseline_and_terminal_identities(self):
        self.terminal("resumed")
        resumed = monitor_module.Monitor(self.root / "window", self.progress, self.events, 3, now=120)
        resumed.poll(now=120)
        self.assertEqual(resumed.baseline["started"], 100)
        self.assertEqual(resumed.summary["successful_runs"], 1)

    def test_rotated_event_recovers_observed_run_and_ignores_other_or_old_runs(self):
        self.data["run"] = {"id": "next", "start": 106, "outcome": None, "exit_mode": "stage10"}
        self.data["heartbeat"] = 110
        self.save()
        events = [
            {"event": "ninja_early_exit_completed", "run_id": "resumed", "time": 105, "cleared_map": 10},
            {"event": "ninja_early_exit_completed", "run_id": "other", "time": 106, "cleared_map": 10},
            {"event": "ninja_early_exit_completed", "run_id": "resumed", "time": 99, "cleared_map": 10},
        ]
        self.events.with_name("events.jsonl.1").write_text("\n".join(map(json.dumps, events)))
        self.monitor.poll(now=110)
        self.assertEqual(self.monitor.summary["successful_runs"], 1)
        self.assertEqual(self.monitor.summary["runs"][0]["run_id"], "resumed")

    def test_persistent_pending_and_stale_heartbeat_need_attention(self):
        self.data.update(pending={"purpose": "swipe", "time": 90}, blocked="Unverified")
        self.save()
        self.monitor.poll(now=101)
        self.data["heartbeat"] = 162
        self.save()
        self.monitor.poll(now=162)
        self.assertEqual(self.monitor.summary["status"], "attention")
        self.assertIn("persisted", self.monitor.summary["reason"])
        self.data.update(pending=None, blocked=None, heartbeat=100)
        self.save()
        self.monitor.poll(now=201)
        self.assertIn("heartbeat", self.monitor.summary["reason"])

    def test_saved_instance_metadata_resolves_namespaced_runtime(self):
        target = {"key": "instance-key", "android_id": "device-id"}
        (self.runtime / "instance_selection.json").write_text(json.dumps(target))
        digest = monitor_module.hashlib.sha256(b"instance-key\0device-id").hexdigest()[:24]
        expected = self.runtime / "instances" / digest / "ninja_road.json"
        expected.parent.mkdir(parents=True)
        expected.write_text("{}")
        self.assertEqual(monitor_module.default_progress(), expected)


if __name__ == "__main__":
    unittest.main()
