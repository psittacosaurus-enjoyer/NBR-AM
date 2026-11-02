"""Read-only audit of saved loot evidence against this repository's loot store.

Use --evidence-dir to select saved job JSON/PNG pairs. Results print as JSON;
--report-dir explicitly enables JSON/text reports. --require-count makes fewer
than --expected jobs an exit failure. Candidates and unknown quantities remain
distinct from evidence integrity. No controller, collector or device is loaded.
"""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import zlib

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PENDING = {'awaiting_evidence', 'capture_pending', 'screenshot_saved', 'ready',
           'recognition_pending', 'retryable_failure', 'operator_attention'}


def read_json(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def png_dimensions(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid PNG signature')
    offset, size, ended, image_data = 8, None, False, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + length + 12
        if end > len(data):
            raise ValueError('Truncated PNG payload')
        payload = data[offset + 8:end - 4]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError('PNG chunk CRC mismatch')
        if offset == 8 and kind != b'IHDR':
            raise ValueError('PNG does not begin with IHDR')
        if kind == b'IHDR':
            if size is not None or len(payload) != 13:
                raise ValueError('Invalid PNG header')
            size = struct.unpack('>II', payload[:8])
        if kind == b'IDAT':
            image_data = image_data or bool(payload)
        if kind == b'IEND':
            ended = end == len(data) and not payload
            break
        offset = end
    if not ended or not size or min(size) <= 0 or not image_data:
        raise ValueError('Incomplete PNG')
    # Reject unexpected dimensions before Pillow allocates decoded pixels.
    # Chunk checks alone do not establish that IDAT contains a valid image.
    if size != (900, 1600):
        raise ValueError('Unexpected screenshot dimensions')
    with Image.open(BytesIO(data)) as image:
        if image.format != 'PNG' or image.size != size:
            raise ValueError('PNG decoder/header mismatch')
        image.load()
    return size


def valid_box(box, width, height):
    return (isinstance(box, list) and len(box) == 4
            and all(type(value) is int for value in box)
            and box[0] >= 0 and box[1] >= 0 and box[2] > 0 and box[3] > 0
            and box[0] + box[2] <= width and box[1] + box[3] <= height)


def completion_id(value):
    if value.get('device_id'):
        identity = f"{value['account']}:{value['device_id']}:{value['stage']}:{value['run_id']}"
    else:
        identity = f"{value['session_id']}:{value['run_id']}"
    return 'job_' + hashlib.sha256(identity.encode()).hexdigest()[:48]


def canonical_identity(value):
    if value.get('device_id'):
        return ('device', *(str(value.get(key)) for key in ('account', 'device_id', 'stage', 'run_id')))
    return ('session', str(value.get('session_id')), str(value.get('run_id')))


def audit(root, evidence_dir, expected=3):
    """Return an integrity report without changing evidence or persistent state."""
    if type(expected) is not int or expected < 1:
        raise ValueError('Expected count must be a positive integer')
    root, evidence_dir = Path(root).resolve(), Path(evidence_dir).resolve()
    loot = root / 'runtime' / 'loot'
    errors, runs = [], []

    def check(ok, label):
        if not ok:
            errors.append(label)

    check(evidence_dir.is_dir(), 'Evidence directory does not exist: ' + str(evidence_dir))
    database = loot / 'loot.sqlite3'
    quick, foreign_keys, completions, sessions = [], [], [], {}
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            quick = [row[0] for row in db.execute('PRAGMA quick_check')]
            foreign_keys = [list(row) for row in db.execute('PRAGMA foreign_key_check')]
            check(quick == ['ok'], 'SQLite quick_check failed')
            check(not foreign_keys, 'SQLite foreign_key_check failed')
            completions = [dict(row) for row in db.execute('SELECT * FROM completions')]
            sessions = {row['session_id']: dict(row) for row in db.execute('SELECT * FROM sessions')}
            required = {'completion_id', 'session_id', 'run_id', 'run_sequence', 'completed_at', 'status', 'result_json'}
            if any(not required <= row.keys() for row in completions):
                raise ValueError('Completion table is missing required columns')
    except (sqlite3.Error, ValueError, OSError, KeyError) as exc:
        errors.append('Cannot audit loot database: ' + str(exc))
        completions, sessions = [], {}
    by_id = defaultdict(list)
    for row in completions:
        by_id[row['completion_id']].append(row)
    duplicate_checks = {}
    for name, fields in [('completion_id', ('completion_id',)),
                         ('session_run', ('session_id', 'run_id')),
                         ('session_sequence', ('session_id', 'run_sequence'))]:
        counts = Counter(tuple(row[field] for field in fields) for row in completions)
        duplicates = [list(key) for key, count in counts.items() if count > 1]
        duplicate_checks[name] = duplicates
        check(not duplicates, 'Global database duplicate: ' + name)
    raw_run_counts = Counter(str(row['run_id']) for row in completions)
    raw_run_repetitions = [dict(run_id=run_id, count=count)
                           for run_id, count in raw_run_counts.items() if count > 1]

    receipts, receipt_identity = {}, defaultdict(list)
    for path in sorted((loot / 'receipts').glob('job_*.json')):
        try:
            value = read_json(path)
            receipts[path.stem] = value
            identity = canonical_identity(value)
            receipt_identity[identity].append(path.stem)
            check(value.get('completion_id') == path.stem, 'Global receipt filename mismatch: ' + path.name)
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append(f'Cannot read global receipt {path.name}: {exc}')
    duplicate_receipts = [dict(identity=list(key), completion_ids=ids)
                          for key, ids in receipt_identity.items() if len(ids) > 1]
    check(not duplicate_receipts, 'Global receipt completion identity duplicate')
    # Database rows do not store device_id. Receipts supply device ownership;
    # rows without receipts retain the legacy session/run identity.
    canonical_rows = defaultdict(list)
    for row in completions:
        identity = canonical_identity(receipts.get(row['completion_id'], row))
        canonical_rows[identity].append(row['completion_id'])
    duplicate_checks['canonical_identity'] = [dict(identity=list(key), completion_ids=ids)
                                             for key, ids in canonical_rows.items() if len(ids) > 1]
    check(not duplicate_checks['canonical_identity'], 'Global database duplicate: canonical_identity')

    jobs = []
    for path in sorted(evidence_dir.glob('job_*.json')):
        try:
            job = read_json(path)
            required_text = ('completion_id', 'session_id', 'account', 'stage', 'process_identity',
                             'screenshot', 'screenshot_sha256', 'validation_screenshot', 'recognition_status')
            if any(not isinstance(job.get(key), str) or not job[key] for key in required_text):
                raise ValueError('Required job identity, status or evidence fields are missing or invalid')
            if type(job.get('run_id')) not in (str, int):
                raise ValueError('Invalid run ID')
            for key in ('completed_at', 'tracking_started'):
                if type(job.get(key)) not in (int, float) or not math.isfinite(job[key]) or job[key] <= 0:
                    raise ValueError('Invalid job timestamp: ' + key)
            if not isinstance(job.get('recognition_result'), dict):
                raise ValueError('Recognition result must be an object')
            for key in ('rewards', 'unknown_rewards'):
                entries = job['recognition_result'].get(key, [])
                if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                    raise ValueError('Invalid recognition entries: ' + key)
            jobs.append((path, job))
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append(f'Cannot read validation job {path.name}: {exc}')
    jobs.sort(key=lambda pair: pair[1].get('completed_at', 0))
    entry_statuses, quantity_statuses, identities, icon_counts = Counter(), Counter(), Counter(), Counter()
    for path, job in jobs:
        before = len(errors)
        cid = path.stem
        label = cid + ': '
        check(job.get('completion_id') == cid, label + 'job filename/ID mismatch')
        check(completion_id(job) == cid, label + 'derived account/device/stage/run ID mismatch')
        result = job.get('recognition_result', {})
        png = path.with_suffix('.png')
        width = height = 0
        digest = None
        try:
            data = png.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            width, height = png_dimensions(data)
        except (ValueError, OSError, struct.error) as exc:
            errors.append(label + 'invalid screenshot: ' + str(exc))
        check((width, height) == (900, 1600), label + 'unexpected screenshot dimensions')
        check(digest == job.get('screenshot_sha256') == result.get('screenshot_sha256'), label + 'screenshot SHA mismatch')
        try:
            saved_path = Path(job['validation_screenshot'])
            if not saved_path.is_absolute():
                saved_path = evidence_dir / saved_path
            check(saved_path.resolve() == png.resolve(), label + 'validation screenshot path mismatch')
        except (ValueError, OSError) as exc:
            errors.append(label + 'invalid validation screenshot path: ' + str(exc))
        receipt = receipts.get(cid)
        check(receipt is not None, label + 'missing durable receipt')
        if receipt:
            check(receipt.get('recorded') is True, label + 'receipt is not recorded')
            for key in ('completion_id', 'session_id', 'run_id', 'run_sequence', 'completed_at', 'stage',
                        'account', 'device_id', 'process_identity', 'screenshot_sha256', 'screenshot'):
                check(receipt.get(key) == job.get(key), label + 'receipt mismatch: ' + key)
        rows = by_id.get(cid, [])
        check(len(rows) == 1, label + 'expected exactly one database completion')
        row = rows[0] if rows else {}
        if row:
            for key in ('completion_id', 'session_id', 'run_id', 'run_sequence', 'completed_at'):
                matches = (str(row.get(key)) == str(job.get(key)) if key == 'run_id'
                           else row.get(key) == job.get(key))
                check(matches, label + 'database identity mismatch: ' + key)
            check(row.get('status') == job.get('recognition_status'), label + 'database status mismatch')
            check(row.get('status') not in PENDING, label + 'database completion is pending')
            try:
                check(json.loads(row['result_json']) == result, label + 'database result differs from saved recognition result')
            except (ValueError, TypeError) as exc:
                errors.append(label + 'invalid database recognition JSON: ' + str(exc))
            session = sessions.get(row['session_id'], {})
            for key in ('account', 'stage', 'process_identity', 'process_started'):
                check(session.get(key) == job.get(key), label + 'database session mismatch: ' + key)
            # ensure_session persists its timestamp before Collector sets its
            # own tracking_started value. Those clocks intentionally differ.
            check(type(session.get('tracking_started')) in (int, float)
                  and session['tracking_started'] <= job.get('tracking_started', 0)
                  <= job.get('completed_at', 0), label + 'invalid session/collector/completion time ordering')
        entries = result.get('rewards', []) + result.get('unknown_rewards', [])
        check(bool(entries), label + 'no reward entries')
        check(result.get('screen_type') == 'acquired', label + 'screen is not acquired rewards')
        seen, details, numerical_totals = set(), [], Counter()
        for index, entry in enumerate(entries, 1):
            entry_label = label + f'entry {index}: '
            box = entry.get('bounding_box')
            check(valid_box(box, width, height), entry_label + 'bounding box is outside screenshot or invalid')
            key = tuple(box) if valid_box(box, width, height) else None
            check(key not in seen, entry_label + 'duplicate bounding box')
            seen.add(key)
            check(entry.get('reward_scope') == 'acquired', entry_label + 'entry is not acquired')
            check(entry.get('visual_state') in ('icon', 'character'), entry_label + 'entry is not revealed')
            quantity, status = entry.get('quantity'), entry.get('quantity_status')
            check(status in ('verified', 'unknown', 'ambiguous'), entry_label + 'invalid quantity status')
            identity_status = entry.get('identity_status')
            check(identity_status in ('matched', 'candidate', 'unknown', 'ambiguous'), entry_label + 'invalid identity status')
            if identity_status in ('matched', 'candidate'):
                check(all(isinstance(entry.get(key), str) and entry[key] for key in ('item_id', 'item_name')),
                      entry_label + 'matched/candidate identity is missing an ID or name')
            if status == 'verified':
                check(type(quantity) is int and quantity > 0, entry_label + 'invalid verified quantity')
                if identity_status == 'matched' and type(quantity) is int and isinstance(entry.get('item_id'), str):
                    numerical_totals[entry['item_id']] += quantity
            else:
                check(quantity is None, entry_label + 'unverified quantity was assigned a number')
            qbox = entry.get('quantity_bounding_box')
            if qbox is not None:
                check(valid_box(qbox, width, height), entry_label + 'invalid quantity bounding box')
            entry_statuses[str(identity_status)] += 1
            quantity_statuses[str(status)] += 1
            identities[tuple(str(entry.get(key)) for key in ('item_id', 'item_name', 'identity_status'))] += 1
            icon_counts[str(entry.get('item_name') or 'Unknown icon')] += 1
            details.append({key: entry.get(key) for key in ('item_id', 'item_name', 'identity_status', 'match_score',
                            'quantity', 'quantity_status', 'reward_scope', 'visual_state', 'bounding_box')})
        check(dict(numerical_totals) == result.get('totals', {}), label + 'numerical totals include unverified quantities or differ from entries')
        runs.append(dict(completion_id=cid, run_id=job.get('run_id'), run_sequence=job.get('run_sequence'),
                         session_id=job.get('session_id'), account=job.get('account'), device_id=job.get('device_id'),
                         completed_at=job.get('completed_at'), database_status=row.get('status'),
                         recognition_status=result.get('status'), screenshot_sha256=digest,
                         dimensions=[width, height], screenshot=str(png), entry_count=len(entries), entries=details,
                         integrity_ok=len(errors) == before, errors=errors[before:]))

    pending_db = [{key: row[key] for key in ('completion_id', 'session_id', 'run_id', 'status')}
                  for row in completions if row['status'] in PENDING]
    pending_files = []
    for path in sorted((loot / 'pending').glob('*.json')):
        try:
            value = read_json(path)
            pending_files.append({key: value.get(key) for key in ('completion_id', 'run_id', 'state', 'attempts', 'error')})
        except (ValueError, OSError, UnicodeError) as exc:
            pending_files.append(dict(path=str(path), error=str(exc)))
            errors.append('Cannot read pending spool ' + path.name + ': ' + str(exc))
    target_ids = {run['completion_id'] for run in runs}
    check(not any(value.get('completion_id') in target_ids for value in pending_files), 'Audited completion remains in pending spool')
    return dict(audited_at_utc=datetime.now(timezone.utc).isoformat(), database_open_mode='ro; query_only=ON',
                repository_root=str(root), evidence_directory=str(evidence_dir),
                captured_count=len(runs), expected_count=expected, expected_count_met=len(runs) >= expected,
                integrity_ok=not errors, errors=errors, database_quick_check=quick, database_foreign_key_issues=foreign_keys,
                global_database_completion_count=len(completions), global_receipt_count=len(receipts),
                global_database_duplicates=duplicate_checks, global_receipt_duplicates=duplicate_receipts,
                global_run_id_repetitions=raw_run_repetitions,
                pending_database=pending_db, pending_spool=pending_files,
                backlog_empty=not pending_db and not pending_files,
                all_entries_identified=bool(runs) and set(entry_statuses) == {'matched'},
                identity_status_counts=dict(entry_statuses), quantity_status_counts=dict(quantity_statuses),
                identities=[dict(item_id=key[0], item_name=key[1], identity_status=key[2], visible_icon_count=count)
                            for key, count in identities.items()], visible_icon_counts=dict(icon_counts),
                quantity_note='Visible icon counts are observations, not verified in-game item quantities. Unknown quantities remain null and excluded from numerical totals.',
                runs=runs)


def text_report(report):
    lines = [f"Integrity: {'PASS' if report['integrity_ok'] else 'FAIL'}",
             f"Captured runs: {report['captured_count']}/{report['expected_count']}; expected count met: {report['expected_count_met']}",
             f"SQLite quick_check: {report['database_quick_check']}; foreign-key issues: {len(report['database_foreign_key_issues'])}",
             f"Pending database/spool: {len(report['pending_database'])}/{len(report['pending_spool'])}",
             f"Identity statuses: {report['identity_status_counts']}", f"Quantity statuses: {report['quantity_status_counts']}",
             report['quantity_note'], '']
    for run in report['runs']:
        lines.append(f"{run['completion_id']}: {run['database_status']}, {run['entry_count']} visible icons, integrity={run['integrity_ok']}")
        for entry in run['entries']:
            lines.append(f"  {entry['bounding_box']}: {entry['item_name']} ({entry['identity_status']}), quantity={entry['quantity']} ({entry['quantity_status']})")
    lines.extend(['', *report['errors']])
    return '\n'.join(lines) + '\n'


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def write_report(path, content):
    # Replacing the report directory entry also avoids following an existing
    # report symlink or modifying evidence through an existing hard link.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.integrity-', suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(content)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True, help='saved job JSON/PNG directory')
    parser.add_argument('--expected', type=positive_integer, default=3)
    parser.add_argument('--require-count', action='store_true')
    parser.add_argument('--report-dir', type=Path, help='explicit destination for integrity_report.json/.txt')
    args = parser.parse_args(argv)
    report = audit(ROOT, args.evidence_dir, args.expected)
    if args.report_dir is not None:
        try:
            report_dir = args.report_dir.resolve()
            report_dir.mkdir(parents=True, exist_ok=True)
            write_report(report_dir / 'integrity_report.json',
                         json.dumps(report, indent=2, ensure_ascii=False) + '\n')
            write_report(report_dir / 'integrity_report.txt', text_report(report))
        except OSError as exc:
            report['errors'].append('Cannot write requested reports: ' + str(exc))
            report['integrity_ok'] = False
    # Escaped JSON remains valid on redirected Windows cp1252/ascii stdout.
    # Explicit report files keep UTF-8 text for readable item names.
    print(json.dumps(report))
    return 0 if report['integrity_ok'] and (report['expected_count_met'] or not args.require_count) else 1


if __name__ == '__main__':
    sys.exit(main())
