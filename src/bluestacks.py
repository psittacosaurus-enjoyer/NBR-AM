"""Read-only BlueStacks discovery and explicit, identity-checked ADB targeting.

Monitoring talks only to an already running local ADB server. It never invokes
ADB, opens an emulator transport, or changes BlueStacks configuration. Only an
explicit ``verify_identity(..., connect=True)`` may connect an ADB transport.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import ctypes
from ctypes import wintypes
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess


@dataclass(frozen=True)
class Instance:
    config_path: str
    instance_id: str
    display_name: str
    adb_path: str
    endpoint: str
    android_id: str
    status: str = "unavailable"
    connection_status: str = "unavailable"
    detail: str = ""

    @property
    def key(self):
        # A friendly rename must not invalidate a saved selection. Manual IDs
        # include the endpoint because no configuration supplies an internal ID.
        path = os.path.normcase(os.path.abspath(self.config_path)) if self.config_path else "manual"
        return hashlib.sha256((path + "\0" + self.instance_id).encode("utf-8")).hexdigest()

    def to_dict(self):
        return {**asdict(self), "key": self.key}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Saved instance must be an object")
        fields = cls.__dataclass_fields__
        if any(not isinstance(value.get(key, ""), str) for key in fields):
            raise ValueError("Saved instance fields must be text")
        return cls(**{key: value[key] for key in fields if key in value})


def _normal_endpoint(value):
    """Accept explicit IPv4 loopback endpoints only; never remote ADB hosts."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:localhost|127(?:\.\d{1,3}){3}):\d{1,5}", value.strip(), re.I):
        raise RuntimeError("ADB address must be a local loopback host:port")
    host, port = value.strip().lower().rsplit(":", 1)
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError()
        except ValueError as exc:
            raise RuntimeError("ADB address must use a valid loopback address") from exc
    if not 1 <= int(port) <= 65535:
        raise RuntimeError("ADB port must be between 1 and 65535")
    return f"127.0.0.1:{int(port)}" if host == "localhost" else f"{host}:{int(port)}"


def _read_config(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            if key in values and values[key] != value:
                raise RuntimeError(f"BlueStacks configuration has conflicting values for {key}")
            values[key] = value
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read BlueStacks configuration: {exc}") from exc
    return values


def _registry_locations():
    """Read product registration, including custom data/install directories."""
    try:
        import winreg
    except ImportError:
        return []
    records = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(hive, "SOFTWARE", 0, winreg.KEY_READ | view) as software:
                    index = 0
                    while True:
                        try:
                            name = winreg.EnumKey(software, index)
                        except OSError:
                            break
                        index += 1
                        if not name.lower().startswith("bluestacks"):
                            continue
                        try:
                            with winreg.OpenKey(software, name) as product:
                                values = {}
                                for field in ("InstallDir", "InstallPath", "DataDir", "UserDefinedDir", "ConfigPath"):
                                    try:
                                        value = winreg.QueryValueEx(product, field)[0]
                                        if isinstance(value, str) and value:
                                            values[field] = os.path.expandvars(value)
                                    except OSError:
                                        pass
                                if values:
                                    records.append(values)
                        except OSError:
                            pass
            except OSError:
                pass
    return records


def _configuration_sources():
    """Return configuration/ADB pairs without searching unrelated user files."""
    sources = []
    records = _registry_locations()
    install_candidates = []
    for record in records:
        install = record.get("InstallDir") or record.get("InstallPath")
        if install:
            install_candidates.append(Path(install) / "HD-Adb.exe")
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            install_candidates.extend(_safe_glob(root, "BlueStacks*/HD-Adb.exe"))

    def add(path, install=None):
        path = Path(path)
        if path.name.lower() != "bluestacks.conf":
            if path.name.lower() == "engine":
                path = path.parent
            path = path / "bluestacks.conf"
        if not path.is_file():
            return
        candidates = ([Path(install) / "HD-Adb.exe"] if install else [])
        candidates.extend(candidate for candidate in install_candidates
                          if candidate.parent.name.casefold() == path.parent.name.casefold())
        # A single installation is unambiguous even with a custom data folder.
        if len(set(install_candidates)) == 1:
            candidates.extend(install_candidates)
        adb = next((str(candidate.resolve()) for candidate in candidates if candidate.is_file()), "")
        sources.append((str(path.resolve()), adb))

    for record in records:
        install = record.get("InstallDir") or record.get("InstallPath")
        for field in ("ConfigPath", "UserDefinedDir", "DataDir"):
            if record.get(field):
                add(record[field], install)
    explicit = os.environ.get("NBR_BLUESTACKS_CONFIG")
    if explicit:
        add(os.path.expandvars(explicit))
    data = os.environ.get("ProgramData")
    if data:
        for path in _safe_glob(data, "BlueStacks*/bluestacks.conf"):
            add(path)
    unique = {}
    for path, adb in sources:
        key = os.path.normcase(path)
        if key not in unique or adb:
            unique[key] = (path, adb)
    return list(unique.values())


def _safe_glob(root, pattern):
    try:
        return list(Path(root).glob(pattern))
    except (OSError, ValueError):
        return []


def _configured_instances(path, adb):
    config = _read_config(path)
    ids = sorted({match.group(1) for key in config
                  if (match := re.match(r"^bst\.instance\.([^.]+)\.", key))})
    instances = []
    for instance_id in ids:
        prefix = f"bst.instance.{instance_id}."
        name = config.get(prefix + "display_name", "").strip() or instance_id
        identity = config.get(prefix + "android_id", "").strip()
        endpoint, issue = "", ""
        try:
            endpoint = _normal_endpoint("127.0.0.1:" + config.get(prefix + "adb_port", ""))
        except RuntimeError:
            issue = "BlueStacks configuration does not provide a valid ADB port."
        if not identity or identity.lower() in {"null", "none", "unknown"}:
            issue = "BlueStacks configuration does not provide a verifiable Android identity."
        if not adb:
            issue = issue or "BlueStacks ADB executable was not found."
        instances.append(Instance(str(Path(path).resolve()), instance_id, name, adb, endpoint, identity,
                                  detail=issue))
    return instances


def _server_port():
    value = os.environ.get("ANDROID_ADB_SERVER_PORT", "5037")
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise RuntimeError("The local ADB server port is invalid")
    return int(value)


def _receive(sock, length):
    data = bytearray()
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise RuntimeError("The local ADB server closed the connection")
        data.extend(part)
    return bytes(data)


def _send_service(sock, service):
    payload = service.encode("utf-8")
    sock.sendall(f"{len(payload):04x}".encode("ascii") + payload)
    status = _receive(sock, 4)
    if status != b"OKAY":
        detail = "ADB request failed"
        if status == b"FAIL":
            length = int(_receive(sock, 4), 16)
            if length <= 65536:
                detail = _receive(sock, length).decode("utf-8", "replace")
        raise RuntimeError(detail)


def _adb_request(service, endpoint=None):
    # Socket access cannot auto-start ADB, unlike `adb devices` / `adb shell`.
    try:
        with socket.create_connection(("127.0.0.1", _server_port()), timeout=1.5) as sock:
            if endpoint:
                _send_service(sock, "host:transport:" + _normal_endpoint(endpoint))
            _send_service(sock, service)
            if not endpoint:
                length = int(_receive(sock, 4), 16)
                if length > 1024 * 1024:
                    raise RuntimeError("Invalid ADB server response")
                return _receive(sock, length).decode("utf-8", "replace")
            chunks, length = [], 0
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    return b"".join(chunks).decode("utf-8", "replace").strip()
                length += len(chunk)
                if length > 1024 * 1024:
                    raise RuntimeError("ADB shell response is too large")
                chunks.append(chunk)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Local ADB connection unavailable: {exc}") from exc


def _device_states():
    states = {}
    for line in _adb_request("host:devices").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                endpoint = _normal_endpoint(parts[0])
            except RuntimeError:
                continue  # USB and unrelated network devices are not targets.
            states[endpoint] = parts[1]
    return states


def _android_identity(endpoint):
    value = _adb_request("shell:settings get secure android_id", endpoint).strip()
    if not value or value.lower() in {"null", "none", "unknown"} or "\n" in value:
        raise RuntimeError("The device did not return a valid Android identity")
    return value


def _is_bluestacks(endpoint):
    props = _adb_request("shell:getprop", endpoint)
    # Generic emulator properties (vbox/ranchu, Samsung model, etc.) are not
    # BlueStacks evidence. Require a vendor-specific property or vendor value.
    return any(re.match(r"^\[[^]]*bluestacks[^]]*\]: \[[^]]+\]$", line, re.I)
               or re.match(r"^\[(?:ro\.hardware|ro\.product\.(?:manufacturer|brand)|ro\.build\.fingerprint)\]: \[[^]]*bluestacks[^]]*\]$", line, re.I)
               for line in props.splitlines())


def _running_instances():
    """Read HD-Player command lines through Windows APIs; launch nothing.

    ADB can be disabled while BlueStacks is running. Match the actual player
    process's installation and --instance argument instead of guessing from an
    open TCP port. Inaccessible process metadata means the state is unknown.
    """
    if os.name != "nt":
        return None

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
        query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                          ctypes.POINTER(wintypes.ULONG)]
        query.restype = wintypes.LONG
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            return None
        running = set()
        unknown = False
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not found:
                return None
            while found:
                if entry.szExeFile.casefold() == "hd-player.exe":
                    handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                    if not handle:
                        unknown = True
                    else:
                        try:
                            length = wintypes.ULONG()
                            query(handle, 60, None, 0, ctypes.byref(length))
                            if not ctypes.sizeof(UnicodeString) <= length.value <= 1024 * 1024:
                                unknown = True
                            else:
                                buffer = ctypes.create_string_buffer(length.value)
                                image, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                                if (query(handle, 60, buffer, len(buffer), ctypes.byref(length)) < 0
                                        or not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))):
                                    unknown = True
                                else:
                                    value = UnicodeString.from_buffer(buffer)
                                    start = ctypes.addressof(buffer)
                                    if (not value.Buffer or value.Buffer < start
                                            or value.Buffer + value.Length > start + len(buffer)
                                            or value.Length % 2):
                                        unknown = True
                                    else:
                                        command = ctypes.wstring_at(value.Buffer, value.Length // 2)
                                        instance_id = _player_instance(command)
                                        if instance_id:
                                            running.add((os.path.normcase(str(Path(image.value).parent.resolve())),
                                                         instance_id))
                                        else:
                                            unknown = True
                        finally:
                            kernel.CloseHandle(handle)
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return None if unknown else running
    except (AttributeError, OSError, ValueError):
        return None


def _player_instance(command):
    matches = re.findall(r'(?:^|\s)(?:--instance|"--instance")(?:=|\s+)(?:"([^"\r\n]+)"|([^\s"]+))', command)
    return (matches[0][0] or matches[0][1]) if len(matches) == 1 else ""


def validate_mapping(instance):
    if not isinstance(instance, Instance):
        raise RuntimeError("A verified BlueStacks instance is required")
    endpoint = _normal_endpoint(instance.endpoint)
    if not instance.instance_id or not instance.android_id or instance.android_id.lower() in {"null", "none", "unknown"}:
        raise RuntimeError("The selected instance has no verifiable identity")
    if not instance.adb_path or not Path(instance.adb_path).is_file():
        raise RuntimeError("BlueStacks ADB executable is unavailable")
    if not instance.config_path:
        if instance.instance_id != "manual@" + endpoint:
            raise RuntimeError("Manual instance identity is invalid; validate Manual Setup again")
        return
    current = _configured_instances(instance.config_path, instance.adb_path)
    matches = [item for item in current if item.instance_id == instance.instance_id]
    if len(matches) != 1:
        raise RuntimeError("The selected BlueStacks instance no longer exists")
    mapped = matches[0]
    if mapped.endpoint != endpoint or mapped.android_id != instance.android_id:
        raise RuntimeError("BlueStacks instance mapping or Android identity changed; select it again")
    if sum(item.endpoint == endpoint for item in current) != 1:
        raise RuntimeError("BlueStacks configuration maps multiple instances to this ADB endpoint")
    for other_path, other_adb in _configuration_sources():
        if os.path.normcase(str(Path(other_path).resolve())) == os.path.normcase(str(Path(instance.config_path).resolve())):
            continue
        try:
            other_instances = _configured_instances(other_path, other_adb)
        except RuntimeError:
            continue
        if any(item.endpoint == endpoint for item in other_instances):
            raise RuntimeError("Multiple BlueStacks configurations map to the selected ADB endpoint")


def verify_identity(instance, connect=False):
    """Verify pinned configuration and Android ID before controller input."""
    validate_mapping(instance)
    if connect:
        try:
            online = _device_states().get(instance.endpoint) == "device"
        except RuntimeError:
            online = False
        if not online:
            try:
                result = subprocess.run([instance.adb_path, "-H", "127.0.0.1", "-P", str(_server_port()),
                                         "connect", instance.endpoint], capture_output=True, timeout=15,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"Cannot connect the selected BlueStacks instance: {exc}") from exc
            if result.returncode:
                raise RuntimeError("ADB could not connect the selected BlueStacks instance")
    if _device_states().get(instance.endpoint) != "device":
        raise RuntimeError("The selected BlueStacks instance is disconnected or not authorized")
    if _android_identity(instance.endpoint) != instance.android_id:
        raise RuntimeError("Android identity mismatch; refusing input to the selected instance")
    if not instance.config_path and not _is_bluestacks(instance.endpoint):
        raise RuntimeError("The manual endpoint cannot be verified as BlueStacks")
    validate_mapping(instance)


def _probe(instance, states, server_detail, running):
    try:
        validate_mapping(instance)
    except RuntimeError as exc:
        return replace(instance, status="unavailable", connection_status="unavailable", detail=str(exc))
    state = states.get(instance.endpoint)
    if state == "device":
        try:
            if _android_identity(instance.endpoint) != instance.android_id:
                raise RuntimeError("Android identity mismatch; this endpoint is not the selected instance")
            if not instance.config_path and not _is_bluestacks(instance.endpoint):
                raise RuntimeError("The manual endpoint cannot be verified as BlueStacks")
        except RuntimeError as exc:
            return replace(instance, status="unavailable", connection_status="unverified", detail=str(exc))
        return replace(instance, status="running", connection_status="connected", detail="Connected; Android identity verified")
    process_key = (os.path.normcase(str(Path(instance.adb_path).parent.resolve())), instance.instance_id)
    status = "unavailable" if running is None or not instance.config_path else "running" if process_key in running else "stopped"
    detail = (f"ADB reports {state}; identity verification is unavailable." if state else
              server_detail or "Instance is not connected to the local ADB server.")
    return replace(instance, status=status, connection_status=state or "disconnected", detail=detail)


def probe_instance(instance):
    try:
        states, detail = _device_states(), ""
    except RuntimeError as exc:
        states, detail = {}, str(exc)
    return _probe(instance, states, detail, _running_instances())


def discover():
    instances, issues = [], []
    for path, adb in _configuration_sources():
        try:
            instances.extend(_configured_instances(path, adb))
        except RuntimeError as exc:
            issues.append(str(exc))
    if not instances:
        return {"instances": [], "detail": " ".join(issues) or
                "BlueStacks configuration was not found. Install BlueStacks or use Manual Setup."}
    try:
        states, server_detail = _device_states(), ""
    except RuntimeError as exc:
        states, server_detail = {}, str(exc)
    running = _running_instances()
    with ThreadPoolExecutor(max_workers=min(8, len(instances))) as pool:
        instances = list(pool.map(lambda item: _probe(item, states, server_detail, running), instances))
    # Two registered installations sharing an endpoint are equally unsafe.
    counts = {item.endpoint: sum(other.endpoint == item.endpoint for other in instances) for item in instances}
    instances = [replace(item, status="unavailable", connection_status="unavailable",
                         detail="Multiple BlueStacks instances use this ADB endpoint; resolve the configuration conflict.")
                 if item.endpoint and counts[item.endpoint] > 1 else item for item in instances]
    return {"instances": sorted(instances, key=lambda item: (item.display_name.casefold(), item.key)),
            "detail": " ".join(issues) or f"Found {len(instances)} BlueStacks instance(s)."}


def resolve_manual(name, endpoint=""):
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 160 or any(ord(char) < 32 for char in name):
        raise RuntimeError("Enter a valid BlueStacks instance name or identifier")
    endpoint = _normal_endpoint(endpoint) if endpoint else ""
    result = discover()
    matches = [item for item in result["instances"]
               if name.casefold() in {item.instance_id.casefold(), item.display_name.casefold()}]
    if len(matches) > 1:
        raise RuntimeError("The instance name is ambiguous; use its unique internal identifier")
    if matches:
        instance = matches[0]
        if endpoint and endpoint != instance.endpoint:
            raise RuntimeError("The entered ADB endpoint does not match this BlueStacks instance")
        verify_identity(instance)
        return probe_instance(instance)
    if result["instances"]:
        raise RuntimeError("No configured BlueStacks instance matches that name or identifier")
    if not endpoint:
        raise RuntimeError("Cannot resolve the instance; enter its explicit local ADB endpoint in Advanced Setup")
    if _device_states().get(endpoint) != "device":
        raise RuntimeError("The entered endpoint is not an existing, authorized ADB transport")
    if not _is_bluestacks(endpoint):
        raise RuntimeError("The entered endpoint cannot be verified as BlueStacks")
    adb_candidates = {str(Path(record[field]) / "HD-Adb.exe") for record in _registry_locations()
                      for field in ("InstallDir", "InstallPath") if record.get(field)}
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            adb_candidates.update(str(path) for path in _safe_glob(os.environ[variable], "BlueStacks*/HD-Adb.exe"))
    adbs = sorted(path for path in adb_candidates if Path(path).is_file())
    if len(adbs) != 1:
        raise RuntimeError("Cannot resolve a unique installed BlueStacks ADB executable")
    instance = Instance("", "manual@" + endpoint, name, adbs[0], endpoint, _android_identity(endpoint))
    verify_identity(instance)
    return replace(instance, status="running", connection_status="connected",
                   detail="Manual BlueStacks endpoint and Android identity verified")
