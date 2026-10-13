"""Deterministic, acc1-only farming. No model, network API, or currency actions."""
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

# Screenshot matching needs no BLAS pool; concurrent CLI imports must stay small.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import cv2
import numpy as np
cv2.setNumThreads(1)

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
CATALOG = ROOT / "patterns" / "catalog.json"
STOP = RUNTIME / "STOP"
ADB = Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe")
CONF = Path(r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf")
SERIAL = "127.0.0.1:5555"
PACKAGE = "com.reblazing.nbrc"
ACTIVITY = "com.reblazing.nbrc/com.blazingdreamers.nbrclient.NbrPlayerActivity"
SIZE = (900, 1600)
STAMINA_WAIT_SECONDS = 600
ctypes.windll.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
ctypes.windll.kernel32.OpenProcess.restype = wintypes.HANDLE
ctypes.windll.kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
ALLOWED_ACTIONS = {
    "retry", "start_mission", "advance_completion", "next", "decline_revive",
    "dismiss_stamina", "back", "nav_right", "emergency", "scroll_missions",
    "select_scrolls", "select_rank_a", "solo", "select_first_helper",
    "title_start", "dismiss_known_popup", "retry_connection", "reveal_rewards",
    "select_rich", "select_rank_b",
}
STATE_ACTIONS = {
    "READY_TO_RETRY": "retry", "READY_TO_START": "start_mission", "IN_BATTLE": None,
    "MISSION_COMPLETE": "advance_completion", "RESULTS_NEXT": "next", "REWARD_REVEAL": "reveal_rewards",
    "RELEASE_SCREEN": "back", "TEAM_MENU": "back", "TITLE_START": "title_start",
    "NEWS_POPUP": "dismiss_known_popup", "INITIAL_POPUP": "dismiss_known_popup", "WELCOME_POPUP": "dismiss_known_popup",
    "RANK_UP_STAMINA_ACK": "dismiss_known_popup", "RANK_UP_RESULTS": "next",
    "HOME_MENU": "nav_right", "HOME_EMERGENCY": "emergency", "SCROLLS_BANNER": "select_scrolls",
    "RANK_SELECTION": "select_rank_a", "SOLO_SELECTION": "solo", "HELPER_SELECTION": "select_first_helper",
    "COMMUNICATION_ERROR": "retry_connection", "STAMINA_PROMPT": "dismiss_stamina",
    "REVIVAL_PROMPT": "decline_revive", "MISSION_FAILED": "back", "LUCKY_REWARD": "reveal_rewards",
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


STAGE_IDS = ("scrolls-a", "rich-b", "practice-part-1", "night-of-tragedy-a")


def load_stage(stage="scrolls-a"):
    if stage not in STAGE_IDS:
        raise ValueError("Unknown farming stage")
    defaults = {"id": stage, "name": "All Scrolls [A]" if stage == "scrolls-a" else "Getting Rich Quick! [B]",
                "catalog": "patterns/catalog.json" if stage == "scrolls-a" else "patterns/rich_b.json",
                "progress": "runtime/farm.json" if stage == "scrolls-a" else "runtime/rich_b.json"}
    if stage == "practice-part-1":
        defaults.update(name="Practice Training [Part 1]", catalog="patterns/catalog.json",
                        progress="runtime/practice_part_1.json", manual_first_battle=True)
    if stage == "night-of-tragedy-a":
        defaults.update(name="Night of Tragedy [A]", catalog="patterns/night_of_tragedy_a.json",
                        progress="runtime/night_of_tragedy_a.json", manual_first_battle=True)
    config = {**defaults, **read_json(ROOT / "stages" / (stage + ".json"))}
    config["id"] = stage
    # Stage files cannot redirect controller state or load an arbitrary catalog.
    if config["catalog"] != defaults["catalog"] or config["progress"] != defaults["progress"]:
        raise RuntimeError("Unexpected stage catalog/progress path")
    config["catalog"] = CATALOG if defaults["catalog"] == "patterns/catalog.json" else ROOT / config["catalog"]
    config["progress"] = RUNTIME / Path(defaults["progress"]).name
    config["manual_first_battle"] = stage in {"practice-part-1", "night-of-tragedy-a"}
    overrides = config.get("state_actions", {})
    allowed_overrides = {"RICH_BANNER": "select_rich", "RANK_SELECTION": "select_rank_b",
                         "SCROLLS_LIST_POSITION": "scroll_missions"} if stage == "rich-b" else {}
    if any(allowed_overrides.get(name) != action for name, action in overrides.items()):
        raise RuntimeError("Unsafe stage action override")
    config["state_actions"] = {**STATE_ACTIONS, **overrides}
    return config


def selected_stage():
    stage = read_json(RUNTIME / "selection.json").get("stage", "scrolls-a")
    if stage not in STAGE_IDS:
        raise RuntimeError("Invalid saved stage selection")
    return stage


def alive(pid):
    if not pid:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


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


def recorded_pids():
    pids = set()
    for path in (RUNTIME / "supervisor.json", RUNTIME / "controller.json",
                 RUNTIME / "farm.json", RUNTIME / "rich_b.json", RUNTIME / "practice_part_1.json",
                 RUNTIME / "night_of_tragedy_a.json"):
        data = read_json(path)
        pids.update(pid for pid in (data.get("pid"), data.get("worker_pid")) if pid)
    return pids


def live_controller_pids(exclude=()):
    return [pid for pid in recorded_pids() if pid not in exclude and alive(pid)]


def controller_exclusions(stage, supervisor_pid=None, launching_supervisor=False):
    """Windows venv launchers remain the parent of the real Python process."""
    own = {os.getpid()}
    claim = read_json(RUNTIME / "supervisor.json")
    if claim.get("stage", "scrolls-a") != stage:
        return own
    if launching_supervisor and claim.get("launching") and claim.get("pid") == os.getppid():
        own.add(os.getppid())
    if supervisor_pid and claim.get("pid") == supervisor_pid:
        own.add(supervisor_pid)
        if claim.get("worker_pid") == os.getppid():
            own.add(os.getppid())
    return own


def select_stage(stage):
    load_stage(stage)
    with lock("control"):
        if lock_busy("worker") or lock_busy("supervisor") or live_controller_pids():
            raise RuntimeError("Farm controller is active; stop and verify it before selecting another stage")
        if selected_stage() != stage:
            write_json(RUNTIME / "session.json", {"stage": stage, "mission_verified": False})
        write_json(RUNTIME / "selection.json", {"stage": stage})
    print(f"Selected {stage}")


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


def _loot_start(stage, supervisor_pid=None):
    try:
        from loot_tracking import Collector, process_identity
        identity, started = process_identity(supervisor_pid or os.getpid())
        return Collector(ROOT, stage, identity, process_started=started, account="acc1")
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
         str(ROOT / "loss_ocr.ps1"), "-ImagePath", str(path)],
        capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Local Windows OCR unavailable: " + result.stderr.decode(errors="replace")[:200])
    return json.loads(result.stdout.decode("utf-8-sig"))["text"]


def notify_loss():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pythonw), str(ROOT / "loss_notify.py")],
                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Device:
    def __init__(self):
        self.identity = None

    def command(self, args, timeout=15):
        if args[:2] in (["shell", "input"], ["shell", "monkey"]) and STOP.exists():
            raise InterruptedError("Stop requested before input")
        r = subprocess.run([str(ADB), "-s", SERIAL, *args], capture_output=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors="replace").strip()[:500])
        return r.stdout

    def verify(self):
        config = dict(re.findall(r'^([^=]+)="(.*)"$', CONF.read_text(encoding="utf-8"), re.MULTILINE))
        prefix = "bst.instance.Pie64."
        if config.get(prefix + "display_name") != "acc1" or config.get(prefix + "adb_port") != "5555":
            raise RuntimeError("acc1 instance/port mapping changed; refusing input")
        identity = self.command(["shell", "settings", "get", "secure", "android_id"]).decode().strip()
        if not identity or identity != config.get(prefix + "android_id"):
            raise RuntimeError("Android identity is not acc1; refusing input")
        self.identity = identity

    def connect(self):
        subprocess.run([str(ADB), "connect", SERIAL], capture_output=True, timeout=15,
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
                self.command(["shell", "input", "mouse", "tap", str(x), str(y)])
            elif action.get("input_mode") == "adb_tap":
                self.command(["shell", "input", "tap", str(x), str(y)])
            elif action["id"] == "select_scrolls":
                # A fixed script emits DOWN / wait / UP with NO MOVE events.
                # Never invoke Monkey without -f: random events are prohibited.
                local = RUNTIME / "fixed_tap.script"
                local.write_text(f"type= raw events\ncount= 1\nspeed= 1.0\nstart data >>\nTap({x},{y},350)\n", encoding="ascii")
                self.command(["push", str(local), "/data/local/tmp/nbr_fixed_tap.script"])
                result = self.command(["shell", "monkey", "-p", PACKAGE, "-f", "/data/local/tmp/nbr_fixed_tap.script", "1"])
                if b"Events injected: 3" not in result or b"Injection Failed" in result:
                    raise RuntimeError("Fixed tap outcome uncertain; input latch retained")
            else:
                self.command(["shell", "input", "swipe", str(x), str(y), str(x), str(y), "350"])
        elif action["kind"] == "swipe" and action["id"] == "scroll_missions":
            x1, y1, x2, y2 = action["points"]
            if not (0 <= x1 < SIZE[0] and 0 <= x2 < SIZE[0] and 330 <= y1 < 1380 and 330 <= y2 < 1380):
                raise RuntimeError("Swipe outside mission list")
            duration = action.get("duration", 700)
            if not 200 <= duration <= 1200:
                raise RuntimeError("Unsafe swipe duration")
            self.command(["shell", "input", "swipe", *map(str, [x1, y1, x2, y2]), str(duration)])
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
        output=self.command(['shell',script])
        if output.strip():
            raise RuntimeError('Hardware touch reported unexpected output; input latch retained')

    def launch(self):
        self.verify()
        if STOP.exists():
            raise InterruptedError("Stop requested")
        self.command(["shell", "am", "start", "-n", ACTIVITY])


class Detector:
    def __init__(self, stage="scrolls-a"):
        self.stage = load_stage(stage)
        self.catalog = self.stage["catalog"]
        self.signature = self.catalog.stat().st_mtime_ns
        self.states = read_json(self.catalog, {"states": []})["states"]
        if self.stage["id"] == "practice-part-1":
            # Reuse shared controls with captured practice title guards. No OCR
            # subprocess is needed for routine battle/results polling.
            title_anchors = {"READY_TO_RETRY": 1, "RESULTS_NEXT": 1,
                             "REWARD_REVEAL": 0, "READY_TO_START": 1,
                             "RANK_UP_STAMINA_ACK": 2, "STAMINA_PROMPT": 3}
            allowed = {*title_anchors, "IN_BATTLE", "MISSION_COMPLETE", "COMMUNICATION_ERROR"}
            self.states = copy.deepcopy([s for s in self.states if s["name"] in allowed])
            self.states.extend(copy.deepcopy(s) for s in read_json(ROOT / "patterns/rich_b.json")["states"]
                               if s["name"] == "STAMINA_PROMPT")
            for state in self.states:
                if state["name"] in title_anchors:
                    start = state["name"] == "READY_TO_START"
                    region = [190, 395, 735, 461] if start else [190, 182, 735, 247]
                    template = "patterns/practice_part_1/start_title.png" if start else "patterns/practice_part_1/results_title.png"
                    if state["name"] in {"STAMINA_PROMPT", "RANK_UP_STAMINA_ACK"}:
                        template = "patterns/practice_part_1/dialog_title.png"
                    state["anchors"][title_anchors[state["name"]]] = {
                        "region": region, "template": template, "correlation": .96, "difference": 12}
        self.templates = {}
        for state in self.states:
            if state.get("action") and (state["action"]["id"] not in ALLOWED_ACTIONS or not state.get("verified_safe")):
                raise RuntimeError("Unverified/unsafe action in catalog")
            if state.get("action"):
                permitted = "scroll_missions" if state["name"].startswith("EMERGENCY_LIST") else self.stage["state_actions"].get(state["name"])
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
        if len(best) != 1:
            return "UNKNOWN", None, scores
        return best[0]["name"], best[0], scores


class Engine:
    def __init__(self, stage="scrolls-a"):
        self.stage = load_stage(stage)
        self.progress = self.stage["progress"]
        session = read_json(RUNTIME / "session.json")
        self.mission_verified = session.get("stage") == stage and session.get("mission_verified", False)
        self.device = Device()
        self.detector = Detector(stage)
        self.data = read_json(self.progress, {})
        self.data["stage"] = stage
        self.data.setdefault("counts", {"started": 0, "completed": 0, "failed": 0, "launches": 0})
        self.last_unknown = 0
        self.unknown_hash = None
        self.device.connect()

    def save(self):
        self.data["heartbeat"] = time.time()
        self.data["worker_pid"] = os.getpid()
        write_json(getattr(self, "progress", RUNTIME / "farm.json"), self.data)

    def evidence(self, frame, name):
        dest = RUNTIME / "screenshots"
        dest.mkdir(exist_ok=True)
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
            event("screen", previous=previous, screen=name, stage=getattr(self, "stage", {}).get("id", "scrolls-a"))
        self.data["screen"] = name
        if name == "UNKNOWN":
            self.evidence(frame, name)
        pending = self.data.get("pending")
        resumed_from_rank = bool(
            pending and (pending["id"], pending["source"]) == ("select_rank_b", "RANK_SELECTION")
            and getattr(self, "stage", {}).get("id") == "rich-b"
            and getattr(self, "mission_verified", False)
            and name in {"IN_BATTLE", "MISSION_COMPLETE"}
        )
        stage_id = getattr(self, "stage", {}).get("id")
        session = read_json(RUNTIME / "session.json") if pending and pending["id"] == "retry_connection" else {}
        resumed_from_connection = bool(
            pending and (pending["id"], pending["source"]) == ("retry_connection", "COMMUNICATION_ERROR")
            and stage_id in STAGE_IDS and getattr(self, "mission_verified", False)
            and session.get("stage") == stage_id and session.get("mission_verified", False)
            and name in {"IN_BATTLE", "MISSION_COMPLETE"}
        )
        resumed_from_entry = bool(pending and
                                 (pending["id"], pending["source"]) in {
                                     ("title_start", "TITLE_START"), ("retry", "READY_TO_RETRY")
                                 } and name in {"IN_BATTLE", "MISSION_COMPLETE"}) or resumed_from_rank or resumed_from_connection
        advanced_to_rewards = bool(pending and pending["id"] == "advance_completion" and
                                   pending["source"] == "MISSION_COMPLETE" and name == "REWARD_REVEAL")
        if pending and name != pending["source"] and (name in pending["expected"] or (spec and spec.get("recovery")) or resumed_from_entry or advanced_to_rewards):
            event("transition_confirmed" if name in pending["expected"] else "recovery_transition", action=pending["id"], source=pending["source"], target=name,
                  seconds=round(time.time() - pending["time"], 2))
            self.data["pending"] = None
            if pending["id"] == "dismiss_stamina":
                self.data["stamina_until"] = max(self.data.get("stamina_until", 0), time.time() + STAMINA_WAIT_SECONDS)
            if pending["id"] == "start_mission" and name in {"IN_BATTLE", "MISSION_COMPLETE"}:
                self.data["counts"]["started"] += 1
                self.data["run"] = {"id": pending["run_id"], "start": pending["time"], "outcome": None}
            elif resumed_from_entry and (not self.data.get("run") or self.data["run"].get("outcome")):
                # The game resumed a mission beyond the menu flow. Track its
                # remaining observed portion so loss monitoring stays active.
                self.data["counts"]["recovered_missions"] = self.data["counts"].get("recovered_missions", 0) + 1
                self.data["run"] = {"id": uuid.uuid4().hex, "start": time.time(), "outcome": None,
                                    "origin": {"title_start": "observed_after_title", "retry": "observed_after_retry",
                                               "select_rank_b": "observed_after_rank_selection",
                                               "retry_connection": "observed_after_connection"}[pending["id"]],
                                    "duration_basis": "since_first_observation"}
        run = self.data.get("run")
        if name == "MISSION_COMPLETE" and run and not run.get("outcome"):
            run["outcome"] = "completed"
            self.data["counts"]["completed"] += 1
            event("mission_completed", stage=getattr(self, "stage", {}).get("id", "scrolls-a"),
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
        _loot_call(self, "_loot_observe", frame, name, previous_run)

    def _loot_observe(self, frame, name, previous_run=None):
        """Reuse the existing results observation; never capture or control input."""
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
            self._loot_evidence_run = run["id"]
            _loot_call(collector, "evidence", run["id"], frame)

    def halt_loss(self, frame, name, text=""):
        # Persist stop before notification or any further processing of this screen.
        STOP.write_text("Loss detected; manual intervention and explicit start required", encoding="utf-8")
        path = RUNTIME / f"loss_{int(time.time())}.png"
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
        write_json(RUNTIME / "loss.json", record)
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
        catalog = getattr(self.detector, "catalog", CATALOG)
        if catalog.stat().st_mtime_ns != self.detector.signature:
            self.detector = Detector(getattr(self, "stage", {}).get("id", "scrolls-a"))
            event("patterns_reloaded")
        foreground = self.device.foreground()
        if foreground in {"com.uncube.launcher3"}:
            self.data["screen"] = "GAME_CLOSED"
            if getattr(self, "stage", {}).get("manual_first_battle"):
                self.data["blocked"] = f"Manually restore {self.stage['name']} and begin its first battle"
                self.save()
                return 30
            if act and (not self.data.get("launch_pending") or time.time()-self.data["launch_pending"] > 120):
                self.data["launch_pending"] = time.time()
                self.data["pending"] = None
                self.save()
                self.device.launch()
                self.data["counts"]["launches"] += 1
                event("game_launch")
            self.save()
            return 10
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
            resuming_night_stamina = (
                self.stage['id'] == 'night-of-tragedy-a' and name == 'STAMINA_PROMPT'
                and (self.data.get('pending') or {}).get('id') == 'retry'
                and self.data['pending'].get('source') == 'READY_TO_RETRY'
            )
            if self.stage.get("manual_first_battle") and not self.mission_verified:
                if name == "IN_BATTLE":
                    # Explicit stage selection plus the user-started battle is
                    # the entry point; results still require the selected stage title.
                    self.mission_verified = True
                    write_json(RUNTIME / "session.json", {"stage": self.stage["id"], "mission_verified": True})
                    self.data["pending"] = None
                    self.data["run"] = {"id": uuid.uuid4().hex, "start": time.time(), "outcome": None,
                                        "origin": "manual_first_battle", "duration_basis": "since_first_observation"}
                elif name not in {"READY_TO_RETRY", "RESULTS_NEXT", "REWARD_REVEAL", "RANK_UP_STAMINA_ACK"} and not resuming_night_stamina and not (
                    name == "READY_TO_START" and (self.data.get("pending") or {}).get("id") == "retry"
                    and self.data["pending"].get("source") == "READY_TO_RETRY"
                ):
                    self.update(frame, "UNKNOWN", None)
                    self.data["blocked"] = "Waiting for the manually started first battle"
                    self.save()
                    return 10
            if name in {"READY_TO_START", "READY_TO_RETRY", "RESULTS_NEXT", "REWARD_REVEAL", "RANK_UP_STAMINA_ACK"} or resuming_night_stamina:
                self.mission_verified = True
                write_json(RUNTIME / "session.json", {"stage": self.stage["id"], "mission_verified": True})
            if name in {"IN_BATTLE", "MISSION_COMPLETE"} and not self.mission_verified:
                self.update(frame, "UNKNOWN", None)
                self.data["blocked"] = "Awaiting a visually verified selected mission title/rank"
                self.save()
                return 10
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
        pending = self.data.get("pending")
        if pending and name == 'SCROLLS_BANNER' and pending['source'] == name and time.time()-pending['time'] > 120:
            self.data['blocked'] = 'Mission banner did not advance; observing until a verified transition or manual resolution'
            self.save()
            return 30
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
            if action["id"] == "dismiss_stamina":
                self.data["stamina_until"] = max(self.data.get("stamina_until", 0), time.time() + STAMINA_WAIT_SECONDS)
            self.save()  # Persist BEFORE input so interruption cannot replay a click.
            event("action_attempt", stage=getattr(self, "stage", {}).get("id", "scrolls-a"),
                  screen=name, action=action["id"], point=action.get("point"))
            try:
                self.device.perform(action)
            except Exception as exc:
                event("action_uncertain", action=action["id"], error=str(exc))
                raise
            event("action_sent", action=action["id"], stage=getattr(self, "stage", {}).get("id", "scrolls-a"))
            return 1
        return 2 if polling_battle else 30 if name == "UNKNOWN" else 2


def worker(stage="scrolls-a", supervisor_pid=None):
    # Hold one account-wide input lock for the entire controller lifetime.
    with contextlib.ExitStack() as stack:
        with lock("control", wait_seconds=10):
            state = read_json(RUNTIME / "supervisor.json")
            if lock_busy("supervisor") and (not supervisor_pid or state.get("pid") != supervisor_pid or state.get("stage", "scrolls-a") != stage):
                raise RuntimeError("An account supervisor is active; direct worker refused")
            if live_controller_pids(exclude=controller_exclusions(stage, supervisor_pid)):
                raise RuntimeError("An existing account controller is still alive")
            stack.enter_context(lock("worker"))
            write_json(RUNTIME / "controller.json", {"worker_pid": os.getpid(), "stage": stage})
            session = read_json(RUNTIME / "session.json")
            if session.get("stage") != stage:
                write_json(RUNTIME / "session.json", {"stage": stage, "mission_verified": False})
        collector = _loot_start(stage, supervisor_pid)
        stack.callback(_loot_call, collector, "close", end_session=not bool(supervisor_pid))
        engine = None
        backoff = 5
        while not STOP.exists():
            try:
                if engine is None:
                    engine = Engine(stage)
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
                time.sleep(.25)
        event("worker_stopped", stage=stage)


def supervise(stage="scrolls-a"):
    with contextlib.ExitStack() as stack:
        with lock("control", wait_seconds=10):
            if lock_busy("worker") or live_controller_pids(exclude=controller_exclusions(stage, launching_supervisor=True)):
                raise RuntimeError("An existing account controller is still alive")
            stack.enter_context(lock("supervisor"))
            write_json(RUNTIME / "supervisor.json", {"pid": os.getpid(), "worker_pid": None, "stage": stage})
        event("supervisor_started", pid=os.getpid(), stage=stage)
        child = None
        attempt = 0
        try:
            while not STOP.exists():
                child = subprocess.Popen([sys.executable, str(ROOT / "farm.py"), "worker", "--stage", stage, "--supervisor-pid", str(os.getpid())], cwd=ROOT,
                                         creationflags=subprocess.CREATE_NO_WINDOW,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                write_json(RUNTIME / "supervisor.json", {"pid": os.getpid(), "worker_pid": child.pid, "started": time.time(), "stage": stage})
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
            write_json(RUNTIME / "supervisor.json", {"pid": None, "worker_pid": None, "stopped": time.time(), "stage": stage})
            event("supervisor_stopped")
            _loot_end_process()


def start(stage=None, *, expected_stop=None):
    stage = stage or selected_stage()
    load_stage(stage)
    with lock("control"):
        if expected_stop is not None and (not STOP.exists() or STOP.read_text(encoding="utf-8") != expected_stop):
            raise InterruptedError("Stop request changed; development restart cancelled")
        state = read_json(RUNTIME / "supervisor.json")
        if alive(state.get("pid")):
            if state.get("stage", "scrolls-a") != stage:
                raise RuntimeError("Another stage is running; stop and verify it before switching")
            print("Already running; no duplicate launched.")
            return
        if lock_busy("worker") or lock_busy("supervisor") or live_controller_pids():
            raise RuntimeError("Existing account controller alive; stop it before restarting")
        if selected_stage() != stage or load_stage(stage)["manual_first_battle"]:
            write_json(RUNTIME / "session.json", {"stage": stage, "mission_verified": False})
        write_json(RUNTIME / "selection.json", {"stage": stage})
        STOP.unlink(missing_ok=True)
        executable = Path(sys.executable).with_name("pythonw.exe")
        env = os.environ.copy()
        env["NBR_LAUNCH_COMMAND"] = subprocess.list2cmdline([str(executable), str(ROOT / "farm.py"), "supervise", "--stage", stage])
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
            write_json(RUNTIME / "supervisor.json", {"pid": launch["ProcessId"], "worker_pid": None,
                                                      "stage": stage, "launching": True})
    # The supervisor must acquire control before it publishes its worker PID.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = read_json(RUNTIME / "supervisor.json")
        if alive(state.get("pid")) and alive(state.get("worker_pid")):
            print(f"Running independently: supervisor {state['pid']}, worker {state.get('worker_pid')}")
            return
        time.sleep(.25)
    raise RuntimeError("Supervisor did not become ready; inspect runtime/events.jsonl")


def stop():
    RUNTIME.mkdir(exist_ok=True)
    with lock("control"):
        STOP.write_text("Intentional user stop\n", encoding="utf-8")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = read_json(RUNTIME / "supervisor.json")
        if not live_controller_pids() and not lock_busy("worker") and not lock_busy("supervisor"):
            print("Stopped. Game left in its current state.")
            return
        time.sleep(.25)
    print("Stop persisted; process is finishing its bounded ADB operation.")


def status(stage=None):
    stage = stage or selected_stage()
    state = read_json(RUNTIME / "supervisor.json")
    farm = read_json(load_stage(stage)["progress"])
    print(json.dumps({"selected_stage": selected_stage(), "stage": stage,
                      "running": bool(live_controller_pids()) or lock_busy("worker") or lock_busy("supervisor"),
                      "active_pids": live_controller_pids(), "stop_requested": STOP.exists(),
                      "supervisor": state, "farm": farm,
                      "stamina_seconds_remaining": max(0, round(farm.get("stamina_until", 0) - time.time()))}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["select", "start", "stop", "status", "supervise", "worker", "observe", "step", "replay"])
    parser.add_argument("--image")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--stage", choices=STAGE_IDS)
    parser.add_argument("--supervisor-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = args.stage or selected_stage()
    if args.command == "select": select_stage(stage)
    elif args.command == "start": start(stage)
    elif args.command == "stop": stop()
    elif args.command == "status": status(stage)
    elif args.command == "supervise": supervise(stage)
    elif args.command == "worker": worker(stage, args.supervisor_pid)
    elif args.command == "replay":
        name, _, scores = Detector(stage).classify(cv2.imread(args.image))
        print(json.dumps({"screen": name, "scores": scores}, indent=2))
    elif args.command == "observe":
        device = Device()
        device.verify()
        frame = device.capture()
        name, _, scores = Detector(stage).classify(frame)
        path = ROOT / "development" / "current.png"
        path.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(path), frame)
        result = {"screen": name, "image": str(path), "pending": read_json(load_stage(stage)["progress"]).get("pending")}
        if not args.brief:
            result["scores"] = scores
        print(json.dumps(result, indent=2))
    else:
        with contextlib.ExitStack() as stack:
            with lock("control"):
                if lock_busy("supervisor") or live_controller_pids():
                    raise RuntimeError("An account controller is active; development step refused")
                stack.enter_context(lock("worker"))
                session = read_json(RUNTIME / "session.json")
                if session.get("stage") != stage:
                    write_json(RUNTIME / "session.json", {"stage": stage, "mission_verified": False})
            engine = Engine(stage)
            if args.command == "step":
                if STOP.exists():
                    raise RuntimeError("Stop flag set; use start to resume")
                engine.tick()
                print(json.dumps(engine.data, indent=2))
            else:
                frame, name, _, scores = engine.observe()
                path = ROOT / "development" / "current.png"
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
