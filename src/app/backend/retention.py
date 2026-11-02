"""Bound finalized diagnostic evidence without evicting unfinished recordings."""
from __future__ import annotations

from contextlib import contextmanager, closing
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid

from app.core.atomic_io import atomic_json


PENDING_STATES = frozenset({'awaiting_evidence', 'capture_pending', 'screenshot_saved',
                            'ready', 'recognition_pending', 'retryable_failure', 'operator_attention'})
PRUNED_WARNING = 'Finalized diagnostic screenshot pruned by the evidence retention policy; loot records retained.'
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class EvidenceCapacityError(OSError):
    def __init__(self, used_bytes, limit_bytes, required_bytes):
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes
        self.required_bytes = required_bytes
        super().__init__(f'Loot evidence storage limit ({limit_bytes} bytes) reached: '
                         f'{used_bytes} bytes retained, {required_bytes} bytes required; '
                         'remaining evidence is protected or not eligible for safe pruning')


class EvidenceRetention:
    """Journaled oldest-first pruning of DB-linked, finalized diagnostics.

    ``admission`` holds the process-shared lock through its caller's PNG commit.
    Incoming bytes are the caller's incremental peak storage reservation.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / 'runtime' / 'loot'
        self.diagnostics = self.directory / 'diagnostics'
        self.database = self.directory / 'loot.sqlite3'
        self.journal_path = self.directory / 'retention_journal.json'
        self.lock_path = self.directory / 'retention.lock'

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        key = os.path.normcase(str(self.lock_path))
        with _LOCKS_GUARD:
            mutex = _LOCKS.setdefault(key, threading.RLock())
        with mutex, self.lock_path.open('a+b') as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'\0')
                stream.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                        raise OSError('Evidence retention is busy; retry capture') from exc
                    time.sleep(.02)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _journal(self):
        try:
            value = json.loads(self.journal_path.read_text('utf-8'))
        except FileNotFoundError:
            return {'schema_version': 1, 'entries': []}
        if (not isinstance(value, dict) or value.get('schema_version') != 1
                or not isinstance(value.get('entries'), list)):
            raise OSError('Invalid evidence retention journal; preserve evidence for review')
        for entry in value['entries']:
            if (not isinstance(entry, dict) or entry.get('phase') not in ('prepared', 'deleted', 'committed', 'cancelled')
                    or not isinstance(entry.get('completion_id'), str) or not isinstance(entry.get('path'), str)
                    or not re.fullmatch('[0-9a-f]{64}', str(entry.get('sha256', '')))
                    or not isinstance(entry.get('size'), int) or entry['size'] < 0):
                raise OSError('Invalid evidence retention journal entry; preserve evidence for review')
        return value

    def _write_journal(self, journal):
        atomic_json(self.journal_path, journal)

    def _direct_diagnostic(self, value):
        if not isinstance(value, str):
            return None
        if self.diagnostics.resolve() != self.diagnostics:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        # Reject symlinks/reparse points and nested paths before resolving.
        if path.parent.resolve() != self.diagnostics.resolve() or path.suffix.lower() != '.png':
            return None
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path if path.parent == self.diagnostics else None
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            return None
        if path.resolve().parent != self.diagnostics.resolve():
            return None
        return path

    @staticmethod
    def _hash(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()

    def _usage(self):
        used = 0
        for folder in (self.directory / 'pending', self.diagnostics):
            for path in folder.glob('*.png'):
                if path.name.endswith('.tmp.png'):
                    continue
                try:
                    used += path.stat().st_size
                except FileNotFoundError:
                    pass  # Recognition may finish and remove a pending PNG.
        return used

    def _reference_files(self):
        folders = [self.root / 'assets', self.root / 'tests', self.root / 'docs',
                   self.root / 'runtime' / 'validation', self.root / 'runtime' / 'recovery',
                   self.root / 'runtime' / 'item_recognition']
        suffixes = {'.json', '.jsonl', '.py', '.md', '.txt', '.yaml', '.yml', '.toml', '.csv'}
        for folder in folders:
            if folder.exists():
                yield from (path for path in folder.rglob('*') if path.is_file() and path.suffix.lower() in suffixes)
        runtime = self.root / 'runtime'
        if runtime.exists():
            yield from runtime.glob('*.json')
        accounts = runtime / 'accounts'
        if accounts.exists():
            yield from accounts.rglob('*.json')

    def _protections(self, rows, protected_ids, protected_paths):
        ids = set(map(str, protected_ids))
        paths = {os.path.normcase(str(Path(path).resolve())) for path in protected_paths}
        names, hashes = set(), set()
        pending = self.directory / 'pending'
        if pending.exists():
            for path in pending.glob('*.json'):
                ids.add(path.stem)
                # Invalid/unreadable pending work must never weaken protection.
                try:
                    value = json.loads(path.read_text('utf-8'))
                except FileNotFoundError:
                    continue  # A finalized job may disappear during cleanup.
                if not isinstance(value, dict):
                    raise OSError(f'Invalid pending recording {path.name}; pruning deferred')
                ids.add(str(value.get('completion_id', path.stem)))
                if value.get('screenshot'):
                    paths.add(os.path.normcase(str(Path(value['screenshot']).resolve())))
        per_account = {}
        for row in sorted(rows, key=lambda value: (value['completed_at'], value['completion_id']), reverse=True):
            if row['status'] not in PENDING_STATES and row['screenshot_path']:
                path = self._direct_diagnostic(row['screenshot_path'])
                if path and path.exists():
                    retained = per_account.setdefault(row['account'], 0)
                    if retained < 3:
                        ids.add(row['completion_id'])
                        per_account[row['account']] = retained + 1
        # Basename and digest references also cover relative paths and JSON escapes.
        name_match = re.compile(r'[^\s"\'<>/\\()\[\]{}=:;,]+\.png', re.IGNORECASE)
        for path in self._reference_files():
            try:
                text = path.read_text('utf-8', errors='replace')
            except FileNotFoundError:
                continue  # An obsolete validation/progress snapshot was removed.
            names.update(match.lower() for match in name_match.findall(text))
            hashes.update(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', text))
            ids.update(re.findall(r'\bjob_[A-Za-z0-9_]+\b', text))
        return ids, paths, names, hashes

    def _rows(self, db):
        return [dict(row) for row in db.execute(
            'SELECT c.*,s.account FROM completions c JOIN sessions s USING(session_id)')]

    def _protected(self, row, path, protections, digest=None):
        ids, paths, names, hashes = protections
        return (row['completion_id'] in ids or row['status'] in PENDING_STATES
                or os.path.normcase(str(path.resolve())) in paths or path.name.lower() in names
                or (digest is not None and digest in hashes)
                or (self.directory / 'pending' / (row['completion_id'] + '.json')).exists())

    def _clear_reference(self, db, row, expected_path):
        warnings = json.loads(row['warnings_json'])
        if not isinstance(warnings, list):
            raise OSError('Invalid completion warnings; evidence pruning deferred')
        if PRUNED_WARNING not in warnings:
            warnings.append(PRUNED_WARNING)
        db.execute('UPDATE completions SET screenshot_path=NULL,warnings_json=? '
                   'WHERE completion_id=? AND screenshot_path=?',
                   (json.dumps(warnings, ensure_ascii=False), row['completion_id'], expected_path))

    def _finish_entry(self, db, journal, entry, protections):
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        path = self._direct_diagnostic(str(self.root / entry['path']))
        row = db.execute('SELECT c.*,s.account FROM completions c JOIN sessions s USING(session_id) '
                         'WHERE completion_id=?', (entry['completion_id'],)).fetchone()
        if path is None or row is None or row['status'] in PENDING_STATES:
            entry['phase'] = 'cancelled'
            self._write_journal(journal)
            return False
        row = dict(row)
        linked = self._direct_diagnostic(row['screenshot_path']) if row['screenshot_path'] else None
        if (linked is not None and linked != path) or (row['screenshot_path'] and linked is None):
            entry['phase'] = 'cancelled'
            self._write_journal(journal)
            return False
        if path.exists():
            warnings = json.loads(row['warnings_json'])
            another_reference = any(other['completion_id'] != row['completion_id']
                                    and self._direct_diagnostic(other['screenshot_path']) == path
                                    for other in self._rows(db) if other['screenshot_path'])
            if (not row['screenshot_path'] or not isinstance(warnings, list) or another_reference
                    or self._protected(row, path, protections, entry['sha256'])
                    or path.stat().st_size != entry['size'] or self._hash(path) != entry['sha256']):
                entry['phase'] = 'cancelled'
                self._write_journal(journal)
                return False
            path.unlink()
        entry['phase'] = 'deleted'
        self._write_journal(journal)
        if row['screenshot_path']:
            self._clear_reference(db, row, row['screenshot_path'])
        db.commit()
        entry['phase'] = 'committed'
        entry['committed_at'] = time.time()
        self._write_journal(journal)
        return True

    def _reconcile_locked(self, db, journal, protections):
        count = reclaimed = 0
        for entry in journal['entries']:
            if entry['phase'] in ('prepared', 'deleted'):
                existed = (self.root / entry['path']).exists()
                if self._finish_entry(db, journal, entry, protections):
                    count += 1
                    reclaimed += entry['size'] if existed else 0
        return count, reclaimed

    def reconcile(self, protected_completion_ids=(), extra_protected_paths=()):
        with self._locked():
            journal = self._journal()
            unfinished = any(entry['phase'] in ('prepared', 'deleted') for entry in journal['entries'])
            if not self.database.exists() or not unfinished:
                return {'pruned_count': 0, 'reclaimed_bytes': 0, 'used_bytes': self._usage(),
                        'journal_path': str(self.journal_path)}
            with closing(sqlite3.connect(self.database, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN IMMEDIATE')
                protections = self._protections(self._rows(db), protected_completion_ids, extra_protected_paths)
                count, reclaimed = self._reconcile_locked(db, journal, protections)
                db.commit()
                return {'pruned_count': count, 'reclaimed_bytes': reclaimed, 'used_bytes': self._usage(),
                        'journal_path': str(self.journal_path)}

    @contextmanager
    def admission(self, incoming_bytes, max_storage_bytes, protected_completion_ids=(), extra_protected_paths=()):
        incoming, limit = max(0, int(incoming_bytes)), max(1, int(max_storage_bytes))
        with self._locked():
            used = self._usage()
            if incoming > limit:
                raise EvidenceCapacityError(used, limit, incoming)
            journal = self._journal()
            unfinished = any(entry['phase'] in ('prepared', 'deleted') for entry in journal['entries'])
            if used + incoming <= limit and not unfinished:
                yield {'pruned_count': 0, 'reclaimed_bytes': 0, 'used_bytes': used,
                       'journal_path': str(self.journal_path)}
                return
            count = reclaimed = 0
            if self.database.exists():
                with closing(sqlite3.connect(self.database, timeout=2)) as db:
                    db.row_factory = sqlite3.Row
                    db.execute('BEGIN IMMEDIATE')
                    rows = self._rows(db)
                    protections = self._protections(rows, protected_completion_ids, extra_protected_paths)
                    count, reclaimed = self._reconcile_locked(db, journal, protections)
                    used = self._usage()
                    if used + incoming > limit:
                        target = min(int(limit * .8), limit - incoming)
                        references = {}
                        for row in rows:
                            if row['screenshot_path']:
                                linked = self._direct_diagnostic(row['screenshot_path'])
                                if linked is not None:
                                    key = os.path.normcase(str(linked.resolve()))
                                    references[key] = references.get(key, 0) + 1
                        for row in sorted(rows, key=lambda value: (value['completed_at'], value['completion_id'])):
                            if used <= target:
                                break
                            path = self._direct_diagnostic(row['screenshot_path'])
                            if (path is None or not path.exists() or self._protected(row, path, protections)
                                    or references.get(os.path.normcase(str(path.resolve())), 0) != 1):
                                continue
                            digest = self._hash(path)
                            if self._protected(row, path, protections, digest):
                                continue
                            entry = {'id': uuid.uuid4().hex, 'phase': 'prepared',
                                     'completion_id': row['completion_id'], 'account': row['account'],
                                     'path': str(path.relative_to(self.root)), 'sha256': digest,
                                     'size': path.stat().st_size, 'prepared_at': time.time()}
                            journal['entries'].append(entry)
                            self._write_journal(journal)
                            if self._finish_entry(db, journal, entry, protections):
                                count += 1
                                reclaimed += entry['size']
                            used = self._usage()
                    db.commit()
            if used + incoming > limit:
                raise EvidenceCapacityError(used, limit, incoming)
            yield {'pruned_count': count, 'reclaimed_bytes': reclaimed, 'used_bytes': used,
                   'journal_path': str(self.journal_path)}

    def ensure_capacity(self, incoming_bytes, max_storage_bytes, protected_completion_ids=(), extra_protected_paths=()):
        with self.admission(incoming_bytes, max_storage_bytes, protected_completion_ids, extra_protected_paths) as info:
            return info
