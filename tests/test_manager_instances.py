"""Offline instance-page checks. All discovery and commands use fake backends."""
from __future__ import annotations

import copy
import threading
import unittest

from PySide6.QtTest import QTest

from manager_gui import ManagerWindow
import test_manager as fixtures


def instance(key="Pie64_7", name="Evening Team", endpoint="127.0.0.1:5675"):
    return dict(key=key, display_name=name, instance_id=key, status="running",
                endpoint=endpoint, connection_status="connected", detail="Fixture identity verified")


class InstanceBackend(fixtures.FakeBackend):
    def __init__(self, records=None, selected=None, state=None):
        super().__init__(state)
        self.records = [instance()] if records is None else records
        self.selected = selected if selected is not None else (
            self.records[0]["key"] if len(self.records) == 1 else None)
        self.instance_calls = []
        self.discovery_gate = None
        self.selection_gate = None
        self.connection_gate = None
        self.selection_error = None
        self.discovery_error = None
        self.selection_required = False

    def instances(self):
        self.record("instances")
        if self.discovery_gate is not None:
            self.discovery_gate.wait(3)
        if self.discovery_error:
            raise ValueError(self.discovery_error)
        return dict(instances=copy.deepcopy(self.records), selected_key=self.selected,
                    active_key=self.state.get("instance", {}).get("key"),
                    locked=bool(self.state.get("running")), selection_required=self.selection_required,
                    detail="Fixture discovery complete")

    def snapshot(self):
        result = super().snapshot()
        result["instance_selection_required"] = self.selected is None or self.selection_required
        if not result.get("running"):
            result["instance"] = next((copy.deepcopy(item) for item in self.records
                                       if item["key"] == self.selected), {})
        return result

    def apply_instance(self, key):
        self.record("apply_instance")
        self.instance_calls.append(("apply", key))
        if self.selection_gate is not None:
            self.selection_gate.wait(3)
        if self.selection_error:
            raise ValueError(self.selection_error)
        self.selected = key
        self.selection_required = False
        return "Verified and saved"

    def manual_instance(self, name, endpoint=""):
        self.record("manual_instance")
        self.instance_calls.append(("manual", name, endpoint))
        if self.selection_gate is not None:
            self.selection_gate.wait(3)
        if self.selection_error:
            raise ValueError(self.selection_error)
        record = instance("manual_1", name, endpoint or "127.0.0.1:5905")
        self.records.append(record)
        self.selected = record["key"]
        return "Verified and saved"

    def connection(self):
        self.record("connection")
        selected = next((item for item in self.records if item["key"] == self.selected), {})
        result = dict(available=bool(selected), instance=selected.get("display_name", "Unknown"),
                      detail="Fixture connection")
        if self.connection_gate is not None:
            self.connection_gate.wait(3)
        return result


class InstancePageTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    setUp = fixtures.GuiTests.setUp
    wait_until = fixtures.GuiTests.wait_until

    def tearDown(self):
        for backend in self.backends:
            for name in ("discovery_gate", "selection_gate", "connection_gate"):
                gate = getattr(backend, name, None)
                if gate is not None:
                    gate.set()
        fixtures.GuiTests.tearDown(self)

    def window(self, backend=None, *, appearance="original", wait=True):
        backend = backend or InstanceBackend()
        self.backends.append(backend)
        window = ManagerWindow(backend=backend, auto_poll=False, settings=self.manager_settings,
                               appearance=appearance)
        self.windows.append(window)
        window.show()
        window.refresh()
        window.refresh_connection()
        if wait:
            self.wait_until(lambda: window._snapshot_known and not window._instances_busy
                            and not window._connection_busy and window.stage_combo.count() == 1)
        return window, backend

    def test_single_custom_instance_and_exact_sidebar_in_both_appearances(self):
        window, backend = self.window()
        for appearance in ("original", "retro98", "original"):
            window.set_appearance(appearance)
            buttons = window._presentation.nav_buttons
            self.assertEqual(list(buttons), ["dashboard", "instance", "settings"])
            self.assertEqual([button.text() for button in buttons.values()],
                             ["Dashboard", "Instance", "Settings"])
            buttons["instance"].click()
            self.assertIs(window._presentation.stack.currentWidget(), window.instance_page)
            self.assertEqual(window.instance_combo.currentData(), "Pie64_7")
            self.assertIn("Evening Team", window.instance_combo.currentText())
            self.assertEqual(window.instance_detail_labels["instance_id"].text(), "Pie64_7")
            self.assertEqual(window.instance_detail_labels["endpoint"].text(), "127.0.0.1:5675")
            self.assertFalse(window.apply_instance_button.isEnabled())
            buttons["dashboard"].click()
            self.assertTrue(window.start_button.isEnabled())
        self.assertEqual(backend.commands, [])
        self.assertEqual(backend.instance_calls, [])
        self.assertTrue(all(thread != threading.get_ident() for action, thread in backend.calls
                            if action == "instances"))

    def test_multiple_instances_require_apply_and_restore_saved_selection(self):
        records = [instance(), instance("Rvc64_2", "Weekend", "127.0.0.1:5795")]
        backend = InstanceBackend(records)
        window, _ = self.window(backend)
        self.assertIsNone(window.instance_combo.currentData())
        self.assertFalse(window.start_button.isEnabled())
        window.instance_combo.setCurrentIndex(window.instance_combo.findData("Rvc64_2"))
        self.assertEqual(backend.instance_calls, [])
        self.assertIsNone(backend.selected)
        self.assertTrue(window.apply_instance_button.isEnabled())
        window.apply_instance_button.click()
        self.wait_until(lambda: not window._instance_command_busy and not window._instances_busy
                        and not window._poll_busy and window.start_button.isEnabled())
        self.assertEqual(backend.selected, "Rvc64_2")
        self.assertEqual(backend.instance_calls, [("apply", "Rvc64_2")])
        window.close()
        reopened, _ = self.window(backend)
        self.assertEqual(reopened.instance_combo.currentData(), "Rvc64_2")
        self.assertEqual(reopened.target_name_label.text(), "Weekend")
        self.assertEqual(backend.commands, [])

    def test_active_target_is_pinned_and_selection_unlocks_after_verified_stop(self):
        records = [instance(), instance("Rvc64_2", "Weekend", "127.0.0.1:5795")]
        state = fixtures.running_snapshot()
        state["instance"] = records[0]
        window, backend = self.window(InstanceBackend(records, selected="Rvc64_2", state=state))
        self.assertEqual(window.target_name_label.text(), "Evening Team")
        self.assertIn("Pie64_7", window.instance_label.text())
        self.assertNotIn("Rvc64_2", window.instance_label.text())
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.apply_instance_button.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        self.assertTrue(window.stop_button.isEnabled())
        window.apply_instance_selection()
        window.show_manual_instance_setup()
        self.assertEqual(backend.instance_calls, [])
        backend.state = dict(fixtures.stopped_snapshot(), stop_requested=True)
        window.refresh()
        self.wait_until(lambda: not window._active and not window._instances_busy
                        and window.instance_combo.isEnabled())
        self.assertTrue(window.manual_instance_button.isEnabled())
        self.assertEqual(window.target_name_label.text(), "Weekend")
        self.assertEqual(backend.commands, [])

    def test_discovery_is_async_and_does_not_disable_active_stop(self):
        state = fixtures.running_snapshot()
        state["instance"] = instance()
        backend = InstanceBackend(state=state)
        backend.discovery_gate = threading.Event()
        window, _ = self.window(backend, wait=False)
        self.wait_until(lambda: window._snapshot_known)
        self.assertTrue(window._instances_busy)
        self.assertTrue(window.stop_button.isEnabled())
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        backend.discovery_gate.set()
        self.wait_until(lambda: not window._instances_busy)
        self.assertEqual(backend.commands, [])

    def test_missing_instances_offer_manual_fallback_and_validate_empty_fields(self):
        window, backend = self.window(InstanceBackend([]))
        self.assertIn("No BlueStacks", window.instance_combo.currentText())
        self.assertFalse(window.start_button.isEnabled())
        window.show_manual_instance_setup()
        window.manual_apply_button.click()
        self.assertIn("Enter an instance", window.manual_error_label.text())
        window.manual_name_edit.setText("Custom Team")
        window.manual_advanced_check.setChecked(True)
        window.manual_apply_button.click()
        self.assertIn("Enter an ADB endpoint", window.manual_error_label.text())
        self.assertEqual(backend.instance_calls, [])
        window.manual_endpoint_edit.setText("127.0.0.1:5905")
        window.manual_apply_button.click()
        self.wait_until(lambda: not window._instance_command_busy and not window._instances_busy
                        and window.start_button.isEnabled())
        self.assertEqual(backend.instance_calls, [("manual", "Custom Team", "127.0.0.1:5905")])
        self.assertEqual(window.instance_combo.currentData(), "manual_1")
        self.assertEqual(window.target_name_label.text(), "Custom Team")

    def test_invalid_manual_target_keeps_dialog_and_previous_target(self):
        window, backend = self.window()
        backend.selection_error = "Identity mismatch"
        window.show_manual_instance_setup()
        window.manual_name_edit.setText("Unknown")
        window.manual_apply_button.click()
        self.wait_until(lambda: not window._instance_command_busy)
        self.assertTrue(window._manual_dialog.isVisible())
        self.assertIn("Identity mismatch", window.manual_error_label.text())
        self.assertEqual(backend.selected, "Pie64_7")
        self.assertEqual(window.target_name_label.text(), "Evening Team")
        self.assertEqual(backend.commands, [])

    def test_pending_apply_blocks_duplicate_changes_and_late_completion_after_close(self):
        backend = InstanceBackend([instance(), instance("Rvc64_2", "Weekend")], selected="Pie64_7")
        backend.selection_gate = threading.Event()
        window, _ = self.window(backend)
        window.instance_combo.setCurrentIndex(window.instance_combo.findData("Rvc64_2"))
        window.apply_instance_button.click()
        self.wait_until(lambda: bool(backend.instance_calls))
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        window.apply_instance_selection()
        window.close()
        before = len(backend.calls)
        backend.selection_gate.set()
        self.wait_until(lambda: backend.selected == "Rvc64_2")
        QTest.qWait(30)
        self.assertEqual(len(backend.calls), before)
        self.assertEqual(backend.instance_calls, [("apply", "Rvc64_2")])
        self.assertEqual(backend.commands, [])

    def test_unknown_state_and_pending_controller_command_lock_selection(self):
        window, backend = self.window()
        backend.snapshot_error = "Monitor unavailable"
        window.refresh()
        self.wait_until(lambda: not window._snapshot_known)
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        backend.snapshot_error = None
        window.refresh()
        self.wait_until(lambda: window._snapshot_known)
        window._pending_action = "stamina-duration"
        window._update_controls()
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        window._pending_action = None

    def test_discovery_failure_is_actionable_and_manual_fallback_remains_available(self):
        backend = InstanceBackend([])
        backend.discovery_error = "BlueStacks configuration unavailable"
        window, _ = self.window(backend)
        self.assertIn("configuration unavailable", window.instance_message_label.text())
        self.assertTrue(window.manual_instance_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertEqual(backend.commands, [])

    def test_late_old_connection_result_cannot_replace_applied_target(self):
        records = [instance(), instance("Rvc64_2", "Weekend", "127.0.0.1:5795")]
        backend = InstanceBackend(records, selected="Pie64_7")
        window, _ = self.window(backend)
        backend.connection_gate = threading.Event()
        window.refresh_connection()
        self.wait_until(lambda: sum(action == "connection" for action, _ in backend.calls) == 2)
        window.instance_combo.setCurrentIndex(window.instance_combo.findData("Rvc64_2"))
        window.apply_instance_button.click()
        self.wait_until(lambda: backend.selected == "Rvc64_2" and not window._instance_command_busy)
        backend.connection_gate.set()
        self.wait_until(lambda: not window._connection_busy and window._connection.get("instance") == "Weekend")
        self.assertEqual(window.target_name_label.text(), "Weekend")
        self.assertIn("Rvc64_2", window.instance_label.text())
        self.assertEqual(backend.commands, [])

    def test_same_key_can_be_reverified_after_mapping_changed(self):
        backend = InstanceBackend()
        backend.selection_required = True
        window, _ = self.window(backend)
        self.assertEqual(window.instance_combo.currentData(), backend.selected)
        self.assertTrue(window.apply_instance_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        window.apply_instance_button.click()
        self.wait_until(lambda: not window._instance_command_busy and not window._instances_busy
                        and not window._poll_busy and window.start_button.isEnabled())
        self.assertEqual(backend.instance_calls, [("apply", "Pie64_7")])
        self.assertFalse(window.apply_instance_button.isEnabled())
        self.assertEqual(backend.commands, [])

    def test_stale_active_discovery_is_refreshed_after_verified_stop(self):
        state = fixtures.running_snapshot()
        state["instance"] = instance()
        window, backend = self.window(InstanceBackend(state=state))
        backend.state = dict(fixtures.stopped_snapshot(), stop_requested=True, instance_locked=False)
        window._instances_busy = True
        window.apply_snapshot(copy.deepcopy(backend.state))
        self.assertTrue(window._instances_again)
        stale = dict(instances=copy.deepcopy(backend.records), selected_key=backend.selected,
                     active_key=backend.selected, locked=True)
        window._on_result("instances", stale, None)
        self.wait_until(lambda: not window._instances_busy and window.instance_combo.isEnabled())
        self.assertTrue(window.manual_instance_button.isEnabled())
        self.assertFalse(window._instances_data["locked"])
        self.assertEqual(backend.commands, [])

    def test_fresh_snapshot_clears_previous_controller_selection_lock(self):
        window, backend = self.window()
        window._instances_data["locked"] = True
        window._update_controls()
        self.assertFalse(window.instance_combo.isEnabled())
        window.apply_snapshot(dict(fixtures.stopped_snapshot(), instance_locked=False))
        self.assertTrue(window.instance_combo.isEnabled())
        self.assertTrue(window.manual_instance_button.isEnabled())
        self.assertEqual(backend.commands, [])

    def test_successful_apply_waits_for_fresh_process_snapshot_before_enabling_controls(self):
        backend = InstanceBackend([instance(), instance("Rvc64_2", "Weekend")], selected="Pie64_7")
        window, _ = self.window(backend)
        backend.snapshot_gate = threading.Event()
        window.instance_combo.setCurrentIndex(window.instance_combo.findData("Rvc64_2"))
        window.apply_instance_button.click()
        self.wait_until(lambda: not window._instance_command_busy and not window._instances_busy
                        and window._instances_data.get("selected_key") == "Rvc64_2")
        self.assertTrue(window._awaiting_verification)
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.instance_combo.isEnabled())
        self.assertFalse(window.manual_instance_button.isEnabled())
        backend.snapshot_gate.set()
        self.wait_until(lambda: window.start_button.isEnabled())
        self.assertEqual(window.target_name_label.text(), "Weekend")
        self.assertEqual(backend.commands, [])

    def test_dashboard_uses_fresh_controller_target_after_external_selection_change(self):
        backend = InstanceBackend([instance(), instance("Rvc64_2", "Weekend")], selected="Pie64_7")
        window, _ = self.window(backend)
        backend.selected = "Rvc64_2"
        window.refresh()
        self.wait_until(lambda: not window._poll_busy)
        self.assertEqual(window.target_name_label.text(), "Weekend")
        self.assertIn("Rvc64_2", window.instance_label.text())
        self.assertEqual(backend.commands, [])


if __name__ == "__main__":
    unittest.main()
