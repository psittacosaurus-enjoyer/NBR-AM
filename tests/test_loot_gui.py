"""Offline loot dashboard checks with injected records; no ADB or live commands."""
from __future__ import annotations

import copy
import threading
import tempfile
import unittest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

from manager_gui import ManagerWindow
import test_manager as manager_test


def dashboard(session_id="session-1", completed_runs=1):
    session = {"session_id": session_id, "account": "acc1", "stage": "rich-b",
               "process_identity": "1001:1", "process_started": 100,
               "tracking_started": 200, "ended_at": None}
    return {"status": "Tracking Active", "detail": "New completions only.",
            "session": session, "sessions": [session], "completed_runs": completed_runs,
            "recognized_icons": 3, "unknown_icons": 1, "ambiguous_icons": 1,
            "recognition_failures": 0, "confirmed_quantities": {"item-2": 7},
            "items": [{"item_id": "item-1", "item_name": "Scroll", "observed_icons": 2,
                       "confirmed_acquired": None, "unknown_quantities": 2},
                      {"item_id": "item-2", "item_name": "Crystal", "observed_icons": 1,
                       "confirmed_acquired": 7, "unknown_quantities": 1}],
            "runs": [{"completion_id": f"{session_id}:1", "run_sequence": 1,
                      "completed_at": 300, "status": "partial",
                      "screenshot_path": "missing-diagnostic.png", "warnings": ["Quantity unavailable"],
                      "unknown_icons": 1, "ambiguous_icons": 1,
                      "rewards": [{"item_id": "item-1", "item_name": "Scroll", "identity_status": "matched",
                                   "quantity": None, "quantity_status": "unknown"},
                                  {"item_id": "unconfirmed", "item_name": "Candidate", "identity_status": "candidate",
                                   "quantity": 99, "quantity_status": "verified"}]}]}


class LootBackend(manager_test.FakeBackend):
    def __init__(self):
        super().__init__(manager_test.running_snapshot())
        self.loot = dashboard()
        self.loot_gate = None
        self.loot_error = None
        self.session_requests = []
        self.page_requests = []

    def loot_snapshot(self, snapshot, session_id=None, limit=100, offset=0):
        self.record("loot")
        self.session_requests.append(session_id)
        self.page_requests.append((limit, offset))
        if self.loot_gate:
            self.loot_gate.wait(5)
        if self.loot_error:
            raise RuntimeError(self.loot_error)
        result = copy.deepcopy(self.loot)
        result["runs"] = result["runs"][offset:offset + limit]
        return result


class LootGuiTests(unittest.TestCase):
    setUpClass = manager_test.GuiTests.__dict__["setUpClass"]
    setUp = manager_test.GuiTests.setUp
    wait_until = manager_test.GuiTests.wait_until
    window = manager_test.GuiTests.window

    def tearDown(self):
        for backend in self.backends:
            if getattr(backend, "loot_gate", None):
                backend.loot_gate.set()
        manager_test.GuiTests.tearDown(self)

    def ready(self):
        window, backend = self.window(LootBackend())
        self.wait_until(lambda: window._loot_data.get("completed_runs") == 1)
        return window, backend

    def test_observed_icons_are_distinct_from_unknown_and_partial_confirmed_quantities(self):
        window, backend = self.ready()
        self.assertEqual(window.loot_item_table.item(0, 1).text(), "2")
        self.assertEqual(window.loot_item_table.item(0, 2).text(), "Unknown")
        self.assertEqual(window.loot_item_table.item(1, 2).text(), "7 (known only)")
        self.assertIn("unknown quantities excluded", window.loot_summary_label.text())
        self.assertIn("Completed runs observed: 1", window.loot_summary_label.text())
        self.assertEqual(window.loot_status_label.text(), "Tracking Active")
        self.assertIn("Process started:", window.loot_session_label.text())
        self.assertIn("Tracking started:", window.loot_session_label.text())
        run = window.loot_run_tree.topLevelItem(0)
        candidate = run.child(2)
        self.assertIn("unconfirmed identity", candidate.text(0))
        self.assertEqual(candidate.text(2), "Unknown")
        self.assertEqual(backend.commands, [])

    def test_history_expansion_survives_automatic_persisted_record_updates(self):
        window, backend = self.ready()
        window.loot_history_toggle.setChecked(True)
        self.assertTrue(window.loot_history.isVisible())
        window.loot_run_tree.topLevelItem(0).setExpanded(True)
        backend.loot["completed_runs"] = 2
        window.refresh()
        self.wait_until(lambda: "Completed runs observed: 2" in window.loot_summary_label.text())
        self.assertTrue(window.loot_run_tree.topLevelItem(0).isExpanded())
        self.assertEqual(backend.commands, [])

    def test_history_pages_keep_full_session_totals_and_return_older_runs(self):
        window, backend = self.ready()
        fixture = backend.loot["runs"][0]
        backend.loot["runs"] = [{**fixture, "run_sequence": sequence,
                                "completion_id": f"session-1:{sequence}"} for sequence in range(105, 0, -1)]
        backend.loot["completed_runs"] = 105
        window.refresh_loot()
        self.wait_until(lambda: window.loot_run_tree.topLevelItemCount() == 100)
        self.assertTrue(window.loot_next_button.isEnabled())
        self.assertFalse(window.loot_previous_button.isEnabled())
        self.assertEqual(window.loot_page_label.text(), "1–100 of 105 runs")
        window.loot_next_button.click()
        self.wait_until(lambda: window.loot_run_tree.topLevelItemCount() == 5)
        self.assertEqual(backend.page_requests[-1], (100, 100))
        self.assertEqual(window.loot_run_tree.topLevelItem(0).text(0), "Run 5")
        self.assertEqual(window.loot_page_label.text(), "101–105 of 105 runs")
        self.assertIn("Completed runs observed: 105", window.loot_summary_label.text())
        self.assertFalse(window.loot_next_button.isEnabled())
        window.loot_previous_button.click()
        self.wait_until(lambda: window.loot_run_tree.topLevelItemCount() == 100)
        self.assertEqual(backend.commands, [])

    def test_historical_selection_reads_session_and_displays_ended_status(self):
        window, backend = self.ready()
        historical = dashboard("session-old")
        historical["status"] = "Session Ended"
        historical["session"]["ended_at"] = 400
        backend.loot = historical
        window.loot_session_combo.setCurrentIndex(1)
        self.wait_until(lambda: window.loot_status_label.text() == "Session Ended")
        self.assertEqual(backend.session_requests[-1], "session-1")
        self.assertIn("Ended:", window.loot_session_label.text())
        self.assertTrue(window.stop_button.isEnabled())
        self.assertEqual(backend.commands, [])

    def test_slow_loot_reader_is_separate_from_farming_poll_and_event_loop(self):
        window, backend = self.ready()
        backend.loot_gate = threading.Event()
        before = len(backend.session_requests)
        window.refresh_loot()
        self.wait_until(lambda: len(backend.session_requests) == before + 1)
        for _ in range(5):
            window.refresh_loot()
        ticks = []
        QTimer.singleShot(20, lambda: ticks.append(True))
        window.refresh()
        QTest.qWait(80)
        self.assertTrue(ticks)
        self.wait_until(lambda: not window._poll_busy, "Farming polling waited for blocked loot reader")
        self.assertTrue(window.stop_button.isEnabled())
        self.assertEqual(len(backend.session_requests), before + 1)
        backend.loot_gate.set()
        self.wait_until(lambda: not window._loot_busy)
        self.assertTrue(all(thread != threading.get_ident() for name, thread in backend.calls if name == "loot"))

    def test_loot_failure_does_not_change_farming_status_and_missing_screenshot_is_graceful(self):
        window, backend = self.ready()
        screenshot = window.loot_run_tree.topLevelItem(0).child(5)
        self.assertEqual(screenshot.text(0), "Open retained screenshot")
        window._open_loot_screenshot(screenshot)
        self.wait_until(lambda: "Screenshot unavailable" in window.loot_detail_label.text())
        backend.loot_error = "Fixture database unavailable"
        window.refresh_loot()
        self.wait_until(lambda: window.loot_status_label.text() == "Tracking Unavailable")
        self.assertIn("database unavailable", window.loot_detail_label.text())
        self.assertEqual(window.status_text, "RUNNING")
        self.assertTrue(window.stop_button.isEnabled())
        self.assertEqual(backend.commands, [])

    def test_close_reopen_reads_same_persisted_session_without_reset_or_commands(self):
        first, backend = self.ready()
        first.close()
        second, _ = self.window(backend)
        self.wait_until(lambda: second._loot_data.get("completed_runs") == 1)
        self.assertEqual(second._loot_data["session"]["session_id"], "session-1")
        self.assertEqual(backend.commands, [])

    def test_gui_reads_persisted_database_and_automatically_updates_on_next_poll(self):
        from loot_tracking import LootStore, read_dashboard
        with tempfile.TemporaryDirectory() as directory:
            store = LootStore(directory)
            sid = store.ensure_session("offline-mock:1", "scrolls-a", 100)
            backend = manager_test.FakeBackend()
            backend.loot_snapshot = lambda snapshot, session_id=None, limit=100, offset=0: read_dashboard(
                directory, snapshot, session_id, limit=limit, offset=offset)
            window, _ = self.window(backend)
            self.wait_until(lambda: window._loot_data.get("session", {}).get("session_id") == sid)
            self.assertEqual(window.loot_status_label.text(), "Waiting for First Completion")
            event = {"completion_id": "offline-completion:1", "session_id": sid,
                     "run_id": "offline-run:1", "completed_at": 300}
            store.finish(event, "recognized", {"rewards": [{"item_id": "offline-scroll",
                         "item_name": "Offline scroll fixture", "identity_status": "matched",
                         "reward_scope": "acquired", "quantity": None, "quantity_status": "unknown"}]}, None, [])
            window.refresh()
            self.wait_until(lambda: window._loot_data.get("completed_runs") == 1)
            self.assertEqual(window.loot_item_table.item(0, 0).text(), "Offline scroll fixture")
            self.assertEqual(window.loot_item_table.item(0, 1).text(), "1")
            self.assertEqual(window.loot_item_table.item(0, 2).text(), "Unknown")
            expanded_event = {**event, 'completion_id': 'offline-completion:2', 'run_id': 'offline-run:2'}
            expanded_result = {'rewards': [{'item_id': 'fandom-material', 'item_name': 'Fandom material fixture',
                'identity_status': 'matched', 'member_ids': ['fandom-material', 'source-alias'],
                'reference_item_id': 'source-alias', 'record_type': 'item', 'reward_scope': 'acquired',
                'quantity': 3, 'quantity_status': 'verified'}]}
            store.finish(expanded_event, 'recognized', expanded_result, None, [])
            store.finish(expanded_event, 'recognized', expanded_result, None, [])
            window.refresh()
            self.wait_until(lambda: window._loot_data.get('completed_runs') == 2)
            self.assertEqual(window._loot_data['recognized_icons'], 2)
            self.assertEqual(window._loot_data['confirmed_quantities'], {'fandom-material': 3})
            self.assertEqual(window.loot_item_table.item(0, 0).text(), 'Fandom material fixture')
            self.assertEqual(window.loot_item_table.item(0, 1).text(), '1')
            self.assertEqual(window.loot_item_table.item(0, 2).text(), '3')
            self.assertEqual(window.loot_item_table.item(1, 0).text(), 'Offline scroll fixture')
            self.assertEqual(window.loot_item_table.item(1, 2).text(), 'Unknown')
            store.end_session(sid)
            window.refresh()
            self.wait_until(lambda: window.loot_status_label.text() == "Session Ended")
            self.assertEqual(backend.commands, [])
            window.close()


if __name__ == "__main__":
    unittest.main()
