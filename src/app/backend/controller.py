"""Small, synchronous GUI adapter for the independent farming controller.

The GUI runs these calls in background threads. Explicit controls may change
controller state; monitoring never connects or launches a device.
"""
from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import closing, ExitStack
from ctypes import wintypes
from datetime import datetime
import json
import ntpath
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from automation import adb as bluestacks
from automation import controller as farm


class ControllerError(RuntimeError):
    """An actionable controller or monitoring error."""


def _command_arguments(command):
    """Use Windows' quoting rules; never execute a discovered command line."""
    if not command:
        return []
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    args = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not args:
        raise ControllerError("Cannot inspect automation process arguments")
    try:
        return [args[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(args)


def _terminate_verified_process(process):
    """Check creation identity and terminate using the SAME handle (no PID race)."""
    pid = process["pid"]
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise ControllerError("Refusing to terminate the Automation Manager or an invalid process")
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
    if not handle:
        if kernel.GetLastError() == 87:  # The process already exited.
            return
        raise ControllerError(f"Cannot safely open automation process {pid} for shutdown")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise ControllerError(f"Cannot verify automation process {pid}")
        ticks = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
        if process["identity"] != f"{pid}:{ticks}":
            return  # Recycled PID; it no longer identifies our process.
        if kernel.WaitForSingleObject(handle, 0) == 0:
            return
        if not kernel.TerminateProcess(handle, 1):
            raise ControllerError(f"Cannot terminate verified automation process {pid}")
    finally:
        kernel.CloseHandle(handle)


class _ProcessLifetime:
    """Retain read-only lifetime evidence after a verified parent exits."""

    def __init__(self, process):
        self.kernel = ctypes.windll.kernel32
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x1000 | 0x00100000, False, process["pid"])
        if not self.handle:
            raise ControllerError(f'Cannot retain lifetime evidence for automation process {process["pid"]}')
        try:
            created, _ = self.times()
            if process["identity"] != f'{process["pid"]}:{created}':
                raise ControllerError("Automation process changed while verifying shutdown ownership")
        except Exception:
            self.close()
            raise

    def times(self):
        values = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(value) for value in values)):
            raise ControllerError("Cannot verify automation parent process lifetime")
        return tuple((value.dwHighDateTime << 32) | value.dwLowDateTime for value in values[:2])

    def contains(self, child):
        created, exited = self.times()
        child_created = int(child["identity"].split(":", 1)[1])
        return created <= child_created and (not exited or child_created <= exited)

    def alive(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise ControllerError("Cannot verify automation process exit")
        return result == 258

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


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
        from app.backend.loot import read_dashboard
        from app.gui.loot import enrich_loot_items
        data = read_dashboard(self.root, snapshot, session_id=session_id, limit=limit, offset=offset)
        data['items'] = enrich_loot_items(self.root, data.get('items') or [])
        if session_id is None and snapshot.get('running'):
            progress = snapshot.get('farm') or {}
            health = progress.get('recording_health')
            recording = (progress.get('run') or {}).get('recording')
            health = dict(health) if isinstance(health, dict) else {}
            if isinstance(recording, dict):
                resolved = (recording.get('state') == 'missing_screenshot'
                            and recording.get('missing_evidence_acknowledged') is True)
                health.update(state=recording.get('state'),
                              error=(None if resolved else recording.get('error')) or health.get('error'),
                              next_attempt_at=recording.get('next_attempt_at'))
            if health:
                # Live farmer failures may predate collector/database startup.
                # Keep them separate from persisted historical-session health.
                data['farm_recording_health'] = health
        return data

    def _invoke(self, action, stage=None, timeout=45, requested_generation=None,
                *, instance_key=None, instance_name=None, endpoint=None,
                offline=False, completion_id=None, reason=None):
        args = [str(self.python), str(self.root / "src" / "farm.py"), action]
        if stage is not None:
            if action in {"boss-swap", "early-exit", "one-team", "map13-team2"}:
                args.extend(["--enabled", "on" if stage else "off"])
            elif action == "ninja-mode":
                args.extend(["--mode", stage])
            else:
                args.extend(["--minutes" if action == "stamina-duration" else "--stage", str(stage)])
        if requested_generation is not None:
            args.extend(["--stop-generation", requested_generation])
        for option, value in (("--instance-key", instance_key),
                              ("--instance-name", instance_name), ("--endpoint", endpoint),
                              ("--completion-id", completion_id), ("--reason", reason)):
            if value is not None:
                args.extend([option, str(value)])
        if offline:
            args.append("--offline")
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
        if action not in {"start", "stop", "restart", "end-stamina-wait", "stamina-duration", "boss-swap", "early-exit", "one-team", "map13-team2", "ninja-mode"}:
            raise ValueError("Unsupported manager command")
        if action == "ninja-mode":
            if stage not in farm.NINJA_EXIT_MODES:
                raise ValueError("Choose Stage 11 Exit, Stage 16 Exit or Stage 16 Strategy")
            return self._invoke(action, stage)
        if action in {"boss-swap", "early-exit", "one-team", "map13-team2"}:
            if type(stage) is not bool:
                raise ValueError("Ninja Road option must be enabled or disabled")
            return self._invoke(action, stage)
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

    @staticmethod
    def _recording_controller_active(snapshot):
        return bool(snapshot.get("running") or snapshot.get("supervisor_running")
                    or snapshot.get("worker_running") or snapshot.get("active_pids"))

    def resolve_saved_input(self, stage, *, expected_instance_key=None):
        """Resolve one guarded cross-stage input latch without starting farming."""
        if stage not in ("universal", "ninja_road"):
            raise ControllerError("Saved input recovery is available only for Universal or Ninja Road farming.")
        before = self.snapshot()
        target = before.get("instance")
        key = target.get("key") if isinstance(target, dict) else None
        if (not isinstance(expected_instance_key, str) or not expected_instance_key
                or not isinstance(key, str) or key != expected_instance_key):
            raise ControllerError("The selected instance changed; refresh status before resolving the saved input.")
        if before.get("instance_error") or before.get("instance_selection_required"):
            raise ControllerError("A verified saved instance is required before resolving the saved input.")
        if self._recording_controller_active(before):
            raise ControllerError("Stop the controller before resolving the saved input.")
        result = self._invoke(
            "resolve-saved-input", stage, timeout=75, instance_key=key,
            reason=("User selected Resolve input in Automation Manager after Start reported an existing "
                    "saved input; resume Universal only from the currently verified mission preparation screen."
                    if stage == "universal" else
                    "User selected Resolve input in Automation Manager after Start reported an existing "
                    "saved input; resume Ninja Road only from the currently verified Ninja Road Home screen."))
        after = self.snapshot()
        refreshed = after.get("instance")
        refreshed_key = refreshed.get("key") if isinstance(refreshed, dict) else None
        if self._recording_controller_active(after) or refreshed_key != key:
            raise ControllerError(
                "Saved input recovery returned without a verified stopped instance; refresh status before continuing.")
        return result.strip() or "Saved input resolved. Use Start to resume farming."

    def _shutdown_inventory(self):
        # CIM is read-only. Include creation ticks in the same observation as
        # command/parent metadata so a recycled PID cannot inherit ownership.
        script = """$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);
            $rows=@(Get-CimInstance Win32_Process | ForEach-Object {
            [pscustomobject]@{pid=[int]$_.ProcessId; parent=[int]$_.ParentProcessId;
              identity=if ($_.CreationDate) { \"$($_.ProcessId):$($_.CreationDate.ToFileTimeUtc())\" } else { $null };
              executable=$_.ExecutablePath; command=$_.CommandLine}
            }); ConvertTo-Json -InputObject $rows -Compress -Depth 3"""
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ControllerError("Cannot enumerate automation processes for shutdown")
            rows = json.loads(result.stdout)
            if not isinstance(rows, list):
                raise ValueError("Invalid process inventory")
            for row in rows:
                if not isinstance(row, dict) or not self._valid_pid(row.get("pid")) or type(row.get("parent")) is not int:
                    # PID 0 is the system idle process, never an automation.
                    if isinstance(row, dict) and row.get("pid") == 0:
                        continue
                    raise ValueError("Invalid process inventory entry")
                row["args"] = _command_arguments(row.get("command"))
                observed = row.get("identity")
                try:
                    identity = farm.process_identity(row["pid"])[0]
                    # WMI truncates creation timestamps to microseconds; native
                    # claims retain 100ns ticks. Reject metadata from another
                    # lifetime before adopting the native, precise identity.
                    row["identity"] = (identity if observed and
                                       int(identity.split(":", 1)[1]) // 10 ==
                                       int(observed.split(":", 1)[1]) // 10 else None)
                except (OSError, ValueError):
                    row["identity"] = None
            return [row for row in rows if row["pid"] > 0]
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
            raise ControllerError(f"Cannot verify automation process inventory: {exc}") from exc

    def _automation_role(self, process):
        normalize = lambda value: ntpath.normcase(ntpath.normpath(str(value)))
        executable = ntpath.basename(process.get("executable") or "").casefold()
        args = process.get("args") or []
        if executable in {"python.exe", "pythonw.exe"} and len(args) >= 2:
            script = normalize(args[1])
            if script == normalize(self.root / "src" / "farm.py") and len(args) >= 3:
                if args[2] in {"start", "stop", "status", "supervise", "worker", "observe", "step",
                               "restart", "end-stamina-wait", "stamina-duration", "select-instance"}:
                    return args[2]
            if script == normalize(self.root / "scripts" / "loss_notify.py"):
                return "notification"
        if executable == "powershell.exe":
            for index, arg in enumerate(args[:-1]):
                if arg.casefold() == "-file" and normalize(args[index + 1]) == normalize(self.root / "scripts" / "loss_ocr.ps1"):
                    return "ocr"
        return None

    @staticmethod
    def _private_adb_client(process):
        # ADB may auto-spawn a shared server. Only ordinary attached clients are
        # eligible, and only after their automation parent has been verified.
        if ntpath.basename(process.get("executable") or "").casefold() not in {"adb.exe", "hd-adb.exe"}:
            return False
        args = [arg.casefold() for arg in process.get("args", [])[1:]]
        index = 0
        while index < len(args) and args[index] in {"-h", "-p", "-s"}:
            index += 2
        return index < len(args) and args[index] in {"shell", "exec-out", "connect", "get-state", "devices"}

    def _protected_child(self, process):
        executable = ntpath.basename(process.get("executable") or "").casefold()
        adb_server = False
        if executable in {"adb.exe", "hd-adb.exe"}:
            args = [arg.casefold() for arg in process.get("args", [])[1:]]
            index = 0
            while index < len(args):
                if args[index] in {"-h", "-p", "-l", "-s"}:
                    index += 2
                elif args[index] == "-a":
                    index += 1
                else:
                    break
            adb_server = args[index:index + 2] in (["fork-server", "server"], ["nodaemon", "server"], ["server", "nodaemon"])
        return (executable.startswith(("hd-", "bluestacks")) and executable != "hd-adb.exe"
                or adb_server
                or executable in {"bstksvc.exe", "bstkssvc.exe", "services.exe", "svchost.exe", "system",
                                  "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "smss.exe"})

    def _shutdown_owned(self, inventory, tracked, unclassified, retained):
        def remember(item, role, collection):
            key = item["pid"], item["identity"]
            if key not in collection:
                lifetime = _ProcessLifetime(item)
                retained.callback(lifetime.close)
                collection[key] = dict(item, role=role, lifetime=lifetime)
            return collection[key]

        def valid_identity(item):
            identity = item.get("identity")
            return (isinstance(identity, str) and identity.startswith(f'{item["pid"]}:')
                    and identity.split(":", 1)[1].isdigit())

        owned = {}
        for item in inventory:
            if item["pid"] == os.getpid():
                continue
            role = self._automation_role(item)
            identity = item.get("identity")
            if role or (item["pid"], identity) in tracked:
                if not valid_identity(item):
                    raise ControllerError(f'Cannot establish ownership of automation process {item["pid"]}')
                owned[item["pid"]] = remember(item, role, tracked)
        while True:
            count = len(tracked) + len(unclassified)
            for item in inventory:
                key = item["pid"], item.get("identity")
                if key in tracked or key in unclassified or item["pid"] == os.getpid() or self._protected_child(item):
                    continue
                parents = [parent for parent in [*tracked.values(), *unclassified.values()]
                           if parent["pid"] == item["parent"]]
                if not parents:
                    continue
                if not valid_identity(item):
                    raise ControllerError(f'Cannot establish ownership of automation child process {item["pid"]}')
                # Retained handles supply exact exit times even if a worker
                # spawns a child between the last inventory and termination.
                parent = next((parent for parent in parents if parent["lifetime"].contains(item)), None)
                if parent:
                    if parent["role"] != "unclassified" and self._private_adb_client(item):
                        owned[item["pid"]] = remember(item, "adb-client", tracked)
                    else:
                        remember(item, "unclassified", unclassified)
            if len(tracked) + len(unclassified) == count:
                break
        return owned

    def shutdown(self, progress=None, *, timeout=10):
        """Stop safely, clean verified private processes, or leave the GUI open.

        Normal window close never calls this method. All waits and subprocesses
        are bounded; callers run it off the GUI thread.
        """
        with ExitStack() as retained:
            return self._shutdown(progress, timeout, retained)

    def _shutdown(self, progress, timeout, retained):
        report = progress or (lambda message: None)
        tracked = {}
        unclassified = {}
        records = list(farm.recorded_processes())
        inventory_errors = []

        def capture():
            inventory = self._shutdown_inventory()
            return inventory, self._shutdown_owned(inventory, tracked, unclassified, retained)

        def verify_claims(inventory, remaining):
            by_pid = {item["pid"]: item for item in inventory}
            claims = [*records, *({"pid": item["pid"], "pid_identity": item["identity"]}
                                  for item in tracked.values())]
            for record in claims:
                pid, identity = record.get("pid"), record.get("pid_identity")
                if pid == os.getpid():
                    continue
                current = by_pid.get(pid)
                if current and (not identity or not current.get("identity") or current.get("identity") == identity) and pid not in remaining:
                    raise ControllerError(f"Cannot verify ownership of recorded automation process {pid}; it was left running")
                if not current and farm.process_may_be_alive(record):
                    raise ControllerError(f"Cannot verify exit of recorded automation process {pid}")

        report("Requesting the controller's safe stop procedure…")
        # Retain children before their parents exit and erase runtime records.
        try:
            capture()
        except ControllerError as exc:
            inventory_errors.append(str(exc))
        stop_error = None
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="nbr-safe-stop") as executor:
            stopping = executor.submit(self.command, "stop")
            while True:
                try:
                    stopping.result(timeout=.5)
                    break
                except FutureTimeout:
                    if stopping.done():
                        exception = stopping.exception()
                        stop_error = str(exception) if exception is not None else None
                        break
                    try:
                        capture()
                    except ControllerError as exc:
                        inventory_errors.append(str(exc))
                except Exception as exc:
                    stop_error = str(exc)
                    break
        if stop_error:
            report(f"Safe stop did not finish: {stop_error}; checking verified automation processes…")
        try:
            # Stop must run before taking its control lock. Hold the same lock
            # across fallback and verification so another Start cannot race it.
            with farm.lock("control", wait_seconds=5):
                farm.write_json(self.root / "runtime" / "stop_generation.json", {"id": uuid.uuid4().hex})
                (self.root / "runtime" / "STOP").write_text("Intentional user stop\n", encoding="utf-8")
                report("Checking remaining NBR automation processes…")
                inventory, remaining = capture()
                records.extend(farm.recorded_processes())
                verify_claims(inventory, remaining)
                deadline = time.monotonic() + max(0, min(float(timeout), 30))
                attempted = set()
                while True:
                    while remaining:
                        # Stop restart-capable parents first. Each child is
                        # handled separately; never tree-kill a PID/ancestor.
                        ordered = sorted(remaining.values(), key=lambda item:
                                         {"start": 0, "restart": 0, "supervise": 1, "worker": 2}.get(item["role"], 3))
                        for item in ordered:
                            identity = (item["pid"], item["identity"])
                            if identity not in attempted:
                                report(f'Terminating verified NBR {item["role"]} process {item["pid"]}…')
                                _terminate_verified_process(item)
                                attempted.add(identity)
                        inventory, remaining = capture()
                        verify_claims(inventory, remaining)
                        if not remaining:
                            break
                        if time.monotonic() >= deadline:
                            raise ControllerError("Shutdown incomplete; verified automation processes remain: " +
                                                  ", ".join(str(pid) for pid in sorted(remaining)))
                        time.sleep(.1)
                    for item in tracked.values():
                        if item["lifetime"].alive():
                            raise ControllerError(f'Shutdown incomplete; verified automation process {item["pid"]} remains running')
                    if unclassified:
                        item = next(iter(unclassified.values()))
                        state = ("was left running" if item["lifetime"].alive()
                                 else "exited before its descendants could be verified")
                        raise ControllerError(f'Shutdown incomplete; unclassified automation descendant {item["pid"]} {state}')
                    # Confirm known parents have exited BEFORE this last scan;
                    # then their late-spawned children cannot escape inventory.
                    known = len(tracked)
                    inventory, remaining = capture()
                    verify_claims(inventory, remaining)
                    if not remaining and len(tracked) == known and not unclassified:
                        break
                    if time.monotonic() >= deadline:
                        raise ControllerError("Shutdown incomplete; new automation descendants were detected during final verification")
                if inventory_errors:
                    raise ControllerError("Shutdown could not verify all process ownership: " + inventory_errors[0])
                if farm.lock_busy("worker") or farm.lock_busy("supervisor"):
                    raise ControllerError("Shutdown incomplete; an automation process still holds its controller lock")
                report("Shutdown verified: no managed automation processes remain.")
                return "All NBR automation processes stopped."
        except ControllerError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ControllerError(f"Shutdown could not be completed: {exc}") from exc

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
