"""Deterministic farming on one verified BlueStacks instance at a time."""
from __future__ import annotations

import argparse
import contextlib
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import msvcrt
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from app.core.process_state import process_identity
from app.core.atomic_io import atomic_json, read_json_file
from automation.adb import Instance, discover, resolve_manual, validate_mapping, verify_identity, _server_port
from automation.safety import require_pearl_free_action_id, require_pearl_safe_action

# Screenshot matching needs no BLAS pool; concurrent CLI imports must stay small.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import cv2
import numpy as np
cv2.setNumThreads(1)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
CATALOG = ROOT / "assets" / "patterns" / "universal.json"
STOP = RUNTIME / "STOP"
ADB = Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe")
CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")
SERIAL = "127.0.0.1:5555"
PACKAGE = "com.reblazing.nbrc"
ACTIVITY = "com.reblazing.nbrc/com.blazingdreamers.nbrclient.NbrPlayerActivity"
SIZE = (900, 1600)
STAMINA_WAIT_SECONDS = 600
MAINTENANCE_RETRY_SECONDS = 60
STAMINA_MINUTES = (5, 10, 15, 20, 25)
LOOT_MAX_ATTEMPTS = 3
LOOT_RETRY_BASE_SECONDS = 1
LOOT_RETRY_MAX_SECONDS = 30
RECOVERY_RETRY_SECONDS = (5, 10, 20)
RECOVERY_BROAD_SECONDS = 30
TRANSITION_TIMEOUT_SECONDS = 90
# UNKNOWN/LOADING frames are normal while the game animates or loads. Within
# these windows the engine only re-observes quickly; it records no recovery,
# saves no diagnostic screenshot and sends no input. Recovery starts after.
UNSETTLED_SCREENS = frozenset({"UNKNOWN", "LOADING"})
TRANSITION_GRACE_SECONDS = {"pending": 60, "battle": 90, "idle": 20}
TRANSITION_POLL_SECONDS = 1.0
# A screen that changes between recognition and dispatch is an animation, not
# a fault: re-observe shortly. Only repeated instability enters recovery.
DISPATCH_DEFER_SECONDS = 0.5
DISPATCH_DEFER_LIMIT = 8
# Observation-only ticks may reuse a recent identity/foreground check. Every
# input path still verifies identity and foreground immediately before input.
IDENTITY_RECHECK_SECONDS = 30
FOREGROUND_RECHECK_SECONDS = 10
LOSS_OCR_INTERVAL_SECONDS = 6
_PROCESS_INSTANCE = None


def instance_dict(instance):
    return instance.to_dict()


def instance_from_dict(value):
    if not isinstance(value, dict):
        raise RuntimeError("Saved instance target is invalid; select a verified instance")
    try:
        instance = Instance(**{name: value[name] for name in Instance.__dataclass_fields__})
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Saved instance target is invalid; select a verified instance") from exc
    if not all(isinstance(getattr(instance, name), str) for name in Instance.__dataclass_fields__):
        raise RuntimeError("Saved instance target is invalid")
    if not instance.instance_id or not instance.endpoint or not instance.android_id or not instance.adb_path:
        raise RuntimeError("Saved instance target is incomplete")
    return instance


def legacy_owner():
    owner = read_json(RUNTIME / "legacy_instance.json")
    if not isinstance(owner, dict):
        raise RuntimeError("Legacy instance ownership record is invalid")
    return owner


def legacy_instance():
    """The old controller can only have owned this exact, config-backed target."""
    try:
        config = dict(re.findall(r'^([^=]+)="(.*)"$', CONF.read_text(encoding="utf-8-sig"), re.MULTILINE))
    except OSError as exc:
        raise RuntimeError("Legacy acc1 mapping is unavailable; instance selection is required") from exc
    prefix = "bst.instance.Pie64."
    if (config.get(prefix + "display_name") != "acc1" or config.get(prefix + "adb_port") != "5555"
            or not config.get(prefix + "android_id")):
        raise RuntimeError("Legacy acc1 mapping changed; refusing to guess a target")
    owner = legacy_owner()
    if owner.get("unresolved"):
        raise RuntimeError("Legacy instance ownership is unresolved; select a separately verified instance")
    if owner and instance_from_dict(owner).android_id != config[prefix + "android_id"]:
        raise RuntimeError("Legacy Android identity changed; select a separately verified instance")
    return Instance(str(CONF), "Pie64", "acc1", str(ADB), SERIAL,
                    config[prefix + "android_id"], "unavailable", "disconnected", "Legacy acc1 target")


def is_legacy_instance(instance):
    coordinates = (instance.instance_id == "Pie64" and instance.endpoint == SERIAL
                   and os.path.normcase(os.path.abspath(instance.config_path)) == os.path.normcase(os.path.abspath(CONF)))
    if not coordinates:
        return False
    owner = legacy_owner()
    if owner.get("unresolved"):
        return False
    return not owner or instance_from_dict(owner).android_id == instance.android_id


def preserve_legacy_owner(instance):
    """Bind old unnamespaced latches before an explicit selection can replace them."""
    path = RUNTIME / "legacy_instance.json"
    if path.exists():
        owner = legacy_owner()
        if not owner.get("unresolved"):
            instance_from_dict(owner)
        return
    previous = read_json(RUNTIME / "instance_selection.json")
    if previous:
        previous = instance_from_dict(previous)
        if is_legacy_instance(previous):
            write_json(path, instance_dict(previous))
            return
    legacy_state = any((RUNTIME / name).exists() for name in
                       ("selection.json", "session.json", "universal.json", "farm.json"))
    if not legacy_state:
        if is_legacy_instance(instance):
            write_json(path, instance_dict(instance))
        return
    if legacy_state or is_legacy_instance(instance):
        try:
            legacy = legacy_instance()
        except RuntimeError:
            # The original device identity cannot be recovered. Keep its old
            # progress quarantined even if Pie64/5555 is recreated in the future.
            write_json(path, {"unresolved": True})
            return
        write_json(path, instance_dict(legacy))


def instance_runtime(instance=None):
    instance = instance or _PROCESS_INSTANCE
    if instance is None or is_legacy_instance(instance):
        return RUNTIME
    identity = hashlib.sha256((instance.key + "\0" + instance.android_id).encode()).hexdigest()[:24]
    return RUNTIME / "instances" / identity


def state_path(name, instance=None):
    return instance_runtime(instance) / name


def instance_account(instance):
    return "acc1" if is_legacy_instance(instance) else "instance_" + hashlib.sha256(
        (instance.key + "\0" + instance.android_id).encode()).hexdigest()[:24]


def controller_active():
    """Locks and legacy claims are authoritative even without creation identities."""
    return bool(live_controller_pids() or lock_busy("worker") or lock_busy("supervisor")
                or any(process_may_be_alive(record) for record in recorded_processes()))


def instance_locked():
    return controller_active() or lock_busy("restart") or lock_busy("control")


def active_instance():
    if not controller_active():
        return None
    targets = []
    for name, key in (("supervisor.json", "pid"), ("controller.json", "worker_pid")):
        record = read_json(RUNTIME / name)
        if record.get("instance") and (process_may_be_alive({"pid": record.get(key),
                "pid_identity": record.get(key + "_identity")}) or lock_busy("supervisor" if key == "pid" else "worker")):
            targets.append(instance_from_dict(record["instance"]))
    if targets:
        if len({(target.key, target.android_id, target.endpoint) for target in targets}) != 1:
            raise RuntimeError("Active controller target records disagree; selection is locked")
        return targets[0]
    # A pre-v1.23 supervisor may respawn this source with no target argument.
    return legacy_instance()


def selected_instance():
    saved = read_json(RUNTIME / "instance_selection.json")
    if saved:
        instance = instance_from_dict(saved)
        validate_mapping(instance)
        return instance
    legacy_state = any((RUNTIME / name).exists() for name in
                       ("selection.json", "session.json", "universal.json", "farm.json"))
    if legacy_state:
        # Never reinterpret old pending input, waits or session history as another account.
        return legacy_instance()
    instances = discover()["instances"]
    if len(instances) != 1:
        raise RuntimeError("Select a BlueStacks instance" if instances else "No verified BlueStacks instance is available")
    validate_mapping(instances[0])
    return instances[0]


def effective_instance():
    return _PROCESS_INSTANCE or active_instance() or selected_instance()


def select_instance(instance):
    with lock("control"):
        if controller_active() or lock_busy("restart"):
            raise RuntimeError("Instance selection is locked while automation is active or its exit is unverified")
        validate_mapping(instance)
        verify_identity(instance, connect=True)
        preserve_legacy_owner(instance)
        write_json(RUNTIME / "instance_selection.json", instance_dict(instance))
    return instance


def stamina_wait_minutes():
    value = read_json(RUNTIME / "manager_settings.json").get("stamina_wait_minutes", STAMINA_WAIT_SECONDS // 60)
    return value if type(value) is int and value in STAMINA_MINUTES else 5


def set_stamina_duration(minutes):
    if type(minutes) is not int or minutes not in STAMINA_MINUTES:
        raise ValueError("Stamina duration must be 5, 10, 15, 20, or 25 minutes")
    with lock("stamina-control"):
        settings = read_json(RUNTIME / "manager_settings.json")
        settings["stamina_wait_minutes"] = minutes
        write_json(RUNTIME / "manager_settings.json", settings)
    event("stamina_duration_saved", minutes=minutes)
    print(f"Subsequent stamina waits: {minutes} minutes")


def boss_swap_enabled():
    return read_json(RUNTIME / "manager_settings.json").get("ninja_boss_swap") is True


def early_exit_enabled():
    return read_json(RUNTIME / "manager_settings.json").get("ninja_early_exit") is True


NINJA_EXIT_MODES = ("stage10", "stage15")


def ninja_exit_mode():
    mode = read_json(RUNTIME / "manager_settings.json").get("ninja_exit_mode", "stage10")
    return mode if mode in NINJA_EXIT_MODES else "stage10"


def set_ninja_exit_mode(mode):
    if mode not in NINJA_EXIT_MODES:
        raise ValueError("Choose Stage 10 Exit or Stage 15 Exit")
    with lock("control"), lock("stamina-control"):
        if controller_active() or lock_busy("restart"):
            raise RuntimeError("Ninja Road mode is locked while automation is active or its exit is unverified")
        settings = read_json(RUNTIME / "manager_settings.json")
        settings["ninja_exit_mode"] = mode
        write_json(RUNTIME / "manager_settings.json", settings)
    event("ninja_exit_mode_setting", mode=mode)
    print("Ninja Road mode: Stage " + ("15" if mode == "stage15" else "10") + " Exit")


def set_boss_swap(enabled):
    if type(enabled) is not bool:
        raise ValueError("Boss Swap must be enabled or disabled")
    with lock("stamina-control"):
        settings = read_json(RUNTIME / "manager_settings.json")
        settings["ninja_boss_swap"] = enabled
        write_json(RUNTIME / "manager_settings.json", settings)
    event("ninja_boss_swap_setting", enabled=enabled)
    print("Boss Swap " + ("enabled" if enabled else "disabled"))


def set_early_exit(enabled):
    if type(enabled) is not bool:
        raise ValueError("Early Exit must be enabled or disabled")
    with lock("stamina-control"):
        settings = read_json(RUNTIME / "manager_settings.json")
        settings["ninja_early_exit"] = enabled
        write_json(RUNTIME / "manager_settings.json", settings)
    event("ninja_early_exit_setting", enabled=enabled)
    print("Early Exit " + ("enabled" if enabled else "disabled"))


def stamina_status(progress):
    if (STOP.exists() or not record_alive(progress, "worker_pid")
            or time.time() - progress.get("heartbeat", 0) > 90
            or progress.get("screen") in {None, "UNKNOWN", "OTHER_FOREGROUND", "GAME_CLOSED"}):
        return "UNKNOWN"
    if progress.get("stamina_until", 0) > time.time():
        return "WAITING"
    # READY means the delay is over; game observations still gate every input.
    return "READY"


def end_stamina_wait():
    with lock("control"), lock("stamina-control"):
        stage = selected_stage()
        target = active_instance()
        progress = read_json(load_stage(stage, target)["progress"])
        if (stamina_status(progress) != "WAITING" or progress.get("pending")
                or progress.get("manager_controls_version") != 1):
            raise RuntimeError("No confirmed interruptible stamina wait is active")
        write_json(state_path("stamina_request.json", target), {
            "worker_pid": progress["worker_pid"], "stage": stage,
            "stamina_until": progress["stamina_until"], "id": uuid.uuid4().hex})
    event("stamina_wait_end_requested", stage=stage)
    print("Stamina delay skip requested; fresh game checks remain required")
ctypes.windll.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
ctypes.windll.kernel32.OpenProcess.restype = wintypes.HANDLE
ctypes.windll.kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
ALLOWED_ACTIONS = {
    "retry", "start_mission", "advance_completion", "next", "dismiss_stamina",
    "dismiss_known_popup", "retry_connection", "reveal_rewards", "enable_auto",
}
UNIVERSAL_AUTO_ACTION = {
    "id": "enable_auto", "kind": "tap", "point": [623, 56],
    "expected": ["IN_BATTLE", "MISSION_COMPLETE"],
}
STATE_ACTIONS = {
    "READY_TO_RETRY": "retry", "READY_TO_START": "start_mission", "IN_BATTLE": "enable_auto",
    "MISSION_COMPLETE": "advance_completion", "RESULTS_NEXT": "next", "TRIAL_COINS_NEXT": "next", "REWARD_REVEAL": "reveal_rewards",
    "RANK_UP_STAMINA_ACK": "dismiss_known_popup", "RANK_UP_RESULTS": "next",
    "COMMUNICATION_ERROR": "retry_connection", "STAMINA_PROMPT": "dismiss_stamina",
    "MAINTENANCE_PROMPT": "dismiss_known_popup",
}


def universal_auto_action(stage, name, spec):
    return bool(stage == "universal" and name == "IN_BATTLE" and spec
                and spec.get("battle_auto_enabled") is False and spec.get("verified_safe")
                and spec.get("action") == UNIVERSAL_AUTO_ACTION)


def read_json(path, default=None):
    try:
        return read_json_file(path)
    except FileNotFoundError:
        return {} if default is None else default


def write_json(path, value):
    atomic_json(path, value, indent=2)


STAGE_IDS = ("universal", "ninja_road")

NINJA_STATE_ACTIONS = {
    "NR_HOME": "nr_available", "NR_AVAILABLE": "nr_select", "NR_ENTRY": "nr_enter",
    "NR_START": "nr_start", "NR_TEAM_SELECT": "nr_team", "NR_TEAM_DEFEATED": "nr_team",
    "NR_ALL_DEFEATED": "nr_finish", "NR_RESULTS": "nr_next", "NR_RETURN": "nr_return",
    "NR_COMBAT_AUTO_OFF": "nr_auto", "NR_COMBAT_NINJUTSU_OFF": "nr_ninjutsu",
    "STAMINA_PROMPT": "dismiss_stamina", "MISSION_COMPLETE": "advance_completion",
    "COMMUNICATION_ERROR": "retry_connection",
    "NR_EARLY_EXIT": "nr_retreat", "NR_RETREAT_CONFIRM": "nr_retreat_confirm",
    "NR_CHARACTER_INFO": "nr_close_info",
    "NR_EARLY_EXIT_PREPARE": "nr_auto",
}
ALLOWED_ACTIONS.update(NINJA_STATE_ACTIONS.values())
ALLOWED_ACTIONS.update({"nr_boss_swap", "nr_close_info"})
ALLOWED_ACTIONS.update({"nr_secret", "nr_strategy_team"})


def load_stage(stage="universal", instance=None):
    if stage not in STAGE_IDS:
        raise ValueError("Unknown farming stage")
    if stage == "ninja_road":
        return {"id": stage, "name": "Ninja Road", "catalog": ROOT / "assets/patterns/ninja_road.json",
                "progress": state_path("ninja_road.json", instance), "state_actions": dict(NINJA_STATE_ACTIONS)}
    return {"id": stage, "name": "Universal — current mission", "catalog": CATALOG,
            "progress": state_path("universal.json", instance), "state_actions": dict(STATE_ACTIONS)}


def selected_stage():
    stage = read_json(RUNTIME / "selection.json").get("stage", "universal")
    return stage if stage in STAGE_IDS else "universal"


def create_engine(stage, instance=None):
    if stage == "ninja_road":
        from automation.ninja_road import NinjaRoadEngine
        return NinjaRoadEngine(stage, instance)
    return Engine(stage, instance)


def progress_records(instance=None):
    """Retain account safeguards from saved controller state across upgrades."""
    for path in instance_runtime(instance).glob("*.json"):
        data = read_json(path)
        if isinstance(data, dict) and any(key in data for key in ("counts", "pending", "connection_pending", "stamina_until", "stage15_strategy")):
            yield path, data


def alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_claim(pid, key="pid"):
    """A PID alone is not durable evidence that this is our controller."""
    try:
        identity = process_identity(pid)[0] if pid else None
    except (OSError, ValueError):
        identity = None  # A child may exit before its first state is published.
    return {key: pid, key + "_identity": identity}


def record_alive(record, key="pid"):
    pid, expected = record.get(key), record.get(key + "_identity")
    if not expected or not alive(pid):
        return False
    try:
        return process_identity(pid)[0] == expected
    except (OSError, ValueError):
        return False


def process_may_be_alive(record):
    """Access denied is not evidence of exit; a recycled, identified PID is."""
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    if alive(pid):
        expected = record.get("pid_identity")
        if expected:
            try:
                return process_identity(pid)[0] == expected
            except (OSError, ValueError):
                pass
        return True
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        try:
            code = ctypes.c_ulong()
            return (not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                    or code.value == 259)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return ctypes.windll.kernel32.GetLastError() != 87


@contextlib.contextmanager
def lock(name, wait_seconds=0):
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME / (name + ".lock")).open("a+b") as f:
        f.seek(0, os.SEEK_END)
        if not f.tell():
            f.write(b"0")
            f.flush()
        f.seek(0)
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{name} already running; no duplicate process/input permitted")
                time.sleep(.05)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def lock_busy(name):
    try:
        with lock(name):
            return False
    except RuntimeError:
        return True


def recorded_processes():
    records = []
    for path in [*RUNTIME.glob("*.json"), *RUNTIME.glob("instances/*/*.json")]:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        for key in ("pid", "worker_pid"):
            if type(data.get(key)) is int and data[key] > 0:
                records.append({"pid": data[key], "pid_identity": data.get(key + "_identity")})
    return records


def recorded_pids():
    return {record["pid"] for record in recorded_processes()}


def live_controller_pids(exclude=()):
    # Older records have no identity; account locks still prevent duplicates
    # while an older controller is running, without trusting a recycled PID.
    return sorted({record["pid"] for record in recorded_processes()
                   if record["pid"] not in exclude and record_alive(record)})


def controller_exclusions(stage, supervisor_pid=None, launching_supervisor=False):
    """Windows venv launchers remain the parent of the real Python process."""
    own = {os.getpid()}
    claim = read_json(RUNTIME / "supervisor.json")
    if claim.get("stage", "universal") != stage:
        return own
    if launching_supervisor and claim.get("launching") and claim.get("pid") == os.getppid():
        own.add(os.getppid())
    if supervisor_pid and claim.get("pid") == supervisor_pid:
        own.add(supervisor_pid)
        if claim.get("worker_pid") == os.getppid():
            own.add(os.getppid())
    return own


def verify_saved_input(instance=None, stage="universal"):
    destination = load_stage(stage, instance)["progress"]
    supported = {load_stage(candidate, instance)["progress"] for candidate in STAGE_IDS}
    previous = read_json(RUNTIME / "selection.json").get("stage")
    legacy_previous = (isinstance(previous, str) and bool(previous) and previous not in STAGE_IDS
                       and (instance is None or is_legacy_instance(instance)))
    for path, progress in progress_records(instance):
        if path == destination:
            continue  # This stage resumes its own durable latch.
        relevant_previous = legacy_previous and (
            progress.get("stage") == previous or path.stem == previous.replace("-", "_")
            or path.name == "farm.json")
        if (path in supported or relevant_previous) and (progress.get("pending") or progress.get("connection_pending")
                or (progress.get("boss_swap") or {}).get("pending")
                or (progress.get("stage15_strategy") or {}).get("pending")):
            raise RuntimeError("Resolve the existing saved input before starting. "
                               "At a Universal Start Mission screen, use Resolve input, then Start.")


def _saved_input_blockers(progress):
    """Return durable input latches without treating ordinary saved state as input."""
    blockers = {}
    if progress.get("pending"):
        blockers["pending"] = progress["pending"]
    if progress.get("connection_pending"):
        blockers["connection_pending"] = progress["connection_pending"]
    boss_swap = progress.get("boss_swap")
    if isinstance(boss_swap, dict) and boss_swap.get("pending"):
        blockers["boss_swap"] = boss_swap["pending"]
    strategy = progress.get("stage15_strategy")
    if isinstance(strategy, dict) and strategy.get("pending"):
        blockers["stage15_strategy"] = strategy["pending"]
    return blockers


def _recording_is_resolved(run):
    recording = run.get("recording") if isinstance(run, dict) else None
    if not recording:
        return True
    return bool(isinstance(recording, dict) and (
        recording.get("state") == "screenshot_saved"
        or (recording.get("state") == "missing_screenshot"
            and recording.get("missing_evidence_acknowledged") is True)
    ))


def _validate_superseded_ninja_input(progress, blockers):
    """Validate the two explicit Ninja latches eligible for cross-stage closure."""
    if len(blockers) != 1:
        raise RuntimeError("Exactly one saved Ninja input is required for recovery")
    kind, blocker = next(iter(blockers.items()))
    run = progress.get("run")
    if kind == "pending":
        if not isinstance(blocker, dict):
            raise RuntimeError("Saved Ninja navigation input is invalid")
        permitted = {
            ("nr_available", "NR_HOME"),
            ("nr_select", "NR_AVAILABLE"),
            ("nr_enter", "NR_ENTRY"),
            ("nr_start", "NR_START"),
        }
        if ((blocker.get("id"), blocker.get("source")) not in permitted
                or not isinstance(blocker.get("expected"), list)
                or not blocker["expected"]
                or any(not isinstance(value, str) or not value for value in blocker["expected"])
                or not isinstance(blocker.get("run_id"), str) or not blocker["run_id"]
                or isinstance(blocker.get("time"), bool)
                or not isinstance(blocker.get("time"), (int, float))
                or not math.isfinite(blocker["time"]) or blocker["time"] <= 0
                or blocker["time"] > time.time()):
            raise RuntimeError("Saved Ninja navigation input is not eligible for recovery")
        if isinstance(run, dict) and run.get("outcome") not in {
                "completed", "failed", "early_exit", "abandoned"}:
            raise RuntimeError("An active Ninja run cannot discard its saved navigation input")
        if not _recording_is_resolved(run):
            raise RuntimeError("Resolve the existing recording before discarding its saved input")
        return kind
    if kind != "boss_swap" or not isinstance(blocker, dict):
        raise RuntimeError("This saved Ninja input requires same-stage recovery")
    boss_swap = progress.get("boss_swap")
    current_team = progress.get("current_team")
    if (not isinstance(run, dict) or not isinstance(run.get("id"), str) or not run["id"]
            or run.get("outcome") is not None or type(current_team) is not int or current_team < 1
            or not isinstance(boss_swap, dict) or boss_swap.get("phase") != "restore"
            or boss_swap.get("team_key") != [run["id"], current_team]
            or blocker.get("purpose") != "swipe" or blocker.get("returned") is not True
            or type(blocker.get("column")) is not int or blocker["column"] not in (0, 1, 2)
            or isinstance(blocker.get("time"), bool)
            or not isinstance(blocker.get("time"), (int, float))
            or not math.isfinite(blocker["time"]) or blocker["time"] <= 0
            or blocker["time"] > time.time() or not _recording_is_resolved(run)):
        raise RuntimeError("Saved Ninja Boss Swap restore input is not eligible for recovery")
    return kind


def _save_input_recovery_evidence(instance, source_bytes, frames):
    directory = instance_runtime(instance) / "recovery" / ("saved_input_" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    state_path = directory / "ninja_road.before.json"
    with state_path.open("xb") as stream:
        stream.write(source_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    images = []
    for index, frame in enumerate(frames, 1):
        image = directory / f"verified_start_{index}.png"
        if cv2.imwrite(str(image), frame) is not True:
            raise OSError(f"Could not save recovery observation {index}")
        images.append(image)
    return state_path, images, hashlib.sha256(source_bytes).hexdigest()


def resolve_saved_input(stage=None, *, reason, expected_instance_key=None):
    """Explicitly close one superseded Ninja latch at verified Universal Start."""
    if stage not in (None, "universal"):
        raise RuntimeError("Saved-input recovery only supports switching from Ninja Road to Universal")
    stage = "universal"
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Provide a reason for resolving the saved input")
    reason = reason.strip()
    with contextlib.ExitStack() as stack:
        stack.enter_context(lock("control"))
        if not STOP.exists():
            raise RuntimeError("Stop farming before resolving a saved input")
        if lock_busy("restart"):
            raise RuntimeError("Full Restart is underway; saved-input recovery refused")
        if controller_active():
            raise RuntimeError("Stop the controller and verify its exit before resolving a saved input")
        stack.enter_context(lock("worker"))
        instance = process_target()
        if expected_instance_key is not None and (
                not isinstance(expected_instance_key, str) or not expected_instance_key
                or instance.key != expected_instance_key):
            raise RuntimeError("The selected instance changed; saved-input recovery refused")
        source_path = load_stage("ninja_road", instance)["progress"]
        destination_path = load_stage(stage, instance)["progress"]
        try:
            source_bytes = source_path.read_bytes()
            progress = json.loads(source_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Saved Ninja state is unavailable or invalid") from exc
        if not isinstance(progress, dict) or progress.get("stage") != "ninja_road":
            raise RuntimeError("Saved Ninja state does not identify Ninja Road")
        pinned = instance_from_dict(progress.get("instance"))
        if ((pinned.key, pinned.android_id, pinned.endpoint)
                != (instance.key, instance.android_id, instance.endpoint)):
            raise RuntimeError("Saved Ninja input target does not match the selected instance")
        destination = read_json(destination_path)
        if _saved_input_blockers(destination):
            raise RuntimeError("Resolve the destination stage input before cross-stage recovery")
        blockers = _saved_input_blockers(progress)
        kind = _validate_superseded_ninja_input(progress, blockers)

        device = Device(instance)
        device.connect()
        detector = Detector(stage)
        frames, observations = [], []
        for index in range(2):
            if index:
                time.sleep(.25)
            device.verify()
            if device.foreground() != PACKAGE:
                raise RuntimeError("Restore the game to a verified Universal Start Mission screen")
            frame = device.capture()
            name, spec, _ = detector.classify(frame)
            if name != "READY_TO_START" or not spec or spec.get("verified_safe") is not True:
                raise RuntimeError("Two fresh verified Universal Start Mission observations are required")
            frames.append(frame)
            observations.append({"time": time.time(), "screen": name,
                                 "android_id": instance.android_id, "instance_key": instance.key,
                                 "endpoint": instance.endpoint, "foreground": PACKAGE})
        if source_path.read_bytes() != source_bytes:
            raise RuntimeError("Saved Ninja input changed during recovery; no state was cleared")

        backup, images, digest = _save_input_recovery_evidence(instance, source_bytes, frames)
        if source_path.read_bytes() != source_bytes:
            raise RuntimeError("Saved Ninja input changed while recovery evidence was saved")
        resolved_at = time.time()
        updated = copy.deepcopy(progress)
        if kind == "pending":
            updated["pending"] = None
        else:
            updated.pop("boss_swap", None)
            updated["run"].update(outcome="abandoned", ended=resolved_at)
        updated["blocked"] = None
        updated.pop("transition_timed_out", None)
        updated.pop("unknown_since", None)
        history = updated.get("saved_input_resolutions", [])
        if not isinstance(history, list):
            raise RuntimeError("Saved-input recovery audit is invalid; no state was cleared")
        audit = {
            "reason": reason,
            "resolved_at": resolved_at,
            "source_stage": "ninja_road",
            "destination_stage": stage,
            "blocker": kind,
            "instance_key": instance.key,
            "original_state": str(backup),
            "original_sha256": digest,
            "observations": [
                {**observation, "image": str(image)}
                for observation, image in zip(observations, images)
            ],
        }
        updated["saved_input_resolutions"] = [*history[-19:], audit]
        if source_path.read_bytes() != source_bytes:
            raise RuntimeError("Saved Ninja input changed before recovery was committed")
        write_json(source_path, updated)
        event("saved_input_resolved", source_stage="ninja_road", destination_stage=stage,
              blocker=kind, reason=reason, original_sha256=digest)
    print("Saved Ninja input archived and resolved; Start can resume Universal farming")


def logger():
    RUNTIME.mkdir(exist_ok=True)
    result = logging.getLogger("nbr")
    if not result.handlers:
        result.setLevel(logging.INFO)
        handler = RotatingFileHandler(RUNTIME / "events.jsonl", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        result.addHandler(handler)
    return result


def event(kind, **values):
    logger().info(json.dumps({"time": time.time(), "event": kind, **values}))


_last_loot_error = float("-inf")


def _loot_tracking_error(exc):
    """Loot diagnostics are bounded and must never enter farmer recovery."""
    global _last_loot_error
    now = time.monotonic()
    if now - _last_loot_error < 60:
        return
    _last_loot_error = now
    try:
        event("loot_tracking_error", error=str(exc)[:500])
    except Exception:
        pass


def _loot_call(collector, method, *args, **kwargs):
    if collector is None:
        return None
    try:
        return getattr(collector, method)(*args, **kwargs)
    except Exception as exc:
        _loot_tracking_error(exc)
        return None


class RecoveringLootCollector:
    """Retry collector startup in the existing worker, independently of device recovery."""
    def __init__(self, stage, supervisor_pid=None, instance=None):
        self.stage, self.supervisor_pid, self.instance = stage, supervisor_pid, instance
        self.collector = None
        self.attempts, self.next_retry_at, self.error = 0, 0, None
        self.closed = False
        self._get()

    def _get(self):
        if self.closed or self.collector is not None or time.time() < self.next_retry_at:
            return self.collector
        try:
            from app.backend.loot import Collector, process_identity
            identity, started = process_identity(self.supervisor_pid or os.getpid())
            self.collector = Collector(ROOT, self.stage, identity, process_started=started,
                account=instance_account(self.instance) if self.instance else "acc1",
                device_id=self.instance.android_id if self.instance else None)
            self.error, self.next_retry_at = None, 0
        except Exception as exc:
            self.attempts += 1
            self.error = str(exc)
            self.next_retry_at = time.time() + min(LOOT_RETRY_MAX_SECONDS,
                LOOT_RETRY_BASE_SECONDS * 2 ** min(self.attempts - 1, 10))
            _loot_tracking_error(exc)
        return self.collector

    def completed(self, *args, **kwargs):
        collector = self._get()
        return collector.completed(*args, **kwargs) if collector else None

    def evidence(self, *args, **kwargs):
        collector = self._get()
        return collector.evidence(*args, **kwargs) if collector else False

    def recover_evidence(self, *args, **kwargs):
        collector = self._get()
        return collector.recover_evidence(*args, **kwargs) if collector else False

    def missing(self, *args, **kwargs):
        collector = self._get()
        return collector.missing(*args, **kwargs) if collector else False

    def resume_pending(self):
        collector = self._get()
        return collector.resume_pending() if collector else []

    def health(self):
        collector = self._get()
        if collector:
            return collector.health()
        return dict(status="collector_initialization_retry", error=self.error,
                    next_retry_at=self.next_retry_at, attempts=self.attempts)

    def close(self, **kwargs):
        self.closed = True
        if self.collector:
            self.collector.close(**kwargs)


def _loot_start(stage, supervisor_pid=None, instance=None):
    return RecoveringLootCollector(stage, supervisor_pid, instance)


class RecordingPersistenceError(RuntimeError):
    """Keep the observed completion in memory while its durable state is retried."""


def _loot_end_process():
    try:
        from app.backend.loot import end_process_session, process_identity
        identity, _ = process_identity(os.getpid())
        end_process_session(ROOT, identity)
    except Exception as exc:
        _loot_tracking_error(exc)


def loss_text(text):
    """Stop-only OCR guard; never infer a permitted control from text."""
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if any(phrase in normalized for phrase in (
        "mission failed", "game over", "you have been defeated", "your team has been defeated",
    )):
        return True
    words = set(normalized.split())
    pearls = bool(words & {"pearl", "pearls"})
    return pearls and ("revive" in words or "revival" in words or
                       ("continue" in words and "ninja" in words))


def ocr_text(frame):
    path = RUNTIME / "loss_probe.png"
    cv2.imwrite(str(path), frame)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "loss_ocr.ps1"), "-ImagePath", str(path)],
        capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Local Windows OCR unavailable: " + result.stderr.decode(errors="replace")[:200])
    return json.loads(result.stdout.decode("utf-8-sig"))["text"]


def notify_loss():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pythonw), str(ROOT / "scripts" / "loss_notify.py")],
                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ActionNotDispatched(RuntimeError):
    """An action failed before any command capable of sending input was attempted."""


class ScreenChanged(RuntimeError):
    """The final pre-dispatch observation no longer matched; no input was sent."""


def _decode_raw_screencap(data):
    """Decode `screencap` raw RGBA output; return None if the format is unfamiliar.

    Raw output skips on-device PNG encoding (the slowest part of a capture).
    Header: width, height, format (+ colorspace on Android 10+), little-endian.
    """
    if len(data) < 16:
        return None
    width, height, pixel_format = np.frombuffer(data, "<u4", count=3)
    payload = int(width) * int(height) * 4
    header = len(data) - payload
    if header not in (12, 16) or int(pixel_format) not in (1, 2) or not width or not height:
        return None
    rgba = np.frombuffer(data, np.uint8, count=payload, offset=header).reshape(int(height), int(width), 4)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)


class Device:
    def __init__(self, instance=None):
        self.target = instance or effective_instance()
        self.identity = None

    def command(self, args, timeout=15):
        if args[:2] in (["shell", "input"], ["shell", "monkey"]) and STOP.exists():
            raise InterruptedError("Stop requested before input")
        r = subprocess.run([self.target.adb_path, "-H", "127.0.0.1", "-P", str(_server_port()),
                            "-s", self.target.endpoint, *args], capture_output=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace").strip()[:500])
        return r.stdout

    def verify(self, timeout=15):
        validate_mapping(self.target)
        if not self.target.config_path:
            verify_identity(self.target)  # Manual endpoints must still identify a BlueStacks device.
        identity = self.command(["shell", "settings", "get", "secure", "android_id"], timeout=timeout).decode().strip()
        if not identity or identity != self.target.android_id:
            raise RuntimeError("Android device identity mismatch; refusing input")
        self.identity = identity

    def connect(self):
        validate_mapping(self.target)
        verify_identity(self.target, connect=True)
        self.verify()

    def foreground(self, timeout=15):
        text = self.command(["shell", "dumpsys", "activity", "activities"], timeout=timeout).decode(errors="replace")
        match = re.search(r'(?:topResumedActivity|mResumedActivity):.*?\bu\d+\s+(\S+)', text)
        return match.group(1).split("/")[0] if match else None

    def capture(self, timeout=15):
        frame = None
        if getattr(self, "raw_capture", True):
            frame = _decode_raw_screencap(self.command(["exec-out", "screencap"], timeout=timeout))
            if frame is None:
                # Unfamiliar raw format: use PNG for the rest of this process.
                self.raw_capture = False
                event("capture_format_fallback", format="png")
        if frame is None:
            data = self.command(["exec-out", "screencap", "-p"], timeout=timeout)
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or (frame.shape[1], frame.shape[0]) != SIZE:
            raise RuntimeError("Screenshot invalid or dimensions changed; refusing input")
        return frame

    def perform(self, action):
        require_pearl_free_action_id(action.get("id") if isinstance(action, dict) else None)
        self._input_attempted = False
        try:
            self._perform(action)
        except Exception as exc:
            if not self._input_attempted:
                raise ActionNotDispatched(str(exc)) from exc
            raise

    def _input(self, args):
        if STOP.exists():
            raise InterruptedError("Stop requested before input")
        # Mark BEFORE invoking ADB: even a timeout may have delivered the input.
        self._input_attempted = True
        return self.command(args)

    def _perform(self, action):
        self.verify()
        if STOP.exists():
            raise InterruptedError("Stop requested")
        if self.foreground() != PACKAGE:
            raise RuntimeError("Game is not foreground; refusing input")
        if action["id"] not in ALLOWED_ACTIONS:
            raise RuntimeError("Action is not on the safe allowlist")
        guard = getattr(self, "input_guard", None)
        if callable(guard):
            guard()  # Fresh screen authorization after identity/foreground checks.
        if action["kind"] == "tap":
            x, y = action["point"]
            if not (0 <= x < SIZE[0] and 0 <= y < SIZE[1]):
                raise RuntimeError("Tap out of screenshot bounds")
            if action.get("input_mode") == "hardware_touch":
                self.hardware_touch(x,y)
            elif action.get("input_mode") == "mouse_tap":
                self._input(["shell", "input", "mouse", "tap", str(x), str(y)])
            elif action.get("input_mode") == "adb_tap":
                self._input(["shell", "input", "tap", str(x), str(y)])
            else:
                self._input(["shell", "input", "swipe", str(x), str(y), str(x), str(y), "350"])
        elif action["kind"] == "swipe" and action["id"] == "nr_boss_swap":
            x1, y1 = action["start"]
            x2, y2 = action["end"]
            duration = action.get("duration", 350)
            if (not all(type(value) is int for value in (x1, y1, x2, y2, duration))
                    or not (0 <= x1 < SIZE[0] and 0 <= x2 < SIZE[0]
                            and 1320 <= y1 < SIZE[1] and 1320 <= y2 < SIZE[1])
                    or not 200 <= duration <= 600
                    or x1 // 300 != x2 // 300):
                raise RuntimeError("Invalid Ninja Road portrait swap; refusing input")
            self._input(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])
        else:
            raise RuntimeError("Unapproved input kind")

    def hardware_touch(self,x,y):
        """Use the observed BlueStacks touch driver; no root or settings changes."""
        dump=self.command(["shell","dumpsys","input"]).decode(errors="replace").replace('\r','')
        match=re.search(r'Device \d+: BlueStacks Virtual Touch\n(.*?)(?=\n  Device |\Z)',dump,re.S)
        if not match:
            raise RuntimeError('BlueStacks touch driver unavailable')
        block=match.group(1)
        required=['SurfaceWidth: 1600px','SurfaceHeight: 900px','SurfaceOrientation: 1',
                  'Last Raw Touch: pointerCount=0','max=32767','XPrecision: 20.480','YPrecision: 36.409',
                  'X scale: 1.000','X ymix: 0.000','X offset: 0.000',
                  'Y xmix: 0.000','Y scale: 1.000','Y offset: 0.000']
        if not all(value in block for value in required):
            raise RuntimeError('Touch calibration changed or another touch is active; refusing input')
        devices=self.command(['shell','getevent','-pl']).decode(errors='replace').replace('\r','')
        node_match=re.search(r'add device \d+: (/dev/input/event\d+)\n\s+name:\s+"BlueStacks Virtual Touch"',devices)
        if not node_match:
            raise RuntimeError('Touch node identity not verified')
        node=node_match.group(1)
        # Android ROTATION_90: logical x=rawY*900/32768,
        # logical y=(32767-rawX)*1600/32768. Invert exactly.
        rawx=32767-round(y*32768/1600)
        rawy=round(x*32768/900)
        if STOP.exists():
            raise InterruptedError('Stop requested before hardware touch')
        release=f'sendevent {node} 3 47 0; sendevent {node} 3 57 -1; sendevent {node} 1 330 0; sendevent {node} 1 272 0; sendevent {node} 0 0 0'
        script=(f"trap '{release}' EXIT HUP INT TERM; "
                f'sendevent {node} 3 47 0; sendevent {node} 3 57 4242; '
                f'sendevent {node} 3 53 {rawx}; sendevent {node} 3 54 {rawy}; '
                f'sendevent {node} 1 330 1; sendevent {node} 1 272 1; sendevent {node} 0 0 0; '
                'dumpsys input > /data/local/tmp/nbr_touch_diagnostics.txt; sleep 0.25')
        output=self._input(['shell',script])
        if output.strip():
            raise RuntimeError('Hardware touch reported unexpected output; input latch retained')

    def launch(self):
        self.verify()
        if STOP.exists():
            raise InterruptedError("Stop requested")
        self.command(["shell", "am", "start", "-n", ACTIVITY])


def _binarize(image, threshold):
    return np.where(image >= int(threshold), 255, 0).astype(np.uint8)


class Detector:
    @staticmethod
    def file_signature(paths):
        result = []
        for path in sorted(set(paths)):
            try:
                info = path.stat()
                stamp = (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
            except FileNotFoundError:
                stamp = None
            result.append((str(path), stamp))
        return tuple(result)

    def changed(self):
        return self.file_signature(self.dependencies) != self.signature

    def __init__(self, stage="universal"):
        self.stage = load_stage(stage)
        self.catalog = self.stage["catalog"]
        config_paths = []
        config_signature = ()
        catalogs = [self.catalog]
        catalog_signature = self.file_signature(catalogs)
        self.states = read_json(self.catalog)['states']
        references = set()
        for state in self.states:
            for anchor in state["anchors"]:
                references.update([anchor["template"], *anchor.get("alternatives", [])])
        template_paths = [ROOT / reference for reference in references]
        template_signature = self.file_signature(template_paths)
        self.templates = {}
        for state in self.states:
            if state.get("action") and (state["action"]["id"] not in ALLOWED_ACTIONS or not state.get("verified_safe")):
                raise RuntimeError("Unverified/unsafe action in catalog")
            if state.get("action"):
                permitted = self.stage["state_actions"].get(state["name"])
                if state["action"]["id"] != permitted or len(state["anchors"]) < 2:
                    raise RuntimeError("State/action mismatch or insufficient visual guards")
                if state["name"] == "IN_BATTLE" and not universal_auto_action(self.stage["id"], state["name"], state):
                    raise RuntimeError("Only verified Universal Auto OFF can authorize battle input")
                safety_state = copy.deepcopy(state)
                point_anchor = safety_state["action"].get("point_anchor")
                if (type(point_anchor) is int and 0 <= point_anchor < len(safety_state.get("anchors", []))
                        and isinstance(safety_state["anchors"][point_anchor].get("region"), list)
                        and len(safety_state["anchors"][point_anchor]["region"]) == 4):
                    x1, y1, x2, y2 = safety_state["anchors"][point_anchor]["region"]
                    dx, dy = safety_state["action"].get("point_offset", [0, 0])
                    safety_state["action"]["point"] = [(x1 + x2) // 2 + dx, (y1 + y2) // 2 + dy]
                require_pearl_safe_action(self.stage["id"], state["name"], safety_state)
            for anchor in state["anchors"]:
                alternatives = anchor.get("alternatives", [])
                if alternatives and (state["name"] != "MISSION_COMPLETE" or anchor.get("search_region") or len(alternatives) > 4):
                    raise RuntimeError("Animation variants are supported only for fixed completion text guards")
                for reference in [anchor["template"], *alternatives]:
                    path = ROOT / reference
                    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if template is None:
                        raise RuntimeError(f"Missing template: {path}")
                    if anchor.get("binarize") is not None:
                        if state.get("action"):
                            raise RuntimeError("Binarized overlay anchors may only identify non-input states")
                        template = _binarize(template, anchor["binarize"])
                    if alternatives and template.shape != (anchor['region'][3]-anchor['region'][1], anchor['region'][2]-anchor['region'][0]):
                        raise RuntimeError("Completion animation template dimensions changed")
                    self.templates[reference] = template
        if (config_signature != self.file_signature(config_paths)
                or catalog_signature != self.file_signature(catalogs)
                or template_signature != self.file_signature(template_paths)):
            raise RuntimeError("Pattern inputs changed while loading; refusing a mixed snapshot")
        self.dependencies = set(config_paths + catalogs + template_paths)
        stamps = dict(config_signature + catalog_signature + template_signature)
        self.signature = tuple((str(path), stamps[str(path)]) for path in sorted(self.dependencies))

    def classify(self, frame):
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        matched = []
        scores = {}
        for state in self.states:
            good = True
            metrics = []
            positions = {}
            for index, anchor in enumerate(state["anchors"]):
                x1, y1, x2, y2 = anchor["region"]
                template = self.templates[anchor["template"]]
                threshold = anchor.get("binarize")
                if anchor.get("search_region"):
                    sx1,sy1,sx2,sy2 = anchor["search_region"]
                    area = gray[sy1:sy2,sx1:sx2]
                    if threshold is not None:
                        area = _binarize(area, threshold)
                    result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
                    location = (0,0)
                    # Repeated words (e.g. Searching) can correlate more strongly
                    # in the wrong color. Check distinct candidates against BOTH guards.
                    for candidate in range(8):
                        _, score, _, loc = cv2.minMaxLoc(result)
                        location = loc
                        tx,ty = loc
                        patch = area[ty:ty+template.shape[0],tx:tx+template.shape[1]]
                        error = float(np.abs(patch.astype(np.int16)-template).mean())
                        if score < anchor.get("correlation",.96) or error <= anchor.get("difference",12):
                            break
                        halfw,halfh = max(1,template.shape[1]//2),max(1,template.shape[0]//2)
                        result[max(0,ty-halfh):ty+halfh+1,max(0,tx-halfw):tx+halfw+1] = -1
                    x1,y1 = sx1+location[0],sy1+location[1]
                    x2,y2 = x1+template.shape[1],y1+template.shape[0]
                crop = gray[y1:y2, x1:x2]
                if threshold is not None:
                    # Overlay text over a dimmed, changing background: compare
                    # the bright glyph mask instead of raw gray levels.
                    crop = _binarize(crop, threshold)
                positions[index] = [x1,y1,x2,y2]
                if crop.shape != template.shape:
                    good = False
                    break
                correlation = float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0, 0])
                difference = float(np.abs(crop.astype(np.int16) - template).mean())
                variants = [] if (np.isfinite(correlation) and correlation >= anchor.get("correlation", .96)
                                  and difference <= anchor.get("difference", 12)) else anchor.get("alternatives", [])
                for reference in variants:
                    variant = self.templates[reference]
                    variant_correlation = float(cv2.matchTemplate(crop, variant, cv2.TM_CCOEFF_NORMED)[0, 0])
                    variant_difference = float(np.abs(crop.astype(np.int16) - variant).mean())
                    if (np.isfinite(variant_correlation) and variant_correlation >= anchor.get("correlation", .96)
                            and variant_difference <= anchor.get("difference", 12)):
                        correlation, difference = variant_correlation, variant_difference
                        break
                metrics.append([round(correlation, 4), round(difference, 2)])
                if not np.isfinite(correlation) or correlation < anchor.get("correlation", .96) or difference > anchor.get("difference", 12):
                    good = False
                    break
            scores[state["name"]] = metrics
            if good:
                observed = copy.deepcopy(state)
                if observed.get("action", {}).get("point_anchor") is not None:
                    x1,y1,x2,y2 = positions[observed["action"]["point_anchor"]]
                    dx,dy = observed["action"].get("point_offset", [0,0])
                    observed["action"]["point"] = [(x1+x2)//2+dx,(y1+y2)//2+dy]
                matched.append(observed)
        if not matched:
            return "UNKNOWN", None, scores
        priority = max(s.get("priority", 0) for s in matched)
        best = [s for s in matched if s.get("priority", 0) == priority]
        # Shared layout variants may agree on exactly the same guarded action.
        unique = {(s['name'], json.dumps(s.get('action'), sort_keys=True)) for s in best}
        if len(unique) == 1:
            best = best[:1]
        if len(best) != 1:
            return "UNKNOWN", None, scores
        return best[0]["name"], best[0], scores


class Engine:
    def __init__(self, stage="universal", instance=None):
        self.device = Device(instance) if instance is not None else Device()
        self.target = getattr(self.device, "target", None)
        self.stage = load_stage(stage, self.target)
        self.progress = self.stage["progress"]
        session = read_json(self.path("session.json"))
        self.mission_verified = session.get("stage") == stage and session.get("mission_verified", False)
        self.detector = Detector(stage)
        self.data = read_json(self.progress, {})
        self._recovery_start_pending = None
        self._recovery_active_run = None
        self._recovery_auto_pending = None
        self._rejected_recovery_origin = None
        pending = self.data.get("pending")
        run = self.data.get("run")
        start_pending = bool(isinstance(pending, dict)
            and (pending.get("id"), pending.get("source")) == ("start_mission", "READY_TO_START")
            and pending.get("run_id") and isinstance(pending.get("time"), (int, float))
            and isinstance(pending.get("expected"), list))
        auto_pending = bool(isinstance(pending, dict)
            and (pending.get("id"), pending.get("source")) == ("enable_auto", "IN_BATTLE")
            and pending.get("expected") == UNIVERSAL_AUTO_ACTION["expected"]
            and isinstance(pending.get("time"), (int, float)))
        active_run = bool((not pending or auto_pending) and isinstance(run, dict) and run.get("id")
            and isinstance(run.get("start"), (int, float)) and run.get("outcome") is None)
        if stage == "universal" and self.data.get("stage") == stage and self.target:
            try:
                saved_target = instance_from_dict(self.data.get("instance"))
            except RuntimeError:
                saved_target = None
            if saved_target and ((saved_target.key, saved_target.android_id, saved_target.endpoint) ==
                                 (self.target.key, self.target.android_id, self.target.endpoint)):
                # Retain the original pinned evidence before save() refreshes its target.
                if start_pending:
                    self._recovery_start_pending = copy.deepcopy(pending)
                if active_run:
                    self._recovery_active_run = copy.deepcopy(run)
                    if auto_pending:
                        self._recovery_auto_pending = copy.deepcopy(pending)
        if (stage == "universal" and (start_pending or active_run)
                and not (self._recovery_start_pending or self._recovery_active_run)):
            key = "pending" if start_pending else "run"
            self._rejected_recovery_origin = dict(key=key, evidence=copy.deepcopy(self.data[key]),
                fields={name: copy.deepcopy(self.data[name]) for name in ("stage", "instance")
                        if name in self.data})
        else:
            self.data["stage"] = stage
        self.data['stamina_until'] = max([self.data.get('stamina_until', 0)] +
            [progress.get('stamina_until', 0) for path, progress in progress_records(self.target)
             if path != self.progress])
        self.data.setdefault("counts", {"started": 0, "completed": 0, "failed": 0, "launches": 0})
        self.last_unknown = 0
        self.unknown_hash = None
        self.device.connect()

    def path(self, name):
        return state_path(name, getattr(self, "target", None))

    def save(self):
        self.data["heartbeat"] = time.time()
        self.data.update(process_claim(os.getpid(), "worker_pid"))
        self.data["manager_controls_version"] = 1
        origin = getattr(self, "_rejected_recovery_origin", None)
        if origin and self.data.get(origin["key"]) == origin["evidence"]:
            # An observation must not repin rejected work for a later process.
            for name in ("stage", "instance"):
                if name in origin["fields"]:
                    self.data[name] = copy.deepcopy(origin["fields"][name])
                else:
                    self.data.pop(name, None)
        else:
            if origin:
                self.data["stage"] = self.stage["id"]
                self._rejected_recovery_origin = None
            if getattr(self, "target", None) is not None:
                self.data["instance"] = instance_dict(self.target)
        write_json(getattr(self, "progress", RUNTIME / "universal.json"), self.data)

    def _recovery_context(self):
        run = self.data.get("run") or {}
        pending = self.data.get("pending") or {}
        target = getattr(self, "target", None)
        return {
            "stage": getattr(self, "stage", {}).get("id", "universal"),
            "screen": self.data.get("screen"),
            "pending_action": pending.get("id"),
            "pending_source": pending.get("source"),
            "run_id": run.get("id"),
            "run_outcome": run.get("outcome"),
            "account": instance_account(target) if target else "acc1",
            "device_id": target.android_id if target else None,
        }

    def _note_recovery_failure(self, kind, error, *, screen=None, scores=None):
        """Persist one bounded recovery attempt without authorizing game input."""
        now = time.time()
        recovery = self.data.get("recovery")
        if (isinstance(recovery, dict) and recovery.get("kind") == kind
                and now < recovery.get("next_attempt_at", 0)):
            return max(.25, recovery["next_attempt_at"] - now), False
        if not isinstance(recovery, dict) or recovery.get("kind") != kind:
            recovery = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "started_at": now,
                "attempts": 0,
                "cycles": 0,
            }
            self.data["recovery"] = recovery
            detected = True
        else:
            detected = False
        recovery["attempts"] = int(recovery.get("attempts", 0)) + 1
        attempt = recovery["attempts"]
        position = (attempt - 1) % (len(RECOVERY_RETRY_SECONDS) + 1)
        escalated = position == len(RECOVERY_RETRY_SECONDS)
        if escalated:
            recovery["cycles"] = int(recovery.get("cycles", 0)) + 1
            delay = RECOVERY_BROAD_SECONDS
        else:
            delay = RECOVERY_RETRY_SECONDS[position]
        recovery.update(
            last_failure_at=now,
            last_error=str(error)[:500],
            last_screen=screen if screen is not None else self.data.get("screen"),
            next_attempt_at=now + delay,
            tier="broader" if escalated else "bounded_retry",
        )
        details = {
            **self._recovery_context(),
            "recovery_id": recovery["id"],
            "recovery_kind": kind,
            "attempt": attempt,
            "cycle": recovery["cycles"],
            "delay_seconds": delay,
            "error": recovery["last_error"],
        }
        event("recovery_detected" if detected else "recovery_retry_scheduled", **details)
        if scores is not None:
            event("recognition_failure", **details, scores=scores)
        if escalated:
            event("recovery_escalated", **details)
        return delay, escalated

    def _verified_recovery_state(self, name):
        """A caller invokes this only after fresh agreeing recognitions."""
        recovery = self.data.get("recovery")
        if (name == "UNKNOWN" or self.data.get("pending")
                or not getattr(self, "mission_verified", True)
                or getattr(self, "_rejected_recovery_origin", None)):
            return
        recovery = self.data.pop("recovery", None)
        if not isinstance(recovery, dict):
            return
        details = {
            **self._recovery_context(),
            "screen": name,
            "recovery_id": recovery.get("id"),
            "recovery_kind": recovery.get("kind"),
            "attempts": recovery.get("attempts", 0),
            "cycles": recovery.get("cycles", 0),
            "seconds": round(time.time() - recovery.get("started_at", time.time()), 2),
        }
        event("recovery_verified", **details)
        event("farming_resumed", **details)

    def _launch_recovery(self, kind):
        """Bring back the same identity-verified game; never click its UI."""
        if STOP.exists():
            return
        details = {**self._recovery_context(), "recovery_kind": kind,
                   "method": "verified_activity_launch"}
        event("recovery_navigation_attempt", **details)
        self.device.launch()
        event("recovery_navigation_sent", **details)

    def _transition_grace(self):
        if self.data.get("pending"):
            return TRANSITION_GRACE_SECONDS["pending"]
        run = self.data.get("run") or {}
        if run.get("id") and run.get("outcome") is None:
            return TRANSITION_GRACE_SECONDS["battle"]
        return TRANSITION_GRACE_SECONDS["idle"]

    def _unsettled(self, frame, scores, reason, name="UNKNOWN"):
        """Treat UNKNOWN/LOADING/disagreeing frames as a transition first.

        Inside the grace window: quick re-observation only (no input, no
        recovery record). Beyond it: the bounded recovery ladder.
        """
        now = time.time()
        since = getattr(self, "_unsettled_since", None)
        if since is None:
            since = self._unsettled_since = now
        if now - since < self._transition_grace():
            self.data["blocked"] = ("Game is loading" if name == "LOADING"
                                    else "Waiting for the screen transition to finish")
            self.save()
            return TRANSITION_POLL_SECONDS
        self.evidence(frame, name)
        if name == "LOADING":
            reason = "Loading screen persisted beyond the transition window"
        return self._recover_unknown(scores, reason)

    def _recover_unknown(self, scores, reason="No configured state matched two fresh screenshots"):
        delay, escalated = self._note_recovery_failure(
            "image_recognition", reason, screen="UNKNOWN", scores=scores)
        self.data["blocked"] = (
            "Recognition recovery escalated; preserving the current run and waiting for a verified state"
            if escalated else "Recognition failed; retrying with bounded backoff")
        self.save()
        # Opening the pinned activity does not reset the game or replay any UI
        # action. Keep the run and uncertain input intact until recognition
        # positively proves what the game accepted.
        if escalated:
            self._launch_recovery("image_recognition")
        return delay

    @staticmethod
    def _action_guard(action):
        fields = ("id", "kind", "expected", "input_mode", "point", "point_anchor",
                  "point_offset", "start", "end", "duration")
        return {key: copy.deepcopy(action[key]) for key in fields if key in action}

    def _perform_verified_action(self, action, name, spec):
        """Native input gets one final fresh recognition at the dispatch boundary."""
        expected = copy.deepcopy(spec)

        def verify_screen():
            if self.detector.changed():
                raise RuntimeError("Recognition inputs changed before dispatch; re-observation required")
            _, observed, current, _ = self.observe()
            if getattr(self, "stage", {}).get("id", "universal") == "universal":
                # Same state plus the identical guarded action is what
                # authorizes input; another layout variant of that state
                # (e.g. a completion-animation frame) is equivalent.
                same = (observed == name and isinstance(current, dict)
                        and current.get("verified_safe") is expected.get("verified_safe")
                        and self._action_guard(current.get("action") or {}) == self._action_guard(action))
            else:
                # Stage engines derive actions from extra spec fields; keep exact.
                same = observed == name and current == expected
            if not same:
                raise ScreenChanged("Screen changed before dispatch; no input sent")
            require_pearl_safe_action(
                getattr(self, "stage", {}).get("id", "universal"), name,
                {**current, "action": action})

        self.device.input_guard = verify_screen
        try:
            self.device.perform(action)
        finally:
            self.device.input_guard = None

    def _recover_foreground(self, foreground):
        screen = "GAME_CLOSED" if foreground == "com.uncube.launcher3" else "OTHER_FOREGROUND"
        delay, escalated = self._note_recovery_failure(
            "game_foreground", f"Expected {PACKAGE}; observed {foreground or 'no resumed activity'}",
            screen=screen)
        self.data["screen"] = screen
        self.data["blocked"] = "Game is not foreground; automatic same-device recovery is retrying"
        self.save()
        if escalated:
            self._launch_recovery("game_foreground")
        return delay

    def _retry_pending_transition(self, name, spec):
        """Retry only the same freshly verified free action, then relaunch broadly."""
        pending = self.data.get("pending")
        if not isinstance(pending, dict):
            return None
        attempted_at = pending.get("last_attempt_at", pending.get("time"))
        timeout = pending.get("timeout", TRANSITION_TIMEOUT_SECONDS)
        if (isinstance(attempted_at, bool) or not isinstance(attempted_at, (int, float))
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or timeout <= 0 or time.time() - attempted_at < timeout):
            return None
        action = copy.deepcopy((spec or {}).get("action"))
        exact_source = name == pending.get("source")
        exact_action = bool(isinstance(action, dict)
            and action.get("id") == pending.get("id")
            and action.get("expected") == pending.get("expected")
            and isinstance(pending.get("retry_guard"), dict)
            and self._action_guard(action) == pending["retry_guard"]
            and (spec or {}).get("verified_safe") is True)
        error = ("Transition timed out while the original verified source remains visible"
                 if exact_source and exact_action else
                 f"Transition timed out at {name}; original source/action cannot be safely replayed")
        delay, escalated = self._note_recovery_failure(
            "transition_timeout", error, screen=name)
        if not exact_source or not exact_action:
            self.data["blocked"] = "Transition unresolved; broader recovery is locating a verified state"
            self.save()
            if escalated:
                self._launch_recovery("transition_timeout")
            return delay
        pending["recovery_attempts"] = int(pending.get("recovery_attempts", 0)) + 1
        pending["last_recovery_at"] = time.time()
        stage_id = getattr(self, "stage", {}).get("id", "universal")
        require_pearl_safe_action(stage_id, name, spec)
        if escalated:
            pending["last_attempt_at"] = time.time()
            pending["broader_recoveries"] = int(pending.get("broader_recoveries", 0)) + 1
            self.data["blocked"] = "Transition retries exhausted; relaunching the verified game activity"
            self.save()
            self._launch_recovery("transition_timeout")
            return delay
        pending["last_attempt_at"] = time.time()
        self.data["blocked"] = "Retrying the same verified Pearl-free action after transition timeout"
        self.save()  # Persist the retry before any command that could send input.
        event("recovery_action_attempt", **self._recovery_context(),
              recovery_id=(self.data.get("recovery") or {}).get("id"),
              action=action["id"], retry=pending["recovery_attempts"], point=action.get("point"))
        try:
            self._perform_verified_action(action, name, spec)
        except ActionNotDispatched as exc:
            event("recovery_action_not_sent", **self._recovery_context(),
                  action=action["id"], error=str(exc))
            raise
        except Exception as exc:
            event("recovery_action_uncertain", **self._recovery_context(),
                  action=action["id"], error=str(exc))
            raise
        event("recovery_action_sent", **self._recovery_context(), action=action["id"])
        return 1

    def consume_stamina_request(self):
        path = self.path("stamina_request.json")
        with lock("stamina-control", wait_seconds=2):
            request = read_json(path)
            if not request:
                return
            path.unlink(missing_ok=True)
            stage = getattr(self, "stage", {}).get("id", "universal")
            if (request.get("worker_pid") != os.getpid() or request.get("stage") != stage
                    or request.get("stamina_until") != self.data.get("stamina_until")
                    or self.data.get("stamina_until", 0) <= time.time()
                    or self.data.get("pending") or STOP.exists()):
                event("stamina_wait_end_rejected", reason="Wait identity changed or input unresolved")
                return
            # Imported deadlines must not resurrect a cancelled account wait on reconnect.
            for progress_path, progress in progress_records(getattr(self, "target", None)):
                if progress_path == getattr(self, "progress", RUNTIME / "universal.json"):
                    continue
                if 0 < progress.get("stamina_until", 0) <= request["stamina_until"]:
                    progress["stamina_until"] = 0
                    write_json(progress_path, progress)
            self.data["stamina_until"] = 0
            self.data["blocked"] = "Stamina delay ended; rechecking game state"
            self.save()
            event("stamina_wait_ended", stage=stage, request_id=request["id"])

    def evidence(self, frame, name):
        dest = self.path("screenshots")
        dest.mkdir(parents=True, exist_ok=True)
        signature = hashlib.sha256(cv2.resize(frame, (45, 80)).tobytes()).hexdigest()
        if time.time() - self.last_unknown < 120 or signature == self.unknown_hash:
            return
        path = dest / f"{int(time.time())}_{name}.png"
        cv2.imwrite(str(path), frame)
        self.last_unknown = time.time()
        self.unknown_hash = signature
        self.data["evidence"] = str(path)
        files = sorted(dest.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for old in files[:-60]:
            old.unlink()

    def observe(self):
        frame = self.device.capture()
        name, spec, scores = self.detector.classify(frame)
        return frame, name, spec, scores

    def update(self, frame, name, spec):
        completed_event = None
        previous_run = self.data.get("run")
        previous = self.data.get("screen")
        if name != previous:
            event("screen", previous=previous, screen=name, stage=getattr(self, "stage", {}).get("id", "universal"))
        self.data["screen"] = name
        auto_enabled = (spec or {}).get("battle_auto_enabled") if name == "IN_BATTLE" else None
        if isinstance(auto_enabled, bool):
            self.data["battle_auto_enabled"] = auto_enabled
        else:
            self.data.pop("battle_auto_enabled", None)
        pending = self.data.get("pending")
        stage_id = getattr(self, "stage", {}).get("id")
        session = read_json(self.path("session.json")) if pending and pending["id"] == "retry_connection" else {}
        resumed_from_connection = bool(
            pending and (pending["id"], pending["source"]) == ("retry_connection", "COMMUNICATION_ERROR")
            and stage_id in STAGE_IDS and getattr(self, "mission_verified", False)
            and session.get("stage") == stage_id and session.get("mission_verified", False)
            and name in {"IN_BATTLE", "MISSION_COMPLETE"}
        )
        resumed_from_entry = bool(pending and
                                 (pending["id"], pending["source"]) in {
                                     ("title_start", "TITLE_START"), ("retry", "READY_TO_RETRY")
                                 } and name in {"IN_BATTLE", "MISSION_COMPLETE"}) or resumed_from_connection
        advanced_to_rewards = bool(pending and pending["id"] == "advance_completion" and
                                   pending["source"] == "MISSION_COMPLETE" and name == "REWARD_REVEAL")
        maintenance_rejection = bool(pending and name == "MAINTENANCE_PROMPT" and
            (pending["id"], pending["source"]) in {
                ("start_mission", "READY_TO_START"), ("retry", "READY_TO_RETRY")})
        recovery = bool(spec and spec.get("recovery") and name != "MAINTENANCE_PROMPT"
                        and pending and pending["source"] != "MAINTENANCE_PROMPT")
        expected_transition = bool(pending and name in pending["expected"] and name != "MAINTENANCE_PROMPT")
        auto_confirmed = bool(stage_id == "universal" and pending
            and (pending["id"], pending["source"]) == ("enable_auto", "IN_BATTLE")
            and name == "IN_BATTLE" and auto_enabled is True and expected_transition)
        if pending and (name != pending["source"] or auto_confirmed) and (expected_transition or recovery or resumed_from_entry or advanced_to_rewards or maintenance_rejection):
            event("action_rejected" if maintenance_rejection else "transition_confirmed" if expected_transition else "recovery_transition", action=pending["id"], source=pending["source"], target=name,
                  seconds=round(time.time() - pending["time"], 2))
            self.data["pending"] = None
            if maintenance_rejection:
                self.data["maintenance_rejection"] = {**pending, "rejected_at": time.time()}
            if pending["id"] == "dismiss_known_popup" and pending["source"] == "MAINTENANCE_PROMPT":
                self.data["maintenance_until"] = max(self.data.get("maintenance_until", 0),
                                                       time.time() + MAINTENANCE_RETRY_SECONDS)
            if pending["id"] == "dismiss_stamina":
                self.data["stamina_until"] = max(self.data.get("stamina_until", 0), time.time() + pending.get("stamina_wait_seconds", stamina_wait_minutes() * 60))
            if pending["id"] == "start_mission" and name in {"IN_BATTLE", "MISSION_COMPLETE"}:
                self.data["counts"]["started"] += 1
                self.data["run"] = {"id": pending["run_id"], "start": pending["time"], "outcome": None}
            elif resumed_from_entry and (not self.data.get("run") or self.data["run"].get("outcome")):
                # The game resumed a mission beyond the menu flow. Track its
                # remaining observed portion so loss monitoring stays active.
                self.data["counts"]["recovered_missions"] = self.data["counts"].get("recovered_missions", 0) + 1
                self.data["run"] = {"id": uuid.uuid4().hex, "start": time.time(), "outcome": None,
                                    "origin": {"title_start": "observed_after_title", "retry": "observed_after_retry",
                                               "retry_connection": "observed_after_connection"}[pending["id"]],
                                    "duration_basis": "since_first_observation"}
        run = self.data.get("run")
        if name == "MISSION_COMPLETE" and run and not run.get("outcome"):
            run["outcome"] = "completed"
            self.data["counts"]["completed"] += 1
            target = getattr(self, "target", None)
            run["recording"] = dict(version=1, completed_at=time.time(),
                account=instance_account(target) if target else "acc1",
                device_id=target.android_id if target else None,
                stage=getattr(self, "stage", {}).get("id", "universal"),
                state="completion_registration_pending", registration_attempts=0, capture_attempts=0,
                next_attempt_at=0, error=None)
            completed_event = dict(stage=getattr(self, "stage", {}).get("id", "universal"),
                  run_id=run["id"], seconds=round(time.time()-run["start"], 2),
                  origin=run.get("origin", "runner_started"),
                  duration_basis=run.get("duration_basis", "mission_entry"))
        if name in {"REVIVAL_PROMPT", "MISSION_FAILED"} and run and not run.get("outcome"):
            run["outcome"] = "failed"
            self.data["counts"]["failed"] += 1
            event("mission_failed", run_id=run["id"])
        if self.data.get("pending"):
            self.data["blocked"] = ("Server maintenance; awaiting notice dismissal"
                if self.data["pending"]["source"] == "MAINTENANCE_PROMPT"
                else "Waiting for Auto-play to turn on" if self.data["pending"]["id"] == "enable_auto"
                else "Awaiting confirmed transition; no further click permitted")
        elif self.data.get("maintenance_until", 0) > time.time() and name in {"READY_TO_START", "READY_TO_RETRY"}:
            self.data["blocked"] = "Waiting before retry after server maintenance"
        elif name == "IN_BATTLE" and auto_enabled is False:
            self.data["blocked"] = "Enabling Auto-play"
        elif self.data.get("stamina_until", 0) > time.time():
            self.data["blocked"] = "Waiting for natural stamina regeneration"
        elif name in UNSETTLED_SCREENS:
            self.data["blocked"] = "Unknown screen; observing without input"
        else:
            self.data["blocked"] = None
        if run and run.get("recording"):
            self._save_recording()
        else:
            self.save()
        if completed_event:
            event("mission_completed", **completed_event)
        self._loot_observe(frame, name, previous_run, spec)

    def _save_recording(self):
        try:
            self.save()
        except (OSError, ValueError) as exc:
            raise RecordingPersistenceError("Cannot persist loot transaction; check runtime disk space and permissions: "
                                            + str(exc)) from exc

    def _recording_matches_target(self, recording):
        target = getattr(self, "target", None)
        return (recording.get("version") == 1
                and recording.get("stage") == getattr(self, "stage", {}).get("id", "universal")
                and recording.get("account") == (instance_account(target) if target else "acc1")
                and recording.get("device_id") == (target.android_id if target else None))

    def _recording_failure(self, recording, phase, message):
        health = _loot_call(getattr(self, "loot_collector", None), "health")
        if isinstance(health, dict):
            self.data["recording_health"] = health
            failure = (health.get("operation_errors") or {}).get(recording.get("completion_id"))
            if isinstance(failure, dict) and failure.get("operation", failure.get("phase")) in {phase, "evidence" if phase == "capture" else "completed"}:
                detail = failure.get("error", failure.get("message"))
                if isinstance(detail, str) and detail:
                    message = detail
        key = phase + "_attempts"
        recording[key] = recording.get(key, 0) + 1
        attempts = recording[key]
        delay = min(LOOT_RETRY_MAX_SECONDS,
                    LOOT_RETRY_BASE_SECONDS * 2 ** min(attempts - 1, 10))
        escalated = attempts >= LOOT_MAX_ATTEMPTS
        recording.update(state="retryable_failure", retry_phase=phase, error=message,
                         recovery_tier="broader" if escalated else "bounded_retry",
                         next_attempt_at=time.time() + delay)
        self.data["blocked"] = message + "; automatic recording recovery is retrying safely."
        _loot_tracking_error(RuntimeError(message))
        self._save_recording()
        event("recording_recovery_retry", **self._recovery_context(),
              completion_id=recording.get("completion_id"), phase=phase,
              attempt=attempts, delay_seconds=delay, error=message)
        if escalated and attempts % LOOT_MAX_ATTEMPTS == 0:
            event("recording_recovery_escalated", **self._recovery_context(),
                  completion_id=recording.get("completion_id"), phase=phase,
                  attempt=attempts, method="collector_health_refresh_and_retry")

    def _revoke_departed_recording(self, name):
        """An agreed new entry/battle cannot supply the old completion's loot."""
        run = self.data.get("run") or {}
        recording = run.get("recording")
        if (self.stage.get("id") != "universal" or name not in {"READY_TO_START", "IN_BATTLE"}
                or run.get("outcome") != "completed" or not isinstance(recording, dict)
                or not self._recording_matches_target(recording)
                or recording.get("state") in {"screenshot_saved", "missing_screenshot"}
                or recording.get("capture_revoked")):
            return
        recording.update(capture_revoked=True, capture_revoked_at=time.time(),
                         capture_revoked_screen=name,
                         capture_revoked_reason="Completed run's rewards were left before evidence was saved")
        self._save_recording()
        event("loot_capture_revoked", run_id=run["id"], completion_id=recording.get("completion_id"),
              screen=name, reason=recording["capture_revoked_reason"])

    def _recover_departed_recording(self, name):
        """Durably close genuinely unavailable evidence and keep farming.

        The caller has already required two agreeing observations of a known
        post-results state. No UI input is sent here. The collector's exact
        run/completion/account/device checks remain authoritative.
        """
        self._recording_recovery_delay = 1
        run = self.data.get("run") or {}
        recording = run.get("recording")
        if (self.stage.get("id") != "universal" or name not in {"READY_TO_START", "IN_BATTLE"}
                or run.get("outcome") != "completed" or not isinstance(recording, dict)
                or (not recording.get("capture_revoked")
                    and recording.get("state") != "missing_screenshot")
                or recording.get("state") == "screenshot_saved"
                or (recording.get("state") == "missing_screenshot"
                    and recording.get("missing_evidence_acknowledged") is True)):
            return False
        if not self._recording_matches_target(recording):
            self.data["blocked"] = "Loot recovery target mismatch; stopping is required to protect the selected account"
            event("recording_recovery_safety_stop", **self._recovery_context(),
                  reason="recording_account_or_device_mismatch")
            STOP.write_text("Safety stop: loot recording account/device mismatch", encoding="utf-8")
            self._save_recording()
            return False
        completion_id = recording.get("completion_id")
        collector = getattr(self, "loot_collector", None)
        if not completion_id or collector is None:
            return False  # Registration/collector recovery continues first.
        now = time.time()
        if now < recording.get("missing_recovery_next_at", 0):
            self._recording_recovery_delay = max(
                1, min(LOOT_RETRY_MAX_SECONDS, recording["missing_recovery_next_at"] - now))
            return False
        reason = (f"Automatic recovery after two verified {name} observations proved that completion "
                  f"{completion_id} rewards were no longer capturable; retain the completion with zero reward credits")
        attempt = int(recording.get("missing_recovery_attempts", 0)) + 1
        event("recording_recovery_attempt", **self._recovery_context(),
              completion_id=completion_id, attempt=attempt, method="verified_missing_evidence")
        retained = _loot_call(collector, "recover_evidence", run["id"],
                              completion_id=completion_id) is True
        if not retained and _loot_call(collector, "missing", run["id"], reason=reason,
                                       completion_id=completion_id) is not True:
            delay = min(LOOT_RETRY_MAX_SECONDS,
                        LOOT_RETRY_BASE_SECONDS * 2 ** min(attempt - 1, 10))
            recording.update(state="retryable_failure", retry_phase="missing_resolution",
                             missing_recovery_attempts=attempt,
                             missing_recovery_next_at=now + delay,
                             error="Automatic missing-evidence acknowledgement failed")
            self.data["blocked"] = "Automatic loot recovery failed; retrying with backoff"
            self._recording_recovery_delay = delay
            self._save_recording()
            event("recording_recovery_failed", **self._recovery_context(),
                  completion_id=completion_id, attempt=attempt, delay_seconds=delay,
                  error=recording["error"])
            return False
        original = copy.deepcopy(run)
        prior_error = recording.get("error")
        resolved_at = time.time()
        if retained:
            reason = (f"Automatic recovery verified retained evidence for completion {completion_id}; "
                      "the original screenshot and reward accounting remain unchanged")
        recording.update(state="screenshot_saved" if retained else "missing_screenshot",
                         missing_evidence_acknowledged=not retained,
                         resolution_mode="automatic_retained_evidence" if retained else "automatic_verified_departure",
                         resolution_reason=reason, resolved_at=resolved_at,
                         next_attempt_at=0, error=None)
        recording.pop("missing_recovery_next_at", None)
        pending = self.data.get("pending")
        results_latch = bool(isinstance(pending, dict)
            and (pending.get("id"), pending.get("source")) in {
                ("advance_completion", "MISSION_COMPLETE"),
                ("next", "RESULTS_NEXT"), ("next", "TRIAL_COINS_NEXT"),
                ("next", "RANK_UP_RESULTS"), ("reveal_rewards", "REWARD_REVEAL")})
        audit = {
            "resolved_at": resolved_at,
            "screen": name,
            "run_id": run.get("id"),
            "completion_id": completion_id,
            "account": recording.get("account"),
            "device_id": recording.get("device_id"),
            "reason": reason,
            "previous_error": prior_error,
            "original_pending": copy.deepcopy(pending) if results_latch else None,
            "original_run": original,
        }
        self.data["last_recording_recovery"] = audit
        if results_latch:
            self.data["pending"] = None
        if name == "IN_BATTLE" and not self.data.get("pending"):
            self.data["counts"]["recovered_missions"] = (
                self.data["counts"].get("recovered_missions", 0) + 1)
            self.data["run"] = {
                "id": uuid.uuid4().hex,
                "start": resolved_at,
                "outcome": None,
                "origin": "observed_after_recording_recovery",
                "duration_basis": "since_first_observation",
            }
            write_json(self.path("session.json"), {"stage": self.stage["id"], "mission_verified": True})
            self.mission_verified = True
        self.data["blocked"] = None
        self.data.pop("recording_health", None)
        self._save_recording()
        event("recording_recovery_verified",
              **{**self._recovery_context(), "screen": name,
                 "completion_id": completion_id, "attempt": attempt})
        event("farming_resumed", **self._recovery_context(),
              recovery_kind="missing_loot_evidence", completion_id=completion_id)
        return True

    def _loot_input_blocked(self, name, action):
        run = self.data.get("run") or {}
        recording = run.get("recording")
        health = self.data.get("recording_health") or {}
        if (not recording and run.get("outcome") == "completed"
                and health.get("status") == "collector_initialization_retry"):
            self.data["blocked"] = "Waiting for collector recovery to verify outstanding saved loot; automatic mission progression paused"
            return True
        if not recording or not hasattr(self, "loot_collector"):
            return False
        if not self._recording_matches_target(recording):
            self.data["blocked"] = "Loot recording account/device does not match; safety stop prevents wrong-account recovery"
            return True
        if recording.get("state") == "missing_screenshot":
            if recording.get("missing_evidence_acknowledged") is True:
                return False
            self.data["blocked"] = "Waiting for durable automatic missing-evidence acknowledgement"
            return True
        if recording.get("state") == "screenshot_saved":
            return False
        if recording.get("capture_revoked"):
            self.data["blocked"] = "Rewards are no longer visible; automatic verified missing-evidence recovery is retrying"
            return True
        if name == "READY_TO_START" and recording.get("completion_id"):
            self.data["blocked"] = ("Rewards are no longer visible; retaining the completion through "
                                    "automatic verified recovery")
            return True
        if not recording.get("completion_id") or action in {"retry", "start_mission", "title_start"}:
            self.data["blocked"] = (recording.get("error") or
                ("Waiting for durable completion registration" if not recording.get("completion_id") else
                 "Waiting for verified loot screenshot persistence")) + "; automatic mission progression paused"
            return True
        return False

    def capture_stopped_recording(self):
        """Capture visible final rewards before the existing collector closes.

        Stop can arrive immediately after revealing rewards. Preserve that final
        frame when it is still visible without running another automation tick.
        Allow a brief reveal animation to settle within one four-second budget.
        Read commands share that deadline; never launch, reconnect or send input.
        """
        original = None
        recording = None
        missing_health = object()
        original_health = missing_health
        try:
            run = self.data.get("run") or {}
            recording = run.get("recording")
            collector = getattr(self, "loot_collector", None)
            if isinstance(collector, RecoveringLootCollector):
                # Shutdown must not initialize SQLite, start a new session or
                # retry collector startup merely to attempt this final read.
                collector = collector.collector
            target = getattr(self, "target", None)
            device_target = getattr(self.device, "target", None)
            if (self.stage.get("id") != "universal" or run.get("outcome") != "completed"
                    or not run.get("id") or not isinstance(recording, dict)
                    or not self._recording_matches_target(recording)
                    or not recording.get("completion_id") or collector is None
                    or recording.get("capture_revoked")
                    or recording.get("state") in {"screenshot_saved", "missing_screenshot"}
                    or target is None or device_target is None or not target.config_path
                    or (target.key, target.android_id, target.endpoint) !=
                       (device_target.key, device_target.android_id, device_target.endpoint)):
                return False
            deadline = time.monotonic() + 4
            ready_observations = 0
            pending = self.data.get("pending")
            awaiting_result_transition = bool(isinstance(pending, dict) and
                (pending.get("id"), pending.get("source")) in {
                    ("advance_completion", "MISSION_COMPLETE"),
                    ("reveal_rewards", "REWARD_REVEAL"),
                    ("next", "RESULTS_NEXT"), ("next", "TRIAL_COINS_NEXT"),
                    ("next", "RANK_UP_RESULTS")})
            def remaining_timeout():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Stopped loot capture deadline reached')
                return min(1, remaining)
            while True:
                self.device.verify(timeout=remaining_timeout())
                if self.device.foreground(timeout=remaining_timeout()) != PACKAGE:
                    return False
                frame = self.device.capture(timeout=remaining_timeout())
                name, _, _ = self.detector.classify(frame)
                remaining_timeout()
                if (name not in {"READY_TO_RETRY", "REWARD_REVEAL", "LUCKY_REWARD"}
                        and not (name == "UNKNOWN" and awaiting_result_transition)):
                    return False
                from item_recognition.reward_layout import has_unrevealed_rewards
                ready = (name == "READY_TO_RETRY" and
                         not has_unrevealed_rewards(frame, ROOT / 'assets/item_recognition'))
                ready_observations = ready_observations + 1 if ready else 0
                if ready_observations == 2:
                    if self.device.foreground(timeout=remaining_timeout()) != PACKAGE:
                        return False
                    remaining_timeout()
                    break
                time.sleep(min(.25, remaining_timeout()))
            if _loot_call(collector, "evidence", run["id"], frame,
                          completion_id=recording["completion_id"]) is not True:
                return False
            original = copy.deepcopy(recording)
            recording.update(state="screenshot_saved", next_attempt_at=0, error=None)
            original_health = self.data.pop("recording_health", missing_health)
            self._save_recording()
            self._loot_evidence_run = run["id"]
            return True
        except Exception as exc:
            if original is not None:
                recording.clear()
                recording.update(original)
                if original_health is missing_health:
                    self.data.pop("recording_health", None)
                else:
                    self.data["recording_health"] = original_health
            _loot_tracking_error(exc)
            return False

    def _loot_observe(self, frame, name, previous_run=None, spec=None):
        """Reuse the existing results observation; never capture or control input."""
        self._loot_waiting_for_reveal = False
        collector = getattr(self, "loot_collector", None)
        if collector is None:
            return
        health = _loot_call(collector, "health")
        if isinstance(health, dict):
            self.data["recording_health"] = health
        run = self.data.get("run")
        recording = run.get("recording") if run else None
        if run and run.get("outcome") == "completed" and not recording:
            # Upgrade only an actual durable outbox job matching saved progress.
            # An old counter or an unrelated results screen is insufficient.
            pending = _loot_call(collector, "resume_pending")
            target = getattr(self, "target", None)
            account = instance_account(target) if target else "acc1"
            device_id = target.android_id if target else None
            candidate = next((job for job in pending if isinstance(job, dict)
                and str(job.get("run_id")) == str(run["id"])
                and job.get("account") == account and job.get("stage") == self.stage["id"]
                and job.get("device_id") in (None, "", device_id)), None) if isinstance(pending, list) else None
            if candidate:
                recording = dict(version=1, account=account, device_id=device_id, stage=self.stage["id"],
                    completed_at=candidate["completed_at"], completion_id=candidate["completion_id"],
                    state="screenshot_capture_pending", registration_attempts=0, capture_attempts=0,
                    next_attempt_at=0, error=None)
                run["recording"] = recording
                self._save_recording()
        if recording:
            if not self._recording_matches_target(recording):
                self.data["blocked"] = "Loot recording account/device does not match; automatic recovery refused"
                return
            if recording.get("state") in {"screenshot_saved", "missing_screenshot"}:
                return
            if time.time() < recording.get("next_attempt_at", 0):
                return
            if not recording.get("completion_id"):
                kwargs = {"completed_at": recording["completed_at"]}
                cid = _loot_call(collector, "completed", run["id"], **kwargs)
                if not cid:
                    self._recording_failure(recording, "registration", "Completion registration failed")
                    return
                # Persist the collector's durable ACK before results navigation.
                recording.update(completion_id=str(cid), state="screenshot_capture_pending",
                                 next_attempt_at=0, error=None)
                self._save_recording()
            if recording.get("capture_revoked"):
                return
        if (name == "READY_TO_RETRY" and run and run.get("outcome") == "completed"
                and getattr(self, "_loot_evidence_run", None) != run["id"]):
            # Old progress without a transaction cannot register an invented completion;
            # evidence() only accepts an existing durable job.
            from item_recognition.reward_layout import has_unrevealed_rewards
            if has_unrevealed_rewards(frame, ROOT / 'assets/item_recognition'):
                self._loot_waiting_for_reveal = True
                return
            evidence_kwargs = {"completion_id": recording["completion_id"]} if recording else {}
            if _loot_call(collector, "evidence", run["id"], frame, **evidence_kwargs):
                self._loot_evidence_run = run["id"]
                if recording:
                    recording.update(state="screenshot_saved", next_attempt_at=0, error=None)
                    self._save_recording()
            elif recording:
                self._recording_failure(recording, "capture", "Loot screenshot persistence failed")

    def halt_loss(self, frame, name, text=""):
        # No verified Pearl-free exit is cataloged for this screen. Stopping is
        # the final safety method, not a normal recovery or a request to spend.
        STOP.write_text("Safety stop: no verified Pearl-free loss/revival exit is available", encoding="utf-8")
        path = self.path(f"loss_{int(time.time())}.png")
        cv2.imwrite(str(path), frame)
        run = self.data.get("run")
        if run and not run.get("outcome"):
            run["outcome"] = "failed"
            self.data["counts"]["failed"] += 1
        self.data["screen"] = name
        self.data["blocked"] = "Safety stop: refusing every unverified Pearl, continue, or revive control"
        self.data["halt_reason"] = "loss"
        self.data["evidence"] = str(path)
        record = {"time": time.time(), "screen": name, "evidence": str(path), "text": text,
                  "run_id": run.get("id") if run else None}
        write_json(self.path("loss.json"), record)
        self.save()
        event("loss_stop", **record)
        try:
            notify_loss()
            event("loss_notification_launched")
        except Exception as exc:
            event("loss_notification_error", error=str(exc))
        return 0

    def tick(self, act=True):
        if STOP.exists() and act:
            return 0
        now = time.time()
        if act:
            self.consume_stamina_request()
            # Input itself always re-verifies identity (Device._perform); an
            # observation-only tick can reuse a recent verification.
            if now - getattr(self, "_identity_checked_at", 0) >= IDENTITY_RECHECK_SECONDS:
                self.device.verify()
                self._identity_checked_at = now
        if self.detector.changed():
            self.detector = Detector(getattr(self, "stage", {}).get("id", "universal"))
            event("patterns_reloaded")
        # A settled, recognized game screen proves the game is displayed; skip
        # the comparatively slow activity dump unless it is due or in doubt.
        if (self.data.get("screen") in UNSETTLED_SCREENS or self.data.get("screen") is None
                or now - getattr(self, "_foreground_checked_at", 0) >= FOREGROUND_RECHECK_SECONDS):
            foreground = self.device.foreground()
            self._foreground_checked_at = now if foreground == PACKAGE else 0
        else:
            foreground = PACKAGE
        if foreground != PACKAGE:
            if act:
                return self._recover_foreground(foreground)
            self.data["screen"] = "GAME_CLOSED" if foreground == "com.uncube.launcher3" else "OTHER_FOREGROUND"
            self.data["blocked"] = "Game is not foreground; observation only"
            self.save()
            return 15
        self.data["launch_pending"] = None
        frame, name, spec, scores = self.observe()
        # A second fresh agreeing observation is mandatory before resolving or sending input.
        if name != "UNKNOWN":
            time.sleep(.25)
            frame2, name2, spec2, scores2 = self.observe()
            if (name2 != name or (name == "IN_BATTLE"
                    and (spec or {}).get("battle_auto_enabled") != (spec2 or {}).get("battle_auto_enabled"))):
                self.update(frame2, "UNKNOWN", None)
                return self._unsettled(frame2, scores2, "Fresh recognition observations disagreed") if act else 1
            if spec and spec.get("action", {}).get("point_anchor") is not None:
                # Seeing the same banner twice is insufficient while the list is
                # still coasting. Require stationary geometry across three captures.
                point1 = spec["action"]["point"]
                point2 = spec2["action"]["point"]
                frame3,name3,spec3,scores3 = self.observe()
                if name3 != name or any(abs(a-b)>1 for a,b in zip(point1,point2)) or any(abs(a-b)>1 for a,b in zip(point2,spec3["action"]["point"])):
                    self.update(frame3,"UNKNOWN",None)
                    self.data["blocked"] = "Waiting for mission-list motion to settle"
                    self.save()
                    return self._unsettled(frame3, scores3, "Guarded action geometry did not settle") if act else 1
                frame2,spec2 = frame3,spec3
            frame, spec, scores = frame2, spec2, scores2
        if name not in UNSETTLED_SCREENS:
            self._unsettled_since = None
        if act and name in {"REVIVAL_PROMPT", "MISSION_FAILED"}:
            return self.halt_loss(frame, name)
        if hasattr(self, "stage"):
            self._revoke_departed_recording(name)
            if act:
                self._recover_departed_recording(name)
                if STOP.exists():
                    return 0
            recording = (self.data.get("run") or {}).get("recording")
            recovering_results = bool(recording and self._recording_matches_target(recording)
                and not recording.get("capture_revoked") and recording.get("state") != "missing_screenshot"
                and name in {"MISSION_COMPLETE", "RESULTS_NEXT", "TRIAL_COINS_NEXT", "REWARD_REVEAL", "READY_TO_RETRY"})
            pending = self.data.get("pending")
            recovering_start = bool(pending and pending == getattr(self, "_recovery_start_pending", None)
                and name in {"IN_BATTLE", "MISSION_COMPLETE", "STAMINA_PROMPT"}
                and name in pending["expected"])
            recovering_run = bool((not pending or pending == getattr(self, "_recovery_auto_pending", None))
                and (self.data.get("run") or {})
                and self.data["run"] == getattr(self, "_recovery_active_run", None)
                and name in {"IN_BATTLE", "MISSION_COMPLETE"})
            recovering_maintenance = bool(name == "MAINTENANCE_PROMPT" and pending and
                (pending["id"], pending["source"]) in {
                    ("start_mission", "READY_TO_START"), ("retry", "READY_TO_RETRY"),
                    ("dismiss_known_popup", "MAINTENANCE_PROMPT")})
            maintenance_return = bool(pending and
                (pending["id"], pending["source"]) == ("dismiss_known_popup", "MAINTENANCE_PROMPT")
                and name in {"READY_TO_START", "READY_TO_RETRY"} and name in pending["expected"])
            if (recovering_results or recovering_start or recovering_run) and not self.mission_verified:
                # A pinned start, active run or recording survives a restart;
                # fresh guarded observations resolve it without repeating its input.
                write_json(self.path("session.json"), {"stage": self.stage["id"], "mission_verified": True})
                self.mission_verified = True
            if (not self.mission_verified and name != "READY_TO_START"
                    and not recovering_results and not recovering_maintenance):
                # Recovery must preserve the pinned mission/account evidence;
                # a configured screen alone cannot authorize another mission.
                self.update(frame, name if maintenance_return else "UNKNOWN",
                            spec if maintenance_return else None)
                self.data["blocked"] = "Unverified mission state; automatic recovery is waiting for a pinned known state"
                self.save()
                event("mission_state_unverified", stage=self.stage["id"], observed=name,
                      pending_action=(pending or {}).get("id"))
                if act and name == "UNKNOWN":
                    return self._unsettled(frame, scores, "No configured state matched two fresh screenshots")
                return 10
            if name == "READY_TO_START":
                self.mission_verified = True
                write_json(self.path("session.json"), {"stage": self.stage["id"], "mission_verified": True})
        run = self.data.get("run")
        if (act and name == "UNKNOWN" and run and not run.get("outcome")
                and now - getattr(self, "_loss_ocr_at", 0) >= LOSS_OCR_INTERVAL_SECONDS):
            # A loss/revival prompt waits for the player, so a throttled check
            # still catches it; no input is ever sent on UNKNOWN meanwhile.
            self._loss_ocr_at = now
            try:
                text = ocr_text(frame)
                if loss_text(text):
                    # Text is used exclusively to stop. Two fresh agreeing reads
                    # prevent a transient animation from being treated as a loss.
                    time.sleep(.5)
                    fresh = self.device.capture()
                    confirmation = ocr_text(fresh)
                    if loss_text(confirmation) and self.device.foreground() == PACKAGE:
                        return self.halt_loss(fresh, "LOSS_DETECTED", confirmation)
            except Exception as exc:
                event("loss_ocr_error", error=str(exc))
        self.update(frame, name, spec)
        if name in UNSETTLED_SCREENS:
            if not act:
                return 5
            return self._unsettled(frame, scores, "No configured state matched two fresh screenshots", name)
        if getattr(self, '_loot_waiting_for_reveal', False):
            self.data['blocked'] = 'Waiting for rewards to finish revealing'
            self.save()
            return 1
        action_id = (spec or {}).get("action", {}).get("id")
        if self._loot_input_blocked(name, action_id):
            self._save_recording()
            return getattr(self, "_recording_recovery_delay", 1)
        if act:
            self._verified_recovery_state(name)
        transition_delay = self._retry_pending_transition(name, spec) if act else None
        if transition_delay is not None:
            return transition_delay
        pending = self.data.get("pending")
        # Preserve the user's current session. A stalled input remains latched;
        # recovery must never force-stop or reset the application.
        observed_run = self.data.get("run")
        polling_battle = name == "IN_BATTLE" or bool(
            name == "UNKNOWN" and observed_run and not observed_run.get("outcome")
            and getattr(self, "mission_verified", True)
        )
        if not act or self.data.get("pending"):
            return 2 if polling_battle else 5
        if self.data.get("maintenance_until", 0) > time.time() and action_id in {"start_mission", "retry"}:
            return 30
        dismissing_stamina = bool(name == "STAMINA_PROMPT" and spec
                                 and spec.get("action", {}).get("id") == "dismiss_stamina")
        if self.data.get("stamina_until", 0) > time.time() and not dismissing_stamina:
            return 30
        if spec and spec.get("action"):
            action = spec["action"]
            # Auto OFF is the sole Universal battle control; all other battle input stays prohibited.
            if name == "IN_BATTLE" and not universal_auto_action(
                    getattr(self, "stage", {}).get("id"), name, spec):
                raise RuntimeError("Battle input prohibited")
            require_pearl_safe_action(getattr(self, "stage", {}).get("id", "universal"), name, spec)
            self.data["pending"] = {"id": action["id"], "source": name, "expected": action["expected"],
                                    "time": time.time(), "run_id": uuid.uuid4().hex,
                                    "retry_guard": self._action_guard(action)}
            previous_stamina_until = self.data.get("stamina_until")
            if action["id"] == "dismiss_stamina":
                self.data["pending"]["stamina_wait_seconds"] = stamina_wait_minutes() * 60
                self.data["stamina_until"] = max(self.data.get("stamina_until", 0), time.time() + self.data["pending"]["stamina_wait_seconds"])
            self.save()  # Persist BEFORE input so interruption cannot replay a click.
            event("action_attempt", stage=getattr(self, "stage", {}).get("id", "universal"),
                  screen=name, action=action["id"], point=action.get("point"))
            try:
                self._perform_verified_action(action, name, spec)
            except ActionNotDispatched as exc:
                self.data["pending"] = None
                if action["id"] == "dismiss_stamina":
                    if previous_stamina_until is None:
                        self.data.pop("stamina_until", None)
                    else:
                        self.data["stamina_until"] = previous_stamina_until
                deferrals = getattr(self, "_dispatch_deferrals", 0) + 1
                if isinstance(exc.__cause__, ScreenChanged) and deferrals <= DISPATCH_DEFER_LIMIT:
                    # Animation between recognition and dispatch: no input was
                    # sent, so simply look again shortly.
                    self._dispatch_deferrals = deferrals
                    self.data["blocked"] = "Screen still animating; re-checking before input"
                    self.save()
                    event("action_deferred", action=action["id"], screen=name, attempt=deferrals,
                          stage=getattr(self, "stage", {}).get("id", "universal"))
                    return DISPATCH_DEFER_SECONDS
                self._dispatch_deferrals = 0
                self.save()
                event("action_not_sent", action=action["id"], error=str(exc))
                raise
            except Exception as exc:
                event("action_uncertain", action=action["id"], error=str(exc))
                raise
            self._dispatch_deferrals = 0
            event("action_sent", action=action["id"], stage=getattr(self, "stage", {}).get("id", "universal"))
            return 1
        return 2 if polling_battle else 30 if name == "UNKNOWN" else 2


def process_target(instance=None, supervisor_pid=None):
    state = read_json(RUNTIME / "supervisor.json")
    if supervisor_pid and state.get("pid") == supervisor_pid:
        pinned = instance_from_dict(state["instance"]) if state.get("instance") else legacy_instance()
        if instance is not None and instance_dict(instance) != instance_dict(pinned):
            raise RuntimeError("Worker target does not match its supervisor; refusing input")
        instance = pinned
    instance = instance or selected_instance()
    validate_mapping(instance)
    return instance


def target_arguments(instance):
    return ["--target", json.dumps(instance_dict(instance), separators=(",", ":"))]


def worker(stage="universal", supervisor_pid=None, instance=None):
    # Hold one account-wide input lock for the entire controller lifetime.
    with contextlib.ExitStack() as stack:
        with lock("control", wait_seconds=10):
            state = read_json(RUNTIME / "supervisor.json")
            if lock_busy("supervisor") and (not supervisor_pid or state.get("pid") != supervisor_pid or state.get("stage", "universal") != stage):
                raise RuntimeError("An account supervisor is active; direct worker refused")
            if live_controller_pids(exclude=controller_exclusions(stage, supervisor_pid)):
                raise RuntimeError("An existing account controller is still alive")
            instance = process_target(instance, supervisor_pid)
            stack.enter_context(lock("worker"))
            write_json(RUNTIME / "controller.json", {**process_claim(os.getpid(), "worker_pid"), "stage": stage,
                                                   "instance": instance_dict(instance)})
            session = read_json(state_path("session.json", instance))
            if session.get("stage") != stage:
                write_json(state_path("session.json", instance), {"stage": stage, "mission_verified": False})
        collector = _loot_start(stage, supervisor_pid, instance)
        stack.callback(_loot_call, collector, "close", end_session=not bool(supervisor_pid))
        engine = None
        stack.callback(lambda: engine.capture_stopped_recording()
                       if stage == "universal" and engine is not None else None)
        backoff = 5
        failure_attempt = 0
        while not STOP.exists():
            try:
                # Collector startup retries continue even while device recovery
                # prevents the engine from producing an observation.
                health = _loot_call(collector, "health")
                if engine is None:
                    engine = create_engine(stage, instance)
                    engine.loot_collector = collector
                if isinstance(health, dict):
                    engine.data["recording_health"] = health
                delay = engine.tick()
                backoff = 5
                failure_attempt = 0
            except RecordingPersistenceError as exc:
                # Preserve the observed completion/time while its state is retried.
                _loot_tracking_error(exc)
                if engine:
                    engine.data["blocked"] = str(exc)
                delay = min(backoff, LOOT_RETRY_MAX_SECONDS)
                backoff = min(backoff * 2, LOOT_RETRY_MAX_SECONDS)
                failure_attempt += 1
                event("worker_recovery_retry", recovery_kind="recording_persistence",
                      attempt=failure_attempt, delay_seconds=delay,
                      error_type=type(exc).__name__, error=str(exc),
                      **(engine._recovery_context() if engine else {"stage": stage}))
            except Exception as exc:
                failure_attempt += 1
                message = str(exc)
                context = engine._recovery_context() if engine else {
                    "stage": stage, "screen": None, "pending_action": None,
                    "pending_source": None, "run_id": None, "run_outcome": None,
                    "account": instance_account(instance), "device_id": instance.android_id,
                }
                delay = backoff
                event("worker_error", **context, attempt=failure_attempt,
                      delay_seconds=delay, error_type=type(exc).__name__, error=message)
                if engine:
                    try:
                        engine._note_recovery_failure("controller_error", message,
                                                      screen=engine.data.get("screen"))
                        engine.data["blocked"] = message + "; automatic controller recovery is retrying"
                        engine.save()
                    except Exception as persist_exc:
                        event("worker_recovery_state_error", **context,
                              error_type=type(persist_exc).__name__, error=str(persist_exc))
                safety_error = any(marker in message.casefold() for marker in (
                    "android device identity mismatch",
                    "mapping or android identity changed",
                    "recording account/device mismatch",
                ))
                if safety_error:
                    STOP.write_text("Safety stop: selected account or device identity changed", encoding="utf-8")
                    event("worker_safety_stop", **context, error=message,
                          reason="wrong_account_or_device_risk")
                engine = None
                backoff = min(backoff * 2, 60)
            deadline = time.monotonic() + delay
            while not STOP.exists() and time.monotonic() < deadline:
                if engine and state_path("stamina_request.json", instance).exists():
                    break
                time.sleep(.25)
        event("worker_stopped", stage=stage)


def supervise(stage="universal", instance=None):
    with contextlib.ExitStack() as stack:
        with lock("control", wait_seconds=10):
            if lock_busy("worker") or live_controller_pids(exclude=controller_exclusions(stage, launching_supervisor=True)):
                raise RuntimeError("An existing account controller is still alive")
            instance = process_target(instance)
            stack.enter_context(lock("supervisor"))
            write_json(RUNTIME / "supervisor.json", {**process_claim(os.getpid()), "worker_pid": None, "stage": stage,
                                                   "instance": instance_dict(instance)})
        event("supervisor_started", pid=os.getpid(), stage=stage)
        child = None
        attempt = 0
        try:
            while not STOP.exists():
                child = subprocess.Popen([sys.executable, str(ROOT / "src" / "farm.py"), "worker", "--stage", stage, "--supervisor-pid", str(os.getpid()), *target_arguments(instance)], cwd=ROOT,
                                         creationflags=subprocess.CREATE_NO_WINDOW,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                write_json(RUNTIME / "supervisor.json", {**process_claim(os.getpid()),
                           **process_claim(child.pid, "worker_pid"), "started": time.time(), "stage": stage,
                           "instance": instance_dict(instance)})
                while child.poll() is None and not STOP.exists():
                    time.sleep(.5)
                if STOP.exists():
                    try:
                        child.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                    break
                attempt += 1
                event("worker_restart", exit_code=child.returncode, attempt=attempt)
                deadline = time.monotonic() + min(5 * attempt, 60)
                while not STOP.exists() and time.monotonic() < deadline:
                    time.sleep(.25)
        finally:
            write_json(RUNTIME / "supervisor.json", {"pid": None, "worker_pid": None, "stopped": time.time(), "stage": stage,
                                                   "instance": instance_dict(instance)})
            event("supervisor_stopped")
            _loot_end_process()


def stop_generation():
    return read_json(RUNTIME / "stop_generation.json").get("id", "")


def check_stop_generation(expected):
    if expected is not None and stop_generation() != expected:
        raise InterruptedError("A newer Stop request cancelled this launch")


def _verify_offline_missing_evidence(progress, instance, stage, completion_id):
    """Inspect retained identities and evidence without opening a device/session."""
    if not STOP.exists():
        raise RuntimeError("Offline missing-evidence recovery requires the existing Stop flag")
    recording = progress["run"]["recording"]
    if (not isinstance(completion_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", completion_id)
            or completion_id != recording["completion_id"]):
        raise ValueError("Offline recovery requires the exact saved --completion-id")
    run_id = progress["run"]["id"]
    directory = RUNTIME / "loot"
    records = [read_json(directory / "pending" / (completion_id + ".json")),
               read_json(directory / "receipts" / (completion_id + ".json"))]
    found = False
    for value in records:
        if not value:
            continue
        found = True
        if (value.get("completion_id") != completion_id or str(value.get("run_id")) != str(run_id)
                or value.get("account") != instance_account(instance)
                or value.get("device_id") != instance.android_id or value.get("stage") != stage
                or value.get("completed_at") != recording.get("completed_at")):
            raise RuntimeError("Retained recording identity does not match the pinned transaction")
        if value.get("screenshot") or value.get("screenshot_sha256"):
            raise RuntimeError("Retained loot evidence exists; recover it instead of marking it missing")
        if value.get("recorded") and value.get("state") != "missing_screenshot":
            raise RuntimeError("A finalized recording receipt already exists; missing closure refused")
    if any(RUNTIME.rglob("*" + completion_id + "*.png")):
        raise RuntimeError("Retained same-job PNG exists; recover it instead of marking it missing")
    database = directory / "loot.sqlite3"
    if database.exists():
        with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT c.*, s.account FROM completions c JOIN sessions s "
                             "ON s.session_id=c.session_id WHERE c.completion_id=?", (completion_id,)).fetchone()
            if row:
                found = True
                if (str(row["run_id"]) != str(run_id) or row["account"] != instance_account(instance)
                        or row["completed_at"] != recording.get("completed_at")):
                    raise RuntimeError("Database completion identity does not match the pinned transaction")
                if row["screenshot_path"] or row["status"] not in {
                        "awaiting_evidence", "capture_pending", "retryable_failure", "operator_attention", "missing_screenshot"}:
                    raise RuntimeError("Database already contains loot evidence or a finalized result; missing closure refused")
    if not found:
        raise RuntimeError("The original durable recording is unavailable; missing closure refused")


def resolve_missing_evidence(stage=None, *, reason, offline=False, completion_id=None):
    """Explicitly retain a lost capture as missing before allowing another mission."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Provide a reason for resolving the unavailable loot evidence")
    reason = reason.strip()
    stage = stage or selected_stage()
    with contextlib.ExitStack() as stack:
        stack.enter_context(lock("control"))
        if controller_active() or lock_busy("restart"):
            raise RuntimeError("Stop the controller and verify its exit before resolving missing evidence")
        stack.enter_context(lock("worker"))
        instance = process_target()
        progress_path = load_stage(stage, instance)["progress"]
        progress = read_json(progress_path)
        pending = progress.get("pending")
        results_expected = None
        if stage == "universal" and isinstance(pending, dict):
            if (pending.get("id"), pending.get("source")) == ("next", "RESULTS_NEXT"):
                results_expected = {"READY_TO_RETRY", "REWARD_REVEAL", "TRIAL_COINS_NEXT"}
            elif (pending.get("id"), pending.get("source")) == ("reveal_rewards", "REWARD_REVEAL"):
                results_expected = {"READY_TO_RETRY", "LUCKY_REWARD"}
        results_pending = results_expected is not None
        if ((pending and (offline or not results_pending)) or progress.get("connection_pending")
                or (progress.get("boss_swap") or {}).get("pending")
                or (progress.get("stage15_strategy") or {}).get("pending")):
            raise RuntimeError("Resolve the existing saved input before resolving missing evidence")
        verify_saved_input(instance, stage)
        pinned = instance_from_dict(progress.get("instance"))
        if ((pinned.key, pinned.android_id, pinned.endpoint) !=
                (instance.key, instance.android_id, instance.endpoint)):
            raise RuntimeError("Saved loot transaction target does not match the selected instance")
        run = progress.get("run") or {}
        recording = run.get("recording") or {}
        if (progress.get("stage") != stage or run.get("outcome") != "completed"
                or not run.get("id") or not recording.get("completion_id")
                or recording.get("version") != 1
                or recording.get("state") not in {"screenshot_capture_pending", "retryable_failure",
                                                  "operator_attention", "missing_screenshot"}):
            raise RuntimeError("No completed transaction with unavailable loot evidence can be resolved")
        if (recording.get("account") != instance_account(instance)
                or recording.get("device_id") != instance.android_id
                or recording.get("stage") != stage):
            raise RuntimeError("Loot recording account/device/stage does not match the pinned target")
        if offline:
            _verify_offline_missing_evidence(progress, instance, stage, completion_id)
        if results_pending:
            # Each input gets its own run_id token, including results inputs;
            # only its timestamp can tie this latch to the completed transaction.
            completed_at, attempted_at = recording.get("completed_at"), pending.get("time")
            expected = pending.get("expected")
            if (not isinstance(expected, list) or len(expected) != len(results_expected)
                    or not all(isinstance(value, str) for value in expected) or set(expected) != results_expected
                    or not isinstance(pending.get("run_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", pending["run_id"])
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in (completed_at, attempted_at))
                    or completed_at <= 0 or not completed_at <= attempted_at <= time.time()):
                raise RuntimeError("The existing saved input is not a verified results latch for this completion")
        observations = []
        home_recovery = False
        if not offline:
            device = Device(instance)
            device.connect()
            detector = Detector(stage)
            home_detector = Detector("ninja_road") if results_pending else None
            for observation in range(2):
                if observation:
                    time.sleep(.25)
                device.verify()
                if device.foreground() != PACKAGE:
                    raise RuntimeError("Restore the game to a verified Start Mission screen before resolving missing evidence")
                frame = device.capture()
                name, _, _ = detector.classify(frame)
                if name == "UNKNOWN" and home_detector is not None:
                    home_name, home_spec, _ = home_detector.classify(frame)
                    if home_name == "NR_HOME" and home_spec and home_spec.get("verified_safe") is True:
                        name = home_name
                permitted = {"READY_TO_START"}
                if results_pending:
                    permitted.add("NR_HOME")
                    if pending["id"] == "reveal_rewards":
                        permitted.discard("READY_TO_START")
                if name not in permitted or (observations and name != observations[0]["screen"]):
                    raise RuntimeError("Two fresh Start Mission observations or two verified Ninja Road Home observations "
                                       "for a supported results latch are required to resolve missing evidence")
                observations.append(dict(time=time.time(), screen=name, android_id=instance.android_id,
                                         instance_key=instance.key, endpoint=instance.endpoint, foreground=PACKAGE))
            home_recovery = observations[0]["screen"] == "NR_HOME"
            if home_recovery:
                # Home proves only that results were departed. Before discarding
                # their latch, verify the original durable job has no retained evidence.
                _verify_offline_missing_evidence(progress, instance, stage, recording["completion_id"])
        collector = _loot_start(stage, instance=instance)
        stack.callback(_loot_call, collector, "close", end_session=True)
        if _loot_call(collector, "missing", run["id"], reason=reason,
                      completion_id=recording["completion_id"]) is not True:
            raise RuntimeError("Missing evidence resolution was not durably acknowledged; saved transaction preserved")
        if offline or home_recovery:
            # Reset before the progress ACK could permit another input. A crash
            # after this write leaves the original transaction safely blocked.
            write_json(state_path("session.json", instance), {"stage": stage, "mission_verified": False})
        resolved_at = time.time()
        recording.update(state="missing_screenshot", missing_evidence_acknowledged=True,
                         resolution_reason=reason, resolved_at=resolved_at)
        if offline:
            recording["resolution_mode"] = "offline"
        elif home_recovery:
            recording["resolution_mode"] = "verified_home"
        if results_pending:
            progress["results_input_resolution"] = dict(
                original_pending=copy.deepcopy(pending), reason=reason, resolved_at=resolved_at,
                run_id=run["id"], completion_id=recording["completion_id"], observations=observations)
            progress["pending"] = None
        progress["blocked"] = None
        progress.pop("recording_health", None)
        write_json(progress_path, progress)
        event("missing_evidence_resolved", stage=stage, account=recording["account"],
              device_id=recording["device_id"], run_id=run["id"],
              completion_id=recording["completion_id"], reason=reason)
    print("Original completion retained with missing loot evidence; Start can resume farming")


def start(stage=None, *, expected_stop=None, restarting=False, requested_generation=None, instance=None):
    stage = stage or selected_stage()
    load_stage(stage)
    with lock("control"):
        check_stop_generation(requested_generation)
        if not restarting and lock_busy("restart"):
            raise RuntimeError("Full Restart is underway; Start refused")
        if expected_stop is not None and (not STOP.exists() or STOP.read_text(encoding="utf-8") != expected_stop):
            raise InterruptedError("Stop request changed; development restart cancelled")
        state = read_json(RUNTIME / "supervisor.json")
        if record_alive(state):
            if state.get("stage", "universal") != stage:
                raise RuntimeError("Another stage is running; stop and verify it before switching")
            print("Already running; no duplicate launched.")
            return
        if controller_active():
            raise RuntimeError("Existing account controller alive; stop it before restarting")
        instance = process_target(instance)
        verify_saved_input(instance, stage)
        if not restarting:
            Device(instance).connect()
        preserve_legacy_owner(instance)
        session = read_json(state_path("session.json", instance))
        if session.get("stage") != stage:
            write_json(state_path("session.json", instance), {"stage": stage, "mission_verified": False})
        write_json(RUNTIME / "instance_selection.json", instance_dict(instance))
        write_json(RUNTIME / "selection.json", {"stage": stage})
        STOP.unlink(missing_ok=True)
        executable = Path(sys.executable).with_name("pythonw.exe")
        env = os.environ.copy()
        env["NBR_LAUNCH_COMMAND"] = subprocess.list2cmdline([str(executable), str(ROOT / "src" / "farm.py"), "supervise", "--stage", stage, *target_arguments(instance)])
        env["NBR_LAUNCH_DIRECTORY"] = str(ROOT)
        # WMI's provider launches this independently of Codex and the calling shell's job.
        script = "$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{CreateFlags=[uint32]0x01000000; ShowWindow=[uint16]0}; $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$env:NBR_LAUNCH_COMMAND; CurrentDirectory=$env:NBR_LAUNCH_DIRECTORY; ProcessStartupInformation=$startup}; $r | ConvertTo-Json -Compress"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=env, capture_output=True, text=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        launch = json.loads(result.stdout)
        if launch["ReturnValue"]:
            raise RuntimeError(f"Independent launch failed: WMI code {launch['ReturnValue']}")
        published = read_json(RUNTIME / "supervisor.json")
        if published.get("pid") != launch["ProcessId"] or published.get("stage") != stage:
            write_json(RUNTIME / "supervisor.json", {**process_claim(launch["ProcessId"]), "worker_pid": None,
                                                      "stage": stage, "launching": True, "instance": instance_dict(instance)})
    # The supervisor must acquire control before it publishes its worker PID.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = read_json(RUNTIME / "supervisor.json")
        if record_alive(state) and record_alive(state, "worker_pid"):
            print(f"Running independently: supervisor {state['pid']}, worker {state.get('worker_pid')}")
            return
        time.sleep(.25)
    raise RuntimeError("Supervisor did not become ready; inspect runtime/events.jsonl")


def stop():
    RUNTIME.mkdir(exist_ok=True)
    # Start may hold this lock during its bounded independent process launch.
    # A busy lock must delay Stop, never discard it.
    with lock("control", wait_seconds=30):
        write_json(RUNTIME / "stop_generation.json", {"id": uuid.uuid4().hex})
        STOP.write_text("Intentional user stop\n", encoding="utf-8")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = read_json(RUNTIME / "supervisor.json")
        if not live_controller_pids() and not lock_busy("worker") and not lock_busy("supervisor"):
            print("Stopped. Game left in its current state.")
            return
        time.sleep(.25)
    print("Stop persisted; process is finishing its bounded ADB operation.")


def full_restart(stage=None, shutdown_timeout=35, *, requested_generation=None):
    """One restart transaction; never discard uncertain input or launch over a live worker."""
    with lock("restart"):
        token = "Full Restart " + uuid.uuid4().hex
        try:
            with lock("control", wait_seconds=10):
                check_stop_generation(requested_generation)
                old_processes = [record for record in recorded_processes() if process_may_be_alive(record)]
                stage = stage or selected_stage()
                load_stage(stage)
                instance = effective_instance()
                STOP.write_text(token, encoding="utf-8")
            event("full_restart_stop_requested", stage=stage)
            deadline = time.monotonic() + shutdown_timeout
            while (any(process_may_be_alive(record) for record in old_processes) or live_controller_pids()
                   or lock_busy("worker") or lock_busy("supervisor")):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Full Restart shutdown failed: old controller still active; no new process launched")
                time.sleep(.25)
            event("full_restart_exit_confirmed", stage=stage)
            with lock("control", wait_seconds=10):
                if not STOP.exists() or STOP.read_text(encoding="utf-8") != token:
                    raise InterruptedError("Stop request changed; Full Restart cancelled")
                if (any(process_may_be_alive(record) for record in old_processes) or controller_active()
                        or lock_busy("worker") or lock_busy("supervisor")):
                    raise RuntimeError("Controller became active during restart; no new process launched")
                verify_saved_input(instance, stage)
                progress_path = load_stage(stage, instance)["progress"]
                progress = read_json(progress_path)
                for key in ("worker_pid", "worker_pid_identity", "heartbeat", "blocked", "screen"):
                    progress.pop(key, None)
                write_json(progress_path, progress)
                write_json(state_path("session.json", instance), {"stage": stage, "mission_verified": False})
                state_path("stamina_request.json", instance).unlink(missing_ok=True)
            event("full_restart_temporary_state_reset", stage=stage, pending_preserved=bool(progress.get("pending")))
            event("full_restart_connection_check", stage=stage)
            Device(instance).connect()
            event("full_restart_identity_verified", stage=stage)
            event("full_restart_relaunch", stage=stage, safeguard="Existing worker ADB connection and device identity validation")
            start(stage, expected_stop=token, restarting=True, requested_generation=requested_generation, instance=instance)
            event("full_restart_completed", stage=stage)
        except Exception as exc:
            event("full_restart_failed", stage=stage, error=str(exc))
            raise


def status(stage=None):
    stage = stage or selected_stage()
    state = read_json(RUNTIME / "supervisor.json")
    instance, instance_error = None, ""
    try:
        instance = effective_instance()
    except (OSError, ValueError, RuntimeError) as exc:
        instance_error = str(exc)
        if not controller_active():
            try:
                saved = read_json(RUNTIME / "instance_selection.json")
                if saved:
                    instance = instance_from_dict(saved)
            except (OSError, ValueError, RuntimeError):
                pass
    farm = read_json(load_stage(stage, instance)["progress"]) if instance else {}
    print(json.dumps({"selected_stage": selected_stage(), "stage": stage,
                      "running": controller_active(),
                      "active_pids": live_controller_pids(), "stop_requested": STOP.exists(),
                      "supervisor": state, "farm": farm,
                      "instance": instance_dict(instance) if instance else None,
                      "instance_account": instance_account(instance) if instance else None,
                      "instance_error": instance_error,
                      "instance_selection_required": instance is None or bool(instance_error) or
                          (not controller_active() and not (RUNTIME / "instance_selection.json").exists()),
                      "instance_locked": instance_locked(),
                      "progress_path": str(load_stage(stage, instance)["progress"]) if instance else None,
                      "session_path": str(state_path("session.json", instance)) if instance else None,
                      "stamina_status": stamina_status(farm),
                      "stamina_wait_minutes": stamina_wait_minutes(),
                      "ninja_boss_swap": boss_swap_enabled(),
                      "ninja_early_exit": early_exit_enabled(),
                      "ninja_exit_mode": ninja_exit_mode(),
                      "stamina_wait_interruptible": farm.get("manager_controls_version") == 1,
                      "stamina_seconds_remaining": max(0, round(farm.get("stamina_until", 0) - time.time()))}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status", "supervise", "worker", "observe", "step", "replay", "restart", "end-stamina-wait", "stamina-duration", "boss-swap", "early-exit", "ninja-mode", "select-instance", "resolve-missing-evidence", "resolve-saved-input"])
    parser.add_argument("--instance-key")
    parser.add_argument("--instance-name")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--minutes", type=int, choices=STAMINA_MINUTES)
    parser.add_argument("--enabled", choices=("on", "off"))
    parser.add_argument("--mode", choices=NINJA_EXIT_MODES)
    parser.add_argument("--image")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--stage", choices=STAGE_IDS)
    parser.add_argument("--reason", help="Audit reason for an explicit input or missing-evidence resolution")
    parser.add_argument("--offline", action="store_true", help="Resolve a stopped, exact missing-evidence transaction without a device")
    parser.add_argument("--completion-id", help="Exact saved completion ID for offline missing-evidence recovery")
    parser.add_argument("--supervisor-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--stop-generation", help=argparse.SUPPRESS)
    parser.add_argument("--target", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = args.stage or selected_stage()
    if args.command == "start": start(stage, requested_generation=args.stop_generation)
    elif args.command == "stop": stop()
    elif args.command == "restart": full_restart(args.stage, requested_generation=args.stop_generation)
    elif args.command == "end-stamina-wait": end_stamina_wait()
    elif args.command == "stamina-duration": set_stamina_duration(args.minutes)
    elif args.command == "boss-swap":
        if args.enabled is None:
            raise ValueError("Choose --enabled on or off")
        set_boss_swap(args.enabled == "on")
    elif args.command == "early-exit":
        if args.enabled is None:
            raise ValueError("Choose --enabled on or off")
        set_early_exit(args.enabled == "on")
    elif args.command == "ninja-mode": set_ninja_exit_mode(args.mode)
    elif args.command == "status": status(stage)
    elif args.command == "resolve-missing-evidence":
        if args.offline or args.completion_id is not None:
            resolve_missing_evidence(stage, reason=args.reason, offline=args.offline, completion_id=args.completion_id)
        else:
            resolve_missing_evidence(stage, reason=args.reason)
    elif args.command == "resolve-saved-input":
        resolve_saved_input(args.stage or "universal", reason=args.reason,
                            expected_instance_key=args.instance_key)
    elif args.command == "supervise": supervise(stage, instance_from_dict(json.loads(args.target)) if args.target else None)
    elif args.command == "worker": worker(stage, args.supervisor_pid, instance_from_dict(json.loads(args.target)) if args.target else None)
    elif args.command == "select-instance":
        if bool(args.instance_key) == bool(args.instance_name):
            raise ValueError("Provide an instance selection or a manual instance name")
        if args.instance_key:
            matches = [item for item in discover()["instances"] if item.key == args.instance_key]
            if len(matches) != 1:
                raise RuntimeError("The selected instance is no longer uniquely available; refresh instances")
            instance = matches[0]
        else:
            instance = resolve_manual(args.instance_name, args.endpoint)
        print(json.dumps(instance_dict(select_instance(instance))))
    elif args.command == "replay":
        name, _, scores = Detector(stage).classify(cv2.imread(args.image))
        print(json.dumps({"screen": name, "scores": scores}, indent=2))
    elif args.command == "observe":
        device = Device()
        device.verify()
        frame = device.capture()
        name, _, scores = Detector(stage).classify(frame)
        path = RUNTIME / "current.png"
        path.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(path), frame)
        result = {"screen": name, "image": str(path),
                  "pending": read_json(load_stage(stage, device.target)["progress"]).get("pending")}
        if not args.brief:
            result["scores"] = scores
        print(json.dumps(result, indent=2))
    else:
        with contextlib.ExitStack() as stack:
            with lock("control"):
                if controller_active() or lock_busy("restart"):
                    raise RuntimeError("An account controller is active; development step refused")
                instance = process_target()
                stack.enter_context(lock("worker"))
                session = read_json(state_path("session.json", instance))
                if session.get("stage") != stage:
                    write_json(state_path("session.json", instance), {"stage": stage, "mission_verified": False})
            engine = create_engine(stage, instance)
            if args.command == "step":
                if STOP.exists():
                    raise RuntimeError("Stop flag set; use start to resume")
                # The one-step input path uses the same durable recording gate.
                collector = _loot_start(stage, instance=instance)
                engine.loot_collector = collector
                stack.callback(_loot_call, collector, "close")
                engine.tick()
                print(json.dumps(engine.data, indent=2))
            else:
                frame, name, _, scores = engine.observe()
                path = RUNTIME / "current.png"
                path.parent.mkdir(exist_ok=True)
                cv2.imwrite(str(path), frame)
                print(json.dumps({"screen": name, "image": str(path), "pending": engine.data.get("pending"), "scores": scores}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        event("fatal", error=str(exc))
        if sys.stderr is not None:
            print(str(exc), file=sys.stderr)
        sys.exit(1)
