"""Exit-and-stop safety checks; no real controller/process/device is controlled."""
import copy
from contextlib import nullcontext
import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.backend import controller as manager_backend
from app.backend.controller import ControllerBackend, ControllerError, _terminate_verified_process


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        self.backend = ControllerBackend(self.root)
        self.current = []
        self.records = []
        self.killed = []
        self.lifetimes = []
        self.safe_stop = Mock(return_value="Stopped")

        class FakeLifetime:
            def __init__(lease, process):
                lease.process = process.copy()
                lease.exited = None
                lease.closed = False
                self.lifetimes.append(lease)

            def contains(lease, child):
                created = int(lease.process["identity"].split(":")[1])
                child_created = int(child["identity"].split(":")[1])
                return created <= child_created and (lease.exited is None or child_created <= lease.exited)

            def alive(lease):
                return any(item["pid"] == lease.process["pid"] and item["identity"] == lease.process["identity"]
                           for item in self.current)

            def close(lease):
                lease.closed = True

        patches = [patch.object(self.backend, "_shutdown_inventory", side_effect=lambda: copy.deepcopy(self.current)),
                   patch.object(self.backend, "command", self.safe_stop),
                   patch.object(manager_backend.farm, "recorded_processes", side_effect=lambda: copy.deepcopy(self.records)),
                   patch.object(manager_backend.farm, "process_may_be_alive", return_value=False),
                   patch.object(manager_backend.farm, "lock", side_effect=lambda *a, **kw: nullcontext()),
                   patch.object(manager_backend.farm, "lock_busy", return_value=False),
                   patch.object(manager_backend, "_ProcessLifetime", FakeLifetime),
                   patch.object(manager_backend, "_terminate_verified_process", side_effect=self.terminate),
                   patch.object(manager_backend.subprocess, "run", side_effect=AssertionError("Unexpected live subprocess"))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def process(self, pid, role="worker", parent=0, *, ticks=None, executable=None, args=None):
        return dict(pid=pid, parent=parent, identity=f"{pid}:{ticks or pid * 100}",
                    executable=executable or r"C:\Python\pythonw.exe",
                    args=args or [r"C:\Python\pythonw.exe", str(self.root / "src" / "farm.py"), role])

    def adb(self, pid, parent, *, server=False, ticks=None):
        return self.process(pid, parent=parent, ticks=ticks, executable=r"C:\BlueStacks\HD-Adb.exe",
                            args=[r"C:\BlueStacks\HD-Adb.exe", *( ["fork-server", "server"] if server else
                                  ["-H", "127.0.0.1", "-P", "5037", "-s", "127.0.0.1:5555", "shell", "input", "tap", "1", "2"])])

    def terminate(self, process):
        self.safe_stop.assert_called_once_with("stop")
        self.assertTrue((self.root / "runtime" / "STOP").exists())
        self.killed.append(process["pid"])
        self.current = [item for item in self.current if (item["pid"], item["identity"]) !=
                        (process["pid"], process["identity"])]

    def test_graceful_stop_exits_without_force_and_reports_verified_success(self):
        self.current = [self.process(101)]
        self.safe_stop.side_effect = lambda action: self.current.clear()
        progress = []
        self.assertIn("stopped", self.backend.shutdown(progress.append))
        self.assertEqual(self.killed, [])
        self.assertIn("verified", progress[-1])
        self.assertTrue((self.root / "runtime" / "stop_generation.json").is_file())

    def test_fallback_stops_supervisor_worker_and_private_children_only(self):
        self.current = [self.process(201, parent=101), self.adb(301, 201), self.process(101, "supervise"),
                        self.adb(401, 201, server=True),
                        self.process(501, parent=201, executable=r"C:\BlueStacks\HD-Player.exe", args=["HD-Player.exe"]),
                        self.process(601, args=["python.exe", str(self.root / "unrelated.py")]),
                        self.process(701, args=["python.exe", str(self.root.parent / "other" / "src" / "farm.py"), "worker"]),
                        self.process(os.getpid())]
        self.backend.shutdown()
        self.assertEqual(self.killed, [101, 201, 301])
        self.assertEqual({item["pid"] for item in self.current}, {401, 501, 601, 701, os.getpid()})
        self.assertTrue(all(lease.closed for lease in self.lifetimes))

    def test_unclassified_descendant_remains_untouched_and_blocks_success(self):
        self.current = [self.process(101), self.process(201, parent=101, args=["python.exe", "unknown.py"])]
        with self.assertRaisesRegex(ControllerError, "unclassified.*201.*left running"):
            self.backend.shutdown()
        self.assertEqual(self.killed, [101])
        self.assertTrue(all(lease.closed for lease in self.lifetimes))

    def test_adb_with_unreadable_arguments_is_not_assumed_to_be_shared_server(self):
        child = self.adb(201, 101)
        child["args"] = []
        self.current = [self.process(101), child]
        with self.assertRaisesRegex(ControllerError, "unclassified.*201.*left running"):
            self.backend.shutdown()
        self.assertEqual(self.killed, [101])

    def test_late_child_created_just_before_parent_termination_is_still_stopped(self):
        self.current = [self.process(101)]
        def terminate_with_racing_child(process):
            if process["pid"] == 101:
                self.current.append(self.adb(201, 101, ticks=10500))
                self.lifetimes[0].exited = 10600
            self.terminate(process)
        with patch.object(manager_backend, "_terminate_verified_process", side_effect=terminate_with_racing_child):
            self.backend.shutdown()
        self.assertEqual(self.killed, [101, 201])

    def test_final_scan_after_parent_exit_catches_late_child(self):
        self.current = [self.process(101)]
        calls = []
        def inventory():
            calls.append(True)
            snapshot = copy.deepcopy(self.current)
            if len(calls) == 3:  # First observation after force misses the child.
                self.lifetimes[0].exited = 10600
                self.current.append(self.adb(201, 101, ticks=10500))
            return snapshot
        with patch.object(self.backend, "_shutdown_inventory", side_effect=inventory):
            self.backend.shutdown()
        self.assertEqual(self.killed, [101, 201])

    def test_child_of_recycled_parent_created_after_parent_exit_is_not_owned(self):
        self.current = [self.process(101)]
        def stop(action):
            self.lifetimes[0].exited = 10500
            self.current = [self.adb(201, 101, ticks=10600)]
        self.safe_stop.side_effect = stop
        self.backend.shutdown()
        self.assertEqual(self.killed, [])

    def test_children_and_claims_survive_parent_exit_and_record_erasure(self):
        self.current = [self.process(101, "supervise"), self.adb(201, 101)]
        self.records = [dict(pid=101, pid_identity="101:10100")]
        def stop(action):
            self.current = [item for item in self.current if item["pid"] != 101]
            self.records.clear()
        self.safe_stop.side_effect = stop
        self.backend.shutdown()
        self.assertEqual(self.killed, [201])

    def test_known_orphan_notification_and_ocr_are_owned_by_exact_script(self):
        self.current = [self.process(101, args=["pythonw.exe", str(self.root / "scripts" / "loss_notify.py")]),
                        self.process(201, executable=r"C:\Windows\powershell.exe",
                                     args=["powershell.exe", "-NoProfile", "-File", str(self.root / "scripts" / "loss_ocr.ps1"), "-ImagePath", "fixture.png"])]
        self.backend.shutdown()
        self.assertEqual(self.killed, [101, 201])

    def test_recycled_tracked_and_recorded_pids_are_left_untouched(self):
        self.current = [self.process(101)]
        self.records = [dict(pid=101, pid_identity="101:10100")]
        self.safe_stop.side_effect = lambda action: self.current.__setitem__(0, self.process(
            101, ticks=99999, args=["pythonw.exe", "unrelated.py"]))
        self.backend.shutdown()
        self.assertEqual(self.killed, [])

    def test_child_older_than_its_recycled_parent_is_not_owned(self):
        self.current = [self.process(201, ticks=90000), self.adb(301, 201, ticks=80000)]
        self.backend.shutdown()
        self.assertEqual(self.killed, [201])

    def test_unidentified_recorded_live_process_blocks_success_without_kill(self):
        self.current = [self.process(101, args=["pythonw.exe", "unrelated.py"])]
        self.records = [dict(pid=101)]
        with self.assertRaisesRegex(ControllerError, "Cannot verify ownership"):
            self.backend.shutdown()
        self.assertEqual(self.killed, [])

    def test_access_denied_record_not_in_inventory_blocks_success(self):
        self.records = [dict(pid=101, pid_identity="101:10100")]
        with patch.object(manager_backend.farm, "process_may_be_alive", return_value=True):
            with self.assertRaisesRegex(ControllerError, "Cannot verify exit"):
                self.backend.shutdown()
        self.assertEqual(self.killed, [])

    def test_missing_creation_identity_never_allows_force_or_success(self):
        self.current = [self.process(101)]
        self.current[0]["identity"] = None
        with self.assertRaisesRegex(ControllerError, "ownership"):
            self.backend.shutdown()
        self.safe_stop.assert_called_once_with("stop")
        self.assertEqual(self.killed, [])

    def test_retained_private_child_with_inaccessible_metadata_blocks_success(self):
        self.current = [self.process(101), self.adb(201, 101)]
        def stop(action):
            self.current = [dict(self.current[1], identity=None, executable=None, args=[])]
        self.safe_stop.side_effect = stop
        with self.assertRaisesRegex(ControllerError, "ownership"):
            self.backend.shutdown()
        self.assertEqual(self.killed, [])
        self.assertTrue(all(lease.closed for lease in self.lifetimes))

    def test_safe_stop_failure_still_allows_verified_bounded_fallback(self):
        self.current = [self.process(101)]
        self.safe_stop.side_effect = ControllerError("safe stop timed out")
        progress = []
        self.backend.shutdown(progress.append)
        self.assertEqual(self.killed, [101])
        self.assertTrue(any("timed out" in message for message in progress))

    def test_failed_termination_never_reports_success(self):
        self.current = [self.process(101)]
        with patch.object(manager_backend, "_terminate_verified_process", return_value=None):
            with self.assertRaisesRegex(ControllerError, "Shutdown incomplete.*101"):
                self.backend.shutdown(timeout=0)

    def test_inventory_failure_still_requests_safe_stop_and_reports_error(self):
        with patch.object(self.backend, "_shutdown_inventory", side_effect=ControllerError("inventory unavailable")):
            with self.assertRaisesRegex(ControllerError, "inventory unavailable"):
                self.backend.shutdown()
        self.safe_stop.assert_called_once_with("stop")

    def test_busy_controller_lock_prevents_false_success(self):
        with patch.object(manager_backend.farm, "lock_busy", return_value=True):
            with self.assertRaisesRegex(ControllerError, "controller lock"):
                self.backend.shutdown()


class ProcessPrimitiveTests(unittest.TestCase):
    def test_retained_native_handle_proves_parent_lifetime_and_is_closed(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 1234
        kernel.WaitForSingleObject.return_value = 0
        def times(handle, created, exited, *unused):
            created._obj.dwLowDateTime = 100
            exited._obj.dwLowDateTime = 200
            return True
        kernel.GetProcessTimes.side_effect = times
        with patch.object(ctypes, "windll", Mock(kernel32=kernel)):
            lifetime = manager_backend._ProcessLifetime(dict(pid=11, identity="11:100"))
            self.assertTrue(lifetime.contains(dict(identity="22:150")))
            self.assertFalse(lifetime.contains(dict(identity="22:250")))
            self.assertFalse(lifetime.alive())
            lifetime.close()
            lifetime.close()
        kernel.CloseHandle.assert_called_once_with(1234)

    def test_retained_handle_identity_mismatch_closes_without_process_control(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 1234
        kernel.GetProcessTimes.return_value = True
        with patch.object(ctypes, "windll", Mock(kernel32=kernel)):
            with self.assertRaisesRegex(ControllerError, "changed"):
                manager_backend._ProcessLifetime(dict(pid=11, identity="11:100"))
        kernel.CloseHandle.assert_called_once_with(1234)
        kernel.TerminateProcess.assert_not_called()

    def test_termination_revalidates_identity_using_same_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 1234
        kernel.WaitForSingleObject.return_value = 258
        kernel.TerminateProcess.return_value = True
        def times(handle, created, *unused):
            self.assertEqual(handle, 1234)
            created._obj.dwLowDateTime = 4567
            created._obj.dwHighDateTime = 0
            return True
        kernel.GetProcessTimes.side_effect = times
        with patch.object(ctypes, "windll", Mock(kernel32=kernel)):
            _terminate_verified_process(dict(pid=111, identity="111:4567"))
        kernel.TerminateProcess.assert_called_once_with(1234, 1)
        kernel.CloseHandle.assert_called_once_with(1234)

    def test_native_pid_reuse_and_manager_are_never_terminated(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 1234
        kernel.GetProcessTimes.return_value = True  # Zero creation ticks differs.
        with patch.object(ctypes, "windll", Mock(kernel32=kernel)):
            _terminate_verified_process(dict(pid=111, identity="111:4567"))
            with self.assertRaisesRegex(ControllerError, "Manager"):
                _terminate_verified_process(dict(pid=os.getpid(), identity="irrelevant"))
        kernel.TerminateProcess.assert_not_called()
        kernel.OpenProcess.assert_called_once()

    def test_inventory_matches_cim_microseconds_to_precise_native_identity(self):
        backend = ControllerBackend()
        completed = subprocess.CompletedProcess([], 0, json.dumps([dict(
            pid=111, parent=222, identity="111:123450", executable="pythonw.exe", command="fixture")]), "")
        with patch.object(manager_backend.subprocess, "run", return_value=completed) as run, \
             patch.object(manager_backend, "_command_arguments", return_value=["pythonw.exe", "fixture.py"]), \
             patch.object(manager_backend.farm, "process_identity", return_value=("111:123457", 1)):
            self.assertEqual(backend._shutdown_inventory()[0]["identity"], "111:123457")
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
