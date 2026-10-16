"""Prepare a physically deduplicated library copy; never change the source library."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

try:
    from . import library as L
    from .fandom_import import compatible, namespace, normalized, preferred_score
except ImportError:
    import library as L
    from fandom_import import compatible, namespace, normalized, preferred_score


PROJECT = Path(__file__).resolve().parents[2]
ACTIVE_LIBRARY = (PROJECT / 'runtime/library').resolve()


def _identifier(value):
    value = str(value).strip()
    return str(int(value)) if value.isdigit() else value.casefold()


def _fact(field, value):
    if field == 'element':
        value = {'1': 'Heart', '2': 'Skill', '3': 'Body', '4': 'Bravery', '5': 'Wisdom'}.get(str(value), value)
    return normalized(value)


def _records(db):
    records = {}
    for row in db.execute(L.SELECT + " WHERE r.record_type='item'"):
        record = L.effective(row)
        record['manual_facts'] = {key: row['manual_' + key] for key in ('display_name', 'category')
                                  if row['manual_' + key] is not None}
        record['assets'] = [dict(asset) for asset in db.execute(
            'SELECT a.*,ra.role FROM assets a JOIN record_assets ra ON a.id=ra.asset_id '
            'WHERE ra.record_id=?', (record['id'],))]
        records[record['id']] = record
    return records


def _source_cards(record):
    cards = set()
    for asset in record['assets']:
        if asset['role'] not in ('card', 'icon', 'image'):
            continue
        match = re.search(r'/(?:missions|lb|materials)/card-(\d+)\.|/Card-(\d+)\.', asset['source_image_url'])
        if match:
            cards.add(str(int(next(value for value in match.groups() if value))))
    if namespace(record) == 'card_no' and record.get('game_id') is not None:
        cards.add(_identifier(record['game_id']))
    return cards


def _compatible(records, proof=None):
    names = {normalized(r['display_name']) for r in records}
    special_tool = (proof and proof.get('kind') == 'explicit_source_card' and len(names) == 1
        and 'special awakening tool' in next(iter(names))
        and {str(r.get('category', '')).casefold() for r in records} == {'special', 'scroll'}
        and any(r['metadata'].get('source') == 'Fandom'
                and r['metadata'].get('source_data', {}).get('source_type') == 'Awakening' for r in records))
    for first in records:
        for second in records:
            scope = namespace(first)
            if (scope and scope == namespace(second) and first.get('game_id') is not None
                    and second.get('game_id') is not None
                    and _identifier(first['game_id']) != _identifier(second['game_id'])):
                return False
            first_facts = {**first, 'category': 'special'} if special_tool else first
            second_facts = {**second, 'category': 'special'} if special_tool else second
            if not compatible(first_facts, second_facts):
                return False
    # Two independently reviewed labels must never overwrite one another.
    manual = [r for r in records if r['verification_status'] == 'manually_verified']
    return (len({normalized(r['display_name']) for r in manual}) <= 1 and
            all(len({normalized(r['manual_facts'][key]) for r in records if key in r['manual_facts']}) <= 1
                for key in ('display_name', 'category')))


def _plan(db, calibration, preferred_ids):
    records = _records(db)
    groups = {rid: {rid} for rid in records}
    evidence, rejected = [], []
    preferred = list(dict.fromkeys([*preferred_ids,
        *(entry.get('canonical_item_id') or entry.get('canonical_id')
          for entry in calibration.get('canonical_groups', []) if entry.get('evidence')),
        *calibration.get('supported_item_ids', [])]))
    priority = {rid: index for index, rid in enumerate(preferred)}

    def merge(ids, proof):
        ids = set(ids)
        if len(ids) < 2 or not ids <= records.keys():
            return
        members = set().union(*(groups[rid] for rid in ids))
        if not _compatible([records[rid] for rid in members], proof):
            rejected.append({'member_ids': sorted(members), 'reason': 'Conflicting identity facts', 'proof': proof})
            return
        for rid in members:
            groups[rid] = members
        evidence.append({'member_ids': sorted(members), **proof})

    aliases = list(db.execute('SELECT * FROM identity_aliases'))
    for alias in aliases:
        merge([alias['alias_id'], alias['canonical_id']],
              {'kind': 'explicit_library_alias', 'canonical_id': alias['canonical_id'],
               'evidence': alias['evidence']})
    by_card, by_id = defaultdict(list), defaultdict(list)
    for rid, record in records.items():
        cards = _source_cards(record)
        if len(cards) == 1:
            card = next(iter(cards))
            # A stated card-number namespace cannot contradict its own asset URL.
            if (namespace(record) != 'card_no' or record.get('game_id') is None
                    or _identifier(record['game_id']) == card):
                by_card[card].append(rid)
        scope = namespace(record)
        if scope and record.get('game_id'):
            by_id[(scope, _identifier(record['game_id']))].append(rid)
    for card, ids in by_card.items():
        merge(ids, {'kind': 'explicit_source_card', 'source_card_id': card})
    for (scope, game_id), ids in by_id.items():
        merge(ids, {'kind': 'scoped_game_id', 'namespace': scope, 'game_id': game_id})
    mapping, details = {}, []
    declared = {row['canonical_id'] for row in aliases}
    for members in sorted({tuple(sorted(group)) for group in groups.values()}):
        canonical = min(members, key=lambda rid: (priority.get(rid, len(priority)),
            rid not in declared, records[rid]['verification_status'] != 'manually_verified',
            -sum(records[rid].get(field) is not None for field in ('game_id', 'element', 'rarity')), rid))
        mapping.update({rid: canonical for rid in members})
        if len(members) > 1:
            details.append({'canonical_id': canonical, 'member_ids': list(members),
                            'evidence': [e for e in evidence if set(e['member_ids']) <= set(members)]})
    collisions = []
    by_art, by_name = defaultdict(set), defaultdict(set)
    for rid, record in records.items():
        by_name[normalized(record['display_name'])].add(mapping[rid])
        for asset in record['assets']:
            if asset.get('sha256'):
                by_art[asset['sha256']].add(mapping[rid])
    for kind, bucket in (('same_name', by_name), ('identical_artwork', by_art)):
        for value, ids in bucket.items():
            if len(ids) > 1:
                pairs = []
                ordered = sorted(ids)
                for index, first in enumerate(ordered):
                    for second in ordered[index + 1:]:
                        a, b = records[first], records[second]
                        differences = [field for field in ('rarity', 'element')
                                       if a.get(field) is not None and b.get(field) is not None
                                       and _fact(field, a[field]) != _fact(field, b[field])]
                        if (namespace(a) and namespace(a) == namespace(b) and a.get('game_id') is not None
                                and b.get('game_id') is not None and _identifier(a['game_id']) != _identifier(b['game_id'])):
                            differences.append('scoped_game_id')
                        if _source_cards(a) and _source_cards(b) and _source_cards(a).isdisjoint(_source_cards(b)):
                            differences.append('explicit_source_card')
                        pairs.append({'record_ids': [first, second], 'distinct_facts': differences})
                collisions.append({'kind': kind, 'value': value, 'record_ids': ordered,
                                   'classification': 'confirmed_distinct' if all(p['distinct_facts'] for p in pairs) else 'unresolved',
                                   'pairs': pairs, 'reason': 'Shared label or pixels do not prove shared item identity'})
    return mapping, details, rejected, collisions


def _merge(db, mapping):
    removed = {rid: canonical for rid, canonical in mapping.items() if rid != canonical}
    db.execute('CREATE TABLE IF NOT EXISTS deduplication_history '
               '(original_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL REFERENCES records(id),original_json TEXT NOT NULL)')
    for rid, canonical in removed.items():
        original = dict(db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone())
        annotation = db.execute('SELECT * FROM annotations WHERE record_id=?', (rid,)).fetchone()
        db.execute('INSERT INTO deduplication_history VALUES(?,?,?)',
                   (rid, canonical, json.dumps({'record': original, 'annotation': dict(annotation) if annotation else None})))
        db.execute('INSERT OR IGNORE INTO record_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('Deduplicated record', rid, canonical, original['source_url'], original['display_name'],
             original['game_id'], original['category'], original['element'], original['rarity'],
             original['metadata'], original['verification_status'], original['content_hash']))
        db.execute('INSERT OR IGNORE INTO record_assets SELECT ?,asset_id,role FROM record_assets WHERE record_id=?',
                   (canonical, rid))
        db.execute('INSERT INTO asset_preferences SELECT ?,asset_id,fidelity_score,basis FROM asset_preferences '
                   'WHERE record_id=? ON CONFLICT(record_id,asset_id) DO UPDATE SET '
                   'fidelity_score=MAX(asset_preferences.fidelity_score,excluded.fidelity_score)', (canonical, rid))
        if annotation:
            db.execute('INSERT OR IGNORE INTO annotations SELECT ?,display_name,category,tags,notes,'
                       'verification_status,updated_at FROM annotations WHERE record_id=?', (canonical, rid))
        for field in ('game_id', 'element', 'rarity'):
            db.execute(f'UPDATE records SET {field}=COALESCE({field},?) WHERE id=?', (original[field], canonical))
        db.execute('UPDATE record_sources SET record_id=? WHERE record_id=?', (canonical, rid))
        for table in ('record_assets', 'asset_preferences', 'annotations'):
            db.execute(f'DELETE FROM {table} WHERE record_id=?', (rid,))
    relationships = [tuple(row) for row in db.execute('SELECT * FROM relationships')]
    db.execute('DELETE FROM relationships')
    db.executemany('INSERT OR IGNORE INTO relationships VALUES(?,?,?)',
        [(mapping.get(first, first), mapping.get(second, second), kind) for first, second, kind in relationships])
    aliases = [tuple(row) for row in db.execute('SELECT * FROM identity_aliases')]
    db.execute('DELETE FROM identity_aliases')
    db.executemany('INSERT OR IGNORE INTO identity_aliases VALUES(?,?,?)',
        [(mapping.get(first, first), mapping.get(second, second), proof) for first, second, proof in aliases
         if mapping.get(first, first) != mapping.get(second, second)])
    db.executemany('DELETE FROM records WHERE id=?', [(rid,) for rid in removed])
    preferences = {}
    for canonical in sorted(set(mapping.values())):
        assets = [dict(row) for row in db.execute('SELECT a.*,ra.role FROM assets a JOIN record_assets ra '
                                                'ON a.id=ra.asset_id WHERE ra.record_id=?', (canonical,))]
        for asset in assets:
            score = preferred_score(asset, asset['role'])
            db.execute('INSERT INTO asset_preferences VALUES(?,?,?,?) ON CONFLICT(record_id,asset_id) '
                       'DO UPDATE SET fidelity_score=MAX(asset_preferences.fidelity_score,excluded.fidelity_score)',
                       (canonical, asset['id'], score, 'Existing fidelity rule: reference role, pixel area, lossless encoding'))
        chosen = db.execute('SELECT a.id,a.local_image_path,a.width,a.height,p.fidelity_score FROM assets a '
                            'JOIN asset_preferences p ON a.id=p.asset_id WHERE p.record_id=? '
                            'AND a.status="ok" AND a.local_image_path IS NOT NULL '
                            'ORDER BY p.fidelity_score DESC,a.id LIMIT 1', (canonical,)).fetchone()
        if chosen:
            preferences[canonical] = dict(chosen)
    L.index(db)
    return preferences


def deduplicate_copy(source, output, calibration_path=None, preferred_ids=()):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (source == output or output.is_relative_to(ACTIVE_LIBRARY) or output.exists()
            or output.with_suffix('.report.json').exists()):
        raise ValueError('Output must be a new isolated database outside the active library')
    if output.suffix != '.sqlite3':
        raise ValueError('Output must have a .sqlite3 extension')
    calibration = json.loads(Path(calibration_path).read_text('utf-8')) if calibration_path else {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=0)) as original:
        with closing(sqlite3.connect(output)) as db:
            def progress(status, _remaining, _total):
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise RuntimeError('Source busy; passive backup refused to wait')
            original.backup(db, pages=256, progress=progress, sleep=0)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            before = dict(db.execute('SELECT record_type,count(*) FROM records GROUP BY record_type'))
            mapping, groups, rejected, collisions = _plan(db, calibration, preferred_ids)
            with db:
                preferred_assets = _merge(db, mapping)
                _, remaining_groups, _, collisions = _plan(db, calibration, preferred_ids)
                if remaining_groups:
                    raise ValueError('Proven duplicate groups remain after merging')
                violations = list(db.execute('PRAGMA foreign_key_check'))
                if violations:
                    raise ValueError('Foreign key validation failed: ' + str([tuple(row) for row in violations]))
            after = dict(db.execute('SELECT record_type,count(*) FROM records GROUP BY record_type'))
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity != 'ok' or any(after.get(kind) != count for kind, count in before.items() if kind != 'item'):
                raise ValueError('Copy integrity or non-item preservation check failed')
    gates = {}
    supported = set(calibration.get('supported_item_ids', []))
    overrides = calibration.get('per_identity_thresholds', {})
    for canonical in {mapping.get(rid, rid) for rid in supported | overrides.keys()}:
        members = {rid for rid, target in mapping.items() if target == canonical} | {canonical}
        limits = [overrides[rid] for rid in members if rid in overrides]
        gates[canonical] = {'member_ids': sorted(members), 'supported_member_ids': sorted(members & supported),
                            **{field: max((float(value.get(field, calibration.get(field, 0))) for value in limits),
                                          default=calibration.get(field, 0)) for field in ('threshold', 'margin')}}
    report = {'source': str(source), 'output': str(output), 'source_modified': False,
              'before_counts': before, 'after_counts': after, 'removed_item_records': before.get('item', 0)-after.get('item', 0),
              'mapping': mapping, 'merged_groups': groups, 'preferred_assets': preferred_assets,
              'calibration_gates_remapped': gates, 'rejected_merges': rejected,
              'unproven_collisions': collisions, 'foreign_key_check': [], 'integrity_check': integrity,
              'remaining_proven_duplicate_groups': remaining_groups,
              'collision_counts': {kind: sum(c['classification'] == kind for c in collisions)
                                   for kind in ('confirmed_distinct', 'unresolved')},
              'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'asset_files_modified': False, 'activation_required': True}
    report_path = output.with_suffix('.report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ACTIVE_LIBRARY / 'data/library.sqlite3')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--calibration', type=Path)
    args = parser.parse_args()
    report = deduplicate_copy(args.source, args.output, args.calibration)
    print(json.dumps({key: report[key] for key in ('output', 'removed_item_records', 'before_counts', 'after_counts')}))


if __name__ == '__main__':
    main()
