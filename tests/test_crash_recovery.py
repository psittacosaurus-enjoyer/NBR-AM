"""Offline checks for emulator crash recovery; no process is closed or launched."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from automation import controller as farm
from automation import crash_recovery as crash
from automation.adb import Instance


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        install = self.root / "BlueStacks_nxt"
        install.mkdir()
        (install / "HD-Adb.exe").write_bytes(b"")
        (install / "HD-Player.exe").write_bytes(b"")
        self.target = Instance(str(self.root / "bluestacks.conf"), "Pie64", "acc1",
                               str(install / "HD-Adb.exe"), "127.0.0.1:5555", "fixture-android-id")
        self.key = crash._install_key(self.target)
        self.stop = self.root / "STOP"
        self.events = []
        self.budget = crash.Budget(self.root / "crash_recovery.json")
        self.players = [(self.key, "Pie64", 4242), (self.key, "Pie64_1", 5151),
                        ("c:\\other\\bluestacks", "Pie64", 6161)]
        self.killed = []
        self.launched = []
        self.patches = [
            patch.object(crash, "_wait", side_effect=self.fake_wait),
            patch.object(crash, "_taskkill", side_effect=self.fake_kill),
            patch.object(crash.adb, "_player_processes", side_effect=lambda: None if self.players is None else list(self.players)),
            patch.object(crash.subprocess, "Popen", side_effect=lambda cmd, **kw: self.launched.append(cmd)),
            patch.object(crash, "CLOSE_GRACE_SECONDS", 0), patch.object(crash, "CLOSE_FORCE_SECONDS", 0),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def fake_wait(self, seconds, stop_path):
        if stop_path is not None and Path(stop_path).exists():
            raise InterruptedError("Stop requested")

    def fake_kill(self, pid, force):
        self.killed.append((pid, force))
        self.players = [p for p in self.players if p[2] != pid]

    def emit(self, kind, **values):
        self.events.append((kind, values))

    def kinds(self):
        return [kind for kind, _ in self.events]

    def restart(self, **kwargs):
        return crash.restart_emulator(self.target, reason="test", emit=self.emit, stop_path=self.stop,
                                      budget=self.budget, activity="pkg/Activity", **kwargs)


class ClassificationTests(unittest.TestCase):
    def test_logged_adb_failures_are_unreachable(self):
        for message in (
            "The selected BlueStacks instance is disconnected or not authorized",
            "ADB could not connect the selected BlueStacks instance",
            "error: device '127.0.0.1:5555' not found",
            "Command '['C:\\\\Program Files\\\\BlueStacks_nxt\\\\HD-Adb.exe', '-H', '127.0.0.1', '-P', '5037']' timed out after 15 seconds",
            "Cannot start the local ADB server: [WinError 2]",
        ):
            with self.subTest(message=message):
                self.assertTrue(crash.is_unreachable_error(message))

    def test_identity_and_ordinary_errors_never_restart_the_emulator(self):
        for message in (
            "Android device identity mismatch; refusing input",
            "Android identity mismatch; refusing input to the selected instance",
            "BlueStacks instance mapping or Android identity changed; select it again",
            "The selected BlueStacks instance no longer exists",
            "Screen changed before dispatch; no input sent",
            "Stop requested before input",
        ):
            with self.subTest(message=message):
                self.assertFalse(crash.is_unreachable_error(message))


class PlayerProcessTests(Fixture):
    def test_only_this_installation_and_instance_match(self):
        self.assertEqual(crash.player_pids(self.target), [4242])

    def test_close_targets_one_pid_gracefully_first(self):
        self.assertEqual(crash.close_player(self.target, self.emit, self.stop), 4242)
        self.assertEqual(self.killed, [(4242, False)])
        self.assertEqual([p[2] for p in self.players], [5151, 6161])

    def test_close_forces_only_after_graceful_close_fails(self):
        def stubborn(pid, force):
            self.killed.append((pid, force))
            if force:
                self.players = [p for p in self.players if p[2] != pid]
        with patch.object(crash, "_taskkill", side_effect=stubborn):
            self.assertEqual(crash.close_player(self.target, self.emit, self.stop), 4242)
        self.assertEqual(self.killed, [(4242, False), (4242, True)])

    def test_unreadable_or_ambiguous_process_table_never_kills(self):
        self.players = None
        with self.assertRaises(crash.CrashRecoveryError):
            crash.close_player(self.target, self.emit, self.stop)
        self.players = [(self.key, "Pie64", 1), (self.key, "Pie64", 2)]
        with self.assertRaises(crash.CrashRecoveryError):
            crash.close_player(self.target, self.emit, self.stop)
        self.assertEqual(self.killed, [])

    def test_taskkill_targets_pid_never_image_name(self):
        source = Path(crash.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/IM", source)
        self.assertIn('"/PID"', source)


class RestartTests(Fixture):
    def test_full_restart_closes_relaunches_boots_and_opens_game(self):
        shells = []

        def shell(instance, *args, timeout=15):
            shells.append(args)
            return "1" if args[:2] == ("getprop", "sys.boot_completed") else ""
        with patch.object(crash, "verify_identity") as verify, patch.object(crash, "_adb_shell", side_effect=shell):
            delay = self.restart()
        self.assertEqual(delay, 5)
        self.assertEqual(self.killed, [(4242, False)])
        self.assertEqual(len(self.launched), 1)
        self.assertTrue(self.launched[0][0].endswith("HD-Player.exe"))
        self.assertEqual(self.launched[0][1:], ["--instance", "Pie64"])
        self.assertIn(("am", "start", "-n", "pkg/Activity"), shells)
        self.assertTrue(verify.called)
        self.assertEqual(self.kinds(), ["emulator_restart_started", "emulator_close_requested", "emulator_closed",
                                        "emulator_launched", "emulator_booted", "emulator_restart_completed"])
        self.assertEqual(len(crash.Budget(self.budget.path).data["emulator_restarts"]), 1)

    def test_missing_player_is_launched_without_closing_anything(self):
        self.players = [(self.key, "Pie64_1", 5151)]
        with patch.object(crash, "verify_identity"), patch.object(crash, "_adb_shell", return_value="1"):
            self.restart()
        self.assertEqual(self.killed, [])
        self.assertEqual(len(self.launched), 1)

    def test_identity_change_after_boot_is_a_safety_stop(self):
        with patch.object(crash, "verify_identity",
                          side_effect=RuntimeError("Android identity mismatch; refusing input to the selected instance")):
            with self.assertRaises(crash.CrashRecoveryUnsafe) as caught:
                self.restart()
        self.assertIn("android device identity mismatch", str(caught.exception).casefold())
        self.assertNotIn("emulator_restart_completed", self.kinds())

    def test_slow_boot_keeps_waiting_until_android_reports_completion(self):
        answers = iter([RuntimeError("The selected BlueStacks instance is disconnected or not authorized"),
                        None, None, None])
        def verify(instance, connect=False):
            value = next(answers)
            if value:
                raise value
        boot = iter(["", "1"])
        def shell(instance, *args, timeout=15):
            return next(boot) if args[0] == "getprop" else ""
        with patch.object(crash, "verify_identity", side_effect=verify), patch.object(crash, "_adb_shell", side_effect=shell):
            self.assertEqual(self.restart(), 5)

    def test_boot_timeout_is_reported_and_retried_later(self):
        with patch.object(crash, "verify_identity", side_effect=RuntimeError("ADB could not connect the selected BlueStacks instance")), \
                patch.object(crash, "BOOT_TIMEOUT_SECONDS", 0), \
                patch.object(crash, "wait_for_boot", side_effect=crash.CrashRecoveryError("BlueStacks did not finish booting")):
            self.assertEqual(self.restart(), 30)
        self.assertIn("emulator_restart_failed", self.kinds())

    def test_stop_during_restart_propagates(self):
        self.stop.write_text("user stop", encoding="utf-8")
        with self.assertRaises(InterruptedError):
            self.restart()
        self.assertEqual(self.launched, [])

    def test_manual_endpoint_is_never_restarted(self):
        manual = Instance("", "manual@127.0.0.1:5555", "m", self.target.adb_path, "127.0.0.1:5555", "x")
        delay = crash.restart_emulator(manual, reason="t", emit=self.emit, stop_path=self.stop,
                                       budget=self.budget, activity="a")
        self.assertEqual(delay, 60)
        self.assertEqual(self.kinds(), ["emulator_restart_unsupported"])
        self.assertEqual(self.killed + self.launched, [])


class BudgetTests(Fixture):
    def test_hourly_budget_then_cooldown_then_fresh_count(self):
        now = 1_000_000.0
        for index in range(crash.EMULATOR_RESTARTS_PER_HOUR):
            self.assertEqual(self.budget.decide("emulator_restarts", now + index)[0], "restart")
            self.budget.note_restart("emulator_restarts", now + index)
        decision, delay = self.budget.decide("emulator_restarts", now + 10)
        self.assertEqual((decision, delay), ("wait", crash.BUDGET_COOLDOWN_SECONDS))
        self.assertEqual(self.budget.decide("emulator_restarts", now + 20)[0], "wait")
        self.assertEqual(self.budget.decide("emulator_restarts", now + 10 + crash.BUDGET_COOLDOWN_SECONDS)[0], "restart")

    def test_budget_survives_a_worker_restart(self):
        self.budget.note_restart("emulator_restarts", 5.0)
        self.assertEqual(crash.Budget(self.budget.path).data["emulator_restarts"], [5.0])

    def test_long_failure_gives_up_with_a_clear_stop(self):
        self.budget.note_failing(0)
        with patch.object(crash.time, "time", return_value=crash.GIVE_UP_AFTER_SECONDS + 1):
            self.restart()
        self.assertIn("crash_recovery_exhausted", self.kinds())
        self.assertTrue(self.stop.read_text(encoding="utf-8").startswith("Crash recovery exhausted"))
        self.assertEqual(self.killed + self.launched, [])

    def test_recognized_screen_ends_the_failure_streak(self):
        self.budget.note_failing(0)
        self.budget.note_healthy()
        self.assertIsNone(crash.Budget(self.budget.path).data["failing_since"])


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.events = []
        self.patches = [patch.object(farm, "RUNTIME", self.runtime),
                        patch.object(farm, "STOP", self.runtime / "STOP"),
                        patch.object(farm, "event", side_effect=lambda kind, **v: self.events.append((kind, v)))]
        for item in self.patches:
            item.start()
        install = self.runtime / "bs"
        install.mkdir()
        self.target = Instance(str(self.runtime / "bluestacks.conf"), "Pie64", "acc1",
                               str(install / "HD-Adb.exe"), "127.0.0.1:5555", "fixture-android-id")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_worker(self, create_engine, restart, pids=None):
        def stop_after(*args, **kwargs):
            farm.STOP.write_text("fixture stop", encoding="utf-8")
            restart(*args)
            return 1
        with contextlib.ExitStack() as stack:
            for item in (
                patch.object(farm, "lock", side_effect=lambda *a, **k: contextlib.nullcontext()),
                patch.object(farm, "lock_busy", return_value=False),
                patch.object(farm, "live_controller_pids", return_value=[]),
                patch.object(farm, "controller_exclusions", return_value=[]),
                patch.object(farm, "process_target", return_value=self.target),
                patch.object(farm, "instance_dict", return_value={"fixture": True}),
                patch.object(farm, "state_path", side_effect=lambda name, *_: self.runtime / name),
                patch.object(farm, "_loot_start", return_value=Mock()),
                patch.object(farm, "_loot_call", return_value=None),
                patch.object(farm, "create_engine", side_effect=create_engine),
                patch.object(farm, "_crash_restart", side_effect=stop_after),
                patch.object(crash, "player_pids", return_value=pids),
                patch.object(farm.time, "monotonic", side_effect=range(0, 10**7, 10)),
                patch.object(farm.time, "sleep"),
            ):
                stack.enter_context(item)
            farm.worker("universal", instance=self.target)

    def test_sustained_adb_loss_restarts_the_emulator(self):
        calls = []
        attempts = []
        def unreachable(*_):
            attempts.append(1)
            if len(attempts) > 200:
                farm.STOP.write_text("runaway", encoding="utf-8")
            raise RuntimeError("The selected BlueStacks instance is disconnected or not authorized")
        self.run_worker(unreachable, lambda *a: calls.append(a), pids=[4242])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "adb_unreachable")
        self.assertGreater(len(attempts), 1)

    def test_missing_player_restarts_after_two_errors(self):
        calls = []
        def unreachable(*_):
            raise RuntimeError("ADB could not connect the selected BlueStacks instance")
        self.run_worker(unreachable, lambda *a: calls.append(a), pids=[])
        self.assertEqual([c[2] for c in calls], ["player_missing"])

    def test_identity_mismatch_stops_and_never_restarts(self):
        calls = []
        def mismatch(*_):
            raise RuntimeError("Android device identity mismatch; refusing input")
        self.run_worker(mismatch, lambda *a: calls.append(a), pids=[])
        self.assertEqual(calls, [])
        self.assertIn("worker_safety_stop", [k for k, _ in self.events])

    def test_crash_test_request_runs_before_any_engine_work(self):
        calls = []
        crash.request_crash_test(self.runtime / "crash_test_request.json")
        built = []
        self.run_worker(lambda *_: built.append(1), lambda *a: calls.append(a))
        self.assertEqual([c[2] for c in calls], ["crash_test"])
        self.assertEqual(built, [])
        self.assertFalse((self.runtime / "crash_test_request.json").exists())

    def test_unsafe_restart_writes_stop(self):
        with patch.object(crash, "restart_emulator", side_effect=crash.CrashRecoveryUnsafe("Android device identity mismatch")):
            farm._crash_restart(self.target, "universal", "t", Mock())
        self.assertTrue(farm.STOP.exists())
        self.assertIn("worker_safety_stop", [k for k, _ in self.events])


if __name__ == "__main__":
    unittest.main(verbosity=2)
