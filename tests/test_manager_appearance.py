"""Offline checks for reversible presentation changes; no ADB or live farming."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel

from manager_gui import ManagerWindow
from manager_art import RetroBanner
import test_manager as fixtures
from test_loot_gui import LootBackend
from test_manager_controls import ControlsBackend


class AppearanceTests(unittest.TestCase):
    wait_until = fixtures.GuiTests.wait_until

    @classmethod
    def setUpClass(cls):
        fixtures.GuiTests.setUpClass.__func__(cls)
        # Some Windows offscreen Qt builds omit system font discovery entirely.
        if not QFontDatabase.families():
            for filename in ("tahoma.ttf", "segoeui.ttf", "consola.ttf", "lucon.ttf"):
                QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / filename))

    def setUp(self):
        fixtures.GuiTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_path = str(Path(self.temp.name) / "appearance.ini")

    def tearDown(self):
        for window in self.windows:
            for dialog in window.findChildren(QDialog):
                dialog.close()
        for backend in self.backends:
            if getattr(backend, "loot_gate", None) is not None:
                backend.loot_gate.set()
        fixtures.GuiTests.tearDown(self)

    def settings(self):
        return QSettings(self.settings_path, QSettings.Format.IniFormat)

    def window(self, backend=None, appearance=None):
        backend = backend or fixtures.FakeBackend()
        self.backends.append(backend)
        window = ManagerWindow(backend=backend, auto_poll=False,
                               settings=self.settings(), appearance=appearance)
        self.windows.append(window)
        window.show()
        self.wait_until(lambda: window.stage_combo.count() == 1)
        window.refresh()
        window.refresh_connection()
        self.wait_until(lambda: window._snapshot_known and bool(window._connection)
                        and not window._poll_busy and not window._loot_busy)
        return window, backend

    def scroll(self, window):
        roots = (window._presentation.original, window._presentation.scroll)
        areas = [area for area in roots if area.isVisible()]
        self.assertEqual(len(areas), 1, "Only the active manager presentation should be visible")
        return areas[0]

    def visible_in_scroll(self, scroll, widget):
        rect = QRect(widget.mapTo(scroll.viewport(), QPoint()), widget.size())
        return rect.intersects(scroll.viewport().rect())

    def visible_in_window(self, window, widget):
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        return widget.isVisible() and rect.intersects(window.rect())

    def test_settings_picker_switches_immediately_and_persists_in_isolated_ini(self):
        window, backend = self.window()
        self.assertEqual(window.appearance, "original")
        window.show_appearance_settings()
        self.app.processEvents()
        dialogs = [dialog for dialog in window.findChildren(QDialog) if dialog.isVisible()]
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(dialogs[0].isModal())
        combo = window.appearance_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ["Original", "Retro 98"])
        combo.setCurrentIndex(combo.findText("Retro 98"))
        self.assertEqual(window.appearance, "retro98")
        combo.setCurrentIndex(combo.findText("Original"))
        self.assertEqual(window.appearance, "original")
        self.assertEqual(self.settings().value("appearance"), "original")
        combo.setCurrentIndex(combo.findText("Retro 98"))
        self.assertEqual(window.appearance, "retro98")
        combo.setCurrentIndex(combo.findText("Original"))
        dialogs[0].close()
        window.close()
        reopened, _ = self.window(backend)
        self.assertEqual(reopened.appearance, "original")
        self.assertEqual(backend.commands, [])

    def test_clean_default_and_optional_details_persist_across_appearances(self):
        window, backend = self.window(LootBackend())
        self.assertEqual(window.appearance, "original")
        self.assertTrue(window.loot_grid.isVisible())
        self.assertTrue(window.stage_combo.isHidden())
        self.assertFalse(window.activity_log_action.isChecked())
        self.assertFalse(window.recognition_details_action.isChecked())
        optional = (window.log_caption, window.log_view, window.loot_status_label, window.loot_summary_label,
                    window.loot_detail_label, window.loot_history, window.loot_item_table)
        self.assertTrue(all(widget.isHidden() for widget in optional))
        calls = list(backend.calls)
        for appearance in ("retro98", "original", "retro98"):
            window.set_appearance(appearance)
            window.activity_log_action.setChecked(True)
            window.recognition_details_action.setChecked(True)
            self.app.processEvents()
            self.assertTrue(all(widget.isVisible() for widget in optional))
            self.assertTrue(window.loot_grid.isVisible())
            if appearance == "retro98":
                self.assertTrue(window._presentation.log_panel.isVisible())
                self.assertTrue(window._presentation.history_panel.isVisible())
                self.assertEqual(set(window._presentation.nav_buttons), {"dashboard", "instance", "settings"})
            window.activity_log_action.setChecked(False)
            window.recognition_details_action.setChecked(False)
            self.assertTrue(all(widget.isHidden() for widget in optional))
            self.assertTrue(window.loot_grid.isVisible())
        self.assertEqual(backend.calls, calls)
        self.assertEqual(backend.commands, [])
        window.activity_log_action.setChecked(True)
        window.recognition_details_action.setChecked(True)
        self.assertTrue(self.settings().value("show_activity_log", type=bool))
        self.assertTrue(self.settings().value("show_recognition_details", type=bool))
        window.close()
        reopened, _ = self.window(backend)
        self.assertEqual(reopened.appearance, "retro98")
        self.assertTrue(reopened.activity_log_action.isChecked())
        self.assertTrue(reopened.recognition_details_action.isChecked())
        self.assertTrue(reopened.log_view.isVisible())
        self.assertTrue(reopened.loot_history.isVisible())

    def test_retro_removes_banner_and_expands_loot_into_available_space(self):
        window, backend = self.window(LootBackend(), appearance='retro98')
        self.assertEqual(window.findChildren(RetroBanner), [])
        window.resize(1000, 760)
        self.app.processEvents()
        first_height = window.loot_grid.height()
        self.assertGreaterEqual(first_height, 260)
        window.resize(1000, 1040)
        self.app.processEvents()
        self.assertGreater(window.loot_grid.height(), first_height)
        self.assertTrue(window._presentation.loot_sort_combo.isVisible())
        window.set_appearance('original')
        self.app.processEvents()
        self.assertEqual(window.loot_grid.minimumHeight(), 132)
        self.assertEqual(window.loot_grid.maximumHeight(), 272)
        self.assertFalse(window._presentation.loot_sort_combo.isVisible())
        self.assertFalse(window._presentation.completed_missions_label.isVisible())
        self.assertEqual(backend.commands, [])

    def test_retro_sorting_persists_and_reorders_new_drops_without_backend_commands(self):
        backend = LootBackend()
        backend.loot['items'] = [
            dict(item_id='body3', item_name='Body XL', element='Body', rarity='3',
                 last_drop_sequence=3, drop_count=2),
            dict(item_id='skill4', item_name='Skill XXL', element='Skill', rarity='4',
                 last_drop_sequence=1, drop_count=1),
            dict(item_id='body4', item_name='Body XXL', element='Body', rarity='4',
                 last_drop_sequence=2, drop_count=4),
        ]
        window, _ = self.window(backend, appearance='retro98')
        combo = window._presentation.loot_sort_combo
        calls = list(backend.calls)
        for order, expected in (('type', ['body4', 'body3', 'skill4']),
                                ('rarity', ['body4', 'skill4', 'body3']),
                                ('recent', ['body3', 'body4', 'skill4'])):
            combo.setCurrentIndex(combo.findData(order))
            self.assertEqual([tile.item['item_id'] for tile in window.loot_grid.tiles], expected)
            self.assertEqual(sum(int(tile.count_label.text()) for tile in window.loot_grid.tiles), 7)
        self.assertEqual(backend.calls, calls)
        self.assertEqual(self.settings().value('retro_loot_sort'), 'recent')
        backend.loot['items'][1]['last_drop_sequence'] = 4
        window.refresh_loot()
        self.wait_until(lambda: window.loot_grid.tiles[0].item['item_id'] == 'skill4')
        window.set_appearance('original')
        self.assertEqual([tile.item['item_id'] for tile in window.loot_grid.tiles], ['body3', 'skill4', 'body4'])
        window.set_appearance('retro98')
        self.assertEqual(combo.currentData(), 'recent')
        self.assertEqual(window.loot_grid.tiles[0].item['item_id'], 'skill4')
        window.close()
        reopened, _ = self.window(backend, appearance='retro98')
        self.assertEqual(reopened._presentation.loot_sort_combo.currentData(), 'recent')
        self.assertEqual(reopened.loot_grid.tiles[0].item['item_id'], 'skill4')
        self.assertEqual(backend.commands, [])

    def test_session_counter_and_smaller_total_survive_reopen_and_new_sessions(self):
        backend = LootBackend()
        backend.state['session_completed_missions'] = 0
        backend.state['farm']['counts'] = {'completed': 559}
        window, _ = self.window(backend, appearance='retro98')
        label = window._presentation.completed_missions_label
        total = window._presentation.total_completed_missions_label
        self.assertEqual(label.text(), 'Completed this session: 0')
        self.assertEqual(total.text(), 'Total completed missions: 559')
        self.assertTrue(total.isVisible())
        backend.state['session_completed_missions'] = 17
        backend.state['farm']['counts']['completed'] = 576
        backend.loot['completed_runs'] = 99
        window.refresh()
        self.wait_until(lambda: label.text()=='Completed this session: 17')
        self.assertEqual(total.text(), 'Total completed missions: 576')
        backend.snapshot_error = 'process state unavailable'
        window.refresh()
        self.wait_until(lambda: bool(window._poll_error) and not window._poll_busy)
        self.assertEqual(label.text(), 'Completed this session: —')
        self.assertEqual(total.text(), 'Total completed missions: —')
        backend.snapshot_error = None
        window.refresh()
        self.wait_until(lambda: label.text()=='Completed this session: 17')
        window.close()
        reopened, _ = self.window(backend, appearance='retro98')
        self.assertEqual(reopened._presentation.completed_missions_label.text(), 'Completed this session: 17')
        backend.state['session_completed_missions'] = 0
        reopened.refresh()
        self.wait_until(lambda: reopened._presentation.completed_missions_label.text()=='Completed this session: 0')
        self.assertEqual(reopened._presentation.total_completed_missions_label.text(), 'Total completed missions: 576')
        self.assertEqual(backend.commands, [])

    def test_round_trip_preserves_control_identity_timers_and_controller_state(self):
        window, backend = self.window(fixtures.FakeBackend(fixtures.running_snapshot()))
        names = ("stage_combo", "start_button", "stop_button", "restart_button",
                 "stamina_duration_combo", "end_stamina_wait_button", "status_label",
                 "runtime_label", "connection_label", "instance_label", "log_view",
                 "loot_grid", "log_caption", "loot_history", "loot_session_combo",
                 "loot_item_table", "loot_run_tree", "loot_previous_button",
                 "loot_next_button", "poll_timer", "connection_timer", "runtime_timer")
        original = {name: getattr(window, name) for name in names}
        timers = [window.poll_timer, window.connection_timer, window.runtime_timer]
        timer_state = [(timer.interval(), timer.isActive()) for timer in timers]
        snapshot = copy.deepcopy(window._snapshot)
        started = window._started_at
        calls = list(backend.calls)
        for appearance in ("original", "retro98", "original", "retro98"):
            with self.subTest(appearance=appearance):
                window.set_appearance(appearance)
                self.app.processEvents()
                self.assertEqual(window.appearance, appearance)
                for name, widget in original.items():
                    self.assertIs(getattr(window, name), widget, name)
                self.assertEqual([(timer.interval(), timer.isActive()) for timer in timers], timer_state)
                self.assertEqual(window._snapshot, snapshot)
                self.assertEqual(window._started_at, started)
                self.assertEqual(window.status_text, "RUNNING")
                self.assertEqual(window.stage_combo.currentData(), "universal")
                self.assertTrue(window.stage_combo.isHidden())
                self.assertFalse(window.start_button.isEnabled())
                self.assertTrue(window.stop_button.isEnabled())
        self.assertEqual(backend.calls, calls, "Appearance changes must not refresh or command the backend")
        self.assertEqual(backend.commands, [])

    def test_switch_during_blocked_command_preserves_lock_and_completion(self):
        window, backend = self.window()
        backend.command_gate = threading.Event()
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: backend.commands == [("start", "universal")])
        button = window.start_button
        timer = window.runtime_timer
        for appearance in ("original", "retro98"):
            window.set_appearance(appearance)
            self.assertIs(window.start_button, button)
            self.assertIs(window.runtime_timer, timer)
            self.assertEqual(window._pending_action, "start")
            self.assertEqual(window.status_text, "STOPPED")
            self.assertFalse(window.stage_combo.isEnabled())
            self.assertFalse(window.start_button.isEnabled())
            self.assertFalse(window.restart_button.isEnabled())
            window.start_automation()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait_until(lambda: bool(ticks))
        self.assertEqual(backend.commands, [("start", "universal")])
        backend.command_gate.set()
        self.wait_until(lambda: window.status_text == "RUNNING" and window.stop_button.isEnabled())
        self.assertEqual(backend.commands, [("start", "universal")])

    def test_history_quantities_expansion_selection_and_log_scroll_survive_switch(self):
        backend = LootBackend()
        sample = backend.loot["runs"][0]
        backend.loot["runs"] = [{**sample, "run_sequence": number,
                                  "completion_id": f"session-1:{number}"}
                                 for number in range(105, 0, -1)]
        backend.loot["completed_runs"] = 105
        window, backend = self.window(backend)
        self.wait_until(lambda: window.loot_item_table.rowCount() == 2)
        window.recognition_details_action.setChecked(True)
        window.activity_log_action.setChecked(True)
        window.loot_session_combo.setCurrentIndex(1)
        self.wait_until(lambda: not window._loot_busy)
        window.loot_next_button.click()
        self.wait_until(lambda: not window._loot_busy and window.loot_run_tree.topLevelItemCount() == 5)
        run = window.loot_run_tree.topLevelItem(0)
        run.setExpanded(True)
        window.loot_run_tree.setCurrentItem(run.child(1))
        window.loot_item_table.setCurrentCell(1, 2)
        cells = [[window.loot_item_table.item(row, column) for column in range(3)]
                 for row in range(2)]
        texts = [[cell.text() for cell in row] for row in cells]
        self.assertEqual(texts[0][2], "2")
        self.assertEqual(texts[1][2], "1")
        window.log_view.setPlainText("\n".join(f"Retained event {i}" for i in range(250)))
        bar = window.log_view.verticalScrollBar()
        bar.setValue(bar.maximum() // 3)
        log_position, log_text = bar.value(), window.log_view.toPlainText()
        selected = window.loot_run_tree.currentItem()
        calls = list(backend.calls)
        for appearance in ("original", "retro98", "original"):
            window.set_appearance(appearance)
            self.app.processEvents()
            self.assertTrue(window.recognition_details_action.isChecked())
            self.assertTrue(window.loot_history.isVisible())
            self.assertIs(window.loot_run_tree.topLevelItem(0), run)
            self.assertTrue(run.isExpanded())
            self.assertIs(window.loot_run_tree.currentItem(), selected)
            self.assertEqual(window.loot_session_combo.currentData(), "session-1")
            self.assertEqual(window.loot_page_label.text(), "101–105 of 105 runs")
            self.assertEqual(window._loot_offset, 100)
            self.assertTrue(window.loot_previous_button.isEnabled())
            self.assertFalse(window.loot_next_button.isEnabled())
            self.assertEqual(window.loot_item_table.currentRow(), 1)
            self.assertEqual(window.loot_item_table.currentColumn(), 2)
            for row in range(2):
                for column in range(3):
                    self.assertIs(window.loot_item_table.item(row, column), cells[row][column])
                    self.assertEqual(cells[row][column].text(), texts[row][column])
            self.assertEqual(window.log_view.toPlainText(), log_text)
            self.assertEqual(bar.value(), log_position)
        self.assertEqual(backend.calls, calls)
        self.assertEqual(backend.commands, [])

    def test_sidebar_targets_dashboard_instance_and_settings_dialog(self):
        window, backend = self.window(appearance="retro98")
        window.activity_log_action.setChecked(True)
        scroll = self.scroll(window)
        buttons = window._presentation.nav_buttons
        self.assertEqual(set(buttons), {"dashboard", "instance", "settings"})
        self.assertEqual([button.text() for button in buttons.values()],
                         ["Dashboard", "Instance", "Settings"])
        buttons["instance"].click()
        self.app.processEvents()
        self.assertIs(window._presentation.stack.currentWidget(), window.instance_page)
        buttons["dashboard"].click()
        self.app.processEvents()
        self.assertTrue(window.status_label.isVisible())
        self.assertEqual(scroll.verticalScrollBar().value(), scroll.verticalScrollBar().minimum())
        buttons["settings"].click()
        self.app.processEvents()
        dialogs = [dialog for dialog in window.findChildren(QDialog) if dialog.isVisible()]
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(window.appearance_combo.isVisible())
        self.assertEqual(backend.commands, [])

    def test_small_and_common_sizes_reach_controls_without_horizontal_overflow(self):
        window, backend = self.window()
        window.activity_log_action.setChecked(True)
        window.recognition_details_action.setChecked(True)
        controls = (window.start_button, window.stop_button,
                    window.restart_button, window.stamina_duration_combo,
                    window.end_stamina_wait_button, window.loot_grid,
                    window.loot_item_table, window.loot_run_tree, window.log_view)
        for appearance in ("original", "retro98"):
            window.set_appearance(appearance)
            sizes = ((800, 640), (1280, 800)) if appearance == "original" else ((800, 600), (1280, 800))
            for width, height in sizes:
                with self.subTest(appearance=appearance, size=(width, height)):
                    window.resize(width, height)
                    self.app.processEvents()
                    self.assertLessEqual(window.width(), width)
                    self.assertLessEqual(window.height(), height)
                    scroll = self.scroll(window)
                    self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                    for control in controls:
                        if scroll.widget().isAncestorOf(control):
                            scroll.ensureWidgetVisible(control, 0, 0)
                        self.app.processEvents()
                        self.assertTrue(control.isVisible(), control.accessibleName() or control.objectName())
                        viewport = scroll.viewport() if scroll.widget().isAncestorOf(control) else window
                        rect = QRect(control.mapTo(viewport, QPoint()), control.size())
                        self.assertGreater(rect.width(), 0)
                        self.assertGreaterEqual(rect.left(), 0)
                        self.assertLessEqual(rect.right(), viewport.width())
                        self.assertTrue(rect.intersects(viewport.rect()))
        self.assertEqual(backend.commands, [])

    def test_real_farming_restart_and_stamina_controls_work_in_both_appearances(self):
        for appearance in ("original", "retro98"):
            with self.subTest(appearance=appearance):
                window, backend = self.window(ControlsBackend(), appearance=appearance)
                stage = "universal"
                QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: window.status_text == "RUNNING" and window.stop_button.isEnabled())
                QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: window.status_text == "STOPPED" and window.restart_button.isEnabled())
                QTest.mouseClick(window.restart_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: window.status_text == "RUNNING" and window.stop_button.isEnabled())
                backend.state.update(stamina_status="WAITING", stamina_wait_minutes=10,
                                     stamina_wait_interruptible=True)
                backend.state["farm"]["stamina_until"] = time.time() + 120
                window.refresh()
                self.wait_until(lambda: window.end_stamina_wait_button.isEnabled())
                window.stamina_duration_combo.setCurrentIndex(window.stamina_duration_combo.findData(15))
                self.wait_until(lambda: window.stamina_duration_combo.isEnabled()
                                and backend.state["stamina_wait_minutes"] == 15)
                QTest.mouseClick(window.end_stamina_wait_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: not window._pending_action and not window._awaiting_verification
                                and window.stamina_status_label.text() == "UNKNOWN")
                self.assertEqual(backend.commands, [("start", stage),
                                                    ("stop", None), ("restart", stage),
                                                    ("stamina-duration", 15),
                                                    ("end-stamina-wait", None)])
                self.assertTrue(all(thread_id != threading.get_ident()
                                    for _, thread_id in backend.calls))

    def test_retained_screenshot_uses_existing_open_action_in_both_appearances(self):
        screenshot = Path(self.temp.name) / "retained.png"
        screenshot.touch()
        for appearance in ("original", "retro98"):
            with self.subTest(appearance=appearance):
                backend = LootBackend()
                backend.loot["runs"][0]["screenshot_path"] = str(screenshot)
                window, _ = self.window(backend, appearance=appearance)
                self.wait_until(lambda: window.loot_run_tree.topLevelItemCount() == 1)
                window.recognition_details_action.setChecked(True)
                run = window.loot_run_tree.topLevelItem(0)
                run.setExpanded(True)
                row = next(run.child(i) for i in range(run.childCount())
                           if run.child(i).text(0) == "Open retained screenshot")
                window.loot_run_tree.scrollToItem(row)
                with patch("manager_gui.QDesktopServices.openUrl", return_value=True) as open_url:
                    window.loot_run_tree.itemDoubleClicked.emit(row, 0)
                    self.wait_until(lambda: open_url.call_count == 1)
                    self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), screenshot)
                self.assertEqual(backend.commands, [])

    def test_both_appearances_omit_the_mission_identifier(self):
        window, backend = self.window()
        for appearance in ('retro98','original'):
            window.set_appearance(appearance)
            self.assertFalse(hasattr(window,'current_stage_label'))
            labels=[label.text() for label in window.findChildren(QLabel) if label.isVisible()]
            self.assertFalse(any(text.startswith('Mission:') for text in labels))
        self.assertEqual(backend.commands, [])

    def test_verified_states_and_wait_countdown_remain_accurate_in_both_appearances(self):
        for appearance in ("original", "retro98"):
            with self.subTest(appearance=appearance):
                window, backend = self.window(appearance=appearance)
                starting = fixtures.running_snapshot()
                starting["worker_running"] = False
                cases = (("STOPPED", fixtures.stopped_snapshot()),
                         ("STARTING", starting), ("RUNNING", fixtures.running_snapshot()),
                         ("STOPPING", {**fixtures.running_snapshot(), "stop_requested": True}))
                for state, snapshot in cases:
                    with self.subTest(state=state):
                        backend.state = snapshot
                        window.refresh()
                        self.wait_until(lambda: not window._poll_busy and window.status_text == state)
                        self.assertEqual(window.status_label.text(), f"● {state}")
                        self.assertIn(window.COLORS[state], window.status_label.styleSheet())
                        self.assertTrue(window.status_label.isVisible())
                        if state in {"STOPPED", "RUNNING"}:
                            self.assertTrue(window.detail_label.isHidden())
                        else:
                            self.assertTrue(window.detail_label.isVisible())
                        self.assertEqual(window.start_button.isEnabled(), state == "STOPPED")
                        self.assertEqual(window.stop_button.isEnabled(), state in {"STARTING", "RUNNING"})
                waiting = fixtures.running_snapshot()
                waiting.update(stamina_status="WAITING", stamina_wait_minutes=10,
                               stamina_wait_interruptible=True)
                waiting["farm"]["stamina_until"] = 2000
                waiting["farm"]["blocked"] = "Waiting for stamina before retrying this mission."
                backend.state = waiting
                with patch("manager_gui.time.time", return_value=1881):
                    window.refresh()
                    self.wait_until(lambda: not window._poll_busy and window.stamina_status_label.text() == "WAITING")
                    window._update_runtime()
                    self.assertEqual(window.stamina_remaining_label.text(), "Remaining: 01:59")
                    self.assertEqual(window.status_text, "RUNNING")
                    self.assertEqual(window.stamina_duration_combo.currentData(), 10)
                    self.assertTrue(window.end_stamina_wait_button.isEnabled())
                    self.assertTrue(window.detail_label.isVisible())
                    self.assertIn("Waiting for stamina", window.detail_label.text())
                backend.snapshot_error = "Fixture monitor failure must remain visible"
                window.refresh()
                self.wait_until(lambda: not window._poll_busy and window.status_text == "ERROR")
                self.assertEqual(window.status_label.text(), "● ERROR")
                self.assertIn(window.COLORS["ERROR"], window.status_label.styleSheet())
                self.assertTrue(window.error_label.isVisible())
                self.assertIn(backend.snapshot_error, window.error_label.text())
                self.assertIn(backend.snapshot_error, window.log_view.toPlainText())
                self.assertEqual(window.stamina_status_label.text(), "UNKNOWN")
                self.assertEqual(window.stamina_remaining_label.text(), "Remaining: —")
                self.assertFalse(window.start_button.isEnabled())
                self.assertFalse(window.end_stamina_wait_button.isEnabled())
                self.assertEqual(backend.commands, [])

    def test_retro_has_no_visible_motto_tagline_or_duplicate_title_labels(self):
        import manager_art

        window, backend = self.window(appearance="retro98")
        labels = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
        visible_text = "\n".join(labels).casefold()
        for phrase in ("efficiency", "more time", "what you enjoy", "get things done"):
            self.assertNotIn(phrase, visible_text)
        self.assertNotIn(window.windowTitle(), labels)
        with patch("manager_art._pixel_text", wraps=manager_art._pixel_text) as paint_text:
            window.grab()
            paint_text.assert_not_called()
        self.assertEqual(backend.commands, [])

    def test_local_arrow_svg_assets_render_as_icons(self):
        directory = Path(__file__).resolve().parent.parent / "assets" / "manager"
        for name in ("arrow-up.svg", "arrow-down.svg", "arrow-left.svg", "arrow-right.svg"):
            with self.subTest(asset=name):
                path = directory / name
                self.assertTrue(path.is_file())
                icon = QIcon(str(path))
                self.assertFalse(icon.isNull())
                pixmap = icon.pixmap(18, 18)
                self.assertFalse(pixmap.isNull())
                image = pixmap.toImage()
                self.assertTrue(any(image.pixelColor(x, y).alpha()
                                    for y in range(image.height()) for x in range(image.width())),
                                "Asset must render visible pixels")

    def test_initial_retro_size_fits_available_screen(self):
        window, backend = self.window(appearance="retro98")
        self.app.processEvents()
        available = window.screen().availableGeometry()
        self.assertLessEqual(window.frameGeometry().width(), available.width())
        self.assertLessEqual(window.frameGeometry().height(), available.height())
        self.assertTrue(window.start_button.isVisible())
        self.assertTrue(window.stop_button.isVisible())
        self.assertTrue(window.restart_button.isVisible())
        self.assertEqual(backend.commands, [])


if __name__ == "__main__":
    unittest.main()
