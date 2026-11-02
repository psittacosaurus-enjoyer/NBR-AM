"""Offline shutdown GUI checks. Every operation uses an injected fake backend."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from app.backend.controller import ControllerError
from app.gui.window import ManagerWindow
import test_manager as fixtures
from test_manager_instances import InstanceBackend


class ShutdownBackend(InstanceBackend):
    def __init__(self, state=None):
        super().__init__(state=state)
        self.shutdown_gate = None
        self.shutdown_error = None
        self.shutdown_calls = []

    def shutdown(self, progress=None):
        self.record("shutdown")
        with self.lock:
            self.shutdown_calls.append(threading.current_thread())
        if progress:
            progress("Fixture: safely stopping managed automation.")
        if self.shutdown_gate is not None:
            self.shutdown_gate.wait(5)
        if self.shutdown_error:
            raise ControllerError(self.shutdown_error)
        with self.lock:
            self.state = fixtures.stopped_snapshot()
        if progress:
            progress("Fixture: verified no managed automation remains.")
        return "Fixture shutdown completed."


class ShutdownGuiTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    wait_until = fixtures.GuiTests.wait_until

    def setUp(self):
        fixtures.GuiTests.setUp(self)
        critical = patch.object(QMessageBox, "critical")
        critical.start()
        self.addCleanup(critical.stop)

    def tearDown(self):
        for backend in self.backends:
            if backend.shutdown_gate is not None:
                backend.shutdown_gate.set()
        for window in self.windows:
            if window._shutdown_pending:
                self.wait_until(lambda: not window._shutdown_pending)
        fixtures.GuiTests.tearDown(self)

    def window(self, backend=None):
        backend = backend or ShutdownBackend()
        self.backends.append(backend)
        window = ManagerWindow(backend=backend, auto_poll=False, settings=self.manager_settings)
        self.windows.append(window)
        window.show()
        window.refresh()
        window.refresh_connection()
        self.wait_until(lambda: window._snapshot_known and bool(window._connection)
                        and not window._instances_busy and window.stage_combo.count() == 1)
        return window, backend

    def confirm_shutdown(self, window):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question:
            window.exit_and_stop_program()
        question.assert_called_once()

    def test_confirmation_cancel_leaves_running_automation_and_window_untouched(self):
        window, backend = self.window(ShutdownBackend(fixtures.running_snapshot()))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            QTest.mouseClick(window.exit_stop_button, Qt.MouseButton.LeftButton)
        question.assert_called_once()
        buttons = question.call_args.args[3]
        default = question.call_args.args[4]
        self.assertTrue(buttons & QMessageBox.StandardButton.Yes)
        self.assertTrue(buttons & QMessageBox.StandardButton.Cancel)
        self.assertEqual(default, QMessageBox.StandardButton.Cancel)
        self.assertFalse(window._shutdown_pending)
        self.assertTrue(window.isVisible())
        self.assertEqual(window.status_text, "RUNNING")
        self.assertEqual(backend.shutdown_calls, [])
        self.assertEqual(backend.commands, [])

    def test_shutdown_reports_progress_stays_responsive_and_blocks_close_and_mutations(self):
        window, backend = self.window(ShutdownBackend(fixtures.running_snapshot()))
        backend.shutdown_gate = threading.Event()
        self.confirm_shutdown(window)
        self.wait_until(lambda: len(backend.shutdown_calls) == 1
                        and "Fixture: safely stopping" in window.log_view.toPlainText())
        self.assertTrue(window._shutdown_pending)
        self.assertEqual(window.status_text, "STOPPING")
        self.assertIn("Fixture: safely stopping", window.detail_label.text())
        worker = backend.shutdown_calls[0]
        self.assertNotEqual(worker.ident, threading.get_ident())
        self.assertFalse(worker.daemon, "Cleanup must survive the GUI event loop closing")
        for control in (window.start_button, window.stop_button, window.restart_button,
                        window.exit_stop_button, window.stage_combo, window.stamina_duration_combo,
                        window.end_stamina_wait_button, window.instance_combo,
                        window.apply_instance_button, window.manual_instance_button,
                        window.refresh_instances_button):
            self.assertFalse(control.isEnabled(), control.objectName() or control.__class__.__name__)
        window.close()
        self.assertTrue(window.isVisible(), "X must wait until explicit shutdown finishes")
        self.assertFalse(window._closed)
        window.start_automation()
        window.stop_automation()
        window.restart_automation()
        window._command("start", "universal")
        window.apply_instance_selection()
        window.show_manual_instance_setup()
        with patch.object(QMessageBox, "question") as question:
            window.exit_and_stop_program()
        question.assert_not_called()
        self.assertEqual(backend.commands, [])
        self.assertEqual(backend.instance_calls, [])
        fired = []
        QTimer.singleShot(20, lambda: fired.append(time.monotonic()))
        QTest.qWait(80)
        self.assertTrue(fired, "Cleanup must not block the GUI event loop")
        self.assertEqual(len(backend.shutdown_calls), 1)
        backend.shutdown_gate.set()
        self.wait_until(lambda: window._closed and not window.isVisible())
        self.assertIn("verified no managed automation", window.log_view.toPlainText())

    def test_late_monitor_result_cannot_replace_shutdown_status_or_progress(self):
        window, backend = self.window(ShutdownBackend(fixtures.running_snapshot()))
        backend.snapshot_gate = threading.Event()
        before = sum(action == "snapshot" for action, _ in backend.calls)
        window.refresh()
        self.wait_until(lambda: sum(action == "snapshot" for action, _ in backend.calls) == before + 1)
        backend.shutdown_gate = threading.Event()
        self.confirm_shutdown(window)
        self.assertTrue(window._shutdown_pending)
        self.assertEqual(window.status_text, "STOPPING")
        self.assertEqual(backend.shutdown_calls, [], "Existing monitor must finish before shutdown begins")
        backend.snapshot_gate.set()
        self.wait_until(lambda: "Fixture: safely stopping" in window.detail_label.text())
        self.assertEqual(window.status_text, "STOPPING")
        self.assertIn("Fixture: safely stopping", window.detail_label.text())
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        backend.shutdown_gate.set()
        self.wait_until(lambda: window._closed)

    def test_failed_shutdown_displays_error_restores_timers_and_allows_retry(self):
        window, backend = self.window()
        for timer in (window.poll_timer, window.connection_timer):
            timer.start(60000)
        backend.shutdown_gate = threading.Event()
        backend.shutdown_error = "Fixture owned worker could not be stopped"
        with patch.object(QMessageBox, "critical") as critical:
            self.confirm_shutdown(window)
            self.wait_until(lambda: len(backend.shutdown_calls) == 1)
            self.assertFalse(window.poll_timer.isActive())
            self.assertFalse(window.connection_timer.isActive())
            backend.shutdown_gate.set()
            self.wait_until(lambda: not window._shutdown_pending and critical.called)
        critical.assert_called_once()
        self.assertTrue(window.isVisible())
        self.assertFalse(window._closed)
        self.assertIn(backend.shutdown_error, window._shutdown_error)
        self.assertIn(backend.shutdown_error, window.error_label.text())
        self.assertIn(backend.shutdown_error, window.log_view.toPlainText())
        self.assertEqual(window.status_text, "ERROR")
        self.assertTrue(window.exit_stop_button.isEnabled())
        self.assertTrue(window.poll_timer.isActive())
        self.assertTrue(window.connection_timer.isActive())
        self.assertTrue(window.runtime_timer.isActive())
        backend.shutdown_error = None
        backend.shutdown_gate = threading.Event()
        self.confirm_shutdown(window)
        self.wait_until(lambda: len(backend.shutdown_calls) == 2)
        backend.shutdown_gate.set()
        self.wait_until(lambda: window._closed and not window.isVisible())
        self.assertEqual(backend.commands, [])

    def test_monitor_drain_timeout_reports_failure_before_backend_shutdown(self):
        window, backend = self.window()

        class StuckMonitor:
            def __init__(self):
                self.join_timeouts = []

            def is_alive(self):
                return True

            def join(self, timeout):
                # Model an expired join without sleeping or creating any process.
                self.join_timeouts.append(timeout)

        monitor = StuckMonitor()
        window._workers.append(monitor)
        try:
            with patch.object(QMessageBox, "critical") as critical:
                self.confirm_shutdown(window)
                self.wait_until(lambda: not window._shutdown_pending and critical.called)
            critical.assert_called_once()
            self.assertEqual(len(monitor.join_timeouts), 1)
            self.assertGreaterEqual(monitor.join_timeouts[0], 0)
            self.assertLessEqual(monitor.join_timeouts[0], 30)
            self.assertEqual(backend.shutdown_calls, [])
            self.assertTrue(window.isVisible())
            self.assertFalse(window._closed)
            self.assertEqual(window.status_text, "ERROR")
            self.assertIn("Monitoring did not finish within 30 seconds", window._shutdown_error)
            self.assertIn(window._shutdown_error, window.log_view.toPlainText())
            self.assertTrue(window.exit_stop_button.isEnabled())
        finally:
            window._workers = [worker for worker in window._workers if worker is not monitor]

    def test_normal_window_close_leaves_automation_running_without_shutdown(self):
        window, backend = self.window(ShutdownBackend(fixtures.running_snapshot()))
        with patch.object(QMessageBox, "question") as question:
            window.close()
        self.assertFalse(window.isVisible())
        self.assertTrue(window._closed)
        self.assertTrue(backend.state["running"])
        self.assertEqual(backend.shutdown_calls, [])
        self.assertEqual(backend.commands, [])
        question.assert_not_called()

    def test_exit_is_available_after_unknown_or_failed_monitoring_but_waits_for_commands(self):
        window, backend = self.window()
        self.assertTrue(window.exit_stop_button.isEnabled())
        window._snapshot_known = False
        window._update_controls()
        self.assertTrue(window.exit_stop_button.isEnabled())
        backend.snapshot_error = "Fixture monitoring unavailable"
        window.refresh()
        self.wait_until(lambda: bool(window._poll_error))
        self.assertTrue(window.exit_stop_button.isEnabled())
        for name, value in (("_pending_action", "restart"), ("_stop_pending", True),
                            ("_instance_command_busy", True)):
            with self.subTest(pending=name):
                previous = getattr(window, name)
                setattr(window, name, value)
                window._update_controls()
                self.assertFalse(window.exit_stop_button.isEnabled())
                with patch.object(QMessageBox, "question") as question:
                    window.exit_and_stop_program()
                question.assert_not_called()
                setattr(window, name, previous)
        window._update_controls()
        self.assertTrue(window.exit_stop_button.isEnabled())
        self.assertEqual(backend.shutdown_calls, [])

    def test_exit_button_remains_visible_and_distinct_in_both_appearances(self):
        window, backend = self.window()
        window.apply_snapshot(fixtures.running_snapshot())
        for appearance in ("original", "retro98", "original"):
            with self.subTest(appearance=appearance):
                window.set_appearance(appearance)
                self.app.processEvents()
                self.assertTrue(window.exit_stop_button.isVisible())
                self.assertEqual(window.exit_stop_button.text(), "Exit && Stop Program")
                self.assertEqual(window.exit_stop_button.accessibleName(), "Exit & Stop Program")
                self.assertNotEqual(window.exit_stop_button.objectName(), window.stop_button.objectName())
                self.assertNotEqual(window.exit_stop_button.palette().color(QPalette.ColorRole.Button),
                                    window.stop_button.palette().color(QPalette.ColorRole.Button))
                self.assertTrue(window.exit_stop_button.isEnabled())
        self.assertEqual(backend.shutdown_calls, [])
        self.assertEqual(backend.commands, [])


if __name__ == "__main__":
    unittest.main()
