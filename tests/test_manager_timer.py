"""Offline elapsed-time regressions; fake clocks never control live farming."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt

import manager_gui
import test_manager as fixtures


class FakeClock:
    def __init__(self, wall=1125.25, monotonic=500.25):
        self.wall_seconds = wall
        self.monotonic_seconds = monotonic

    def time(self):
        return self.wall_seconds

    def monotonic(self):
        return self.monotonic_seconds

    def advance(self, seconds):
        self.wall_seconds += seconds
        self.monotonic_seconds += seconds


class ManagerTimerTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GuiTests.setUpClass.__func__)
    setUp = fixtures.GuiTests.setUp
    tearDown = fixtures.GuiTests.tearDown
    wait_until = fixtures.GuiTests.wait_until
    window = fixtures.GuiTests.window

    def timer_window(self):
        window, _ = self.window()
        window.runtime_timer.stop()
        return window

    @staticmethod
    def running_snapshot(started_at=1000):
        snapshot = fixtures.running_snapshot()
        snapshot["started_at"] = started_at
        return snapshot

    def assert_interval(self, window, milliseconds):
        # ceil() can add one millisecond at floating-point boundaries.
        self.assertAlmostEqual(window.runtime_timer.interval(), milliseconds, delta=1)

    def test_callbacks_and_snapshot_refreshes_follow_elapsed_second_boundaries(self):
        window = self.timer_window()
        clock = FakeClock()
        snapshot = self.running_snapshot()
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(snapshot)
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assertEqual(window.runtime_timer.timerType(), Qt.TimerType.PreciseTimer)
            self.assertTrue(window.runtime_timer.isSingleShot())
            self.assert_interval(window, 750)

            # A poll between ticks must preserve the upcoming second boundary.
            clock.advance(0.4)
            window.apply_snapshot(snapshot)
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assert_interval(window, 350)

            # Even an early callback must wait out the remaining fraction.
            clock.advance(0.2)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assert_interval(window, 150)

            for expected in ("00:02:06", "00:02:07", "00:02:08"):
                clock.advance(0.2 if expected == "00:02:06" else 1)
                window._update_runtime()
                self.assertEqual(window.runtime_label.text(), expected)
                self.assert_interval(window, 950)

    def test_same_run_snapshots_preserve_elapsed_across_wall_clock_corrections(self):
        window = self.timer_window()
        clock = FakeClock()
        snapshot = self.running_snapshot()
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(snapshot)
            clock.advance(0.5)
            clock.wall_seconds += 3600
            window.apply_snapshot(snapshot)
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assert_interval(window, 250)

            clock.advance(0.6)
            clock.wall_seconds -= 7200
            window.apply_snapshot(snapshot)
            self.assertEqual(window.runtime_label.text(), "00:02:06")
            self.assert_interval(window, 650)

    def test_delayed_callback_catches_up_to_actual_elapsed_time(self):
        window = self.timer_window()
        clock = FakeClock()
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(self.running_snapshot())
            clock.advance(3.5)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:02:08")
            self.assert_interval(window, 250)
            clock.advance(0.3)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:02:09")
            self.assert_interval(window, 950)

    def test_stop_freezes_elapsed_and_new_run_resets_it(self):
        window = self.timer_window()
        clock = FakeClock()
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(self.running_snapshot())
            clock.advance(3.4)
            stopped = fixtures.stopped_snapshot()
            stopped["stop_requested"] = True
            window.apply_snapshot(stopped)
            self.assertEqual(window.runtime_label.text(), "00:02:08")

            clock.advance(10)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:02:08")

            window.apply_snapshot(self.running_snapshot(clock.time() - 0.25))
            self.assertEqual(window.runtime_label.text(), "00:00:00")
            self.assert_interval(window, 750)
            clock.advance(0.8)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:00:01")

    def test_unknown_or_invalid_start_time_shows_unknown_elapsed(self):
        window = self.timer_window()
        clock = FakeClock()
        with patch.object(manager_gui, "time", clock):
            for started_at in (None, True, 0, -1, float("nan"), float("inf"), "1000"):
                with self.subTest(started_at=started_at):
                    window.apply_snapshot(fixtures.stopped_snapshot())
                    window.apply_snapshot(self.running_snapshot(started_at))
                    self.assertEqual(window.runtime_label.text(), "—")
                    clock.advance(1)
                    window._update_runtime()
                    self.assertEqual(window.runtime_label.text(), "—")

    def test_changed_start_time_resets_run_without_an_intermediate_stopped_poll(self):
        window = self.timer_window()
        clock = FakeClock()
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(self.running_snapshot())
            clock.advance(1)
            window.apply_snapshot(self.running_snapshot(clock.time() - 0.4))
            self.assertEqual(window.runtime_label.text(), "00:00:00")
            self.assert_interval(window, 600)
            clock.advance(0.65)
            window._update_runtime()
            self.assertEqual(window.runtime_label.text(), "00:00:01")
            self.assert_interval(window, 950)

    def test_stamina_countdown_can_schedule_before_runtime_boundary(self):
        window = self.timer_window()
        clock = FakeClock()
        snapshot = self.running_snapshot()
        snapshot["stamina_status"] = "WAITING"
        snapshot["farm"]["stamina_until"] = clock.time() + 120.125
        with patch.object(manager_gui, "time", clock):
            window.apply_snapshot(snapshot)
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assertEqual(window.stamina_remaining_label.text(), "Remaining: 02:01")
            self.assert_interval(window, 125)

            clock.advance(0.13)
            window._update_runtime()
            self.assertEqual(window.stamina_remaining_label.text(), "Remaining: 02:00")
            self.assertEqual(window.runtime_label.text(), "00:02:05")
            self.assert_interval(window, 620)


if __name__ == "__main__":
    unittest.main()
