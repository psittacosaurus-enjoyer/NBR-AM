"""Read-only reward identities derived from the existing reference library.

Artwork and names alone never establish alias identity. Automatic aliases require
an explicit shared game identifier and compatible source semantics. Reviewed
calibration aliases additionally require persisted evidence, with the same checks.
"""
from __future__ import annotations
from collections import defaultdict
import re


def _character_asset(record, asset):
    if asset.get('status') != 'ok' or not asset.get('local_image_path'):
        return False
    url = asset.get('source_image_url', '')
    role = asset.get('role')
    if any(part in url for part in ('/abilities/', '/materials/', '/cutins/')):
        return False
    if role in ('card', 'portrait'):
        return True
    if role != 'icon':
        return False
    if any(part in url for part in ('/ninjas/', '/story/units/', '/road/units/')):
        return True
    # Mission portraits must explicitly agree with this form's source card number.
    original = record.get('metadata', {}).get('original', {})
    explicit_icon = original.get('icon')
    if original.get('kind') == 'ninja' and explicit_icon and '/missions/card-' in url and url.endswith(explicit_icon):
        return True
    number = original.get('cardNo')
    mission = re.search(r'/missions/card-(\d+)\.', url)
    return bool(number is not None and mission and int(mission.group(1)) == int(number))


def character_references(source):
    """Use only the library's public paged lookup and full-record API."""
    seen = set()
    for status in ('source_verified', 'manually_verified'):
        page = 1
        while True:
            found = source.search(record_type='character', status=status, page=page, limit=120)
            for summary in found['records']:
                if summary['id'] in seen:
                    continue
                seen.add(summary['id'])
                record = source.get(summary['id'])
                if record:
                    record['assets'] = [a for a in record['assets'] if _character_asset(record, a)]
                    if record['assets']:
                        yield record
            if page * found['limit'] >= found['total']:
                break
            page += 1


def _value(value):
    return str(value).strip().casefold() if value is not None else None


def compatible(records):
    """Missing source facts may be enriched, but known facts cannot conflict."""
    for field in ('record_type', 'display_name', 'game_id', 'element', 'rarity'):
        if len({_value(r.get(field)) for r in records if r.get(field) is not None}) > 1:
            return False
    # Generic source kinds "unit" and "item" do not contradict a named material
    # category; two different specific material categories do contradict.
    categories = {_value(r.get('category')) for r in records if r.get('category')}
    return len(categories - {'unit', 'item'}) <= 1


def canonical_groups(records, reviewed=(), preferred=()):
    """Return member->canonical map and canonical group evidence."""
    by_id = {r['id']: r for r in records}
    groups = {rid: {rid} for rid in by_id}
    evidence = defaultdict(list)
    explicit = {}

    def merge(ids, why, canonical=None):
        ids = set(ids)
        if not ids or not ids <= by_id.keys():
            return False
        members = set().union(*(groups[rid] for rid in ids))
        if not compatible([by_id[rid] for rid in members]):
            return False
        for rid in members:
            groups[rid] = members
            evidence[rid].append(why)
            if canonical:
                explicit[rid] = canonical
        return True

    by_source = defaultdict(list)
    for r in records:
        if r.get('game_id'):
            by_source[(r['record_type'], _value(r['display_name']), str(r['game_id']))].append(r['id'])
    for key, ids in by_source.items():
        if len(ids) > 1:
            merge(ids, {'kind': 'library_game_id', 'game_id': key[2]})
    for entry in reviewed:
        canonical = entry.get('canonical_item_id') or entry.get('canonical_id')
        ids = entry.get('member_ids', [])
        if canonical in ids and entry.get('evidence'):
            merge(ids, {'kind': 'reviewed_calibration', 'evidence': entry['evidence']}, canonical)

    preferred = set(preferred)
    mapping, details = {}, {}
    for rid, members in groups.items():
        canonical = explicit.get(rid)
        if canonical not in members:
            canonical = min(members, key=lambda mid: (mid not in preferred,
                -sum(by_id[mid].get(k) is not None for k in ('game_id', 'element', 'rarity')), mid))
        mapping[rid] = canonical
        details[canonical] = {'canonical_group': canonical, 'member_ids': sorted(members),
                              'evidence': evidence[rid]}
    return mapping, details
