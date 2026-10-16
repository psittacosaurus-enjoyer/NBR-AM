"""Instance adapter tests: subprocesses and discovery are always mocked."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bluestacks
from manager_backend import ControllerBackend, ControllerError


def instance(name="Main", identifier="Pie64", port=5621):
    return bluestacks.Instance(config_path="fixture.conf", instance_id=identifier,
        display_name=name, adb_path="adb.exe", endpoint=f"127.0.0.1:{port}",
        android_id=f"id-{identifier}", status="running", connection_status="Connected", detail="")


class InstanceBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ControllerBackend(Path(self.temp.name))

    def test_active_target_wins_over_discovery_order(self):
        first, active = instance(), instance("Custom", "Pie64_7", 5671)
        state = dict(running=True, instance=active.to_dict(), instance_locked=True)
        with patch("bluestacks.discover", return_value={"instances": [first, active], "detail": ""}), \
             patch.object(self.backend, "_instance_state", return_value=state):
            result = self.backend.instances()
        self.assertEqual(result["selected_key"], active.key)
        self.assertEqual(result["active_key"], active.key)
        self.assertTrue(result["locked"])

    def test_missing_saved_instance_remains_visible_without_substitution(self):
        saved = instance("Saved", "Pie64_saved", 5681)
        with patch("bluestacks.discover", return_value={"instances": [instance()], "detail": ""}), \
             patch.object(self.backend, "_instance_state", return_value=dict(
                 running=False, instance=saved.to_dict(), instance_error="Saved target missing")):
            result = self.backend.instances()
        self.assertEqual(result["selected_key"], saved.key)
        missing = next(item for item in result["instances"] if item["key"] == saved.key)
        self.assertEqual(missing["status"], "unavailable")
        self.assertIn("missing", result["detail"])

    def test_unknown_controller_state_locks_selection(self):
        with patch("bluestacks.discover", return_value={"instances": [], "detail": "No installation"}), \
             patch.object(self.backend, "_instance_state", side_effect=ControllerError("Unavailable")):
            result = self.backend.instances()
        self.assertTrue(result["locked"])
        self.assertIsNone(result["selected_key"])

    def test_selection_uses_controller_cli_as_structured_arguments(self):
        completed = subprocess.CompletedProcess([], 0, "saved", "")
        with patch("manager_backend.subprocess.run", return_value=completed) as run:
            self.backend.apply_instance("config|Pie64_42")
            self.assertEqual(run.call_args.args[0][-2:], ["--instance-key", "config|Pie64_42"])
            self.backend.manual_instance(" Custom Account ", "127.0.0.1:5641")
            self.assertEqual(run.call_args.args[0][-4:],
                             ["--instance-name", "Custom Account", "--endpoint", "127.0.0.1:5641"])
            self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_rejected_selection_is_reported(self):
        completed = subprocess.CompletedProcess([], 1, "", "Farming is active")
        with patch("manager_backend.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(ControllerError, "Farming is active"):
                self.backend.apply_instance(instance().key)

    def test_connection_uses_pinned_process_endpoint(self):
        active = instance("Running custom account", "Pie64_active", 5691)
        with patch.object(self.backend, "_instance_state", return_value=dict(
                running=True, instance=active.to_dict())), \
             patch("bluestacks.verify_identity") as verify:
            result = self.backend.connection()
        verify.assert_called_once_with(active)
        self.assertTrue(result["available"])
        self.assertEqual(result["instance"], active.display_name)

    def test_malformed_status_fails_closed(self):
        for value in ("[]", json.dumps({"running": "yes"}), "not-json"):
            with self.subTest(value=value), patch.object(self.backend, "_invoke", return_value=value):
                with self.assertRaises(ControllerError):
                    self.backend._instance_state()

    def test_loot_history_remains_with_selected_instance(self):
        from loot_tracking import LootStore, read_dashboard
        store = LootStore(self.backend.root)
        first = store.ensure_session("fixture:first", "universal", account="first")
        second = store.ensure_session("fixture:second", "universal", account="second")
        store.accept(dict(session_id=first, completion_id="first:1", run_id="one",
                          run_sequence=1, completed_at=1))
        state = dict(running=False, instance_account="second")
        data = read_dashboard(self.backend.root, state)
        self.assertEqual([item["session_id"] for item in data["sessions"]], [second])
        self.assertEqual(data["completed_runs"], 0)
        rejected = read_dashboard(self.backend.root, state, session_id=first)
        self.assertIsNone(rejected["session"])
        previous = read_dashboard(self.backend.root, dict(running=False, instance_account="first"))
        self.assertEqual(previous["completed_runs"], 1)


if __name__ == "__main__":
    unittest.main()
