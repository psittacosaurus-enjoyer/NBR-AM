"""Offline manager checks. No test sends farming commands or contacts ADB.

Qt tests create the real window on the offscreen platform with a fake backend.
Controller adapter tests replace subprocesses, process probes and device access.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import manager_backend
from manager_backend import ControllerBackend, ControllerError
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from manager_gui import ManagerWindow


def stopped_snapshot(stage="scrolls-a"):
    return {"running": False, "active_pids": [], "stop_requested": False,
            "selected_stage": stage, "stage": stage, "supervisor": {}, "farm": {},
            "started_at": None, "worker_running": False, "supervisor_running": False}


def running_snapshot(stage="rich-b"):
    state = stopped_snapshot(stage)
    state.update(running=True, active_pids=[1001, 1002, 1003],
                 supervisor_running=True, worker_running=True,
                 started_at=time.time() - 125,
                 supervisor={"pid": 1001, "worker_pid": 1002, "stage": stage},
                 farm={"worker_pid": 1003, "screen": "IN_BATTLE", "stage": stage})
    return state


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        self.backend = ControllerBackend(self.root)

    def test_stage_catalog_matches_existing_controller(self):
        self.assertEqual(self.backend.stages(), [
            {"id": "scrolls-a", "name": "All Scrolls [A]"},
            {"id": "rich-b", "name": "Getting Rich Quick! [B]"},
            {"id": "practice-part-1", "name": "Practice Training [Part 1]"},
            {"id": "night-of-tragedy-a", "name": "Night of Tragedy [A]"}])

    def test_start_select_and_account_stop_use_existing_cli_only(self):
        result = subprocess.CompletedProcess([], 0, "Success\n", "")
        with patch.object(manager_backend.subprocess, "run", return_value=result) as run:
            for action, stage in (("start", "rich-b"), ("select", "scrolls-a"),
                                  ("start", "practice-part-1"), ("select", "practice-part-1"),
                                  ("start", "night-of-tragedy-a"), ("select", "night-of-tragedy-a"),
                                  ("stop", "rich-b")):
                self.assertEqual(self.backend.command(action, stage), "Success")
                args = run.call_args.args[0]
                self.assertEqual(args[:3], [str(self.backend.python), str(self.root / "src" / "farm.py"), action])
                self.assertEqual(args[3:], [] if action == "stop" else ["--stage", stage])
                self.assertEqual(run.call_args.kwargs["cwd"], self.root)
                self.assertFalse(run.call_args.kwargs.get("shell", False))
                self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_invalid_controls_never_create_a_process(self):
        with patch.object(manager_backend.subprocess, "run") as run:
            for action, stage in (("kill", None), ("worker", "rich-b"),
                                  ("start", None), ("select", None),
                                  ("start", "unrecognized"), ("stop", "unrecognized")):
                with self.subTest(action=action, stage=stage), self.assertRaises(ValueError):
                    self.backend.command(action, stage)
            run.assert_not_called()

    def test_failed_missing_and_timed_out_cli_return_actionable_errors(self):
        result = subprocess.CompletedProcess([], 2, "", "Existing controller alive")
        with patch.object(manager_backend.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ControllerError, "Existing controller alive"):
                self.backend.command("start", "rich-b")
        for error, message in ((FileNotFoundError("missing interpreter"), "Cannot run"),
                               (subprocess.TimeoutExpired("fake", 45), "timed out")):
            with patch.object(manager_backend.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(ControllerError, message):
                    self.backend.command("stop")

    def test_snapshot_recovers_existing_worker_and_process_elapsed_time(self):
        state = running_snapshot()
        state["supervisor"]["started"] = 200
        with patch.object(self.backend, "_invoke", return_value=json.dumps(state)) as invoke, \
             patch.object(manager_backend.farm, "alive", side_effect=lambda pid: pid in {1001, 1003}), \
             patch.object(manager_backend, "_process_started", return_value=100) as started:
            result = self.backend.snapshot()
        invoke.assert_called_once_with("status", timeout=15)
        self.assertTrue(result["worker_running"])
        self.assertTrue(result["supervisor_running"])
        self.assertEqual(result["started_at"], 100)
        started.assert_called_once_with(1001)

    def test_snapshot_preserves_stopped_and_rejects_invalid_status(self):
        with patch.object(self.backend, "_invoke", return_value=json.dumps(stopped_snapshot())), \
             patch.object(manager_backend.farm, "alive", return_value=False):
            self.assertFalse(self.backend.snapshot()["worker_running"])
        malformed = ["not json", "[]", "{}"]
        for changes in ({"running": "yes"}, {"active_pids": [True]},
                        {"stage": "unknown"}, {"farm": []},
                        {"supervisor": {"pid": -10}}):
            malformed.append(json.dumps({**stopped_snapshot(), **changes}))
        for value in malformed:
            with self.subTest(value=value), patch.object(self.backend, "_invoke", return_value=value):
                with self.assertRaises(ControllerError):
                    self.backend.snapshot()

    def test_connection_unavailable_does_not_start_adb_or_touch_device(self):
        adb = self.root / "fake-adb.exe"
        adb.touch()
        with patch.object(manager_backend.farm, "ADB", adb), \
             patch.object(manager_backend.socket, "create_connection", side_effect=OSError("offline")), \
             patch.object(manager_backend.farm, "Device") as device, \
             patch.object(manager_backend.subprocess, "run") as run:
            result = self.backend.connection()
        self.assertFalse(result["available"])
        device.assert_not_called()
        run.assert_not_called()

    def test_connection_requires_existing_device_identity_verification(self):
        adb = self.root / "fake-adb.exe"
        adb.touch()
        with patch.object(manager_backend.farm, "ADB", adb), \
             patch.object(manager_backend.socket, "create_connection"), \
             patch.object(manager_backend.farm, "Device") as device:
            self.assertTrue(self.backend.connection()["available"])
            device.return_value.verify.assert_called_once_with()
            device.return_value.connect.assert_not_called()
            device.return_value.verify.side_effect = RuntimeError("Android identity is not acc1")
            result = self.backend.connection()
            self.assertFalse(result["available"])
            self.assertIn("not acc1", result["detail"])

    def test_log_partial_records_append_and_rotation(self):
        path = self.root / "runtime" / "events.jsonl"
        first = json.dumps({"time": 1, "event": "worker_started", "stage": "rich-b"})
        second = json.dumps({"time": 2, "event": "screen", "screen": "IN_BATTLE"})
        path.write_text(first + "\n" + second[:12], encoding="utf-8")
        lines = self.backend.read_events()
        self.assertEqual(len(lines), 1)
        self.assertIn("worker started", lines[0])
        self.assertEqual(self.backend.read_events(), [])
        with path.open("a", encoding="utf-8") as stream:
            stream.write(second[12:] + "\n")
        self.assertIn("IN_BATTLE", self.backend.read_events()[0])
        path.rename(path.with_suffix(".old"))
        path.write_text(first + "\n", encoding="utf-8")
        self.assertIn("worker started", self.backend.read_events()[0])
        path.write_text("bad\n", encoding="utf-8")
        self.assertIn("Unreadable event", self.backend.read_events()[0])

    def test_initial_log_is_bounded_and_poll_does_not_repeat(self):
        path = self.root / "runtime" / "events.jsonl"
        path.write_text("".join(json.dumps({"time": i + 1, "event": "event", "index": i}) + "\n"
                                for i in range(150)), encoding="utf-8")
        lines = self.backend.read_events()
        self.assertEqual(len(lines), 100)
        self.assertIn("index: 149", lines[-1])
        self.assertEqual(self.backend.read_events(), [])


class FakeBackend:
    """Thread-safe fixtures; it has no reference to the real controller."""
    def __init__(self, state=None):
        self.state = state or stopped_snapshot()
        self.commands = []
        self.calls = []
        self.events = []
        self.command_gate = None
        self.snapshot_gate = None
        self.command_error = None
        self.snapshot_error = None
        self.available = True
        self.lock = threading.Lock()

    def record(self, action):
        with self.lock:
            self.calls.append((action, threading.get_ident()))

    def stages(self):
        self.record("stages")
        return [{"id": "scrolls-a", "name": "All Scrolls [A]"},
                {"id": "rich-b", "name": "Getting Rich Quick! [B]"},
                {"id": "practice-part-1", "name": "Practice Training [Part 1]"},
                {"id": "night-of-tragedy-a", "name": "Night of Tragedy [A]"}]

    def snapshot(self):
        self.record("snapshot")
        if self.snapshot_gate is not None:
            self.snapshot_gate.wait(5)
        if self.snapshot_error:
            raise ControllerError(self.snapshot_error)
        with self.lock:
            return copy.deepcopy(self.state)

    def read_events(self):
        self.record("read_events")
        with self.lock:
            lines, self.events = self.events, []
            return lines

    def connection(self):
        self.record("connection")
        return {"available": self.available, "instance": "acc1 · Pie64 · 127.0.0.1:5555",
                "detail": "Identity verified" if self.available else "BlueStacks unavailable"}

    def command(self, action, stage=None):
        self.record("command")
        with self.lock:
            self.commands.append((action, stage))
        if self.command_gate is not None:
            self.command_gate.wait(5)
        if self.command_error:
            raise ControllerError(self.command_error)
        with self.lock:
            if action == "start":
                self.state = running_snapshot(stage)
            elif action == "stop":
                self.state = {**stopped_snapshot(self.state["stage"]), "stop_requested": True}
            elif action == "select":
                self.state = stopped_snapshot(stage)
        return f"{action} fixture completed"


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.windows = []
        self.backends = []

    def tearDown(self):
        for backend in self.backends:
            for gate in (backend.command_gate, backend.snapshot_gate):
                if gate is not None:
                    gate.set()
        for window in self.windows:
            window.close()
        QTest.qWait(30)
        for window in self.windows:
            window.deleteLater()
        self.app.processEvents()

    def wait_until(self, predicate, message="GUI did not reach expected state", timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), message)

    def window(self, backend=None):
        backend = backend or FakeBackend()
        self.backends.append(backend)
        window = ManagerWindow(backend=backend, auto_poll=False)
        self.windows.append(window)
        window.show()
        self.wait_until(lambda: window.stage_combo.count() == 4, "Stages did not load")
        window.refresh()
        window.refresh_connection()
        self.wait_until(lambda: window._snapshot_known and bool(window._connection), "Initial monitoring did not finish")
        return window, backend

    def test_real_window_launches_with_four_friendly_stages_and_stopped_controls(self):
        window, backend = self.window()
        self.assertTrue(window.isVisible())
        self.assertEqual(window.windowTitle(), "NBR Automation Manager")
        self.assertEqual([window.stage_combo.itemText(i) for i in range(4)],
                         ["All Scrolls [A]", "Getting Rich Quick! [B]", "Practice Training [Part 1]",
                          "Night of Tragedy [A]"])
        self.assertEqual(window.status_text, "STOPPED")
        self.assertTrue(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        self.assertTrue(window.stage_combo.isEnabled())
        self.assertIn("acc1", window.instance_label.text())
        self.assertEqual(backend.commands, [])

    def test_practice_selection_start_stop_uses_existing_controller_and_locks_dropdown(self):
        self.check_manual_stage_selection_start_stop("practice-part-1", "Practice Training [Part 1]")

    def test_night_selection_start_stop_uses_existing_controller_and_locks_dropdown(self):
        self.check_manual_stage_selection_start_stop("night-of-tragedy-a", "Night of Tragedy [A]")

    def check_manual_stage_selection_start_stop(self, stage, name):
        window, backend = self.window()
        window.stage_combo.setCurrentIndex(window.stage_combo.findData(stage))
        self.wait_until(lambda: backend.commands == [("select", stage)] and
                        window.stage_combo.isEnabled())
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: window.status_text == "RUNNING" and window.stop_button.isEnabled())
        self.assertFalse(window.stage_combo.isEnabled())
        self.assertEqual(window.stage_combo.currentData(), stage)
        self.assertIn(name, window.current_stage_label.text())
        self.assertTrue(window.runtime_label.text().startswith("00:02:"))
        QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: window.status_text == "STOPPED" and window.stage_combo.isEnabled())
        self.assertEqual(backend.commands, [("select", stage),
                                           ("start", stage), ("stop", None)])

    def test_reopening_adopts_run_stage_elapsed_and_never_stops_on_close(self):
        backend = FakeBackend(running_snapshot())
        first, _ = self.window(backend)
        self.assertEqual(first.status_text, "RUNNING")
        self.assertEqual(first.stage_combo.currentData(), "rich-b")
        self.assertFalse(first.stage_combo.isEnabled())
        self.assertFalse(first.start_button.isEnabled())
        self.assertTrue(first.stop_button.isEnabled())
        self.assertTrue(first.runtime_label.text().startswith("00:02:"))
        first.close()
        second, _ = self.window(backend)
        self.assertEqual(second.status_text, "RUNNING")
        self.assertEqual(second.stage_combo.currentData(), "rich-b")
        self.assertEqual(backend.commands, [])

    def test_stage_change_persists_through_existing_select_command_and_reopen(self):
        window, backend = self.window()
        window.stage_combo.setCurrentIndex(window.stage_combo.findData("rich-b"))
        self.wait_until(lambda: backend.commands == [("select", "rich-b")] and
                        window.stage_combo.isEnabled())
        self.assertEqual(window.stage_combo.currentData(), "rich-b")
        window.close()
        reopened, _ = self.window(backend)
        self.assertEqual(reopened.stage_combo.currentData(), "rich-b")
        self.assertEqual(backend.commands, [("select", "rich-b")])

    def test_start_stop_requests_are_async_and_duplicate_clicks_are_blocked(self):
        window, backend = self.window(FakeBackend(stopped_snapshot("rich-b")))
        backend.command_gate = threading.Event()
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: backend.commands == [("start", "rich-b")])
        window.start_automation()
        window.start_automation()
        self.assertEqual(window.status_text, "STOPPED", "A click must not invent a running process")
        self.assertFalse(window.stage_combo.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        ticks = []
        timer = QTimer(window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        QTest.qWait(100)
        timer.stop()
        self.assertGreaterEqual(len(ticks), 3, "GUI event loop blocked during controller command")
        self.assertEqual(backend.commands, [("start", "rich-b")])
        backend.command_gate.set()
        self.wait_until(lambda: window.status_text == "RUNNING" and window.stop_button.isEnabled())
        backend.command_gate = threading.Event()
        QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: len(backend.commands) == 2)
        window.stop_automation()
        self.assertEqual(backend.commands, [("start", "rich-b"), ("stop", None)])
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        backend.command_gate.set()
        self.wait_until(lambda: window.status_text == "STOPPED" and window.start_button.isEnabled())
        self.assertTrue(all(thread != threading.get_ident() for _, thread in backend.calls))

    def test_slow_monitoring_keeps_window_responsive_and_does_not_overlap(self):
        window, backend = self.window()
        backend.snapshot_gate = threading.Event()
        before = sum(name == "snapshot" for name, _ in backend.calls)
        window.refresh()
        self.wait_until(lambda: sum(name == "snapshot" for name, _ in backend.calls) == before + 1)
        for _ in range(5):
            window.refresh()
        fired = []
        QTimer.singleShot(20, lambda: fired.append(True))
        QTest.qWait(80)
        self.assertTrue(fired)
        self.assertEqual(sum(name == "snapshot" for name, _ in backend.calls), before + 1)
        backend.snapshot_gate.set()
        self.wait_until(lambda: not window._poll_busy)

    def test_verified_starting_stopping_states_use_expected_colors(self):
        window, _ = self.window()
        starting = running_snapshot()
        starting["worker_running"] = False
        window.apply_snapshot(starting)
        self.assertEqual(window.status_text, "STARTING")
        self.assertIn(window.COLORS["STARTING"], window.status_label.styleSheet())
        self.assertFalse(window.start_button.isEnabled())
        window.apply_snapshot({**running_snapshot(), "stop_requested": True})
        self.assertEqual(window.status_text, "STOPPING")
        self.assertIn(window.COLORS["STOPPING"], window.status_label.styleSheet())
        self.assertFalse(window.stop_button.isEnabled())
        window.apply_snapshot({**stopped_snapshot(), "stop_requested": True})
        self.assertEqual(window.status_text, "STOPPED")

    def test_unexpected_exit_is_visible_and_logged(self):
        window, backend = self.window(FakeBackend(running_snapshot()))
        backend.state = stopped_snapshot("rich-b")
        window.refresh()
        self.wait_until(lambda: window.status_text == "ERROR")
        self.assertIn("exited unexpectedly", window.error_label.text())
        self.assertIn("exited unexpectedly", window.log_view.toPlainText())
        self.assertFalse(window.stop_button.isEnabled())

    def test_monitor_error_disables_start_until_a_verified_recovery(self):
        window, backend = self.window()
        backend.snapshot_error = "Fixture status unavailable"
        window.refresh()
        self.wait_until(lambda: window.status_text == "ERROR")
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stage_combo.isEnabled())
        self.assertIn("Fixture status unavailable", window.error_label.text())
        self.assertIn("Fixture status unavailable", window.log_view.toPlainText())
        backend.snapshot_error = None
        window.refresh()
        self.wait_until(lambda: window.status_text == "STOPPED" and window.start_button.isEnabled())

    def test_failed_snapshot_still_consumes_and_displays_activity_events(self):
        window, backend = self.window()
        backend.snapshot_error = "Fixture status unavailable"
        backend.events = ["[2000-01-01 00:00:00] Worker error remains visible"]
        window.refresh()
        self.wait_until(lambda: window.status_text == "ERROR" and
                        "Worker error remains visible" in window.log_view.toPlainText())
        self.assertEqual(backend.events, [])
        self.assertFalse(window.start_button.isEnabled())

    def test_command_completion_keeps_controls_locked_until_fresh_snapshot(self):
        window, backend = self.window()
        backend.snapshot_gate = threading.Event()
        window.stage_combo.setCurrentIndex(window.stage_combo.findData("rich-b"))
        self.wait_until(lambda: window._pending_action is None and window._awaiting_verification)
        self.assertEqual(backend.commands, [("select", "rich-b")])
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        self.assertFalse(window.stage_combo.isEnabled())
        window.start_automation()
        self.assertEqual(len(backend.commands), 1)
        backend.snapshot_gate.set()
        self.wait_until(lambda: window.start_button.isEnabled() and window.stage_combo.isEnabled())
        self.assertEqual(window.stage_combo.currentData(), "rich-b")

    def test_stale_poll_failure_after_command_is_ignored_until_fresh_result(self):
        window, backend = self.window()
        stale_gate, fresh_gate = threading.Event(), threading.Event()
        entered = []

        def controlled_snapshot():
            backend.record("snapshot")
            entered.append(True)
            if len(entered) == 1:
                stale_gate.wait(5)
                raise ControllerError("Stale pre-command failure")
            fresh_gate.wait(5)
            return copy.deepcopy(backend.state)

        backend.snapshot = controlled_snapshot
        try:
            window.refresh()
            self.wait_until(lambda: len(entered) == 1)
            window.stage_combo.setCurrentIndex(window.stage_combo.findData("rich-b"))
            self.wait_until(lambda: window._pending_action is None and window._awaiting_verification)
            stale_gate.set()
            self.wait_until(lambda: len(entered) == 2)
            self.assertNotEqual(window.status_text, "ERROR")
            self.assertNotIn("Stale pre-command failure", window.log_view.toPlainText())
            self.assertFalse(window.start_button.isEnabled())
            self.assertFalse(window.stage_combo.isEnabled())
            fresh_gate.set()
            self.wait_until(lambda: window.start_button.isEnabled() and window.stage_combo.isEnabled())
            self.assertEqual(window.stage_combo.currentData(), "rich-b")
        finally:
            stale_gate.set()
            fresh_gate.set()

    def test_command_error_is_displayed_and_logged_without_claiming_running(self):
        window, backend = self.window()
        backend.command_error = "Fixture duplicate controller refusal"
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: window.status_text == "ERROR")
        self.assertIn("Fixture duplicate controller refusal", window.error_label.text())
        self.assertIn("Fixture duplicate controller refusal", window.log_view.toPlainText())
        self.assertFalse(window.stop_button.isEnabled())
        self.assertEqual(backend.commands, [("start", "scrolls-a")])

    def test_disconnection_allows_explicit_guarded_start_and_existing_run_stop(self):
        window, backend = self.window()
        backend.available = False
        window.refresh_connection()
        self.wait_until(lambda: "Unavailable" in window.connection_label.text())
        self.assertEqual(window.status_text, "ERROR")
        self.assertTrue(window.start_button.isEnabled())
        self.assertIn("BlueStacks unavailable", window.error_label.text())
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: window.status_text == "RUNNING")
        self.assertEqual(backend.commands, [("start", "scrolls-a")])
        self.assertTrue(window.stop_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())

    def test_activity_log_is_bounded_and_scrolls_to_latest_event(self):
        window, backend = self.window()
        backend.events = [f"[2000-01-01 00:00:00] Fixture event {number}" for number in range(1300)]
        window.refresh()
        self.wait_until(lambda: "Fixture event 1299" in window.log_view.toPlainText())
        self.assertLessEqual(window.log_view.document().blockCount(), 1200)
        bar = window.log_view.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())


if __name__ == "__main__":
    unittest.main()
