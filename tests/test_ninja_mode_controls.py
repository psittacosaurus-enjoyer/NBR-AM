"""Offline exit-mode controls: fake manager and no game or device input."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.backend import controller as manager_backend
from automation import controller as farm
import test_manager as fixtures
from test_manager_controls import ControlsBackend


class ModeBackend(ControlsBackend):
    def command(self, action, stage=None):
        result = super().command(action, stage)
        if action == "ninja-mode":
            with self.lock:
                self.state["ninja_exit_mode"] = stage
        return result


class NinjaModeControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nbr-ninja-modes-")
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        for replacement in (patch.object(farm, "RUNTIME", self.runtime),
                            patch.object(farm, "controller_active", return_value=False),
                            patch.object(farm, "lock_busy", return_value=False),
                            patch.object(farm, "event")):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_setting_defaults_and_preserves_existing_stage10_options(self):
        self.assertEqual(farm.ninja_exit_mode(), "stage10")
        settings = {"ninja_boss_swap": True, "ninja_early_exit": True, "stamina_wait_minutes": 15}
        farm.write_json(self.runtime / "manager_settings.json", settings)
        for mode in ("stage15", "stage10"):
            farm.set_ninja_exit_mode(mode)
            self.assertEqual(farm.ninja_exit_mode(), mode)
            self.assertEqual(farm.read_json(self.runtime / "manager_settings.json"),
                             {**settings, "ninja_exit_mode": mode})
        farm.write_json(self.runtime / "manager_settings.json", {"ninja_exit_mode": "unknown"})
        self.assertEqual(farm.ninja_exit_mode(), "stage10")

    def test_active_or_unverified_controller_and_restart_lock_reject_mode_change(self):
        farm.set_ninja_exit_mode("stage10")
        for guard in (patch.object(farm, "controller_active", return_value=True),
                      patch.object(farm, "lock_busy", return_value=True)):
            with guard, self.assertRaisesRegex(RuntimeError, "mode is locked"):
                farm.set_ninja_exit_mode("stage15")
            self.assertEqual(farm.ninja_exit_mode(), "stage10")
        for mode in (None, True, 10, "stage16"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                farm.set_ninja_exit_mode(mode)

    def test_backend_routes_only_valid_modes_using_explicit_mode_flag(self):
        backend = manager_backend.ControllerBackend()
        result = subprocess.CompletedProcess([], 0, "Saved", "")
        with patch.object(manager_backend.subprocess, "run", return_value=result) as run:
            for mode in farm.NINJA_EXIT_MODES:
                backend.command("ninja-mode", mode)
                self.assertEqual(run.call_args.args[0][-3:], ["ninja-mode", "--mode", mode])
            run.reset_mock()
            for mode in (None, True, 10, "stage16"):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    backend.command("ninja-mode", mode)
            run.assert_not_called()

    def test_saved_strategy_input_blocks_switching_stage_and_preserves_own_resume(self):
        path = farm.load_stage("ninja_road")["progress"]
        state = {"stage15_strategy": {"pending": {"purpose": "setting", "target": False}}}
        farm.write_json(path, state)
        with self.assertRaisesRegex(RuntimeError, "existing saved input"):
            farm.verify_saved_input(stage="universal")
        farm.verify_saved_input(stage="ninja_road")
        self.assertEqual(farm.read_json(path), state)

    def test_missing_evidence_resolution_cannot_discard_strategy_input(self):
        path = farm.load_stage("ninja_road")["progress"]
        state = {"stage15_strategy": {"pending": {"purpose": "team", "target": 2}}}
        farm.write_json(path, state)
        with patch.object(farm, "process_target", return_value=None), \
             self.assertRaisesRegex(RuntimeError, "existing saved input"):
            farm.resolve_missing_evidence("ninja_road", reason="offline guard check")
        self.assertEqual(farm.read_json(path), state)


class NinjaModeGuiTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    setUp = fixtures.GuiTests.setUp
    tearDown = fixtures.GuiTests.tearDown
    wait_until = fixtures.GuiTests.wait_until
    window = fixtures.GuiTests.window

    def ninja_window(self, state=None):
        window, backend = self.window(ModeBackend(state or fixtures.stopped_snapshot("ninja_road")))
        window.stage_combo.addItem("Ninja Road", "ninja_road")
        window.stage_combo.setCurrentIndex(window.stage_combo.findData("ninja_road"))
        window.apply_snapshot(backend.snapshot())
        return window, backend

    def test_mode_selection_persists_in_both_appearances_without_changing_legacy_options(self):
        state = fixtures.stopped_snapshot("ninja_road")
        state.update(ninja_boss_swap=True, ninja_early_exit=True)
        window, backend = self.ninja_window(state)
        combo = window.ninja_mode_combo
        self.assertEqual([combo.itemText(index) for index in range(combo.count())],
                         ["Stage 10 Exit", "Stage 15 Exit"])
        for appearance in ("original", "retro98"):
            window.set_appearance(appearance)
            self.assertTrue(combo.isVisible())
            for mode in ("stage15", "stage10"):
                combo.setCurrentIndex(combo.findData(mode))
                self.wait_until(lambda: window._pending_action is None and not window._awaiting_verification)
                self.assertEqual(backend.commands[-1], ("ninja-mode", mode))
                self.assertEqual(combo.currentData(), mode)
                self.assertEqual(window.early_exit_check.isVisible(), mode == "stage10")
                self.assertTrue(window.early_exit_check.isChecked())
                self.assertTrue(window.boss_swap_check.isChecked())
        window.stage_combo.setCurrentIndex(0)
        self.assertFalse(combo.isVisible())

    def test_active_attempt_displays_pinned_mode_and_prevents_changes(self):
        state = fixtures.running_snapshot("ninja_road")
        state["ninja_exit_mode"] = "stage10"
        state["farm"]["run"] = {"id": "resumed", "exit_mode": "stage15", "outcome": None}
        window, backend = self.ninja_window(state)
        self.assertEqual(window.ninja_mode_combo.currentData(), "stage15")
        self.assertFalse(window.ninja_mode_combo.isEnabled())
        self.assertFalse(window.early_exit_check.isVisible())
        window._ninja_mode_changed(0)
        self.assertEqual(backend.commands, [])
        legacy = copy.deepcopy(state)
        legacy["farm"]["run"].pop("exit_mode")
        window.apply_snapshot(legacy)
        self.assertEqual(window.ninja_mode_combo.currentData(), "stage10")
        self.assertTrue(window.early_exit_check.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
