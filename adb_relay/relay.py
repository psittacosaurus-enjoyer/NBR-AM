"""NBR ADB Relay: a standalone, read-only bridge to one BlueStacks instance.

Independent of the NBR Automation Manager: stdlib only, imports nothing from
NBR and reads nothing outside this folder. Another process (for example an AI
workspace that can only see files) drops a JSON request into ``requests/``;
the relay runs one allowlisted, read-only ADB command against the configured
device and writes the result into ``responses/``.

The relay never sends input. There is no generic shell command: only the
operations in COMMANDS exist, and each builds its own fixed argument list.
Every device command first confirms the Android ID in ``relay.local.json``.

Request  requests/<id>.json   {"command": "screencap", ...options}
Response responses/<id>.json  {"id", "ok", "command", "elapsed_ms", "result" | "error"}
         responses/<id>.png   (screencap only)
Stop     create a file named STOP in this folder, or close the window.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

VERSION = 1
HERE = Path(__file__).resolve().parent
REQUESTS = HERE / "requests"
RESPONSES = HERE / "responses"
CONFIG = HERE / "relay.local.json"
STATUS = HERE / "status.json"
STOP = HERE / "STOP"
LOCK = HERE / "relay.lock"

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PROP_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,92}$")
IDENTITY_TTL = 20.0
RESPONSE_TTL = 15 * 60
HEARTBEAT = 2.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

log = logging.getLogger("adb_relay")


class RelayError(Exception):
    pass


def load_config():
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RelayError(f"Missing {CONFIG.name}; copy relay.example.json and fill it in") from None
    for key in ("adb_path", "serial", "android_id"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise RelayError(f"{CONFIG.name} needs a non-empty '{key}'")
    config.setdefault("poll_seconds", 0.2)
    return config


def write_atomic(path: Path, data: bytes):
    temporary = path.with_name(path.name + ".part")
    with open(temporary, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value):
    write_atomic(path, json.dumps(value, indent=2).encode("utf-8"))


class SingleInstance:
    """Hold an OS lock on relay.lock for the life of the process."""

    def __enter__(self):
        self.stream = open(LOCK, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RelayError("Another ADB relay is already running from this folder") from None
        return self

    def __exit__(self, *exc):
        self.stream.close()


class Device:
    def __init__(self, config):
        self.adb = config["adb_path"]
        self.serial = config["serial"]
        self.android_id = config["android_id"].strip()
        self.verified_at = float("-inf")

    def run(self, *args, timeout=15, binary=False, serial=True):
        command = [self.adb, *(["-s", self.serial] if serial else []), *args]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout,
                                    creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RelayError(f"adb {' '.join(args[:2])} timed out after {timeout}s") from None
        except OSError as exc:
            raise RelayError(f"Cannot run adb at {self.adb}: {exc}") from None
        if result.returncode:
            detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
            raise RelayError(f"adb {' '.join(args[:2])} failed: {detail[-500:]}")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def connect(self):
        if ":" in self.serial:
            output = self.run("connect", self.serial, serial=False, timeout=10)
            if "connected" not in output.lower():
                raise RelayError(f"adb connect {self.serial}: {output.strip()}")

    def verify(self, force=False):
        if not force and time.monotonic() - self.verified_at < IDENTITY_TTL:
            return
        try:
            observed = self.run("shell", "settings", "get", "secure", "android_id", timeout=10).strip()
        except RelayError:
            self.connect()  # One reconnect for a dropped TCP endpoint, then re-check.
            observed = self.run("shell", "settings", "get", "secure", "android_id", timeout=10).strip()
        if observed != self.android_id:
            self.verified_at = float("-inf")
            raise RelayError(f"Wrong device: Android ID {observed!r} is not the configured {self.android_id!r}")
        self.verified_at = time.monotonic()


def foreground(device):
    text = device.run("shell", "dumpsys", "activity", "activities", timeout=15)
    for line in text.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line or "ResumedActivity:" in line:
            match = re.search(r"\b([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", line)
            if match:
                return {"package": match.group(1), "activity": match.group(2), "line": line.strip()}
    return {"package": None, "activity": None, "line": None}


def screencap(device, request_id):
    started = time.perf_counter()
    data = device.run("exec-out", "screencap", "-p", binary=True, timeout=20)
    capture_ms = round((time.perf_counter() - started) * 1000, 1)
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 33:
        raise RelayError("screencap did not return a PNG image")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    image = RESPONSES / f"{request_id}.png"
    write_atomic(image, data)
    return {"image": image.name, "width": width, "height": height, "bytes": len(data), "capture_ms": capture_ms}


def timing(device, request):
    count = request.get("count", 5)
    if type(count) is not int or not 1 <= count <= 30:
        raise RelayError("timing count must be an integer from 1 to 30")
    samples = {"screencap_raw_ms": [], "screencap_png_ms": [], "foreground_ms": [], "identity_ms": []}
    for _ in range(count):
        for key, call in (("screencap_raw_ms", lambda: device.run("exec-out", "screencap", binary=True, timeout=20)),
                          ("screencap_png_ms", lambda: device.run("exec-out", "screencap", "-p", binary=True, timeout=20)),
                          ("foreground_ms", lambda: device.run("shell", "dumpsys", "activity", "activities")),
                          ("identity_ms", lambda: device.run("shell", "settings", "get", "secure", "android_id"))):
            started = time.perf_counter()
            call()
            samples[key].append(round((time.perf_counter() - started) * 1000, 1))
    return {key: {"samples": values, "median": sorted(values)[len(values) // 2]} for key, values in samples.items()}


def getprop(device, request):
    key = request.get("key")
    if not isinstance(key, str) or not PROP_PATTERN.match(key):
        raise RelayError("getprop needs a property name like ro.product.model")
    return {"key": key, "value": device.run("shell", "getprop", key).strip()}


# Every operation the relay can perform. None of them sends input.
COMMANDS = {
    "ping": lambda device, request, request_id: {"pong": True, "version": VERSION},
    "devices": lambda device, request, request_id: {"output": device.run("devices", "-l", serial=False)},
    "identity": lambda device, request, request_id: {"android_id": device.android_id, "verified": True,
                                                     "serial": device.serial},
    "foreground": lambda device, request, request_id: foreground(device),
    "screencap": lambda device, request, request_id: screencap(device, request_id),
    "window_size": lambda device, request, request_id: {"output": device.run("shell", "wm", "size").strip()},
    "getprop": lambda device, request, request_id: getprop(device, request),
    "timing": lambda device, request, request_id: timing(device, request),
}
NEEDS_IDENTITY = {"identity", "foreground", "screencap", "window_size", "getprop", "timing"}


def handle(device, path: Path, state):
    request_id = path.stem
    working = path.with_suffix(".working")
    try:
        os.replace(path, working)
    except OSError:
        return  # Another pass already claimed it, or the writer is still finishing.
    started = time.perf_counter()
    response = {"id": request_id, "ok": False, "received": time.time()}
    try:
        if not ID_PATTERN.match(request_id):
            raise RelayError("Request file names may use only letters, digits, '-' and '_'")
        try:
            request = json.loads(working.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RelayError(f"Unreadable request: {exc}") from None
        command = request.get("command") if isinstance(request, dict) else None
        response["command"] = command
        if command not in COMMANDS:
            raise RelayError(f"Unknown command {command!r}; allowed: {', '.join(sorted(COMMANDS))}")
        if command in NEEDS_IDENTITY:
            device.verify(force=command == "identity")
        response["result"] = COMMANDS[command](device, request, request_id)
        response["ok"] = True
    except RelayError as exc:
        response["error"] = str(exc)
    except Exception as exc:  # A bad request must never stop the relay.
        log.exception("Unexpected failure handling %s", request_id)
        response["error"] = f"{type(exc).__name__}: {exc}"
    response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if ID_PATTERN.match(request_id):
        write_json(RESPONSES / f"{request_id}.json", response)
    try:
        working.unlink()
    except OSError:
        pass
    state["handled"] += 1
    state["last_request"] = {"id": request_id, "command": response.get("command"),
                             "ok": response["ok"], "time": time.time(), "elapsed_ms": response["elapsed_ms"]}
    log.info("%s %s ok=%s %.0fms %s", request_id, response.get("command"), response["ok"],
             response["elapsed_ms"], response.get("error", ""))


def prune():
    cutoff = time.time() - RESPONSE_TTL
    for path in RESPONSES.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def heartbeat(device, state):
    write_json(STATUS, {
        "running": True, "pid": os.getpid(), "version": VERSION, "time": time.time(),
        "started": state["started"], "serial": device.serial, "android_id": device.android_id,
        "identity_verified_seconds_ago": (None if device.verified_at == float("-inf")
                                          else round(time.monotonic() - device.verified_at, 1)),
        "handled": state["handled"], "last_request": state["last_request"],
        "commands": sorted(COMMANDS),
    })


def main():
    REQUESTS.mkdir(exist_ok=True)
    RESPONSES.mkdir(exist_ok=True)
    handler = RotatingFileHandler(HERE / "relay.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)
    try:
        config = load_config()
        with SingleInstance():
            try:
                STOP.unlink()
            except FileNotFoundError:
                pass
            device = Device(config)
            try:
                device.connect()
                device.verify(force=True)
                log.info("Verified %s (Android ID %s)", device.serial, device.android_id)
            except RelayError as exc:
                log.warning("Device not verified yet: %s (requests will retry)", exc)
            state = {"started": time.time(), "handled": 0, "last_request": None}
            log.info("ADB relay v%s watching %s  (create STOP or close this window to quit)", VERSION, REQUESTS)
            next_beat = next_prune = 0.0
            try:
                while not STOP.exists():
                    for path in sorted(REQUESTS.glob("*.json"), key=lambda p: p.stat().st_mtime):
                        handle(device, path, state)
                    now = time.monotonic()
                    if now >= next_beat:
                        heartbeat(device, state)
                        next_beat = now + HEARTBEAT
                    if now >= next_prune:
                        prune()
                        next_prune = now + 60
                    time.sleep(config["poll_seconds"])
            except KeyboardInterrupt:
                pass
            finally:
                write_json(STATUS, {"running": False, "pid": os.getpid(), "time": time.time(),
                                    "handled": state["handled"]})
                log.info("ADB relay stopped")
    except RelayError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
