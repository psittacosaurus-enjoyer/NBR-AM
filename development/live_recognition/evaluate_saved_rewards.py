"""Evaluate inspected real reward screens without updating farming history."""
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'item_recognition'))
from recognizer import ItemRecognizer

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
IDS = {'H': 'item_f2616312e0532b2751841d9a', 'A': 'item_31ac7f4f725807dff03f5472',
       'K': 'character_2c6e2c21ff0640de0776e944'}
# Independently visually inspected saved_runs_contact.png, in spatial grid order.
TRUTH = {1:'KAHAAAK', 2:'HAAHA', 3:'HAHAHHAK', 4:'KHHHAHHA', 5:'KAH AHA'.replace(' ',''),
         6:'HAA', 7:'AHA AHHA'.replace(' ',''), 8:'HA', 9:'HAAHAH', 10:'HAHAAA'}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    library = ROOT / 'game_library/data/library.sqlite3'
    before = sha(library)
    recognizer = ItemRecognizer(library_path=ROOT/'game_library', calibration_path=OUT/'calibration_proposed.json')
    sources = json.loads((OUT/'saved_run_sources.json').read_text('utf-8'))
    reports = []
    for source in sources:
        number = source['run_sequence']
        original = sha(source['screenshot_path'])
        result = recognizer.analyze_screenshot(source['screenshot_path'], stage='night-of-tragedy-a')
        entries = sorted(result['rewards'] + result['unknown_rewards'], key=lambda e:(e['bounding_box'][1],e['bounding_box'][0]))
        expected = [IDS[token] for token in TRUTH[number]]
        actual = [e['item_id'] if e['identity_status']=='matched' else None for e in entries]
        reports.append({**source,'split':'training' if number==1 else 'holdout', 'screenshot_sha256':original,
                        'expected_ids':expected, 'actual_ids':actual, 'correct':actual==expected,
                        'all_quantities_unknown':all(e['quantity'] is None for e in entries),
                        'source_unchanged':sha(source['screenshot_path'])==original,
                        'rewards':entries})
    previous = json.loads((ROOT/'item_recognition/reports/ground_truth.json').read_text('utf-8'))
    old = Counter()
    for example in previous['examples']:
        result = recognizer.analyze_screenshot(example['path'])
        entries = result['rewards'] + result['unknown_rewards']
        for truth in example.get('rewards',[]):
            found = next((e for e in entries if abs(e['bounding_box'][0]-truth['bounding_box'][0])<10 and abs(e['bounding_box'][1]-truth['bounding_box'][1])<10), None)
            old['icons'] += 1
            if found and found['identity_status']=='matched':
                expected = recognizer.identity_map.get(truth['item_id'],truth['item_id'])
                old['correct_verified' if found['item_id']==expected else 'incorrect_verified'] += 1
            elif found:
                old[found['identity_status']] += 1
            else:
                old['missed'] += 1
        if not example.get('rewards'):
            old['negative_screens'] += 1
            old['accepted_on_negative_screens'] += sum(e['identity_status']=='matched' for e in entries)
    summary={'saved_runs':len(reports),'holdout_runs':sum(r['split']=='holdout' for r in reports),
             'holdout_icons':sum(len(r['actual_ids']) for r in reports if r['split']=='holdout'),
             'holdout_correct_icons':sum(sum(a==b for a,b in zip(r['actual_ids'],r['expected_ids'])) for r in reports if r['split']=='holdout'),
             'all_runs_correct':all(r['correct'] for r in reports),'quantity_defaults_used':False,
             'library_unchanged':sha(library)==before,'original_recognition_dataset':dict(old),
             'reference_report':{k:v for k,v in recognizer.reference_report().items() if k not in ('excluded_references','canonical_groups')}}
    (OUT/'offline_validation.json').write_text(json.dumps({'summary':summary,'runs':reports},indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)
    assert summary['all_runs_correct'] and summary['library_unchanged']
    assert not old['incorrect_verified'] and not old['accepted_on_negative_screens']
    assert all(r['source_unchanged'] and r['all_quantities_unknown'] for r in reports)

if __name__=='__main__':
    main()
