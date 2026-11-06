"""Emulator crash recovery: restart exactly one identity-pinned BlueStacks instance.

This is the top of the recovery ladder. It runs only from the worker when the
selected instance has been unreachable over ADB for a sustained period, when
its player process is gone, or when the user asks for a crash test. It sends no
game UI input: it closes the one HD-Player process whose installation and
``--instance`` match the saved selection, starts that same instance, waits for
Android to boot, re-verifies the Android identity and mapping, and opens the
game activity with ``am start``. Everything after that is ordinary recognition.

Safety:
- The target PID is re-read from the live process table immediately before it
  is closed; a missing, ambiguous or unreadable process table never kills.
- Shared BlueStacks services are never touched, and processes are never
  matched by image name alone (that would close every multibox account).
- After the boot, an Android identity or mapping change is terminal: the error
  text carries the worker's identity-mismatch marker so it writes STOP.
- Restarts are budgeted and the budget is persisted, so a restarted worker
  cannot loop restarts. When the hourly budget is spent recovery waits; after
  a long unbroken failure it stops farming with a clear reason.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from automation import adb
from automation.adb import Instance, verify_identity


# A disconnected emulator must stay unreachable this long before a restart.
UNREACHABLE_RESTART_SECONDS = 180
# A missing player process is proof enough after this many consecutive errors.
MISSING_PLAYER_ERRORS = 2
EMULATOR_RESTARTS_PER_HOUR = 4
GAME_RESTARTS_PER_HOUR = 6
BUDGET_WINDOW_SECONDS = 3600
BUDGET_COOLDOWN_SECONDS = 1800
GIVE_UP_AFTER_SECONDS = 3 * 3600
CLOSE_GRACE_SECONDS = 10
CLOSE_FORCE_SECONDS = 15
BOOT_TIMEOUT_SECONDS = 300
BOOT_POLL_SECONDS = 3
BOOT_SETTLE_SECONDS = 5
RELAUNCH_PAUSE_SECONDS = 2
PLAYER_EXE = "HD-Player.exe"

UNREACHABLE_MARKERS = (
    "disconnected or not authorized",
    "could not connect",
    "cannot connect",
    "not found",
    "offline",
    "timed out",
    "cannot start the local adb server",
    "adb could not start",
    "did not return a valid android identity",
    "device unauthorized",
    "no devices/emulators",
    "connection reset",
)
FATAL_MARKERS = (
    "identity mismatch",
    "mapping or android identity changed",
    "no longer exists",
    "multiple bluestacks configurations",
    "maps multiple instances",
    "cannot be verified as bluestacks",
    "no verifiable identity",
)


class CrashRecoveryError(RuntimeError):
    """Restart could not complete; no game input was sent."""


class CrashRecoveryUnsafe(CrashRecoveryError):
    """Identity or mapping changed across the restart: farming must stop."""


def is_unreachable_error(message):
    text = str(message).casefold()
    return not any(m in text for m in FATAL_MARKERS) and any(m in text for m in UNREACHABLE_MARKERS)


def _player_path(instance):
    return Path(instance.adb_path).parent / PLAYER_EXE


def _install_key(instance):
    return os.path.normcase(str(Path(instance.adb_path).parent.resolve()))


def supported(instance):
    """Only configured (not manual) instances map to one player process."""
    return isinstance(instance, Instance) and bool(instance.config_path and instance.instance_id)


def player_pids(instance):
    """PIDs of this exact instance's player; None when the table is unreadable."""
    players = adb._player_processes()
    if players is None:
        return None
    key = _install_key(instance)
    return [pid for directory, instance_id, pid in players
            if directory == key and instance_id == instance.instance_id]


class Budget:
    """Persisted restart budget for one account."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _recent(self, kind, now):
        values = [t for t in self.data.get(kind, []) if isinstance(t, (int, float)) and now - t < BUDGET_WINDOW_SECONDS]
        self.data[kind] = values
        return values

    def decide(self, kind, now=None):
        """('restart', 0) | ('wait', seconds) | ('give_up', reason)."""
        now = time.time() if now is None else now
        limit = EMULATOR_RESTARTS_PER_HOUR if kind == "emulator_restarts" else GAME_RESTARTS_PER_HOUR
        failing = self.data.get("failing_since")
        if isinstance(failing, (int, float)) and now - failing >= GIVE_UP_AFTER_SECONDS:
            return "give_up", (f"Automatic crash recovery has not restored farming for "
                               f"{int((now - failing) // 60)} minutes")
        cooldown = self.data.get("cooldown_until", 0)
        if isinstance(cooldown, (int, float)) and now < cooldown:
            return "wait", cooldown - now
        if cooldown:
            # Cooldown served: the hourly count starts over.
            self.data["cooldown_until"] = 0
            self.data[kind] = []
        if len(self._recent(kind, now)) >= limit:
            self.data["cooldown_until"] = now + BUDGET_COOLDOWN_SECONDS
            self.save()
            return "wait", BUDGET_COOLDOWN_SECONDS
        return "restart", 0

    def note_failing(self, now=None):
        if not isinstance(self.data.get("failing_since"), (int, float)):
            self.data["failing_since"] = time.time() if now is None else now
            self.save()

    def note_restart(self, kind, now=None):
        now = time.time() if now is None else now
        self._recent(kind, now).append(now)
        self.data["last_restart_at"] = now
        self.save()

    def note_healthy(self):
        """A recognized game screen: the failure streak is over (budget kept)."""
        if self.data.get("failing_since") is not None or self.data.get("status"):
            self.data["failing_since"] = None
            self.data["status"] = ""
            self.save()

    def set_status(self, text):
        self.data["status"] = text
        self.data["status_at"] = time.time()
        self.save()


def _stopped(stop_path):
    return stop_path is not None and Path(stop_path).exists()


def _wait(seconds, stop_path):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _stopped(stop_path):
            raise InterruptedError("Stop requested")
        time.sleep(min(.25, max(0, deadline - time.monotonic())))


def _adb_shell(instance, *args, timeout=15):
    result = subprocess.run([instance.adb_path, "-H", "127.0.0.1", "-P", str(adb._server_port()),
                             "-s", instance.endpoint, "shell", *args], capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip()[:300] or "ADB shell failed")
    return result.stdout.decode(errors="replace").strip()


def _taskkill(pid, force):
    command = ["taskkill", "/PID", str(int(pid))] + (["/F"] if force else [])
    try:
        subprocess.run(command, capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass


def _await_exit(instance, pid, seconds, stop_path):
    deadline = time.monotonic() + seconds
    while True:
        pids = player_pids(instance)
        if pids is not None and pid not in pids:
            return True
        if time.monotonic() >= deadline:
            return False
        _wait(.5, stop_path)


def close_player(instance, emit, stop_path):
    """Close only this instance's player. Returns the PID closed, or None if none ran."""
    pids = player_pids(instance)
    if pids is None:
        raise CrashRecoveryError("Windows process table is unreadable; refusing to close BlueStacks")
    if len(pids) > 1:
        raise CrashRecoveryError(f"{len(pids)} player processes claim instance {instance.instance_id}; refusing to guess")
    if not pids:
        return None
    pid = pids[0]
    emit("emulator_close_requested", pid=pid, instance_id=instance.instance_id, method="window_close")
    _taskkill(pid, force=False)
    if _await_exit(instance, pid, CLOSE_GRACE_SECONDS, stop_path):
        return pid
    # Re-read right before forcing: never force a PID that changed hands.
    current = player_pids(instance)
    if current is None or current != [pid]:
        if current is not None and pid not in current:
            return pid
        raise CrashRecoveryError("Player process changed while closing; refusing to force")
    emit("emulator_close_requested", pid=pid, instance_id=instance.instance_id, method="force")
    _taskkill(pid, force=True)
    if _await_exit(instance, pid, CLOSE_FORCE_SECONDS, stop_path):
        return pid
    raise CrashRecoveryError(f"BlueStacks player {pid} did not exit")


def launch_player(instance):
    player = _player_path(instance)
    if not player.is_file():
        raise CrashRecoveryError(f"{PLAYER_EXE} not found next to the BlueStacks ADB executable")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([str(player), "--instance", instance.instance_id], cwd=str(player.parent),
                         creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise CrashRecoveryError(f"Could not start BlueStacks: {exc}") from exc


def wait_for_boot(instance, stop_path, timeout=BOOT_TIMEOUT_SECONDS):
    """Connect, prove identity, and wait for Android to finish booting."""
    deadline = time.monotonic() + timeout
    last = "BlueStacks has not answered yet"
    while time.monotonic() < deadline:
        if _stopped(stop_path):
            raise InterruptedError("Stop requested")
        try:
            verify_identity(instance, connect=True)
            if _adb_shell(instance, "getprop", "sys.boot_completed") == "1":
                _wait(BOOT_SETTLE_SECONDS, stop_path)
                verify_identity(instance)
                return
            last = "Android is still booting"
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            text = str(exc)
            if any(m in text.casefold() for m in FATAL_MARKERS):
                raise CrashRecoveryUnsafe(
                    "Android device identity mismatch after BlueStacks restart; refusing input: " + text) from exc
            last = text[:300]
        _wait(BOOT_POLL_SECONDS, stop_path)
    raise CrashRecoveryError(f"BlueStacks did not finish booting within {timeout} s ({last})")


def restart_emulator(instance, *, reason, emit, stop_path, budget, activity, context=None):
    """Run one budgeted emulator restart. Returns seconds until the worker continues.

    Raises CrashRecoveryUnsafe (worker must STOP) or InterruptedError (Stop).
    Other failures are reported and returned as a retry delay.
    """
    context = dict(context or {})
    if not supported(instance):
        emit("emulator_restart_unsupported", **context, reason="manual endpoint has no BlueStacks instance mapping")
        return 60
    budget.note_failing()
    decision, value = budget.decide("emulator_restarts")
    if decision == "give_up":
        emit("crash_recovery_exhausted", **context, reason=value)
        if stop_path is not None:
            Path(stop_path).write_text("Crash recovery exhausted: " + value, encoding="utf-8")
        budget.set_status("Needs attention: " + value)
        return 1
    if decision == "wait":
        budget.set_status(f"Restart budget used; next BlueStacks restart in {int(value // 60) + 1} min")
        emit("emulator_restart_deferred", **context, reason="hourly restart budget spent", delay_seconds=round(value))
        return min(value, 300)
    started = time.time()
    emit("emulator_restart_started", **context, reason=reason, instance_id=instance.instance_id)
    budget.note_restart("emulator_restarts", started)
    budget.set_status("Restarting BlueStacks")
    try:
        closed = close_player(instance, emit, stop_path)
        emit("emulator_closed", **context, pid=closed, was_running=closed is not None)
        _wait(RELAUNCH_PAUSE_SECONDS, stop_path)
        launch_player(instance)
        emit("emulator_launched", **context, instance_id=instance.instance_id)
        budget.set_status("Waiting for BlueStacks to boot")
        wait_for_boot(instance, stop_path)
        emit("emulator_booted", **context, seconds=round(time.time() - started, 1))
        _adb_shell(instance, "am", "start", "-n", activity, timeout=30)
        emit("emulator_restart_completed", **context, seconds=round(time.time() - started, 1),
             method="verified_activity_launch")
        budget.set_status("BlueStacks restarted; waiting for a recognized game screen")
        return 5
    except (CrashRecoveryUnsafe, InterruptedError):
        raise
    except (CrashRecoveryError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        emit("emulator_restart_failed", **context, error=str(exc)[:500])
        budget.set_status("BlueStacks restart failed; retrying")
        return 30


def request_crash_test(path):
    Path(path).write_text(json.dumps({"requested_at": time.time()}), encoding="utf-8")


def take_crash_test(path):
    path = Path(path)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True
