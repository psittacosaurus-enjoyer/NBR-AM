"""Durable Ninja retreat totals in both appearances; fake monitoring only."""
import copy
import unittest

import test_manager as fixtures


class NinjaRetreatTrackerTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    setUp = fixtures.GuiTests.setUp
    tearDown = fixtures.GuiTests.tearDown
    wait_until = fixtures.GuiTests.wait_until

    def ninja_window(self, state=None, appearance="original"):
        if state is None:
            state = fixtures.running_snapshot("ninja_road")
            state["farm"]["counts"] = {"early_exits": 31, "started": 99, "completed": 17, "failed": 8}
        backend = fixtures.FakeBackend(state)
        window, _ = fixtures.GuiTests.window(self, backend)
        window.stage_combo.addItem("Ninja Road", "ninja_road")
        window.apply_snapshot(backend.snapshot())
        window.set_appearance(appearance)
        self.app.processEvents()
        return window, backend

    def test_confirmed_total_updates_and_same_widget_moves_between_appearances(self):
        window, backend = self.ninja_window()
        label = window.retreated_ninja_roads_label
        self.assertEqual(label.text(), "Retreated Ninja Roads: 31")
        self.assertIn("Total confirmed", label.toolTip())
        self.assertIn("Saved across Stop, Restart", label.toolTip())
        for appearance in ("retro98", "original", "retro98"):
            with self.subTest(appearance=appearance):
                window.set_appearance(appearance)
                self.app.processEvents()
                self.assertIs(window.retreated_ninja_roads_label, label)
                self.assertTrue(label.isVisible())
                owner = window._presentation.retro if appearance == "retro98" else window._presentation.original
                self.assertTrue(owner.isAncestorOf(label))
                self.assertEqual(label.text(), "Retreated Ninja Roads: 31")
        backend.state["farm"]["counts"]["early_exits"] = 32
        window.refresh()
        self.wait_until(lambda: label.text() == "Retreated Ninja Roads: 32")
        self.assertEqual(backend.commands, [])

    def test_polling_failure_clears_stale_total_and_recovery_restores_it(self):
        window, backend = self.ninja_window(appearance="retro98")
        label = window.retreated_ninja_roads_label
        backend.snapshot_error = "process state unavailable"
        window.refresh()
        self.wait_until(lambda: bool(window._poll_error) and not window._poll_busy)
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), "Retreated Ninja Roads: —")
        backend.snapshot_error = None
        window.refresh()
        self.wait_until(lambda: label.text() == "Retreated Ninja Roads: 31")
        window._snapshot_known = False
        window._render_state()
        self.assertEqual(label.text(), "Retreated Ninja Roads: —")
        self.assertEqual(backend.commands, [])

    def test_missing_or_invalid_values_are_unknown_and_zero_is_valid(self):
        window, backend = self.ninja_window()
        for value in (None, True, False, -1, 1.5, "31"):
            with self.subTest(value=value):
                state = copy.deepcopy(backend.state)
                state["farm"]["counts"]["early_exits"] = value
                window.apply_snapshot(state)
                self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: —")
        for counts in ({}, {"started": 100, "completed": 50, "failed": 50}):
            state = copy.deepcopy(backend.state)
            state["farm"]["counts"] = counts
            window.apply_snapshot(state)
            self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: —")
        state["farm"]["counts"]["early_exits"] = 0
        window.apply_snapshot(state)
        self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: 0")
        self.assertEqual(backend.commands, [])

    def test_stage_mismatch_never_borrows_another_stages_count(self):
        window, backend = self.ninja_window()
        state = fixtures.stopped_snapshot("universal")
        state["farm"] = {"stage": "universal", "counts": {"early_exits": 999}}
        window.apply_snapshot(state)
        self.assertTrue(window.retreated_ninja_roads_label.isHidden())
        window.stage_combo.setCurrentIndex(window.stage_combo.findData("ninja_road"))
        self.assertTrue(window.retreated_ninja_roads_label.isVisible())
        self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: —")
        state["stage"] = state["selected_stage"] = "ninja_road"
        window.apply_snapshot(state)
        self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: —")
        state["farm"]["stage"] = "ninja_road"
        state["farm"]["counts"]["early_exits"] = 4
        window.apply_snapshot(state)
        self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: 4")
        self.assertEqual(backend.commands, [])

    def test_stopping_restart_identity_and_reopening_do_not_reset_durable_total(self):
        window, backend = self.ninja_window()
        for running in (False, True):
            state = (fixtures.running_snapshot("ninja_road") if running
                     else fixtures.stopped_snapshot("ninja_road"))
            state["farm"] = {"stage": "ninja_road", "counts": {"early_exits": 31}}
            state["supervisor"]["pid_identity"] = "new-process:created"
            backend.state = state
            window.apply_snapshot(state)
            self.assertEqual(window.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: 31")
        window.close()
        reopened, reopened_backend = self.ninja_window(copy.deepcopy(backend.state), appearance="retro98")
        self.assertEqual(reopened.retreated_ninja_roads_label.text(), "Retreated Ninja Roads: 31")
        self.assertEqual(backend.commands, [])
        self.assertEqual(reopened_backend.commands, [])


if __name__ == "__main__":
    unittest.main()
