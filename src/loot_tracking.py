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

from process_state import process_identity


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


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
            sequence = event.get('run_sequence')
            if sequence is None:
                sequence = db.execute('SELECT COALESCE(MAX(run_sequence),0)+1 FROM completions WHERE session_id=?',
                                      (event['session_id'],)).fetchone()[0]
            db.execute("""INSERT INTO completions
                (completion_id,session_id,run_id,run_sequence,completed_at,status)
                VALUES(?,?,?,?,?,'awaiting_evidence') ON CONFLICT(completion_id) DO NOTHING""",
                       (event['completion_id'], event['session_id'], event['run_id'], sequence, event['completed_at']))

    def finalized(self, completion_id):
        with self.connect() as db:
            row = db.execute("SELECT status FROM completions WHERE completion_id=?", (completion_id,)).fetchone()
            return bool(row and row['status'] != 'awaiting_evidence')

    def finish(self, event, status, result, screenshot_path, warnings):
        self.accept(event)
        with self.connect() as db:
            db.execute("""UPDATE completions SET status=?,result_json=?,screenshot_path=?,warnings_json=?
                WHERE completion_id=? AND status='awaiting_evidence'""",
                       (status, json.dumps(result, ensure_ascii=False), screenshot_path,
                        json.dumps(warnings, ensure_ascii=False), event['completion_id']))

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
        if run['status'] not in ('recognized', 'uncertain', 'awaiting_evidence'):
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
    """Worker-owned collector; bounded pending recognition, durable replayable event spool.

    Only screenshot encoding/saving and small event writes occur at observation time.
    All item matching/reference loading runs in the background. The spool is separate
    from existing screenshots and logs, and is never inferred from historical counts.
    """
    def __init__(self, root, stage, process_identity, process_started=None, account="acc1",
                 recognizer_factory=None, max_pending=8):
        self.root = Path(root)
        self.store = LootStore(root)
        self.store.reconcile_ended()
        self.session_id = self.store.ensure_session(process_identity, stage, process_started, account)
        self.stage, self.account = stage, account
        self.directory = self.store.directory
        self.spool = self.directory / "pending"
        self.spool.mkdir(exist_ok=True)
        self.max_pending = max(1, int(max_pending))
        self.factory = recognizer_factory or (lambda: _recognizer(root))
        self.recognizer = None
        self._reload_references = recognizer_factory is None
        self._recognizer_revision = None
        # Explicit development opt-in: retain a bounded set of real validation
        # frames without changing normal production screenshot retention.
        self.validation_directory = None
        validation = os.environ.get('NBR_LOOT_VALIDATION_DIR')
        validation_limit = os.environ.get('NBR_LOOT_VALIDATION_MAX_RUNS', '4')
        # WMI independent launches do not inherit caller environment variables.
        # A bounded, expiring project-local opt-in supports those launches too.
        capture_config = self.root / 'runtime' / 'validation' / 'validation_capture.json'
        if capture_config.is_file():
            settings = json.loads(capture_config.read_text('utf-8'))
            if settings.get('enabled') and settings.get('expires_at', 0) > time.time():
                validation = settings.get('directory')
                validation_limit = settings.get('max_runs', 4)
        if validation:
            target = Path(validation).resolve()
            allowed = (self.root / 'runtime' / 'validation').resolve()
            if target.is_relative_to(allowed):
                target.mkdir(parents=True, exist_ok=True)
                self.validation_directory = target
        self.validation_limit = min(10, max(0, int(validation_limit)))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._busy = False
        self._attempts = {}
        self._generation = uuid.uuid4().hex
        self._inflight = set()
        self._events = {}
        self._ready_count = 0
        with self.store.connect() as db:
            self._sequence = db.execute('SELECT COALESCE(MAX(run_sequence),0) FROM completions WHERE session_id=?',
                                        (self.session_id,)).fetchone()[0]
        for path in self.spool.glob('*.json'):
            try:
                value = self._read_spool(path)
                if value is None:
                    continue
                self._ready_count += int(value['state'] == 'ready')
                if value['session_id'] == self.session_id:
                    self._sequence = max(self._sequence, value.get('run_sequence') or 0)
                if len(self._events) >= self.max_pending + 2:
                    continue
                if self.store.finalized(value['completion_id']):
                    self._cleanup(value)
                    continue
                if value['session_id'] != self.session_id and value['state'] == 'awaiting_evidence':
                    value['state'] = 'missing_screenshot'
                    _atomic_json(path, value)
                self._events[value['completion_id']] = value
            except Exception as exc:
                self._error(exc)
        self._thread = threading.Thread(target=self._loop, name="nbr-loot-recognition", daemon=True)
        self._thread.start()

    def _path(self, value):
        return self.spool / (value['completion_id'] + '.json')

    def _read_spool(self, path):
        """Isolate damaged records so one file cannot starve the durable queue."""
        try:
            value = json.loads(path.read_text('utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Spool record must be an object')
            for field in ('completion_id', 'session_id', 'account', 'stage'):
                if not isinstance(value.get(field), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value[field]):
                    raise ValueError(f'Invalid spool {field}')
            if path.name != value['completion_id'] + '.json':
                raise ValueError('Spool filename does not match completion ID')
            if value.get('state') not in {'awaiting_evidence', 'ready', 'missing_screenshot', 'backlog_limit'}:
                raise ValueError('Invalid spool state')
            if type(value.get('run_id')) not in (str, int):
                raise ValueError('Invalid spool run ID')
            sequence = value.get('run_sequence')
            if sequence is not None and (type(sequence) is not int or sequence < 1):
                raise ValueError('Invalid spool run sequence')
            completed = value.get('completed_at')
            if type(completed) not in (int, float) or not math.isfinite(completed) or completed <= 0:
                raise ValueError('Invalid spool completion time')
            attempts = value.get('persistence_attempts', 0)
            if type(attempts) is not int or attempts < 0:
                raise ValueError('Invalid spool retry count')
            screenshot = value.get('screenshot')
            if screenshot is not None:
                expected_screenshot = self.spool.resolve() / (value['completion_id'] + '.png')
                if (not isinstance(screenshot, str)
                        or Path(screenshot).resolve() != expected_screenshot):
                    raise ValueError('Spool screenshot must belong to this completion')
            return value
        except FileNotFoundError:
            return None  # A completed event may have just been removed.
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            try:
                quarantine = self.directory / 'quarantine'
                quarantine.mkdir(exist_ok=True)
                target = quarantine / f'{path.stem}_{uuid.uuid4().hex}.json'
                path.replace(target)
                _atomic_json(target.with_suffix('.error.json'), {
                    'source': path.name, 'time': time.time(), 'error': str(exc)})
                cached = self._events.pop(path.stem, None)
                if cached and cached.get('state') == 'ready':
                    self._ready_count = max(0, self._ready_count - 1)
                self._attempts.pop(path.stem, None)
                self._error(ValueError(f'Quarantined {path.name}: {exc}'))
            except OSError as quarantine_error:
                self._error(quarantine_error)
            return None
        except OSError as exc:
            self._error(exc)  # Transient access errors remain eligible for retry.
            return None

    def _completion_id(self, run_id):
        return self.session_id + '_' + hashlib.sha256(str(run_id).encode()).hexdigest()[:24]

    def _error(self, exc):
        # An independent durable ledger survives an unavailable SQLite database.
        try:
            _atomic_json(self.directory / 'persistence_error.json', {'time': time.time(), 'error': str(exc),
                         'session_id': self.session_id, 'category': 'persistence_failure'})
        except Exception:
            pass

    def completed(self, run_id, completed_at=None):
        try:
            with self._lock:
                cid = self._completion_id(run_id)
                if cid in self._events:
                    return cid
                try:
                    if self.store.finalized(cid):
                        return None
                except sqlite3.Error as exc:
                    self._error(exc)  # Preserve the event/evidence even when SQLite is unavailable.
                saved = self.spool / (cid + '.json')
                value = self._read_spool(saved) if saved.exists() else None
                if value is not None:
                    # A replayed completion keeps its original durable time and sequence.
                    while len(self._events) >= self.max_pending + 2:
                        removable = next((key for key, v in self._events.items()
                                          if key not in self._inflight and v['state'] != 'awaiting_evidence'), None)
                        if removable is None:
                            return cid  # Already durable; do not grow the active cache.
                        self._events.pop(removable)
                        self._attempts.pop(removable, None)
                    self._events[cid] = value
                    return cid
                for previous in list(self._events.values()):
                    if previous['session_id'] == self.session_id and previous['state'] == 'awaiting_evidence':
                        self.missing(previous['run_id'])
                while len(self._events) >= self.max_pending + 2:
                    removable = next((key for key, v in self._events.items()
                                      if key not in self._inflight and v['state'] != 'awaiting_evidence'), None)
                    if removable is None:
                        break
                    self._events.pop(removable)
                    self._attempts.pop(removable, None)
                self._sequence += 1
                value = dict(session_id=self.session_id, completion_id=cid, run_id=run_id, run_sequence=self._sequence,
                             completed_at=completed_at or time.time(), stage=self.stage, account=self.account,
                             state='awaiting_evidence', screenshot=None)
                _atomic_json(self._path(value), value)
                self._events[cid] = value
                return cid
        except Exception as exc:
            self._error(exc)
            return None

    def evidence(self, run_id, frame):
        """Save acquired-reward evidence for this completion."""
        try:
            with self._lock:
                value = self._events.get(self._completion_id(run_id))
                if not value or value['state'] != 'awaiting_evidence':
                    return False
                import cv2
                screenshot = self.spool / (value['completion_id'] + '.png')
                if not cv2.imwrite(str(screenshot), frame):
                    raise OSError('Unable to save results screenshot')
                with screenshot.open('r+b') as stream:
                    os.fsync(stream.fileno())
                value['screenshot'] = str(screenshot.resolve())
                value['state'] = 'ready' if self._ready_count < self.max_pending else 'backlog_limit'
                if value['state'] == 'ready':
                    self._ready_count += 1
                _atomic_json(self._path(value), value)
                if value['state'] == 'backlog_limit':
                    # Persist overload immediately without adding recognition work.
                    self._process(dict(value))
                return True
        except Exception as exc:
            self._error(exc)
            self.missing(run_id, 'screenshot_save_error: ' + str(exc))
            return False

    def missing(self, run_id, reason='missing_screenshot'):
        try:
            with self._lock:
                value = self._events.get(self._completion_id(run_id))
                if value and value['state'] == 'awaiting_evidence':
                    value['state'] = 'missing_screenshot'
                    value['error'] = reason
                    _atomic_json(self._path(value), value)
        except Exception as exc:
            self._error(exc)

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
        if value['state'] == 'awaiting_evidence':
            return
        if self.store.finalized(value['completion_id']):
            self._cleanup(value)
            return
        result, warnings = {}, []
        if value.get('persistence_error'):
            warnings.append('Recovered persistence failure: ' + value['persistence_error'])
        status = value['state']
        source = Path(value['screenshot']) if value.get('screenshot') else None
        if status == 'ready':
            if source is None or not source.is_file():
                status = 'missing_screenshot'
            else:
                try:
                    revision = _recognition_revision(self.root) if self._reload_references else None
                    if self.recognizer is None or (self._reload_references and revision != self._recognizer_revision):
                        # Build off to the side: never mutate a reference snapshot
                        # during matching or keep a partially loaded replacement.
                        replacement = self.factory()
                        if self._reload_references and _recognition_revision(self.root) != revision:
                            raise RuntimeError('Recognition inputs changed while references loaded; retry on the next completion.')
                        self.recognizer = replacement
                        self._recognizer_revision = revision
                    result = self.recognizer.analyze_screenshot(screenshot_path=source)
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
                except Exception as exc:
                    status = 'recognition_exception'
                    warnings.append(str(exc))
        if status != 'recognized':
            warnings.append(value.get('error', status))
        diagnostic = None
        if status != 'recognized' and source and source.is_file():
            stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime(value['completed_at']))
            target = self.directory / 'diagnostics' / f"{stamp}_{value['account']}_{value['stage']}_{value['completion_id']}_{status}.png"
            target.parent.mkdir(exist_ok=True)
            # Copy until the associated database record commits; never touch external originals.
            import shutil
            shutil.copyfile(source, target)
            diagnostic = str(target.resolve())
        self.store.finish(value, status, result, diagnostic, warnings)
        if self.validation_directory and source and source.is_file():
            target = self.validation_directory / (value['completion_id'] + '.png')
            if target.exists() or len(list(self.validation_directory.glob('*.png'))) < self.validation_limit:
                import shutil
                shutil.copyfile(source, target)
                _atomic_json(target.with_suffix('.json'), {**value, 'validation_screenshot': str(target),
                             'recognition_status': status, 'recognition_result': result})
        self._cleanup(value)
        failure = self.directory / 'persistence_error.json'
        if failure.exists() and not any(cid != value['completion_id'] for cid in self._attempts):
            error = json.loads(failure.read_text('utf-8'))
            if error.get('session_id') == self.session_id:
                failure.unlink(missing_ok=True)

    def _cleanup(self, value):
        if value.get('screenshot'):
            source = Path(value['screenshot'])
            if source.parent.resolve() == self.spool.resolve():
                source.unlink(missing_ok=True)
        self._path(value).unlink(missing_ok=True)
        with self._lock:
            self._events.pop(value['completion_id'], None)
            if value['state'] == 'ready':
                self._ready_count = max(0, self._ready_count - 1)

    def _loop(self):
        while not self._stop.is_set():
            try:
                with self.store.connect() as db:
                    db.execute('UPDATE sessions SET heartbeat=? WHERE session_id=?', (time.time(), self.session_id))
                with self._lock:
                    # Replay disk events in bounded batches; a failed database cannot grow RAM indefinitely.
                    for path in self.spool.glob('*.json'):
                        if len(self._events) >= self.max_pending + 2:
                            break
                        value = self._read_spool(path)
                        if value is None:
                            continue
                        cid = value['completion_id']
                        if (cid not in self._events and not
                            (value.get('retry_generation') == self._generation and value.get('persistence_attempts', 0) >= 3)):
                            if value['session_id'] != self.session_id and value['state'] == 'awaiting_evidence':
                                value['state'] = 'missing_screenshot'
                            self._events[cid] = value
                    batch = [dict(v) for v in self._events.values()]
                for value in batch:
                    cid = value['completion_id']
                    if self._attempts.get(cid, 0) >= 3:
                        continue
                    self._busy = True
                    try:
                        self._process(value)
                        self._attempts.pop(cid, None)
                    except Exception as exc:
                        self._attempts[cid] = self._attempts.get(cid, 0) + 1
                        self._error(exc)
                        with self._lock:
                            value = dict(self._events.get(cid, value))
                            value.update(retry_generation=self._generation, persistence_attempts=self._attempts[cid],
                                         persistence_error=str(exc))
                            try:
                                _atomic_json(self._path(value), value)
                            except Exception as spool_error:
                                self._error(spool_error)
                        if self._attempts[cid] >= 3:
                            # Keep durable spool for recovery, release failed in-memory work.
                            with self._lock:
                                self._events.pop(cid, None)
                                self._attempts.pop(cid, None)
                    finally:
                        self._busy = False
            except Exception as exc:
                self._error(exc)
            self._stop.wait(.2)

    def wait_idle(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._events and not self._busy and not any(self.spool.glob('*.json')):
                    return True
            time.sleep(.02)
        return False

    def close(self, end_session=False):
        # Also finalize waiting events evicted from RAM during persistence outages.
        for path in self.spool.glob('*.json'):
            try:
                with self._lock:
                    value = self._read_spool(path)
                if value is None:
                    continue
                if value['session_id'] == self.session_id and value['state'] == 'awaiting_evidence':
                    with self._lock:
                        value['state'] = 'missing_screenshot'
                        _atomic_json(path, value)
                        if value['completion_id'] in self._events:
                            self._events[value['completion_id']] = value
            except FileNotFoundError:
                pass
            except Exception as exc:
                self._error(exc)
        for value in list(self._events.values()):
            if value['session_id'] == self.session_id:
                self.missing(value['run_id'])
        self.wait_idle(timeout=2)
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


def read_dashboard(root, snapshot, session_id=None, limit=100, offset=0):
    """Passive Manager access: never starts collection or backfills runtime logs.

    Only ended-session metadata may be reconciled after abnormal process exits.
    """
    directory = Path(root) / 'runtime' / 'loot'
    path = directory / 'loot.sqlite3'
    if not path.exists():
        detail = ('Running controller lacks loot instrumentation; available on its next natural start.'
                  if snapshot.get('running') else 'Tracking starts with the next instrumented farming process.')
        return dict(status='Tracking Unavailable', detail=detail,
                    session=None, sessions=[], completed_runs=0, recognized_icons=0, unknown_icons=0, ambiguous_icons=0,
                    recognition_failures=0, failures=0, total_drops=0, unidentified_drops=0, drop_counts={},
                    confirmed_quantities={}, items=[], runs=[],
                    history_offset=max(0, int(offset)), history_limit=max(1, min(int(limit), 1000)))
    LootStore(root).reconcile_ended()
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
        db.row_factory = sqlite3.Row
        data = _dashboard(db, session_id, limit, offset, account=snapshot.get('instance_account'))
    session = data['session']
    if session_id is None and snapshot.get('running'):
        pid = snapshot.get('supervisor', {}).get('pid') or snapshot.get('farm', {}).get('worker_pid')
        identity, _ = process_identity(pid)
        matching = next((s for s in data['sessions'] if s['process_identity'] == identity), None)
        if not matching:
            data.update(status='Tracking Unavailable', detail='This running process lacks loot instrumentation; next natural start required.',
                        session=None, completed_runs=0, recognized_icons=0, unknown_icons=0, ambiguous_icons=0,
                        recognition_failures=0, failures=0, total_drops=0, unidentified_drops=0, drop_counts={},
                        confirmed_quantities={}, items=[], runs=[])
        elif not session or session['session_id'] != matching['session_id']:
            return read_dashboard(root, snapshot, matching['session_id'], limit, offset)
    if data['session'] and not data['session']['ended_at'] and time.time() - (data['session']['heartbeat'] or 0) > 30:
        data.update(status='Tracking Unavailable', detail='Loot collector heartbeat is stale; saved history remains available.')
    failure = directory / 'persistence_error.json'
    if failure.exists() and data.get('session'):
        error = json.loads(failure.read_text('utf-8'))
        if error.get('session_id') == data['session']['session_id']:
            data.update(status='Recognition Error', detail='Persistence failure: ' + error['error'])
    return data
