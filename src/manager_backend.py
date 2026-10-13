"""Small, synchronous GUI adapter for the existing acc1 controller.

The GUI runs these calls in background threads. Only an explicit command()
may change controller state; monitoring never connects or launches a device.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess

import farm


class ControllerError(RuntimeError):
    """An actionable controller or monitoring error."""


def _process_started(pid):
    """Read a Windows process creation time without controlling the process."""
    if not pid:
        return None
    kernel = ctypes.windll.kernel32
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600
    finally:
        kernel.CloseHandle(handle)


class ControllerBackend:
    def __init__(self, root=farm.ROOT):
        self.root = Path(root).resolve()
        self.python = self.root / ".venv" / "Scripts" / "python.exe"
        self._event_identity = None
        self._event_offset = 0
        self._event_partial = b""
        self._event_discarding = False

    def stages(self):
        return [{"id": stage, "name": farm.load_stage(stage)["name"]} for stage in farm.STAGE_IDS]

    def loot_snapshot(self, snapshot, session_id=None, limit=100, offset=0):
        """Read durable loot records without launching a collector or controlling farming."""
        from loot_tracking import read_dashboard
        return read_dashboard(self.root, snapshot, session_id=session_id, limit=limit, offset=offset)

    def _invoke(self, action, stage=None, timeout=45):
        args = [str(self.python), str(self.root / "src" / "farm.py"), action]
        if stage is not None:
            args.extend(["--stage", stage])
        try:
            result = subprocess.run(args, cwd=self.root, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            # subprocess.run bounds only this short-lived CLI client. The
            # controller's independent supervisor/worker are never terminated.
            raise ControllerError(f"Controller {action} timed out; refresh status before retrying.") from exc
        except OSError as exc:
            raise ControllerError(f"Cannot run the existing controller: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or f"Exit code {result.returncode}").strip()
            raise ControllerError(f"Controller {action} failed: {detail[-2000:]}")
        return result.stdout.strip()

    def command(self, action, stage=None):
        if action not in {"start", "stop", "select"}:
            raise ValueError("Only start, stop, and select commands are supported")
        if stage is not None and stage not in farm.STAGE_IDS:
            raise ValueError("Unknown farming stage")
        if action in {"start", "select"} and stage is None:
            raise ValueError("Select a farming stage first")
        if action == "stop":
            stage = None  # Existing stop is account-wide, regardless of selection.
        return self._invoke(action, stage)

    def snapshot(self):
        try:
            state = json.loads(self._invoke("status", timeout=15))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ControllerError("Controller returned invalid status JSON") from exc
        if not isinstance(state, dict):
            raise ControllerError("Controller status must be an object")
        for key in ("running", "stop_requested"):
            if not isinstance(state.get(key), bool):
                raise ControllerError(f"Controller status is missing a valid {key}")
        for key in ("supervisor", "farm"):
            if not isinstance(state.get(key), dict):
                raise ControllerError(f"Controller status is missing a valid {key}")
        if state.get("selected_stage") not in farm.STAGE_IDS or state.get("stage") not in farm.STAGE_IDS:
            raise ControllerError("Controller returned an unknown stage")
        pids = state.get("active_pids")
        if not isinstance(pids, list) or any(not self._valid_pid(pid) for pid in pids):
            raise ControllerError("Controller returned invalid process IDs")
        supervisor = state["supervisor"]
        progress = state["farm"]
        supervisor_pid = supervisor.get("pid")
        worker_pids = (supervisor.get("worker_pid"), progress.get("worker_pid"))
        for pid in (supervisor_pid, *worker_pids):
            if pid is not None and not self._valid_pid(pid):
                raise ControllerError("Controller returned an invalid process ID")
        state["supervisor_running"] = bool(farm.alive(supervisor_pid))
        state["worker_running"] = any(farm.alive(pid) for pid in worker_pids if pid)
        started = _process_started(supervisor_pid) if state["supervisor_running"] else None
        if started is None and state["worker_running"]:
            started = next((_process_started(pid) for pid in reversed(worker_pids)
                            if pid and farm.alive(pid)), None)
        if started is None:
            started = supervisor.get("started")
        state["started_at"] = started if isinstance(started, (int, float)) and started > 0 else None
        return state

    @staticmethod
    def _valid_pid(pid):
        return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0

    def connection(self):
        result = {"available": False, "instance": f"acc1 · Pie64 · {farm.SERIAL}", "detail": ""}
        try:
            if not farm.ADB.is_file():
                raise ControllerError("BlueStacks ADB executable is unavailable")
            # An ADB client may otherwise start a server as a side effect. A
            # passive socket probe first ensures a server is already listening.
            try:
                with socket.create_connection(("127.0.0.1", 5037), timeout=.5):
                    pass
            except OSError:
                result["detail"] = "ADB server is unavailable. Open BlueStacks acc1."
                return result
            farm.Device().verify()  # Read-only mapping and Android identity check.
            result.update(available=True, detail="Connected; acc1 identity verified")
        except Exception as exc:
            result["detail"] = f"Connection unavailable: {str(exc)[:500]}"
        return result

    def read_events(self):
        """Tail bounded chunks, carrying incomplete lines across polls/rotation."""
        path = self.root / "runtime" / "events.jsonl"
        try:
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                initial = identity != self._event_identity or info.st_size < self._event_offset
                if initial:
                    self._event_identity = identity
                    self._event_offset = max(0, info.st_size - 65_536)
                    self._event_partial = b""
                    self._event_discarding = self._event_offset > 0
                stream.seek(self._event_offset)
                data = stream.read(131_072)
                self._event_offset = stream.tell()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ControllerError(f"Cannot read activity log: {exc}") from exc
        if self._event_discarding:
            newline = data.find(b"\n")
            if newline < 0:
                return []
            data = data[newline + 1:]
            self._event_discarding = False
        lines = (self._event_partial + data).split(b"\n")
        self._event_partial = lines.pop()
        if len(self._event_partial) > 131_072:
            # Skip an oversized incomplete record without later treating its
            # remaining fragment as a fresh JSON event.
            self._event_partial = b""
            self._event_discarding = True
        if initial:
            lines = lines[-100:]
        return [self._format_event(line) for line in lines if line.strip()]

    @staticmethod
    def _format_event(line):
        try:
            event = json.loads(line.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("Event must be an object")
            timestamp = datetime.fromtimestamp(float(event["time"])).strftime("%Y-%m-%d %H:%M:%S")
            name = str(event.get("event", "event")).replace("_", " ")
            details = "; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in event.items()
                                if key not in {"time", "event"})
            return f"[{timestamp}] {name}" + (f" — {details}" if details else "")
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, OSError):
            return "[activity log] Unreadable event: " + line.decode("utf-8", errors="replace")[:240]
