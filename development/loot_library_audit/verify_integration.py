"""Read-only library integration audit using the Manager collector's exact factory."""
from collections import Counter, defaultdict
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from loot_tracking import _recognizer
from item_recognition import recognizer as module

OUTPUT = Path(__file__).resolve().parent
DATABASE = ROOT / 'game_library/data/library.sqlite3'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--completion-id', help='Trace one specific durable completion rather than the latest saved run.')
    arguments = parser.parse_args()
    before = digest(DATABASE)
    api_calls, library_records = [], {}
    loader = module._library_module

    def audited_loader():
        library = loader()
        original = library.ReferenceLibrary.item_references
        def lookup(self, manually_verified_only=False):
            api_calls.append({'api': 'ReferenceLibrary.item_references',
                              'manually_verified_only': manually_verified_only,
                              'database': self.db.execute('PRAGMA database_list').fetchall()[0]['file']})
            for record in original(self, manually_verified_only=manually_verified_only):
                library_records[record['id']] = record
                yield record
        library.ReferenceLibrary.item_references = lookup
        return library

    with patch.object(module, '_library_module', side_effect=audited_loader):
        recognizer = _recognizer(ROOT)
    report = recognizer.reference_report()
    library = loader()
    with library.ReferenceLibrary(DATABASE) as source:
        record_type_counts = dict(source.db.execute('SELECT record_type,COUNT(*) FROM records GROUP BY record_type').fetchall())
        item_ids = [row['id'] for row in source.db.execute("SELECT id FROM records WHERE record_type='item'")]
        not_returned = [source.get(record_id) for record_id in item_ids if record_id not in library_records]
    omitted = [{'item_id': r['id'], 'item_name': r['display_name'], 'asset_count': len(r['assets'])} for r in not_returned]
    excluded = [library_records[r['item_id']] for r in recognizer.excluded_references]
    exclusion_audit = {'count': len(excluded),
                       'with_hp_attack_and_cycle': sum(all(k in r.get('metadata', {}).get('original', {}) for k in ('hp', 'attack', 'cycle')) for r in excluded),
                       'with_noncombat_asset_urls': sum(any(not any(marker in a['source_image_url'] for marker in ('/enemies/', '/bosses/', '/ninjas/')) for a in r['assets']) for r in excluded),
                       'name_overlap_with_eligible': sorted({r['display_name'] for r in excluded} & {r['display_name'] for r in recognizer.references})}
    raw_images = [image for record in recognizer.references for image in record['reference_paths']]
    loaded_ids = {image['item_id'] for image in recognizer._images}
    decoded_paths = {image['path'] for image in recognizer._images}
    with closing(sqlite3.connect((ROOT / 'runtime/loot/loot.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        sessions = [dict(r) for r in db.execute('SELECT * FROM sessions ORDER BY tracking_started DESC')]
        runs = [dict(r) for r in db.execute('SELECT * FROM completions ORDER BY completed_at DESC')]
    selected = next(r for r in runs if r['screenshot_path'] and Path(r['screenshot_path']).is_file()
                    and (not arguments.completion_id or r['completion_id'] == arguments.completion_id))
    screenshot = Path(selected['screenshot_path'])
    screenshot_before = digest(screenshot)
    session = next(s for s in sessions if s['session_id'] == selected['session_id'])
    result = recognizer.analyze_screenshot(screenshot_path=screenshot, stage=session['stage'],
                                          debug_path=OUTPUT / 'trace.debug.png')
    stored_result = json.loads(selected['result_json'])
    entries = result['rewards'] + result['unknown_rewards']
    stored_entries = stored_result['rewards'] + stored_result['unknown_rewards']
    comparisons = []
    for entry in entries:
        candidates = []
        for candidate in entry['candidates']:
            record = library_records[candidate['item_id']]
            canonical_paths = [str((ROOT / 'game_library' / asset['local_image_path']).resolve())
                               for asset in record['assets'] if asset['status'] == 'ok' and asset['local_image_path']]
            path = Path(candidate['reference_path'])
            candidates.append({**candidate, 'canonical_library_name': record['display_name'],
                               'verification_status': record['verification_status'],
                               'source_key': record['source_key'], 'source_url': record['source_url'],
                               'id_name_match_library': candidate['item_name'] == record['display_name'],
                               'image_path_belongs_to_record': str(path) in canonical_paths,
                               'image_decoded': str(path) in decoded_paths,
                               'image_sha256': digest(path)})
        comparisons.append({'bounding_box': entry['bounding_box'], 'identity_status': entry['identity_status'],
                            'reason': entry['reason'], 'margin': entry['score_margin'],
                            'quantity': entry['quantity'], 'candidates': candidates})
    # Compare fresh factory output with the actual previously recorded worker output.
    keys = ('bounding_box', 'identity_status', 'item_id', 'item_name', 'match_score', 'score_margin', 'candidates')
    replay_equal = [{k: e.get(k) for k in keys} for e in entries] == [{k: e.get(k) for k in keys} for e in stored_entries]
    by_name, by_content = defaultdict(set), defaultdict(set)
    for image in recognizer._images:
        by_name[image['item_name']].add(image['item_id'])
        by_content[digest(image['path'])].add(image['item_id'])
    report.pop('excluded_references')  # Counts only: this audit does not duplicate the reference catalog.
    audit = {'lookup_path': 'Manager dashboard reads SQLite <- farmer-owned Collector -> loot_tracking._recognizer -> ItemRecognizer -> ReferenceLibrary.item_references -> game_library/data/library.sqlite3',
             'api_calls': api_calls, 'reference_report': report,
             'database_record_type_counts': record_type_counts, 'item_records_omitted_by_api': omitted,
             'exclusion_audit': exclusion_audit,
             'eligible_ids_with_decoded_images': len(loaded_ids), 'resolved_existing_image_paths': sum(Path(p).is_file() for p in raw_images),
             'eligible_ids_without_decoded_images': sorted({r['id'] for r in recognizer.references} - loaded_ids),
             'all_loaded_ids_names_match_library': all(image['item_name'] == library_records[image['item_id']]['display_name'] for image in recognizer._images),
             'library_sha256_before': before, 'library_sha256_after': digest(DATABASE),
             'calibration_library_sha256': recognizer.calibration.get('library_sha256') if recognizer.calibration else None,
             'calibration_active': recognizer.calibration is not None, 'supported_verified_identity_count': len(recognizer.supported),
             'eligible_unvalidated_identity_count': len(loaded_ids - recognizer.supported),
             'threshold': recognizer.threshold, 'required_margin': recognizer.margin,
             'quantity_reader_loaded': recognizer.quantity_reader is not None,
             'same_name_multiple_id_groups': sum(len(ids) > 1 for ids in by_name.values()),
             'identical_image_multiple_id_groups': sum(len(ids) > 1 for ids in by_content.values()),
             'runtime_snapshot': {'session': session, 'runs_observed': len(runs),
                                  'completion_status_counts': dict(Counter(r['status'] for r in runs)),
                                  'reward_identity_counts': dict(Counter(e['identity_status'] for r in runs for e in json.loads(r['result_json']).get('rewards', []) + json.loads(r['result_json']).get('unknown_rewards', [])))},
             'trace': {'completion_id': selected['completion_id'], 'run_sequence': selected['run_sequence'],
                       'screenshot': str(screenshot), 'screenshot_sha256_before': screenshot_before,
                       'screenshot_sha256_after': digest(screenshot), 'screen_type': result['screen_type'],
                       'reward_region': result['reward_region'], 'diagnostics': result['diagnostics'],
                       'result_status': result['status'], 'stored_live_output_reproduced_exactly': replay_equal,
                       'reward_identity_counts': dict(Counter(e['identity_status'] for e in entries)),
                       'icons': comparisons}}
    target = OUTPUT / 'integration_audit.json'
    target.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in audit.items() if k not in ('trace', 'runtime_snapshot')}, indent=2))
    print('TRACE', json.dumps({k: v for k, v in audit['trace'].items() if k != 'icons'}, indent=2))
    print('RUNTIME', json.dumps({k: v for k, v in audit['runtime_snapshot'].items() if k != 'session'}))
    print('REPORT', str(target))

if __name__ == '__main__':
    main()
