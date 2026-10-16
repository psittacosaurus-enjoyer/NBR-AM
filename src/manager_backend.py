"""Small, synchronous GUI adapter for the independent farming controller.

The GUI runs these calls in background threads. Only an explicit command()
may change controller state; monitoring never connects or launches a device.
"""
from __future__ import annotations

import ctypes
from contextlib import closing
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import bluestacks
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
        from manager_loot import enrich_loot_items
        data = read_dashboard(self.root, snapshot, session_id=session_id, limit=limit, offset=offset)
        data['items'] = enrich_loot_items(self.root, data.get('items') or [])
        return data

    def _invoke(self, action, stage=None, timeout=45, requested_generation=None,
                *, instance_key=None, instance_name=None, endpoint=None):
        args = [str(self.python), str(self.root / "src" / "farm.py"), action]
        if stage is not None:
            args.extend(["--minutes" if action == "stamina-duration" else "--stage", str(stage)])
        if requested_generation is not None:
            args.extend(["--stop-generation", requested_generation])
        for option, value in (("--instance-key", instance_key),
                              ("--instance-name", instance_name), ("--endpoint", endpoint)):
            if value is not None:
                args.extend([option, str(value)])
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

    def prepare_command(self, action, stage=None):
        # Capture at the user's click, before a background thread or CLI starts.
        generation = (farm.read_json(self.root / "runtime/stop_generation.json").get("id", "")
                      if action in {"start", "restart"} else None)
        return lambda: self.command(action, stage, requested_generation=generation)

    def command(self, action, stage=None, *, requested_generation=None):
        if action not in {"start", "stop", "restart", "end-stamina-wait", "stamina-duration"}:
            raise ValueError("Unsupported manager command")
        if action == "stamina-duration":
            if type(stage) is not int or stage not in (5, 10, 15, 20, 25):
                raise ValueError("Stamina duration must be 5, 10, 15, 20, or 25 minutes")
            return self._invoke(action, stage)
        if action == "start" and stage is None:
            stage = "universal"
        if stage is not None and stage not in farm.STAGE_IDS:
            raise ValueError("Unknown farming stage")
        if action in {"stop", "end-stamina-wait"}:
            stage = None  # Existing stop is account-wide, regardless of selection.
        if action == "restart":
            return self._invoke(action, stage, timeout=150, requested_generation=requested_generation)
        if action == "start" and requested_generation is not None:
            return self._invoke(action, stage, requested_generation=requested_generation)
        if action == "stop":
            return self._invoke(action, timeout=75)
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
        state["supervisor_running"] = farm.record_alive(supervisor)
        worker_records = (supervisor, progress)
        state["worker_running"] = any(farm.record_alive(record, "worker_pid") for record in worker_records)
        started = _process_started(supervisor_pid) if state["supervisor_running"] else None
        if started is None and state["worker_running"]:
            started = next((_process_started(record["worker_pid"]) for record in reversed(worker_records)
                            if farm.record_alive(record, "worker_pid")), None)
        if started is None:
            started = supervisor.get("started")
        state["started_at"] = started if isinstance(started, (int, float)) and started > 0 else None
        state['session_completed_missions'] = self.session_completed_missions(state)
        return state

    def session_completed_missions(self, snapshot):
        """Count durable completion events for this farming process, including recognition failures."""
        identity = (snapshot.get('supervisor', {}).get('pid_identity')
                    or snapshot.get('farm', {}).get('worker_pid_identity'))
        path = self.root / 'runtime/loot/loot.sqlite3'
        if not identity or not path.is_file():
            return None
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
                row = db.execute('SELECT COUNT(c.completion_id) FROM sessions s '
                                 'LEFT JOIN completions c ON c.session_id=s.session_id '
                                 'WHERE s.process_identity=? GROUP BY s.session_id', (identity,)).fetchone()
                return row[0] if row else None
        except (sqlite3.Error, OSError):
            return None

    @staticmethod
    def _valid_pid(pid):
        return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0

    def _instance_state(self):
        """Ask the controller for its effective target, including older active workers."""
        try:
            state = json.loads(self._invoke("status", timeout=15))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ControllerError("Controller returned invalid instance status") from exc
        if not isinstance(state, dict) or type(state.get("running")) is not bool:
            raise ControllerError("Controller returned invalid instance status")
        return state

    def instances(self):
        discovered = bluestacks.discover()
        entries = [item.to_dict() for item in discovered["instances"]]
        detail = discovered.get("detail", "")
        try:
            state = self._instance_state()
        except ControllerError as exc:
            return dict(instances=entries, selected_key=None, active_key=None,
                        locked=True, detail=f"{detail} {exc}".strip())
        target = state.get("instance")
        key = target.get("key") if isinstance(target, dict) else None
        if target and key and all(item["key"] != key for item in entries):
            # A disconnected saved/active target remains visible; never silently
            # substitute a different discovered device.
            entries.append({**target, "status": "unavailable",
                            "connection_status": "Disconnected",
                            "detail": state.get("instance_error") or "Saved instance is unavailable."})
        return dict(instances=entries, selected_key=key,
                    active_key=key if state["running"] else None,
                    locked=bool(state.get("instance_locked", state["running"])),
                    selection_required=bool(state.get("instance_selection_required", not key)),
                    detail=" ".join(filter(None, (detail, state.get("instance_error", "")))))

    def apply_instance(self, key):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Select an instance first")
        # The CLI re-resolves and verifies under the same control lock used by
        # Start. A stale GUI status cannot allow switching a running process.
        return self._invoke("select-instance", instance_key=key)

    def manual_instance(self, name, endpoint=""):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Enter an instance name or identifier")
        return self._invoke("select-instance", instance_name=name.strip(), endpoint=endpoint.strip())

    def connection(self):
        result = {"available": False, "instance": "Select an instance", "detail": ""}
        try:
            state = self._instance_state()
            data = state.get("instance")
            if not data:
                result["detail"] = state.get("instance_error") or "Open Instance to select your BlueStacks instance."
                return result
            target = bluestacks.Instance.from_dict(data)
            result["instance"] = target.display_name
            bluestacks.verify_identity(target)  # Passive ADB transport; never connects or launches.
            result.update(available=True, detail=f"Connected; {target.display_name} identity verified")
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
