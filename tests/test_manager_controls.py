"""Offline stamina/restart controls: fake controller only, no ADB or live commands."""
import copy
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from app.backend import controller as manager_backend
import test_manager as fixtures


class ControlsBackend(fixtures.FakeBackend):
    def command(self, action, stage=None):
        result = super().command(action, stage)
        with self.lock:
            if action == "stamina-duration":
                self.state["stamina_wait_minutes"] = stage
            elif action == "end-stamina-wait":
                self.state["farm"]["stamina_until"] = 0
                self.state["stamina_status"] = "UNKNOWN"  # Rechecking game.
            elif action == "restart":
                self.state = fixtures.running_snapshot(stage or self.state["stage"])
            elif action in {"boss-swap", "early-exit"}:
                self.state["ninja_boss_swap" if action == "boss-swap" else "ninja_early_exit"] = stage
        return result


class ManagerControlsTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    setUp = fixtures.GuiTests.setUp
    tearDown = fixtures.GuiTests.tearDown
    wait_until = fixtures.GuiTests.wait_until
    window = fixtures.GuiTests.window

    def test_ninja_options_persist_in_both_appearances(self):
        window, backend = self.window(ControlsBackend(fixtures.stopped_snapshot("ninja_road")))
        window.stage_combo.addItem("Ninja Road", "ninja_road")
        window.stage_combo.setCurrentIndex(window.stage_combo.findData("ninja_road"))
        window.apply_snapshot(backend.snapshot())
        for appearance in ("original", "retro98"):
            window.set_appearance(appearance)
            for control, action, key in ((window.boss_swap_check, "boss-swap", "ninja_boss_swap"),
                                         (window.early_exit_check, "early-exit", "ninja_early_exit")):
                self.assertTrue(control.isVisible())
                for value in (True, False):
                    control.click()
                    self.wait_until(lambda: window._pending_action is None and not window._awaiting_verification)
                    self.assertEqual(backend.commands[-1], (action, value))
                    self.assertIs(backend.state[key], value)
                    self.assertEqual(control.isChecked(), value)
        window.stage_combo.setCurrentIndex(0)
        self.assertFalse(window.boss_swap_check.isVisible())
        self.assertFalse(window.early_exit_check.isVisible())

    def waiting_window(self):
        state = fixtures.running_snapshot()
        state.update(stamina_status="WAITING", stamina_wait_minutes=10, stamina_wait_interruptible=True)
        state["farm"]["stamina_until"] = time.time() + 120
        return self.window(ControlsBackend(state))

    def test_end_wait_commands_controller_and_countdown_uses_its_deadline(self):
        window, backend = self.waiting_window()
        with patch('app.gui.window.time.time', return_value=backend.state["farm"]["stamina_until"] - 119):
            window._update_runtime()
        self.assertEqual(window.stamina_remaining_label.text(), "Remaining: 01:59")
        self.assertTrue(window.end_stamina_wait_button.isEnabled())
        QTest.mouseClick(window.end_stamina_wait_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: window.stamina_status_label.text() == "UNKNOWN")
        self.assertEqual(backend.commands, [("end-stamina-wait", None)])
        self.assertTrue(window._active)
        self.assertFalse(window.end_stamina_wait_button.isEnabled())

    def test_end_wait_disabled_for_unverified_stopped_and_other_states(self):
        window, backend = self.waiting_window()
        for changes in ({"stamina_status": "READY"}, {"stamina_status": "UNKNOWN"},
                        {"worker_running": False}, {"stop_requested": True},
                        {"stamina_wait_interruptible": False},
                        {"farm": {**backend.state["farm"], "pending": {"action": "tap"}}}):
            state = copy.deepcopy(backend.state)
            state.update(changes)
            window.apply_snapshot(state)
            self.assertFalse(window.end_stamina_wait_button.isEnabled())
            window.end_stamina_wait()
        window._poll_error = "Monitoring unavailable"
        window._render_state()
        self.assertEqual(window.stamina_status_label.text(), "UNKNOWN")
        self.assertEqual(window.stamina_remaining_label.text(), "Remaining: —")
        self.assertEqual(backend.commands, [])

    def test_all_durations_saved_through_controller_and_restored_on_reopen(self):
        window, backend = self.waiting_window()
        self.assertEqual(window.stamina_duration_combo.currentData(), 10)
        deadline = backend.state["farm"]["stamina_until"]
        for minutes in (5, 10, 15, 20, 25):
            window.stamina_duration_combo.setCurrentIndex(window.stamina_duration_combo.findData(minutes))
            self.wait_until(lambda: window._pending_action is None and not window._awaiting_verification)
            self.assertEqual(backend.commands[-1], ("stamina-duration", minutes))
            self.assertEqual(backend.state["farm"]["stamina_until"], deadline)
        window.close()
        reopened, _ = self.window(backend)
        self.assertEqual(reopened.stamina_duration_combo.currentData(), 25)
        self.assertEqual(len(backend.commands), 5)
        state = copy.deepcopy(backend.state)
        state["stamina_wait_minutes"] = 7
        reopened.apply_snapshot(state)
        self.assertEqual(reopened.stamina_duration_combo.currentData(), 5)

    def test_restart_runs_in_background_and_cannot_be_requested_twice(self):
        window, backend = self.waiting_window()
        backend.command_gate = threading.Event()
        QTest.mouseClick(window.restart_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: bool(backend.commands))
        self.assertEqual(backend.commands, [("restart", None)])
        self.assertFalse(window.restart_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertTrue(window.stop_button.isEnabled())
        window.restart_automation()
        ticked = []
        QTimer.singleShot(0, lambda: ticked.append(True))
        self.wait_until(lambda: bool(ticked))
        self.assertEqual(len(backend.commands), 1)
        backend.command_gate.set()
        self.wait_until(lambda: window._pending_action is None and not window._awaiting_verification)
        self.assertEqual(window.status_text, "RUNNING")
        self.assertTrue(window.restart_button.isEnabled())

    def test_restart_available_in_error_when_monitoring_unavailable(self):
        window, backend = self.window(ControlsBackend(fixtures.stopped_snapshot("rich-b")))
        backend.snapshot_error = "Status unavailable"
        window.refresh()
        self.wait_until(lambda: window.status_text == "ERROR")
        self.assertTrue(window.restart_button.isEnabled())
        backend.command_error = "Old farming process did not exit; no relaunch performed"
        window.restart_automation()
        self.wait_until(lambda: bool(window._command_error))
        self.assertEqual(backend.commands, [("restart", None)])
        self.assertIn("did not exit", window.error_label.text())
        self.assertFalse(window.start_button.isEnabled())


class BackendControlsTests(unittest.TestCase):
    def test_ninja_options_use_explicit_enabled_cli_flag(self):
        backend = manager_backend.ControllerBackend()
        result = subprocess.CompletedProcess([], 0, "Saved", "")
        with patch.object(manager_backend.subprocess, "run", return_value=result) as run:
            for action in ("boss-swap", "early-exit"):
                for value in (True, False):
                    backend.command(action, value)
                    self.assertEqual(run.call_args.args[0][-3:], [action, "--enabled", "on" if value else "off"])
                for invalid in (None, "on", 1):
                    with self.assertRaises(ValueError):
                        backend.command(action, invalid)

    def test_controls_use_correct_cli_arguments_and_restart_timeout(self):
        backend = manager_backend.ControllerBackend()
        result = subprocess.CompletedProcess([], 0, "Completed", "")
        with patch.object(manager_backend.subprocess, "run", return_value=result) as run:
            for minutes in (5, 10, 15, 20, 25):
                backend.command("stamina-duration", minutes)
                self.assertEqual(run.call_args.args[0][-2:], ["--minutes", str(minutes)])
            backend.command("end-stamina-wait")
            self.assertEqual(run.call_args.args[0][-1], "end-stamina-wait")
            backend.command("restart", "universal")
            self.assertEqual(run.call_args.args[0][-3:], ["restart", "--stage", "universal"])
            self.assertEqual(run.call_args.kwargs["timeout"], 150)
            backend.command("restart")
            self.assertEqual(run.call_args.args[0][-1], "restart")

    def test_invalid_durations_never_spawn_a_process(self):
        backend = manager_backend.ControllerBackend()
        with patch.object(manager_backend.subprocess, "run") as run:
            for minutes in (None, True, "5", 1, 30, 5.0):
                with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                    backend.command("stamina-duration", minutes)
            run.assert_not_called()

