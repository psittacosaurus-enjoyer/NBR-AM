"""Passive loot accounting. No device access; recognition uses saved results evidence."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager, closing
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from app.core.process_state import process_identity
from app.core.atomic_io import atomic_json, replace_atomic, read_json_file
from app.backend.retention import EvidenceRetention


def _atomic_json(path, value):
    atomic_json(path, value)


PENDING_STATES = frozenset({'awaiting_evidence', 'capture_pending', 'screenshot_saved',
                            'ready', 'recognition_pending', 'retryable_failure', 'operator_attention'})


class LootStore:
    def __init__(self, root):
        self.directory = Path(root) / "runtime" / "loot"
        self.path = self.directory / "loot.sqlite3"
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, account TEXT NOT NULL, stage TEXT NOT NULL,
                    process_identity TEXT NOT NULL UNIQUE, process_started REAL,
                    tracking_started REAL NOT NULL, ended_at REAL, heartbeat REAL);
                CREATE TABLE IF NOT EXISTS completions (
                    completion_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    run_id TEXT NOT NULL, run_sequence INTEGER NOT NULL, completed_at REAL NOT NULL,
                    status TEXT NOT NULL, screenshot_path TEXT, result_json TEXT NOT NULL DEFAULT '{}',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(session_id,run_id), UNIQUE(session_id,run_sequence));
                CREATE INDEX IF NOT EXISTS completion_session ON completions(session_id,run_sequence);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=.1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ensure_session(self, process_identity, stage, process_started=None, account="acc1"):
        sid = "session_" + hashlib.sha256(f"{account}:{process_identity}".encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?,NULL,?)",
                       (sid, account, stage, process_identity, process_started, time.time(), time.time()))
        return sid

    def reconcile_ended(self):
        """Recover session endings after a process exit that bypassed its finally block."""
        for session in self.sessions():
            identity = session['process_identity']
            parts = identity.split(':')
            if session['ended_at'] or len(parts) != 2 or not all(p.isdigit() for p in parts):
                continue
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, int(parts[0]))
            ended = False
            if handle:
                try:
                    code = wintypes.DWORD()
                    if kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                        ended = code.value != 259
                    if not ended:
                        try:
                            current, _ = process_identity(int(parts[0]))
                            ended = current != identity
                        except OSError:
                            pass  # Inaccessible identity is unavailable, not proof of exit.
                finally:
                    kernel.CloseHandle(handle)
            else:
                ended = kernel.GetLastError() == 87  # ERROR_INVALID_PARAMETER: PID no longer exists.
            if ended:
                self.end_session(session['session_id'])

    def end_session(self, session_id):
        with self.connect() as db:
            db.execute("UPDATE sessions SET ended_at=COALESCE(ended_at,?) WHERE session_id=?", (time.time(), session_id))

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sessions ORDER BY tracking_started DESC")]

    def accept(self, event):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if event.get('process_identity'):
                db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?,NULL,?)",
                           (event['session_id'], event['account'], event['stage'], event['process_identity'],
                            event.get('process_started'), event.get('tracking_started', event['completed_at']), time.time()))
            sequence = event.get('run_sequence')
            if sequence is None:
                sequence = db.execute('SELECT COALESCE(MAX(run_sequence),0)+1 FROM completions WHERE session_id=?',
                                      (event['session_id'],)).fetchone()[0]
            db.execute("""INSERT INTO completions
                (completion_id,session_id,run_id,run_sequence,completed_at,status)
                VALUES(?,?,?,?,?,'awaiting_evidence') ON CONFLICT(completion_id) DO NOTHING""",
                       (event['completion_id'], event['session_id'], event['run_id'], sequence, event['completed_at']))
            row = db.execute('SELECT session_id,run_id,completed_at FROM completions WHERE completion_id=?',
                             (event['completion_id'],)).fetchone()
            if not row or row['session_id'] != event['session_id'] or str(row['run_id']) != str(event['run_id']):
                raise ValueError('Completion ID conflicts with another recording')

    def finalized(self, completion_id):
        with self.connect() as db:
            row = db.execute("SELECT status FROM completions WHERE completion_id=?", (completion_id,)).fetchone()
            return bool(row and row['status'] not in PENDING_STATES)

    def evidence_recorded(self, completion_id):
        with self.connect() as db:
            row = db.execute('SELECT status,screenshot_path FROM completions WHERE completion_id=?',
                             (completion_id,)).fetchone()
            return bool(row and row['status'] not in PENDING_STATES
                        and (row['status'] == 'recognized' or row['screenshot_path']))

    def update_pending(self, event):
        self.accept(event)
        with self.connect() as db:
            placeholders = ','.join('?' for _ in PENDING_STATES)
            db.execute(f"UPDATE completions SET status=?,screenshot_path=?,warnings_json=? "
                       f"WHERE completion_id=? AND status IN ({placeholders})",
                       (event['state'], event.get('screenshot'), json.dumps([event['error']] if event.get('error') else []),
                        event['completion_id'], *PENDING_STATES))

    def finish(self, event, status, result, screenshot_path, warnings):
        self.accept(event)
        with self.connect() as db:
            placeholders = ','.join('?' for _ in PENDING_STATES)
            db.execute(f"""UPDATE completions SET status=?,result_json=?,screenshot_path=?,warnings_json=?
                WHERE completion_id=? AND status IN ({placeholders})""",
                       (status, json.dumps(result, ensure_ascii=False), screenshot_path,
                        json.dumps(warnings, ensure_ascii=False), event['completion_id'], *PENDING_STATES))

    def dashboard(self, session_id=None, limit=100, offset=0):
        with self.connect() as db:
            return _dashboard(db, session_id, limit, offset)


def _dashboard(db, session_id=None, limit=100, offset=0, *, account=None):
    # A single read transaction keeps totals and history consistent during writes.
    db.execute("BEGIN")
    sessions = [dict(row) for row in db.execute(
        "SELECT * FROM sessions ORDER BY tracking_started DESC" if account is None else
        "SELECT * FROM sessions WHERE account=? ORDER BY tracking_started DESC",
        () if account is None else (account,))]
    session = next((s for s in sessions if s['session_id'] == session_id), None) if session_id else next(iter(sessions), None)
    answer = dict(status="Tracking Unavailable", detail="No instrumented farming session recorded.",
                  session=session, sessions=sessions, completed_runs=0, recognized_icons=0, unknown_icons=0,
                  ambiguous_icons=0, recognition_failures=0, failures=0, total_drops=0, unidentified_drops=0, drop_counts={},
                  confirmed_quantities={}, items=[], runs=[],
                  outstanding_jobs=0, capture_pending=0, attention_required=0, recording_states={},
                  collector_health=dict(status='Tracking Unavailable', error=None, next_retry_at=None),
                  history_offset=max(0, int(offset)), history_limit=max(1, min(int(limit), 1000)))
    if not session:
        return answer
    rows = db.execute("SELECT * FROM completions WHERE session_id=? ORDER BY run_sequence DESC", (session['session_id'],))
    items = {}
    for row in rows:
        run = dict(row)
        run.update(account=session['account'], stage=session['stage'])
        result = json.loads(run.pop('result_json'))
        run['warnings'] = [warning for warning in json.loads(run.pop('warnings_json'))
                           if warning != 'Unknown quantities are excluded from numerical totals. Stars indicate rarity, not quantity.']
        entries = result.get('rewards', []) + result.get('unknown_rewards', [])
        run['rewards'] = entries
        run['unknown_icons'] = run['ambiguous_icons'] = run['drop_count'] = 0
        answer['completed_runs'] += 1
        answer['recording_states'][run['status']] = answer['recording_states'].get(run['status'], 0) + 1
        if run['status'] in PENDING_STATES:
            answer['outstanding_jobs'] += 1
            answer['capture_pending'] += int(run['status'] in ('awaiting_evidence', 'capture_pending'))
            answer['attention_required'] += int(run['status'] == 'operator_attention')
        elif run['status'] not in ('recognized', 'uncertain'):
            answer['recognition_failures'] += 1
        for reward in entries:
            if reward.get('reward_scope') != 'acquired':
                continue
            visible_icon = reward.get('visual_state', 'icon') == 'icon'
            reward['drop_count'] = int(visible_icon)
            if visible_icon:
                run['drop_count'] += 1
                answer['total_drops'] += 1
            identity = reward.get('identity_status')
            identified = visible_icon and identity == 'matched' and reward.get('item_id') and reward.get('item_name')
            if visible_icon and not identified:
                answer['unidentified_drops'] += 1
            if identified:
                answer['recognized_icons'] += 1
                item = items.setdefault(reward['item_id'], dict(item_id=reward['item_id'], item_name=reward['item_name'],
                                       observed_icons=0, drop_count=0, confirmed_acquired=0, unknown_quantities=0,
                                       count_basis='one_drop_per_acquired_icon',
                                       last_drop_at=run['completed_at'], last_drop_sequence=run['run_sequence'],
                                       record_type=reward.get('record_type', 'item'), rarity=reward.get('rarity'), element=reward.get('element')))
                item['observed_icons'] += 1
                item['drop_count'] += 1
                # The game's acquired grid represents one drop per icon. Text
                # numbers, rarity stars and condition cards cannot multiply it.
                item['confirmed_acquired'] = item['drop_count']
                answer['drop_counts'][reward['item_id']] = item['drop_count']
                answer['confirmed_quantities'][reward['item_id']] = item['drop_count']
            elif identity in ('candidate', 'ambiguous'):
                run['ambiguous_icons'] += 1
                answer['ambiguous_icons'] += 1
            else:
                run['unknown_icons'] += 1
                answer['unknown_icons'] += 1
        if answer['completed_runs'] > answer['history_offset'] and len(answer['runs']) < answer['history_limit']:
            answer['runs'].append(run)
    answer['failures'] = answer['recognition_failures']
    answer['items'] = sorted(items.values(), key=lambda item: item['item_name'])
    answer['status'] = ('Session Ended' if session['ended_at'] else 'Recognition Error' if answer['recognition_failures']
                        else 'Tracking Active' if answer['completed_runs'] else 'Waiting for First Completion')
    answer['detail'] = "Each acquired icon counts as one drop. Unidentified drops remain separate."
    if answer['attention_required']:
        answer['status'] = 'Operator Attention Required'
    elif answer['capture_pending']:
        answer['status'] = 'Screenshot Capture Pending'
    elif answer['outstanding_jobs']:
        answer['status'] = 'Recognition Pending'
    return answer


def _recognizer(root):
    from item_recognition.recognizer import ItemRecognizer
    return ItemRecognizer(library_path=Path(root) / "runtime" / "library")


def _recognition_revision(root):
    """Cheap invalidation token; the recognizer remains the read-only API owner."""
    root = Path(root)
    database = root / 'runtime' / 'library' / 'data' / 'library.sqlite3'
    paths = [database, database.with_name(database.name + '-wal'),
             root / 'assets' / 'item_recognition' / 'calibration.json',
             root / 'assets' / 'item_recognition' / 'profiles.json',
             root / 'assets' / 'item_recognition' / 'reviewed_rewards.json']
    revision = []
    for path in paths:
        try:
            stat = path.stat()
            revision.append((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        except FileNotFoundError:
            revision.append(None)
    return tuple(revision)


class Collector:
    """Durable completion/evidence outbox; matching never runs on the farm thread.

    JSON jobs are the registration acknowledgement. SQLite is an idempotent
    projection of that outbox and may recover independently after an outage.
    Pending evidence is never evicted to meet a memory or storage limit.
    """
    def __init__(self, root, stage, process_identity, process_started=None, account="acc1",
                 recognizer_factory=None, max_pending=8, device_id=None,
                 retry_base=.2, retry_max=30, max_jobs=10000, max_storage_bytes=1024**3):
        self.root = Path(root)
        self.store = LootStore(root)
        self.store.reconcile_ended()
        self.session_id = self.store.ensure_session(process_identity, stage, process_started, account)
        self.stage, self.account = stage, account
        self.device_id = str(device_id) if device_id is not None else ''
        self.process_identity, self.process_started = process_identity, process_started
        self.tracking_started = time.time()
        self.directory = self.store.directory
        self.retention = EvidenceRetention(self.root)
        self.spool = self.directory / "pending"
        self.spool.mkdir(exist_ok=True)
        self.max_pending = max(1, int(max_pending))
        self.max_jobs = max(1, int(max_jobs))
        self.max_storage_bytes = max(1, int(max_storage_bytes))
        self.retry_base, self.retry_max = max(.01, retry_base), max(.01, retry_max)
        self.factory = recognizer_factory or (lambda: _recognizer(root))
        self.recognizer = None
        self._reload_references = recognizer_factory is None
        self._recognizer_revision = None
        self.validation_directory = None
        validation = os.environ.get('NBR_LOOT_VALIDATION_DIR')
        validation_limit = os.environ.get('NBR_LOOT_VALIDATION_MAX_RUNS', '4')
        capture_config = self.root / 'runtime' / 'validation' / 'validation_capture.json'
        if capture_config.is_file():
            settings = json.loads(capture_config.read_text('utf-8'))
            if settings.get('enabled') and settings.get('expires_at', 0) > time.time():
                validation = settings.get('directory')
                validation_limit = settings.get('max_runs', 4)
        if validation:
            target = Path(validation).resolve()
            if target.is_relative_to((self.root / 'runtime' / 'validation').resolve()):
                target.mkdir(parents=True, exist_ok=True)
                self.validation_directory = target
        self.validation_limit = min(10, max(0, int(validation_limit)))
        self._lock = threading.RLock()
        self._health_lock = threading.RLock()
        self._publication_lock = threading.RLock()
        self._stop = threading.Event()
        self._busy = False
        self._attempts = {}
        self._inflight = set()
        self._events = {}
        self._ready_count = 0
        self._health = dict(status='Tracking Active', error=None, next_retry_at=None)
        self._operation_errors = {}
        self._telemetry_error = None
        with self.store.connect() as db:
            self._sequence = db.execute('SELECT COALESCE(MAX(run_sequence),0) FROM completions WHERE session_id=?',
                                        (self.session_id,)).fetchone()[0]
        for path in self.spool.glob('*.json'):
            value = self._read_spool(path)
            if value and value['session_id'] == self.session_id:
                self._sequence = max(self._sequence, value.get('run_sequence') or 0)
        self._thread = threading.Thread(target=self._loop, name="nbr-loot-recognition", daemon=True)
        self._thread.start()

    def _path(self, value):
        return self.spool / (value['completion_id'] + '.json')

    def _owns(self, value):
        return (value.get('account') == self.account and
                (not self.device_id or value.get('device_id', '') in ('', self.device_id)))

    def _read_spool(self, path):
        """Quarantine malformed metadata; leave transient access failures in place."""
        try:
            value = json.loads(path.read_text('utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Spool record must be an object')
            for field in ('completion_id', 'session_id', 'account', 'stage'):
                if not isinstance(value.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value[field]):
                    raise ValueError(f'Invalid spool {field}')
            if path.name != value['completion_id'] + '.json':
                raise ValueError('Spool filename does not match completion ID')
            if value.get('state') not in PENDING_STATES | {'missing_screenshot', 'backlog_limit'}:
                raise ValueError('Invalid spool state')
            if type(value.get('run_id')) not in (str, int):
                raise ValueError('Invalid spool run ID')
            sequence = value.get('run_sequence')
            if sequence is not None and (type(sequence) is not int or sequence < 1):
                raise ValueError('Invalid spool run sequence')
            completed = value.get('completed_at')
            if type(completed) not in (int, float) or not math.isfinite(completed) or completed <= 0:
                raise ValueError('Invalid spool completion time')
            for field in ('persistence_attempts', 'attempts'):
                if type(value.get(field, 0)) is not int or value.get(field, 0) < 0:
                    raise ValueError('Invalid spool retry count')
            next_retry = value.get('next_retry_at', 0)
            if type(next_retry) not in (float, int) or not math.isfinite(next_retry) or next_retry < 0:
                raise ValueError('Invalid spool retry time')
            screenshot = value.get('screenshot')
            if screenshot is not None and (not isinstance(screenshot, str) or
                    Path(screenshot).resolve() != self.spool.resolve() / (value['completion_id'] + '.png')):
                raise ValueError('Spool screenshot must belong to this completion')
            if value['state'] == 'backlog_limit' and screenshot:
                value['state'] = 'recognition_pending'
            return value
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            try:
                quarantine = self.directory / 'quarantine'
                quarantine.mkdir(exist_ok=True)
                target = quarantine / f'{path.stem}_{uuid.uuid4().hex}.json'
                path.replace(target)
                _atomic_json(target.with_suffix('.error.json'),
                             {'source': path.name, 'time': time.time(), 'error': str(exc)})
                self._events.pop(path.stem, None)
                self._error(ValueError(f'Quarantined {path.name}: {exc}'), 'Operator Attention Required')
            except OSError as quarantine_error:
                self._error(quarantine_error)
            return None
        except OSError as exc:
            self._error(exc)
            return None

    def _completion_id(self, run_id):
        identity = f'{self.account}:{self.device_id}:{self.stage}:{run_id}' if self.device_id else f'{self.session_id}:{run_id}'
        return 'job_' + hashlib.sha256(identity.encode()).hexdigest()[:48]

    def _telemetry_failure(self, exc):
        with self._health_lock:
            self._telemetry_error = dict(message=str(exc), operation='health_publication',
                exception_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None), time=time.time())

    def _error(self, exc, status='Retryable Failure', next_retry_at=None, *,
               operation=None, completion_id=None, path=None):
        # Job changes and publication use one lock order. In particular a stale
        # empty-backlog heartbeat cannot erase a newly failed capture.
        with self._lock, self._publication_lock:
            self._record_error(exc, status, next_retry_at, operation=operation,
                               completion_id=completion_id, path=path)

    def _record_error(self, exc, status, next_retry_at, *, operation, completion_id, path):
        failure = dict(error=str(exc), message=str(exc), operation=operation or 'recording',
                       exception_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None),
                       time=time.time(), session_id=self.session_id, category='persistence_failure')
        if completion_id:
            failure['completion_id'] = completion_id
        if path is not None:
            failure['path'] = str(path)
        for key in ('used_bytes', 'limit_bytes', 'required_bytes'):
            if hasattr(exc, key):
                failure[key] = getattr(exc, key)
        with self._health_lock:
            self._health = dict(status=status, error=str(exc), next_retry_at=next_retry_at)
            if completion_id and operation == 'capture':
                self._operation_errors[completion_id] = failure
            payload = {**self._health, 'operation_errors': dict(self._operation_errors),
                       'time': time.time(), 'session_id': self.session_id,
                       'account': self.account, 'device_id': self.device_id}
            if self._operation_errors:
                failure = max(self._operation_errors.values(), key=lambda value: value['time'])
                payload.update(status='Screenshot Capture Pending', error=failure['message'])
        try:
            with self._publication_lock:
                # Persist the cause first; telemetry failure cannot replace it.
                _atomic_json(self.directory / 'persistence_error.json', failure)
                _atomic_json(self.directory / f'health_{self.account}.json', payload)
        except Exception as publication_error:
            self._telemetry_failure(publication_error)

    def _clear_capture_error(self, completion_id):
        with self._health_lock:
            removed = self._operation_errors.pop(completion_id, None)
            if removed and self._health.get('error') == removed['message']:
                self._health = dict(status='Tracking Active', error=None, next_retry_at=None)

    def _cache(self, value):
        cid = value['completion_id']
        self._events[cid] = value
        while len(self._events) > self.max_pending + 2:
            removable = next((key for key in self._events if key not in self._inflight and key != cid), None)
            if removable is None:
                break
            self._events.pop(removable, None)

    def _save(self, value):
        _atomic_json(self._path(value), value)
        self._cache(value)

    def _job(self, run_id, completion_id=None):
        cid = completion_id or self._completion_id(run_id)
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', cid):
            raise ValueError('Invalid completion ID')
        path = self.spool / (cid + '.json')
        value = self._read_spool(path) if path.exists() else None
        if value and (not self._owns(value) or str(value['run_id']) != str(run_id) or value['stage'] != self.stage):
            raise ValueError('Recording belongs to another account, device, stage or run')
        return value

    def _receipt(self, run_id, completion_id=None):
        cid = completion_id or self._completion_id(run_id)
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', cid):
            raise ValueError('Invalid completion ID')
        path = self.directory / 'receipts' / (cid + '.json')
        if not path.exists():
            return None
        value = json.loads(path.read_text('utf-8'))
        if (value.get('completion_id') != cid or not self._owns(value)
                or str(value.get('run_id')) != str(run_id) or value.get('stage') != self.stage):
            raise ValueError('Recording receipt belongs to another account, device, stage or run')
        return value

    def _storage_used(self):
        return sum(path.stat().st_size for folder in (self.spool, self.directory / 'diagnostics')
                   if folder.exists() for path in folder.glob('*.png') if not path.name.endswith('.tmp.png'))

    def completed(self, run_id, completed_at=None, completion_id=None):
        """Return an explicit durable job ACK; None means the farmer must retry."""
        try:
            with self._lock:
                cid = completion_id or self._completion_id(run_id)
                value = self._job(run_id, cid)
                if value:
                    self._cache(value)
                    return cid
                if self._receipt(run_id, cid):
                    return cid
                # A committed completion is itself an idempotent registration ACK.
                try:
                    if self.store.finalized(cid):
                        return cid
                except sqlite3.Error as exc:
                    self._error(exc)
                if sum(1 for _ in self.spool.glob('*.json')) >= self.max_jobs:
                    raise OSError(f'Loot backlog storage limit ({self.max_jobs} jobs) reached; restore recognition or archive completed evidence')
                self._sequence += 1
                value = dict(schema_version=2, session_id=self.session_id, completion_id=cid,
                             run_id=run_id, run_sequence=self._sequence,
                             completed_at=time.time() if completed_at is None else completed_at,
                             stage=self.stage, account=self.account, device_id=self.device_id,
                             process_identity=self.process_identity, process_started=self.process_started,
                             tracking_started=self.tracking_started, state='awaiting_evidence',
                             screenshot=None, attempts=0, next_retry_at=0)
                # Validation before write also rejects nonfinite timestamps/run metadata.
                if type(run_id) not in (str, int) or not isinstance(value['completed_at'], (int, float)) or not math.isfinite(value['completed_at']) or value['completed_at'] <= 0:
                    raise ValueError('Invalid completion metadata')
                self._save(value)
                return cid
        except Exception as exc:
            self._error(exc, 'Completion Registration Pending')
            return None

    @staticmethod
    def _verify_image(path, expected_hash=None):
        import cv2
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_hash and digest != expected_hash:
            raise ValueError('Saved screenshot checksum mismatch; recapture or restore evidence')
        frame = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if frame is None or not frame.size:
            raise ValueError('Saved results screenshot cannot be decoded')
        return digest

    def evidence(self, run_id, frame, completion_id=None):
        """ACK only an atomic, fsynced, decoded PNG plus verified job metadata."""
        temp = None
        cid = completion_id or self._completion_id(run_id)
        screenshot = self.spool / (cid + '.png')
        try:
            with self._lock:
                value = self._job(run_id, completion_id)
                if not value:
                    receipt = self._receipt(run_id, completion_id)
                    if receipt:
                        saved = bool(receipt.get('screenshot'))
                    else:
                        saved = self.store.evidence_recorded(cid)
                    if saved:
                        self._clear_capture_error(cid)
                    return saved
                if value['state'] == 'missing_screenshot':
                    return False  # An explicitly closed recording cannot accept another run's rewards.
                screenshot = self.spool / (value['completion_id'] + '.png')
                if value.get('screenshot') and value.get('screenshot_sha256'):
                    try:
                        self._verify_image(screenshot, value['screenshot_sha256'])
                        self._clear_capture_error(cid)
                        return True
                    except (OSError, ValueError):
                        pass  # The still-visible results frame can repair this job.
                import cv2
                if frame is None or not getattr(frame, 'size', 0):
                    raise ValueError('Device returned an empty results screenshot')
                temp = screenshot.with_name(f'{screenshot.stem}.{os.getpid()}.{threading.get_ident()}.tmp.png')
                if not cv2.imwrite(str(temp), frame):
                    raise OSError('Unable to save results screenshot')
                with temp.open('r+b') as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                digest = self._verify_image(temp)
                old_size = screenshot.stat().st_size if screenshot.exists() else 0
                # Reserve both the pending PNG and a possible diagnostic copy.
                required = max(0, 2 * temp.stat().st_size - old_size)
                with self.retention.admission(required, self.max_storage_bytes,
                                              protected_completion_ids=(cid,)):
                    replace_atomic(temp, screenshot)
                self._verify_image(screenshot, digest)
                saved = dict(value, screenshot=str(screenshot.resolve()), screenshot_sha256=digest,
                             state='recognition_pending', attempts=0, next_retry_at=0)
                saved.pop('error', None)
                self._save(saved)
                self._clear_capture_error(cid)
                return True
        except Exception as exc:
            self._error(exc, 'Screenshot Capture Pending', operation='capture',
                        completion_id=cid, path=screenshot)
            return False
        finally:
            if temp:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass  # Cleanup must not replace the capture failure or ACK.

    def recover_evidence(self, run_id, completion_id=None):
        """ACK only this transaction's retained or already finalized evidence.

        No current game frame is accepted. A same-job PNG committed before its
        metadata can be reattached, while a durable finalized receipt remains
        authoritative after ordinary cleanup removes the pending image.
        """
        cid = completion_id or self._completion_id(run_id)
        try:
            with self._lock:
                def matches(value):
                    return bool(value and value.get('completion_id') == cid
                        and str(value.get('run_id')) == str(run_id)
                        and value.get('account') == self.account
                        and value.get('device_id', '') == self.device_id
                        and value.get('stage') == self.stage)

                value = self._job(run_id, cid)
                if value:
                    if not matches(value) or value['state'] == 'missing_screenshot':
                        return False
                    candidate = self.spool / (cid + '.png')
                    if not candidate.is_file():
                        return False
                    digest = self._verify_image(candidate, value.get('screenshot_sha256'))
                    if not value.get('screenshot') or not value.get('screenshot_sha256'):
                        recovered = dict(value, screenshot=str(candidate.resolve()),
                                         screenshot_sha256=digest, state='recognition_pending',
                                         attempts=0, next_retry_at=0)
                        recovered.pop('error', None)
                        self._save(recovered)
                    self._clear_capture_error(cid)
                    return True

                receipt = self._receipt(run_id, cid)
                if receipt:
                    if (not matches(receipt) or receipt.get('recorded') is not True
                            or receipt.get('state') == 'missing_screenshot'
                            or not receipt.get('screenshot')
                            or not isinstance(receipt.get('screenshot_sha256'), str)
                            or not re.fullmatch(r'[0-9a-f]{64}', receipt['screenshot_sha256'])):
                        return False
                    candidate = Path(receipt['screenshot'])
                    if candidate.exists():
                        self._verify_image(candidate, receipt['screenshot_sha256'])
                    self._clear_capture_error(cid)
                    return True

                # SQLite has no device column. Only the deterministic ID binds
                # this fallback to the collector's exact device/account/stage.
                if not self.device_id or cid != self._completion_id(run_id):
                    return False
                with self.store.connect() as db:
                    row = db.execute('SELECT c.run_id,c.status,c.screenshot_path,s.account,s.stage '
                        'FROM completions c JOIN sessions s ON s.session_id=c.session_id '
                        'WHERE c.completion_id=?', (cid,)).fetchone()
                if (not row or str(row['run_id']) != str(run_id)
                        or row['account'] != self.account or row['stage'] != self.stage
                        or row['status'] in PENDING_STATES | {'missing_screenshot', 'backlog_limit'}):
                    return False
                if row['screenshot_path'] and Path(row['screenshot_path']).exists():
                    self._verify_image(Path(row['screenshot_path']))
                self._clear_capture_error(cid)
                return True
        except Exception as exc:
            self._error(exc, 'Screenshot Capture Pending', operation='capture',
                        completion_id=cid)
            return False

    def missing(self, run_id, reason='missing_screenshot', completion_id=None):
        """ACK verified unavailable evidence; refuse every retained screenshot."""
        try:
            with self._lock:
                value = self._job(run_id, completion_id)
                if not value:
                    receipt = self._receipt(run_id, completion_id)
                    return bool(receipt and receipt.get('recorded')
                                and receipt.get('state') == 'missing_screenshot'
                                and not receipt.get('screenshot'))
                # A PNG may have committed before its metadata. Preserve it for
                # ordinary recovery rather than abandoning recoverable evidence.
                if value.get('screenshot') or (self.spool / (value['completion_id'] + '.png')).exists():
                    return False
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError('Missing evidence resolution requires a reason')
                if value['state'] != 'missing_screenshot':
                    self._save(dict(value, state='missing_screenshot', error=reason,
                                    resolution_reason=reason, resolved_at=time.time(), next_retry_at=0))
                self._clear_capture_error(value['completion_id'])
                return True
        except Exception as exc:
            self._error(exc)
            return False

    def resume_pending(self):
        """Return only persisted transactions; a results screen alone creates none."""
        with self._lock:
            return [value for path in self.spool.glob('*.json')
                    if (value := self._read_spool(path)) and self._owns(value)
                    and value['stage'] == self.stage and not value.get('screenshot')]

    def health(self):
        states = {}
        pending_ids = set()
        unreadable_jobs = 0
        with self._lock:
            for path in self.spool.glob('*.json'):
                value = self._read_spool(path)
                if value and self._owns(value):
                    states[value['state']] = states.get(value['state'], 0) + 1
                    pending_ids.add(value['completion_id'])
                elif value is None:
                    try:
                        unreadable_jobs += int(path.exists())
                    except OSError:
                        unreadable_jobs += 1
        with self._health_lock:
            info = {**self._health, 'operation_errors': dict(self._operation_errors),
                    'telemetry_error': self._telemetry_error}
            capture_errors = [value for cid, value in self._operation_errors.items() if cid in pending_ids]
            if capture_errors:
                primary = max(capture_errors, key=lambda value: value['time'])
                info.update(status='Screenshot Capture Pending', error=primary['message'])
        return {**info, 'outstanding_jobs': sum(states.values()), 'recording_states': states,
                'unreadable_jobs': unreadable_jobs,
                'capture_pending': sum(states.get(s, 0) for s in ('awaiting_evidence', 'capture_pending')),
                'attention_required': states.get('operator_attention', 0)}

    def retry(self, completion_id):
        """Retry retained operator-attention evidence under the same accounting ID."""
        with self._lock:
            path = self.spool / (completion_id + '.json')
            value = self._read_spool(path)
            if not value or not self._owns(value) or not value.get('screenshot'):
                return False
            self._verify_image(Path(value['screenshot']), value.get('screenshot_sha256'))
            self._save(dict(value, state='recognition_pending', attempts=0, next_retry_at=0, error=None))
            return True

    def _process(self, value):
        cid = value['completion_id']
        with self._lock:
            if cid in self._inflight:
                return
            self._inflight.add(cid)
        try:
            self._process_event(value)
        finally:
            with self._lock:
                self._inflight.discard(cid)

    def _process_event(self, value):
        self.store.accept(value)
        if self.store.finalized(value['completion_id']):
            self._cleanup(value)
            return
        if value['state'] in ('awaiting_evidence', 'capture_pending'):
            # Crash between PNG replacement and job update: use only this job's
            # complete, verified evidence; never an unrelated results screenshot.
            candidate = self.spool / (value['completion_id'] + '.png')
            if not candidate.is_file():
                self.store.update_pending(value)
                return
            digest = self._verify_image(candidate)
            with self._lock:
                current = self._read_spool(self._path(value))
                if current is None:
                    return
                value = dict(current, screenshot=str(candidate.resolve()),
                             screenshot_sha256=digest, state='recognition_pending')
                self._save(value)
        if value['state'] == 'operator_attention':
            self.store.update_pending(value)
            return
        result, warnings = {}, []
        status = value['state']
        source = Path(value['screenshot']) if value.get('screenshot') else None
        if source:
            self._verify_image(source, value.get('screenshot_sha256'))
            revision = _recognition_revision(self.root) if self._reload_references else None
            if self.recognizer is None or (self._reload_references and revision != self._recognizer_revision):
                replacement = self.factory()
                transient_errors = [error.get('error', 'Reference image temporarily unavailable')
                                    for error in getattr(replacement, 'reference_errors', [])
                                    if error.get('retryable')]
                if transient_errors:
                    raise OSError('Recognition references temporarily unavailable: ' + '; '.join(transient_errors))
                if self._reload_references and _recognition_revision(self.root) != revision:
                    raise RuntimeError('Recognition inputs changed while references loaded; retrying saved evidence')
                self.recognizer, self._recognizer_revision = replacement, revision
            result = self.recognizer.analyze_screenshot(screenshot_path=source)
            if result.get('retryable'):
                raise OSError('; '.join(result.get('warnings', [])) or 'Recognition infrastructure temporarily unavailable')
            warnings.extend(result.get('warnings', []))
            entries = result.get('rewards', []) + result.get('unknown_rewards', [])
            if result.get('status') == 'error':
                status = 'invalid_screenshot'
            elif result.get('status') in ('unsupported_screen', 'no_visible_rewards', 'no_acquired_rewards') or not entries:
                status = 'no_reward_region'
            elif (result.get('status') not in ('partial', 'complete')
                  or result.get('diagnostics', {}).get('parsing_complete') is False
                  or result.get('diagnostics', {}).get('reliable') is False
                  or any(r.get('reward_scope') != 'acquired' for r in entries)):
                status = 'incomplete_results'
            elif any(r.get('identity_status') != 'matched' or r.get('visual_state') == 'unrevealed' for r in entries):
                status = 'uncertain'
            else:
                status = 'recognized'
        if status != 'recognized':
            warnings.append(value.get('error') or status)
        diagnostic = None
        if status != 'recognized' and source:
            stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime(value['completed_at']))
            target = self.directory / 'diagnostics' / f"{stamp}_{value['account']}_{value['stage']}_{value['completion_id']}_{status}.png"
            target.parent.mkdir(exist_ok=True)
            required = max(0, source.stat().st_size - (target.stat().st_size if target.exists() else 0))
            with self.retention.admission(required, self.max_storage_bytes,
                                          protected_completion_ids=(value['completion_id'],)):
                self._copy_evidence(source, target, value.get('screenshot_sha256'))
            diagnostic = str(target.resolve())
        self.store.finish(value, status, result, diagnostic, warnings)
        # Validation collection is optional and cannot invalidate committed loot.
        if self.validation_directory and source:
            try:
                target = self.validation_directory / (value['completion_id'] + '.png')
                if target.exists() or len(list(self.validation_directory.glob('*.png'))) < self.validation_limit:
                    self._copy_evidence(source, target, value.get('screenshot_sha256'))
                    _atomic_json(target.with_suffix('.json'), {**value, 'validation_screenshot': str(target),
                                 'recognition_status': status, 'recognition_result': result})
            except Exception as exc:
                self._error(exc)
        self._cleanup(value)

    @staticmethod
    def _copy_evidence(source, target, digest):
        import shutil
        temp = target.with_name(f'{target.stem}.{os.getpid()}.{threading.get_ident()}.tmp.png')
        try:
            shutil.copyfile(source, temp)
            with temp.open('r+b') as stream:
                os.fsync(stream.fileno())
            Collector._verify_image(temp, digest)
            replace_atomic(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup(self, value):
        # Delete only after SQLite commits; a crash before cleanup replays without
        # changing the already committed completion or crediting rewards twice.
        with self._lock:
            if value.get('screenshot') or value['state'] == 'missing_screenshot':
                # A compact durable ACK survives the cleanup/worker-ACK crash
                # window, including a subsequent SQLite outage.
                _atomic_json(self.directory / 'receipts' / (value['completion_id'] + '.json'),
                             {**value, 'recorded': True, 'recorded_at': time.time()})
            if value.get('screenshot'):
                source = Path(value['screenshot'])
                if source.parent.resolve() == self.spool.resolve():
                    source.unlink(missing_ok=True)
            self._path(value).unlink(missing_ok=True)
            self._events.pop(value['completion_id'], None)
            self._attempts.pop(value['completion_id'], None)

    def _retry_failure(self, value, exc):
        retryable = isinstance(exc, (OSError, sqlite3.Error, RuntimeError, TimeoutError, ImportError))
        with self._lock:
            current = self._read_spool(self._path(value))
            if current is None:
                return
            attempts = current.get('attempts', 0) + 1
            delay = min(self.retry_max, self.retry_base * (2 ** min(attempts - 1, 16)))
            next_retry = time.time() + delay
            # Awaiting evidence remains eligible for capture independently of DB retries.
            state = ('retryable_failure' if current.get('screenshot') else current['state']) if retryable else 'operator_attention'
            current.update(state=state, attempts=attempts, next_retry_at=next_retry,
                           error=str(exc), retryable=retryable)
            try:
                self._save(current)
            except Exception as write_error:
                self._error(write_error)
            try:
                self.store.update_pending(current)
            except Exception:
                pass
            self._error(exc, 'Retryable Failure' if retryable else 'Operator Attention Required', next_retry)

    def _loop(self):
        while not self._stop.is_set():
            # A heartbeat failure must not prevent replay or exhaust a job.
            try:
                with self.store.connect() as db:
                    db.execute('UPDATE sessions SET heartbeat=? WHERE session_id=?', (time.time(), self.session_id))
            except Exception as exc:
                self._error(exc)
            processed = 0
            try:
                for path in self.spool.glob('*.json'):
                    if self._stop.is_set():
                        break
                    with self._lock:
                        value = self._read_spool(path)
                    if not value or not self._owns(value):
                        continue
                    if value.get('next_retry_at', 0) > time.time() or value['state'] == 'operator_attention':
                        continue
                    # Capture-waiting jobs are projected, but do not consume the
                    # recognition batch limit or starve the disk backlog.
                    waiting = value['state'] in ('awaiting_evidence', 'capture_pending')
                    self._busy = True
                    try:
                        self._process(value)
                    except Exception as exc:
                        self._retry_failure(value, exc)
                    finally:
                        self._busy = False
                    if not waiting:
                        processed += 1
                        if processed >= self.max_pending:
                            break
            except Exception as exc:
                self._error(exc)
            try:
                self._write_health()
            except Exception as exc:
                self._telemetry_failure(exc)
            self._stop.wait(.1)

    def _write_health(self):
        with self._lock, self._publication_lock:
            self._write_health_locked()

    def _write_health_locked(self):
        info = self.health()
        if info['attention_required']:
            info['status'] = 'Operator Attention Required'
        elif info['capture_pending']:
            info['status'] = 'Screenshot Capture Pending'
        elif not info['outstanding_jobs'] and not info['unreadable_jobs']:
            with self._health_lock:
                self._health = dict(status='Tracking Active', error=None, next_retry_at=None)
                self._operation_errors.clear()
            info.update(status='Tracking Active', error=None, next_retry_at=None, operation_errors={})
            failure = self.directory / 'persistence_error.json'
            try:
                if failure.exists() and read_json_file(failure).get('session_id') == self.session_id:
                    failure.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        with self._publication_lock:
            _atomic_json(self.directory / f'health_{self.account}.json',
                         {**info, 'time': time.time(), 'session_id': self.session_id,
                          'account': self.account, 'device_id': self.device_id})
        with self._health_lock:
            self._telemetry_error = None

    def wait_idle(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                outstanding = any((value := self._read_spool(path)) and self._owns(value)
                                  for path in self.spool.glob('*.json'))
                if not outstanding and not self._busy:
                    return True
            time.sleep(.02)
        return False

    def close(self, end_session=False):
        # Stop preserves capture-pending transactions exactly as recorded. A new
        # process can resume the original account/device/run after a restart.
        self._stop.set()
        self._thread.join(timeout=2)
        if end_session:
            try:
                self.store.end_session(self.session_id)
            except Exception as exc:
                self._error(exc)


def end_process_session(root, identity):
    store = LootStore(root)
    for session in store.sessions():
        if session['process_identity'] == identity:
            store.end_session(session['session_id'])


def _empty_dashboard(detail, limit, offset):
    return dict(status='Tracking Unavailable', detail=detail, session=None, sessions=[], completed_runs=0,
                recognized_icons=0, unknown_icons=0, ambiguous_icons=0, recognition_failures=0, failures=0,
                total_drops=0, unidentified_drops=0, drop_counts={}, confirmed_quantities={}, items=[], runs=[],
                outstanding_jobs=0, capture_pending=0, attention_required=0, recording_states={},
                collector_health=dict(status='Tracking Unavailable', error=None, next_retry_at=None),
                history_offset=max(0, int(offset)), history_limit=max(1, min(int(limit), 1000)))


def _passive_json(path):
    try:
        value = read_json_file(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _dashboard_pending(directory, account, device_id=None, session_id=None):
    """Read durable metadata without constructing a collector or repairing files."""
    answer = {}
    for path in (directory / 'pending').glob('*.json'):
        value = _passive_json(path)
        cid = value.get('completion_id')
        if (not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', cid)
                or path.name != cid + '.json' or value.get('account') != account
                or not isinstance(value.get('state'), str) or value['state'] not in PENDING_STATES
                or not isinstance(value.get('session_id'), str)
                or (session_id and value.get('session_id') != session_id)
                or (device_id and value.get('device_id', '') not in ('', device_id))):
            continue
        completed = value.get('completed_at')
        if type(completed) not in (float, int) or not math.isfinite(completed) or completed <= 0:
            continue
        screenshot = value.get('screenshot')
        if screenshot:
            try:
                if (not isinstance(screenshot, str) or
                        Path(screenshot).resolve() != (directory / 'pending' / (cid + '.png')).resolve()):
                    continue
            except (OSError, ValueError):
                continue
        answer[cid] = value
    return answer


def read_dashboard(root, snapshot, session_id=None, limit=100, offset=0):
    """Passive history and durable backlog access, including database outages.

    Monitoring never starts a collector or controls farming. Existing session
    endings may be reconciled; pending work and evidence are never changed here.
    """
    directory = Path(root) / 'runtime' / 'loot'
    path = directory / 'loot.sqlite3'
    detail = ('Running controller lacks loot instrumentation; available on its next natural start.'
              if snapshot.get('running') else 'Tracking starts with the next instrumented farming process.')
    data = _empty_dashboard(detail, limit, offset)
    account = snapshot.get('instance_account')
    device_id = (snapshot.get('instance') or {}).get('android_id')
    db_pending, database_error = {}, None
    database_available = False
    if path.exists():
        try:
            LootStore(root).reconcile_ended()
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
                db.row_factory = sqlite3.Row
                data = _dashboard(db, session_id, limit, offset, account=account)
                if session_id is None and snapshot.get('running'):
                    supervisor, farm = snapshot.get('supervisor') or {}, snapshot.get('farm') or {}
                    identity = supervisor.get('pid_identity') or farm.get('worker_pid_identity')
                    if not identity:
                        try:
                            identity, _ = process_identity(supervisor.get('pid') or farm.get('worker_pid'))
                        except (OSError, ValueError, TypeError):
                            identity = None
                    matching = next((s for s in data['sessions'] if s['process_identity'] == identity), None)
                    if matching and (not data['session'] or data['session']['session_id'] != matching['session_id']):
                        db.rollback()
                        data = _dashboard(db, matching['session_id'], limit, offset, account=account)
                    elif not matching:
                        sessions = data['sessions']
                        data = _empty_dashboard('This running process lacks loot instrumentation; next natural start required.', limit, offset)
                        data['sessions'] = sessions
                selected = data.get('session') or {}
                account = account or selected.get('account') or 'acc1'
                placeholders = ','.join('?' for _ in PENDING_STATES)
                query = ('SELECT c.* FROM completions c JOIN sessions s ON s.session_id=c.session_id '
                         f'WHERE s.account=? AND c.status IN ({placeholders})')
                parameters = [account, *PENDING_STATES]
                if session_id:
                    query += ' AND c.session_id=?'
                    parameters.append(session_id)
                db_pending = {row['completion_id']: dict(row) for row in db.execute(query, parameters)}
                database_available = True
        except (sqlite3.Error, OSError, ValueError) as exc:
            database_error = str(exc)
            data = _empty_dashboard('Loot history database is temporarily unavailable; saved pending work remains on disk.', limit, offset)
    account = account or 'acc1'
    pending = _dashboard_pending(directory, account, device_id, session_id)
    if not database_available and pending:
        sessions = {}
        for value in pending.values():
            sid = value.get('session_id')
            if not isinstance(sid, str):
                continue
            sessions.setdefault(sid, dict(session_id=sid, account=account, stage=value.get('stage'),
                process_identity=value.get('process_identity'), process_started=value.get('process_started'),
                tracking_started=value.get('tracking_started', value['completed_at']), ended_at=None, heartbeat=None))
        data['sessions'] = sorted(sessions.values(), key=lambda value: value['tracking_started'], reverse=True)
        data['session'] = next((s for s in data['sessions'] if s['session_id'] == session_id), None) if session_id else next(iter(data['sessions']), None)
        sid = (data['session'] or {}).get('session_id')
        jobs = [value for value in pending.values() if value.get('session_id') == sid]
        jobs.sort(key=lambda value: (value['completed_at'], value['completion_id']), reverse=True)
        data['completed_runs'] = len(jobs)
        data['runs'] = [{**value, 'status': value['state'], 'recording_state': value['state'],
                         'screenshot_path': value.get('screenshot'), 'warnings': [], 'rewards': [],
                         'unknown_icons': 0, 'ambiguous_icons': 0}
                        for value in jobs[data['history_offset']:data['history_offset'] + data['history_limit']]]
    combined = {**db_pending, **pending}
    states = {}
    for value in combined.values():
        state = value.get('state') or value.get('status')
        states[state] = states.get(state, 0) + 1
    data.update(outstanding_jobs=len(combined),
                capture_pending=sum(states.get(state, 0) for state in ('awaiting_evidence', 'capture_pending')),
                attention_required=states.get('operator_attention', 0))
    data['recording_states'] = {**{key: count for key, count in data['recording_states'].items() if key not in PENDING_STATES}, **states}
    for run in data['runs']:
        job = pending.get(run['completion_id'])
        if job:
            run.update(recording_state=job['state'], error=job.get('error'), next_retry_at=job.get('next_retry_at'))
    health = _passive_json(directory / f'health_{account}.json') if re.fullmatch(r'[A-Za-z0-9_-]{1,200}', account) else {}
    if (health.get('account') != account or (session_id and health.get('session_id') != session_id)
            or (device_id and health.get('device_id', '') not in ('', device_id))):
        health = {}
    data['collector_health'] = {key: health.get(key) for key in
                              ('status', 'error', 'next_retry_at', 'telemetry_error', 'operation_errors')}
    if database_error:
        data['collector_health'].update(status='Retryable Failure', error=database_error)
    if data['attention_required']:
        data['status'] = 'Operator Attention Required'
    elif data['capture_pending']:
        data['status'] = 'Screenshot Capture Pending'
    elif data['outstanding_jobs']:
        data['status'] = (health.get('status') or 'Retryable Failure') if health.get('error') else 'Recognition Pending'
    elif health.get('error') and not (data.get('session') or {}).get('ended_at'):
        data['status'] = health.get('status') or 'Retryable Failure'
    session = data.get('session')
    if (session and not session.get('ended_at') and session.get('heartbeat')
            and time.time() - session['heartbeat'] > 30 and not combined):
        data.update(status='Tracking Unavailable', detail='Loot collector heartbeat is stale; saved history remains available.')
    failure = _passive_json(directory / 'persistence_error.json')
    if (failure.get('session_id') == (session or {}).get('session_id') and failure.get('error')
            and failure.get('operation') != 'health_publication'
            and (data['outstanding_jobs'] or not (session or {}).get('ended_at'))):
        data['collector_health']['error'] = failure['error']
        data.update(status='Retryable Failure', detail='Persistence failure: ' + str(failure['error']))
    return data
