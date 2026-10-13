"""Read-only reward identities derived from the existing reference library.

Artwork and names alone never establish alias identity. Automatic aliases require
the library's explicit canonical identity or a shared game identifier and compatible source semantics. Reviewed
calibration aliases additionally require persisted evidence. An explicitly
reviewed shared source card may establish alternate names, but never conflicting
item type, element, rarity, game ID or material category.
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


def compatible(records, allow_alternate_names=False):
    """Missing source facts may be enriched, but known facts cannot conflict."""
    for field in ('record_type', 'display_name', 'game_id', 'element', 'rarity'):
        if field == 'display_name' and allow_alternate_names:
            continue
        if len({_value(r.get(field)) for r in records if r.get(field) is not None}) > 1:
            return False
    # Generic source kinds "unit" and "item" do not contradict a named material
    # category; two different specific material categories do contradict.
    categories = {_value(r.get('category')) for r in records if r.get('category')}
    return len(categories - {'unit', 'item'}) <= 1


def _shared_item_source_card(records, source_card_id):
    """Validate an explicitly reviewed item card identity using source URLs."""
    if not str(source_card_id).isdigit():
        return False
    expected = int(source_card_id)
    for record in records:
        if record.get('record_type') != 'item':
            return False
        game_id = record.get('game_id')
        if game_id is not None and (not str(game_id).isdigit() or int(game_id) != expected):
            return False
        cards = {int(found.group(1)) for asset in record.get('assets', [])
                 if (found := re.search(r'/(?:missions|lb)/card-(\d+)\.',
                                        asset.get('source_image_url', '')))}
        if cards != {expected}:
            return False
    return bool(records)


def canonical_groups(records, reviewed=(), preferred=()):
    """Return member->canonical map and canonical group evidence.

    The library suppresses alias records in lookup results, but returns their IDs
    on the canonical record. Keep those IDs as calibration membership evidence;
    canonical outputs must still point to a loaded record.
    """
    records = list(records)
    by_id = {r['id']: r for r in records}
    groups = {rid: {rid} for rid in by_id}
    evidence = defaultdict(list)
    explicit = {}
    library_members = defaultdict(set)

    def merge(ids, why, canonical=None, reviewed_source_card=None):
        ids = set(ids)
        if not ids or not ids <= by_id.keys():
            return False
        members = set().union(*(groups[rid] for rid in ids))
        member_records = [by_id[rid] for rid in members]
        allow_alternate_names = reviewed_source_card is not None
        if allow_alternate_names and not _shared_item_source_card(member_records, reviewed_source_card):
            return False
        if not compatible(member_records, allow_alternate_names=allow_alternate_names):
            return False
        for rid in members:
            groups[rid] = members
            evidence[rid].append(why)
            if canonical:
                explicit[rid] = canonical
        return True

    # These declarations come from the library's reviewed identity_aliases API,
    # not from artwork similarity or source display-name guesses.
    for record in records:
        canonical = record.get('canonical_id')
        if canonical not in by_id:
            continue
        ids = {record['id'], canonical, *record.get('aliases', [])}
        if ids == {record['id']}:
            continue
        loaded = ids & by_id.keys()
        if merge(loaded, {'kind': 'library_canonical_identity',
                          'canonical_id': canonical}, canonical):
            library_members[canonical].update(ids)

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
            source_card = entry.get('source_card_id') if entry.get('allow_alternate_names') is True else None
            why = {'kind': 'reviewed_calibration', 'evidence': entry['evidence']}
            if source_card is not None:
                why['source_card_id'] = str(source_card)
            merge(ids, why, canonical, reviewed_source_card=source_card)

    preferred = set(preferred)
    mapping, details = {}, {}
    for rid, members in groups.items():
        declared = {explicit[mid] for mid in members if explicit.get(mid) in members}
        canonical = min(declared or members, key=lambda mid: (mid not in preferred,
                -sum(by_id[mid].get(k) is not None for k in ('game_id', 'element', 'rarity')), mid))
        mapping[rid] = canonical
        alias_members = set().union(*(library_members[mid] for mid in members))
        details[canonical] = {'canonical_group': canonical, 'member_ids': sorted(members | alias_members),
                              'evidence': evidence[rid]}
    for canonical, group in details.items():
        for member in group['member_ids']:
            mapping.setdefault(member, canonical)
    return mapping, details
