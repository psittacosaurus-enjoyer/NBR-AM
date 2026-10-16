"""Offline target ownership tests. No live ADB or controller operation is allowed."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import farm
from bluestacks import Instance


class InstanceControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nbr-instance-controller-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.config = self.root / "bluestacks.conf"
        self.adb = self.root / "HD-Adb.exe"
        self.adb.write_bytes(b"fixture")
        self.first = self.instance("Pie64_8", "Main account", "5588", "id-one")
        self.second = self.instance("Pie64_9", "Custom account", "5599", "id-two")
        self.patches = [
            patch.object(farm, "RUNTIME", self.runtime),
            patch.object(farm, "STOP", self.runtime / "STOP"),
            patch.object(farm, "CONF", self.config),
            patch.object(farm, "ADB", self.adb),
            patch.object(farm, "event"),
            patch.object(farm, "discover", return_value={"instances": [self.first], "detail": ""}),
            patch.object(farm, "validate_mapping"),
            patch.object(farm, "verify_identity"),
            patch.object(farm, "alive", return_value=False),
            patch.object(farm, "process_may_be_alive", return_value=False),
            patch.object(farm, "process_identity", side_effect=lambda pid: (f"{pid}:fixture", 1)),
            patch.object(farm.subprocess, "run", side_effect=AssertionError("Unexpected external command")),
            patch.object(farm.subprocess, "Popen", side_effect=AssertionError("Unexpected process launch")),
            patch.object(farm, "_server_port", return_value=5037),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def instance(self, identifier, name, port, android_id):
        return Instance(str(self.config), identifier, name, str(self.adb),
                        "127.0.0.1:" + port, android_id, "running", "connected", "")

    def legacy(self):
        self.config.write_text('\n'.join([
            'bst.instance.Pie64.display_name="acc1"',
            'bst.instance.Pie64.adb_port="5555"',
            'bst.instance.Pie64.android_id="legacy-id"',
        ]), encoding="utf-8")
        return farm.legacy_instance()

    def test_single_discovery_is_unambiguous_and_read_only(self):
        self.assertEqual(farm.selected_instance(), self.first)
        self.assertFalse((self.runtime / "instance_selection.json").exists())
        farm.verify_identity.assert_not_called()

    def test_multiple_or_missing_instances_require_explicit_choice(self):
        for instances in ([self.first, self.second], []):
            farm.discover.return_value = {"instances": instances, "detail": ""}
            with self.assertRaises(RuntimeError):
                farm.selected_instance()

    def test_apply_verifies_then_persists_one_atomic_selection(self):
        with patch.object(farm, "write_json", wraps=farm.write_json) as write:
            self.assertEqual(farm.select_instance(self.second), self.second)
        farm.validate_mapping.assert_called_once_with(self.second)
        farm.verify_identity.assert_called_once_with(self.second, connect=True)
        write.assert_called_once()
        self.assertEqual(farm.selected_instance(), self.second)
        self.assertEqual(farm.read_json(self.runtime / "instance_selection.json")["key"], self.second.key)

    def test_identity_mismatch_leaves_previous_selection_unchanged(self):
        farm.select_instance(self.first)
        previous = (self.runtime / "instance_selection.json").read_bytes()
        farm.verify_identity.side_effect = RuntimeError("Android identity mismatch")
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            farm.select_instance(self.second)
        self.assertEqual((self.runtime / "instance_selection.json").read_bytes(), previous)

    def test_saved_mapping_changes_fail_without_auto_fallback(self):
        farm.write_json(self.runtime / "instance_selection.json", self.first.to_dict())
        farm.validate_mapping.side_effect = RuntimeError("Mapping changed")
        with self.assertRaisesRegex(RuntimeError, "Mapping changed"):
            farm.selected_instance()
        farm.discover.assert_not_called()

    def test_malformed_saved_selection_fails_closed(self):
        for invalid in ({"instance_id": "Pie64"}, ["acc1"], {**self.first.to_dict(), "android_id": ""}):
            farm.write_json(self.runtime / "instance_selection.json", invalid)
            with self.assertRaises(RuntimeError):
                farm.selected_instance()

    def test_busy_controller_or_restart_locks_reject_selection(self):
        for name in ("worker", "supervisor", "restart", "control"):
            with self.subTest(name=name), farm.lock(name):
                with self.assertRaises(RuntimeError):
                    farm.select_instance(self.second)
        farm.verify_identity.assert_not_called()
        self.assertFalse((self.runtime / "instance_selection.json").exists())

    def test_unidentified_live_legacy_pid_locks_selection(self):
        farm.write_json(self.runtime / "old-stage.json", {"worker_pid": 1234, "pending": {"id": "retry"}})
        farm.process_may_be_alive.side_effect = lambda record: record["pid"] == 1234
        with self.assertRaisesRegex(RuntimeError, "unverified"):
            farm.select_instance(self.second)
        farm.verify_identity.assert_not_called()

    def test_namespaced_worker_claims_also_lock_selection(self):
        path = farm.state_path("universal.json", self.first)
        farm.write_json(path, {"worker_pid": 1234})
        farm.process_may_be_alive.return_value = True
        with self.assertRaises(RuntimeError):
            farm.select_instance(self.second)

    def test_active_pinned_target_overrides_a_changed_saved_selection(self):
        farm.write_json(self.runtime / "supervisor.json", {"pid": 1234, "instance": self.first.to_dict()})
        farm.write_json(self.runtime / "instance_selection.json", self.second.to_dict())
        farm.process_may_be_alive.side_effect = lambda record: record.get("pid") == 1234
        self.assertEqual(farm.effective_instance(), self.first)

    def test_legacy_active_target_is_never_replaced_by_single_discovery(self):
        legacy = self.legacy()
        farm.write_json(self.runtime / "supervisor.json", {"pid": 1234})
        farm.write_json(self.runtime / "instance_selection.json", self.second.to_dict())
        farm.process_may_be_alive.side_effect = lambda record: record.get("pid") == 1234
        self.assertEqual(farm.effective_instance(), legacy)
        farm.discover.assert_not_called()

    def test_legacy_worker_respawn_ignores_new_saved_selection(self):
        legacy = self.legacy()
        farm.write_json(self.runtime / "supervisor.json", {"pid": 1234})
        farm.write_json(self.runtime / "instance_selection.json", self.second.to_dict())
        self.assertEqual(farm.process_target(supervisor_pid=1234), legacy)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            farm.process_target(self.second, supervisor_pid=1234)

    def test_legacy_migration_only_accepts_exact_original_mapping(self):
        legacy = self.legacy()
        farm.write_json(self.runtime / "session.json", {"stage": "universal", "mission_verified": True})
        self.assertEqual(farm.selected_instance(), legacy)
        self.assertEqual(farm.instance_runtime(legacy), self.runtime)
        for old, new in (("acc1", "Custom"), ("5555", "5588"), ("legacy-id", "")):
            self.legacy()
            self.config.write_text(self.config.read_text().replace(old, new), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                farm.selected_instance()
        farm.discover.assert_not_called()

    def test_friendly_rename_preserves_legacy_latches_after_migration(self):
        legacy = self.legacy()
        farm.select_instance(legacy)
        renamed = self.instance("Pie64", "Renamed account", "5555", "legacy-id")
        farm.select_instance(renamed)
        self.assertEqual(farm.instance_runtime(renamed), self.runtime)
        self.assertEqual(farm.instance_account(renamed), "acc1")

    def test_replaced_legacy_android_identity_gets_separate_state(self):
        legacy = self.legacy()
        farm.write_json(self.runtime / "universal.json", {"pending": {"id": "retry"}})
        farm.select_instance(legacy)
        replacement = self.instance("Pie64", "acc1", "5555", "replacement")
        farm.select_instance(replacement)
        self.assertNotEqual(farm.instance_runtime(replacement), self.runtime)
        self.assertNotEqual(farm.instance_account(replacement), "acc1")
        self.assertEqual(farm.read_json(self.runtime / "universal.json")["pending"], {"id": "retry"})

    def test_new_user_custom_name_at_legacy_coordinates_can_be_selected(self):
        custom = self.instance("Pie64", "My custom name", "5555", "new-user-id")
        farm.select_instance(custom)
        self.assertEqual(farm.selected_instance(), custom)

    def test_unresolvable_legacy_owner_can_never_adopt_recreated_instance(self):
        farm.write_json(self.runtime / "universal.json", {"pending": {"id": "retry"}, "stamina_until": 5000})
        farm.select_instance(self.second)
        self.assertTrue(farm.read_json(self.runtime / "legacy_instance.json")["unresolved"])
        with self.assertRaisesRegex(RuntimeError, "ownership is unresolved"):
            self.legacy()
        recreated = self.instance("Pie64", "acc1", "5555", "replacement")
        farm.select_instance(recreated)
        self.assertNotEqual(farm.instance_runtime(recreated), self.runtime)
        self.assertNotEqual(farm.instance_account(recreated), "acc1")
        self.assertEqual(farm.read_json(self.runtime / "universal.json")["stamina_until"], 5000)

    def test_missing_legacy_mapping_does_not_import_old_state_to_another_instance(self):
        farm.write_json(self.runtime / "universal.json", {"pending": {"id": "retry"}, "stamina_until": 500})
        with self.assertRaisesRegex(RuntimeError, "Legacy acc1"):
            farm.selected_instance()

    def test_instance_progress_session_and_loot_identity_are_distinct(self):
        legacy = self.legacy()
        self.assertEqual(farm.load_stage(instance=legacy)["progress"], self.runtime / "universal.json")
        self.assertNotEqual(farm.state_path("session.json", self.first), farm.state_path("session.json", self.second))
        self.assertNotEqual(farm.instance_account(self.first), farm.instance_account(self.second))
        changed = self.instance("Pie64_8", "Main account", "5588", "replacement-identity")
        self.assertNotEqual(farm.instance_runtime(self.first), farm.instance_runtime(changed))

    def test_engine_does_not_import_other_instances_pending_wait_or_session(self):
        farm.write_json(self.runtime / "universal.json", {"pending": {"id": "retry"}, "stamina_until": 5000})
        farm.write_json(farm.state_path("universal.json", self.first), {"pending": {"id": "start_mission"}, "stamina_until": 4000})
        farm.write_json(self.runtime / "session.json", {"stage": "universal", "mission_verified": True})
        device = Mock(target=self.second)
        with patch.object(farm, "Device", return_value=device), patch.object(farm, "Detector"):
            engine = farm.Engine(instance=self.second)
        self.assertFalse(engine.mission_verified)
        self.assertEqual(engine.data["stamina_until"], 0)
        self.assertNotIn("pending", engine.data)
        engine.save()
        self.assertEqual(farm.read_json(engine.progress)["instance"]["key"], self.second.key)
        self.assertEqual(farm.read_json(self.runtime / "universal.json")["stamina_until"], 5000)

    def test_device_commands_and_reconnect_stay_frozen_after_selection_changes(self):
        device = farm.Device(self.first)
        farm.write_json(self.runtime / "instance_selection.json", self.second.to_dict())
        farm.subprocess.run.side_effect = None
        farm.subprocess.run.return_value = subprocess.CompletedProcess([], 0, b"id-one\n", b"")
        device.command(["shell", "settings", "get", "secure", "android_id"])
        device.connect()
        for call in farm.subprocess.run.call_args_list:
            argv = call.args[0]
            self.assertIn(self.first.endpoint, argv)
            self.assertNotIn(self.second.endpoint, argv)
            self.assertEqual(argv[1:5], ["-H", "127.0.0.1", "-P", "5037"])

    def test_device_identity_mismatch_never_dispatches_input(self):
        device = farm.Device(self.first)
        farm.subprocess.run.side_effect = None
        farm.subprocess.run.return_value = subprocess.CompletedProcess([], 0, b"wrong-account", b"")
        with self.assertRaisesRegex(farm.ActionNotDispatched, "identity mismatch"):
            device.perform({"id": "retry", "kind": "tap", "point": [450, 1500]})
        self.assertEqual(farm.subprocess.run.call_count, 1)
        self.assertNotIn("input", farm.subprocess.run.call_args.args[0])

    def test_device_refuses_changed_mapping_before_connect(self):
        device = farm.Device(self.first)
        farm.validate_mapping.side_effect = RuntimeError("Mapping changed")
        with self.assertRaisesRegex(RuntimeError, "Mapping changed"):
            device.connect()
        farm.subprocess.run.assert_not_called()

    def test_manual_device_vendor_is_reverified_before_input(self):
        manual = Instance("", "manual@127.0.0.1:5588", "Manual", str(self.adb),
                          "127.0.0.1:5588", "id-one")
        farm.verify_identity.side_effect = RuntimeError("BlueStacks identity unavailable")
        device = farm.Device(manual)
        with self.assertRaisesRegex(RuntimeError, "BlueStacks identity"):
            device.verify()
        farm.subprocess.run.assert_not_called()

    def test_start_refuses_unverified_target_without_launch_or_selection_write(self):
        device = Mock()
        device.connect.side_effect = RuntimeError("Android identity mismatch")
        farm.STOP.write_text("old stop")
        with patch.object(farm, "Device", return_value=device):
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                farm.start(instance=self.first)
        farm.subprocess.run.assert_not_called()
        self.assertEqual(farm.STOP.read_text(), "old stop")
        self.assertFalse((self.runtime / "instance_selection.json").exists())

    def test_supervisor_respawns_workers_with_same_frozen_target(self):
        launches = []
        def launch(argv, **kwargs):
            launches.append(argv)
            farm.write_json(self.runtime / "instance_selection.json", self.second.to_dict())
            child = Mock(pid=1234, returncode=1)
            child.poll.return_value = 1
            if len(launches) == 2:
                farm.STOP.write_text("fixture stop")
            return child
        farm.subprocess.Popen.side_effect = launch
        with patch.object(farm.time, "sleep"), patch.object(farm.time, "monotonic", side_effect=range(100)), \
             patch.object(farm, "_loot_end_process"):
            farm.supervise(instance=self.first)
        self.assertEqual(len(launches), 2)
        for argv in launches:
            target = json.loads(argv[argv.index("--target") + 1])
            self.assertEqual(target["android_id"], self.first.android_id)
            self.assertEqual(target["endpoint"], self.first.endpoint)

    def test_status_reports_active_target_and_namespaced_progress_read_only(self):
        farm.write_json(self.runtime / "supervisor.json", {"pid": 1234, "instance": self.first.to_dict()})
        farm.write_json(farm.state_path("universal.json", self.first), {"counts": {"started": 3}})
        farm.process_may_be_alive.side_effect = lambda record: record.get("pid") == 1234
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            farm.status()
        status = json.loads(output.getvalue())
        self.assertEqual(status["instance"]["key"], self.first.key)
        self.assertTrue(status["instance_locked"])
        self.assertEqual(status["farm"]["counts"]["started"], 3)
        self.assertFalse((self.runtime / "instance_selection.json").exists())

    def test_status_retains_missing_saved_target_with_error_and_required_selection(self):
        farm.write_json(self.runtime / "instance_selection.json", self.first.to_dict())
        farm.validate_mapping.side_effect = RuntimeError("Saved mapping unavailable")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            farm.status()
        status = json.loads(output.getvalue())
        self.assertEqual(status["instance"]["key"], self.first.key)
        self.assertTrue(status["instance_selection_required"])
        self.assertIn("unavailable", status["instance_error"])

    def test_inferred_target_requires_apply_and_status_does_not_persist_it(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            farm.status()
        status = json.loads(output.getvalue())
        self.assertEqual(status["instance"]["key"], self.first.key)
        self.assertTrue(status["instance_selection_required"])
        self.assertFalse((self.runtime / "instance_selection.json").exists())

    def test_cli_selection_status_and_start_use_the_same_custom_target(self):
        farm.discover.return_value = {"instances": [self.first, self.second], "detail": ""}
        selected_output = io.StringIO()
        with patch.object(farm.sys, "argv", ["farm.py", "select-instance", "--instance-key", self.second.key]), \
             contextlib.redirect_stdout(selected_output):
            farm.main()
        saved = farm.read_json(self.runtime / "instance_selection.json")
        self.assertEqual(json.loads(selected_output.getvalue()), saved)
        self.assertEqual(saved["endpoint"], self.second.endpoint)
        self.assertEqual(saved["key"], self.second.key)

        status_output = io.StringIO()
        with patch.object(farm.sys, "argv", ["farm.py", "status"]), \
             contextlib.redirect_stdout(status_output):
            farm.main()
        status = json.loads(status_output.getvalue())
        self.assertEqual(status["instance"], saved)
        self.assertFalse(status["instance_selection_required"])

        def independent_launch(argv, **kwargs):
            farm.write_json(self.runtime / "supervisor.json", {
                "pid": 9001, "pid_identity": "9001:fixture",
                "worker_pid": 9002, "worker_pid_identity": "9002:fixture",
                "stage": "universal", "instance": saved,
            })
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ReturnValue": 0, "ProcessId": 9001}), "")

        farm.subprocess.run.side_effect = independent_launch
        format_command = subprocess.list2cmdline
        with patch.object(farm.sys, "argv", ["farm.py", "start"]), \
             patch.object(farm, "Device") as device, \
             patch.object(farm, "alive", side_effect=lambda pid: pid in (9001, 9002)), \
             patch.object(farm.subprocess, "list2cmdline", wraps=format_command) as launch_command, \
             contextlib.redirect_stdout(io.StringIO()):
            farm.main()
        device.assert_called_once_with(self.second)
        device.return_value.connect.assert_called_once_with()
        farm.subprocess.run.assert_called_once()
        argv = launch_command.call_args.args[0]
        self.assertIn("supervise", argv)
        pinned = json.loads(argv[argv.index("--target") + 1])
        self.assertEqual(pinned, saved)
        self.assertEqual(pinned["endpoint"], "127.0.0.1:5599")
        launch_env = farm.subprocess.run.call_args.kwargs["env"]
        self.assertEqual(launch_env["NBR_LAUNCH_COMMAND"], format_command(argv))
        self.assertEqual(farm.read_json(self.runtime / "supervisor.json")["instance"], saved)

    def test_cli_manual_selection_resolves_and_verifies_before_persisting(self):
        with patch.object(farm, "resolve_manual", return_value=self.second) as resolve, \
             patch.object(farm.sys, "argv", ["farm.py", "select-instance", "--instance-name", "Custom account",
                                              "--endpoint", self.second.endpoint]), \
             contextlib.redirect_stdout(io.StringIO()):
            farm.main()
        resolve.assert_called_once_with("Custom account", self.second.endpoint)
        farm.verify_identity.assert_called_once_with(self.second, connect=True)
        self.assertEqual(farm.read_json(self.runtime / "instance_selection.json"), self.second.to_dict())


class ProcessLivenessTests(unittest.TestCase):
    def test_access_denied_is_uncertain_and_blocks_selection(self):
        with patch.object(farm, "alive", return_value=False), \
             patch.object(farm.ctypes.windll.kernel32, "OpenProcess", return_value=0), \
             patch.object(farm.ctypes.windll.kernel32, "GetLastError", return_value=5):
            self.assertTrue(farm.process_may_be_alive({"pid": 1234}))

    def test_invalid_pid_confirms_exit(self):
        with patch.object(farm, "alive", return_value=False), \
             patch.object(farm.ctypes.windll.kernel32, "OpenProcess", return_value=0), \
             patch.object(farm.ctypes.windll.kernel32, "GetLastError", return_value=87):
            self.assertFalse(farm.process_may_be_alive({"pid": 1234}))

    def test_proven_recycled_pid_does_not_block_new_selection(self):
        with patch.object(farm, "alive", return_value=True), \
             patch.object(farm, "process_identity", return_value=("1234:new", 1)):
            self.assertFalse(farm.process_may_be_alive({"pid": 1234, "pid_identity": "1234:old"}))

    def test_unreadable_creation_identity_is_uncertain(self):
        with patch.object(farm, "alive", return_value=True), \
             patch.object(farm, "process_identity", side_effect=OSError("access denied")):
            self.assertTrue(farm.process_may_be_alive({"pid": 1234, "pid_identity": "1234:old"}))


if __name__ == "__main__":
    unittest.main()
