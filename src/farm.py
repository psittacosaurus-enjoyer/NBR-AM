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
import msvcrt
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from process_state import process_identity
from bluestacks import Instance, discover, resolve_manual, validate_mapping, verify_identity, _server_port

# Screenshot matching needs no BLAS pool; concurrent CLI imports must stay small.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import cv2
import numpy as np
cv2.setNumThreads(1)

ROOT = Path(__file__).resolve().parents[1]
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
STAMINA_MINUTES = (5, 10, 15, 20, 25)
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
    "dismiss_known_popup", "retry_connection", "reveal_rewards",
}
STATE_ACTIONS = {
    "READY_TO_RETRY": "retry", "READY_TO_START": "start_mission", "IN_BATTLE": None,
    "MISSION_COMPLETE": "advance_completion", "RESULTS_NEXT": "next", "TRIAL_COINS_NEXT": "next", "REWARD_REVEAL": "reveal_rewards",
    "RANK_UP_STAMINA_ACK": "dismiss_known_popup", "RANK_UP_RESULTS": "next",
    "COMMUNICATION_ERROR": "retry_connection", "STAMINA_PROMPT": "dismiss_stamina",
}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {} if default is None else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    # Windows readers can briefly hold a destination without delete-sharing.
    # Keep atomic replacement and retry only this transient sharing conflict.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)


STAGE_IDS = ("universal",)


def load_stage(stage="universal", instance=None):
    if stage not in STAGE_IDS:
        raise ValueError("Unknown farming stage")
    return {"id": stage, "name": "Universal — current mission", "catalog": CATALOG,
            "progress": state_path("universal.json", instance), "state_actions": dict(STATE_ACTIONS)}


def selected_stage():
    return "universal"


def progress_records(instance=None):
    """Retain account safeguards from saved controller state across upgrades."""
    for path in instance_runtime(instance).glob("*.json"):
        data = read_json(path)
        if isinstance(data, dict) and any(key in data for key in ("counts", "pending", "stamina_until")):
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


def verify_saved_input(instance=None):
    if instance is not None and not is_legacy_instance(instance):
        return  # Pending input and stamina state are isolated by immutable device identity.
    previous = read_json(RUNTIME / "selection.json").get("stage")
    if not isinstance(previous, str) or previous == "universal":
        return
    for path, progress in progress_records():
        if path == RUNTIME / "universal.json":
            continue  # The current worker resumes its own durable latch.
        if previous and (progress.get("stage") == previous or
                         path.stem == previous.replace("-", "_") or path.name == "farm.json"):
            if progress.get("pending"):
                raise RuntimeError("Resolve the existing saved input before starting")


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


def _loot_start(stage, supervisor_pid=None, instance=None):
    try:
        from loot_tracking import Collector, process_identity
        identity, started = process_identity(supervisor_pid or os.getpid())
        return Collector(ROOT, stage, identity, process_started=started,
                         account=instance_account(instance) if instance else "acc1")
    except Exception as exc:
        _loot_tracking_error(exc)
        return None


def _loot_end_process():
    try:
        from loot_tracking import end_process_session, process_identity
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

    def verify(self):
        validate_mapping(self.target)
        if not self.target.config_path:
            verify_identity(self.target)  # Manual endpoints must still identify a BlueStacks device.
        identity = self.command(["shell", "settings", "get", "secure", "android_id"]).decode().strip()
        if not identity or identity != self.target.android_id:
            raise RuntimeError("Android device identity mismatch; refusing input")
        self.identity = identity

    def connect(self):
        validate_mapping(self.target)
        subprocess.run([self.target.adb_path, "-H", "127.0.0.1", "-P", str(_server_port()),
                        "connect", self.target.endpoint], capture_output=True, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        self.verify()

    def foreground(self):
        text = self.command(["shell", "dumpsys", "activity", "activities"]).decode(errors="replace")
        match = re.search(r'(?:topResumedActivity|mResumedActivity):.*?\bu\d+\s+(\S+)', text)
        return match.group(1).split("/")[0] if match else None

    def capture(self):
        data = self.command(["exec-out", "screencap", "-p"])
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or (frame.shape[1], frame.shape[0]) != SIZE:
            raise RuntimeError("Screenshot invalid or dimensions changed; refusing input")
        return frame

    def perform(self, action):
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
            for anchor in state["anchors"]:
                alternatives = anchor.get("alternatives", [])
                if alternatives and (state["name"] != "MISSION_COMPLETE" or anchor.get("search_region") or len(alternatives) > 4):
                    raise RuntimeError("Animation variants are supported only for fixed completion text guards")
                for reference in [anchor["template"], *alternatives]:
                    path = ROOT / reference
                    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if template is None:
                        raise RuntimeError(f"Missing template: {path}")
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
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        matched = []
        scores = {}
        for state in self.states:
            good = True
            metrics = []
            positions = {}
            for index, anchor in enumerate(state["anchors"]):
                x1, y1, x2, y2 = anchor["region"]
                template = self.templates[anchor["template"]]
                if anchor.get("search_region"):
                    sx1,sy1,sx2,sy2 = anchor["search_region"]
                    area = gray[sy1:sy2,sx1:sx2]
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
        if getattr(self, "target", None) is not None:
            self.data["instance"] = instance_dict(self.target)
        write_json(getattr(self, "progress", RUNTIME / "universal.json"), self.data)

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
        previous_run = self.data.get("run")
        previous = self.data.get("screen")
        if name != previous:
            event("screen", previous=previous, screen=name, stage=getattr(self, "stage", {}).get("id", "universal"))
        self.data["screen"] = name
        if name == "UNKNOWN":
            self.evidence(frame, name)
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
        if pending and name != pending["source"] and (name in pending["expected"] or (spec and spec.get("recovery")) or resumed_from_entry or advanced_to_rewards):
            event("transition_confirmed" if name in pending["expected"] else "recovery_transition", action=pending["id"], source=pending["source"], target=name,
                  seconds=round(time.time() - pending["time"], 2))
            self.data["pending"] = None
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
            event("mission_completed", stage=getattr(self, "stage", {}).get("id", "universal"),
                  run_id=run["id"], seconds=round(time.time()-run["start"], 2),
                  origin=run.get("origin", "runner_started"),
                  duration_basis=run.get("duration_basis", "mission_entry"))
            _loot_call(getattr(self, "loot_collector", None), "completed", run["id"],
                       completed_at=time.time())
        if name in {"REVIVAL_PROMPT", "MISSION_FAILED"} and run and not run.get("outcome"):
            run["outcome"] = "failed"
            self.data["counts"]["failed"] += 1
            event("mission_failed", run_id=run["id"])
        if self.data.get("pending"):
            self.data["blocked"] = "Awaiting confirmed transition; no further click permitted"
        elif self.data.get("stamina_until", 0) > time.time():
            self.data["blocked"] = "Waiting for natural stamina regeneration"
        elif name == "UNKNOWN":
            self.data["blocked"] = "Unknown screen; observing without input"
        else:
            self.data["blocked"] = None
        self.save()
        _loot_call(self, "_loot_observe", frame, name, previous_run, spec)

    def _loot_observe(self, frame, name, previous_run=None, spec=None):
        """Reuse the existing results observation; never capture or control input."""
        self._loot_waiting_for_reveal = False
        collector = getattr(self, "loot_collector", None)
        if collector is None:
            return
        run = self.data.get("run")
        if (previous_run and previous_run.get("outcome") == "completed"
                and (not run or previous_run.get("id") != run.get("id"))):
            _loot_call(collector, "missing", previous_run["id"], reason="missing_screenshot")
        if (name == "READY_TO_RETRY" and run and run.get("outcome") == "completed"
                and getattr(self, "_loot_evidence_run", None) != run["id"]):
            # The collector accepts only completions durably observed this session;
            # historical farm progress cannot fabricate rewards on attachment.
            from item_recognition.reward_layout import has_unrevealed_rewards
            if has_unrevealed_rewards(frame, ROOT / 'assets/item_recognition'):
                self._loot_waiting_for_reveal = True
                return
            if _loot_call(collector, "evidence", run["id"], frame):
                self._loot_evidence_run = run["id"]

    def halt_loss(self, frame, name, text=""):
        # Persist stop before notification or any further processing of this screen.
        STOP.write_text("Loss detected; manual intervention and explicit start required", encoding="utf-8")
        path = self.path(f"loss_{int(time.time())}.png")
        cv2.imwrite(str(path), frame)
        run = self.data.get("run")
        if run and not run.get("outcome"):
            run["outcome"] = "failed"
            self.data["counts"]["failed"] += 1
        self.data["screen"] = name
        self.data["blocked"] = "Loss detected; stopped for manual repair"
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
        if act:
            self.consume_stamina_request()
        if self.detector.changed():
            self.detector = Detector(getattr(self, "stage", {}).get("id", "universal"))
            event("patterns_reloaded")
        foreground = self.device.foreground()
        if foreground in {"com.uncube.launcher3"}:
            self.data["screen"] = "GAME_CLOSED"
            self.data['blocked'] = 'Restore the game manually to the current mission Start Mission screen'
            self.save()
            return 30
        if foreground != PACKAGE:
            self.data["screen"] = "OTHER_FOREGROUND"
            self.data["blocked"] = "Unrecognized foreground; no input"
            self.save()
            return 30
        self.data["launch_pending"] = None
        frame, name, spec, _ = self.observe()
        # A second fresh agreeing observation is mandatory before resolving or sending input.
        if name != "UNKNOWN":
            time.sleep(.25)
            frame2, name2, spec2, _ = self.observe()
            if name2 != name:
                self.update(frame2, "UNKNOWN", None)
                return 1
            if spec and spec.get("action", {}).get("point_anchor") is not None:
                # Seeing the same banner twice is insufficient while the list is
                # still coasting. Require stationary geometry across three captures.
                point1 = spec["action"]["point"]
                point2 = spec2["action"]["point"]
                frame3,name3,spec3,_ = self.observe()
                if name3 != name or any(abs(a-b)>1 for a,b in zip(point1,point2)) or any(abs(a-b)>1 for a,b in zip(point2,spec3["action"]["point"])):
                    self.update(frame3,"UNKNOWN",None)
                    self.data["blocked"] = "Waiting for mission-list motion to settle"
                    self.save()
                    return 1
                frame2,spec2 = frame3,spec3
            frame, spec = frame2, spec2
        if act and name in {"REVIVAL_PROMPT", "MISSION_FAILED"}:
            return self.halt_loss(frame, name)
        if hasattr(self, "stage"):
            if not self.mission_verified and name != 'READY_TO_START':
                self.update(frame, 'UNKNOWN', None)
                self.data['blocked'] = 'Prepare any mission Start Mission screen, then Start'
                self.save()
                return 10
            if name == 'READY_TO_START':
                self.mission_verified = True
                write_json(self.path("session.json"), {"stage": self.stage["id"], "mission_verified": True})
        run = self.data.get("run")
        if act and name == "UNKNOWN" and run and not run.get("outcome"):
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
        if getattr(self, '_loot_waiting_for_reveal', False):
            self.data['blocked'] = 'Waiting for rewards to finish revealing'
            self.save()
            return 1
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
        dismissing_stamina = bool(name == "STAMINA_PROMPT" and spec
                                 and spec.get("action", {}).get("id") == "dismiss_stamina")
        if self.data.get("stamina_until", 0) > time.time() and not dismissing_stamina:
            return 30
        if spec and spec.get("action"):
            action = spec["action"]
            # No unguarded clicks in battle, even if a catalog is accidentally changed.
            if name == "IN_BATTLE":
                raise RuntimeError("Battle input prohibited")
            self.data["pending"] = {"id": action["id"], "source": name, "expected": action["expected"],
                                    "time": time.time(), "run_id": uuid.uuid4().hex}
            previous_stamina_until = self.data.get("stamina_until")
            if action["id"] == "dismiss_stamina":
                self.data["pending"]["stamina_wait_seconds"] = stamina_wait_minutes() * 60
                self.data["stamina_until"] = max(self.data.get("stamina_until", 0), time.time() + self.data["pending"]["stamina_wait_seconds"])
            self.save()  # Persist BEFORE input so interruption cannot replay a click.
            event("action_attempt", stage=getattr(self, "stage", {}).get("id", "universal"),
                  screen=name, action=action["id"], point=action.get("point"))
            try:
                self.device.perform(action)
            except ActionNotDispatched as exc:
                self.data["pending"] = None
                if action["id"] == "dismiss_stamina":
                    if previous_stamina_until is None:
                        self.data.pop("stamina_until", None)
                    else:
                        self.data["stamina_until"] = previous_stamina_until
                self.save()
                event("action_not_sent", action=action["id"], error=str(exc))
                raise
            except Exception as exc:
                event("action_uncertain", action=action["id"], error=str(exc))
                raise
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
        backoff = 5
        while not STOP.exists():
            try:
                if engine is None:
                    engine = Engine(stage, instance)
                    engine.loot_collector = collector
                delay = engine.tick()
                backoff = 5
            except Exception as exc:
                event("worker_error", error=str(exc))
                if engine:
                    engine.data["blocked"] = str(exc)
                    engine.save()
                engine = None
                delay = backoff
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
        verify_saved_input(instance)
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
                verify_saved_input(instance)
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
                      "stamina_wait_interruptible": farm.get("manager_controls_version") == 1,
                      "stamina_seconds_remaining": max(0, round(farm.get("stamina_until", 0) - time.time()))}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status", "supervise", "worker", "observe", "step", "replay", "restart", "end-stamina-wait", "stamina-duration", "select-instance"])
    parser.add_argument("--instance-key")
    parser.add_argument("--instance-name")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--minutes", type=int, choices=STAMINA_MINUTES)
    parser.add_argument("--image")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--stage", choices=STAGE_IDS)
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
    elif args.command == "status": status(stage)
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
            engine = Engine(stage, instance)
            if args.command == "step":
                if STOP.exists():
                    raise RuntimeError("Stop flag set; use start to resume")
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
