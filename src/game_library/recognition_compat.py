"""Verify an additive library against its recoverable snapshot without changing thresholds."""
import argparse
import copy
import gc
import hashlib
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import cv2
try:
    from . import library as L
    from item_recognition.recognizer import ItemRecognizer,PROJECT,DEFAULT_TEMPLATES,file_hash
except ImportError:
    import library as L
    from item_recognition.recognizer import ItemRecognizer,PROJECT,DEFAULT_TEMPLATES,file_hash

def evidence_paths(calibration):
    paths=set()
    ground_truth=json.loads((DEFAULT_TEMPLATES/'ground_truth.json').read_text('utf-8'))
    for example in ground_truth['examples']:
        path=Path(example['path']);path=path if path.is_absolute() else PROJECT/path
        if path.is_file(): paths.add(path)
    def visit(obj):
        if isinstance(obj,dict):
            for key in ('path','source'):
                if not obj.get(key): continue
                path=Path(obj[key]);path=path if path.is_absolute() else PROJECT/path
                if path.is_file() and path.suffix.lower() in ('.png','.jpg','.webp'): paths.add(path)
            for value in obj.values(): visit(value)
        elif isinstance(obj,list):
            for value in obj: visit(value)
    visit(calibration.get('live_reward_evidence',{}))
    return sorted(paths,key=str)

def status_counts(result):
    """Report abstentions separately; candidates are never counted as verified."""
    counts=Counter(r['identity_status'] for r in result.get('rewards',[])+result.get('unknown_rewards',[]))
    return {state:counts[state] for state in ('matched','candidate','ambiguous','unknown')}

def unique_screenshots(paths):
    """Count immutable screenshot contents once, retaining duplicate-path evidence."""
    by_hash={};duplicates=[]
    for path in sorted({Path(p).resolve() for p in paths},key=str):
        sha256=file_hash(path)
        if sha256 in by_hash:
            duplicates.append({'sha256':sha256,'counted_path':str(by_hash[sha256]),'duplicate_path':str(path)})
        else: by_hash[sha256]=path
    return list(by_hash.values()),duplicates

def expand_labeled_calibration(calibration_path=DEFAULT_TEMPLATES/'calibration.json',promote=False):
    """Add only independently labeled acquired items passing unchanged acceptance gates.

    Existing calibration, characters, quantity interpretation and thresholds are
    preserved. A consumed holdout is explicitly recorded as calibration evidence,
    rather than being presented as independent accuracy evidence for its new ID.
    """
    from item_recognition.validate import assign
    current=json.loads(Path(calibration_path).read_text('utf-8'))
    dataset=json.loads((DEFAULT_TEMPLATES/'ground_truth.json').read_text('utf-8'))
    recognizer=ItemRecognizer(calibration_path=calibration_path)
    added=set();evidence=[];rejected=[]
    already_supported=set(recognizer.supported_items)
    for group in getattr(recognizer,'canonical_groups',{}).values():
        if already_supported.intersection(group['member_ids']): already_supported.update(group['member_ids'])
    for example in dataset['examples']:
        eligible=[r for r in example.get('rewards',[]) if r.get('item_id') and
                  r.get('identity_status')=='manually_labeled' and r['item_id'] not in already_supported]
        if example.get('screen_type')!='acquired' or not eligible: continue
        path=Path(example['path']);path=path if path.is_absolute() else PROJECT/path
        if file_hash(path)!=example['sha256']: raise ValueError('Ground-truth source changed: '+str(path))
        if not example.get('validation'): raise ValueError('Missing independent label validation: '+str(path))
        result=recognizer.analyze_screenshot(path)
        pairs,_=assign(result.get('rewards',[])+result.get('unknown_rewards',[]),eligible)
        for truth,prediction in pairs:
            expected=truth['item_id'];canonical=recognizer.identity_map.get(expected,expected)
            if canonical not in recognizer._records or recognizer._records[canonical]['record_type']!='item':
                rejected.append({'expected_item_id':expected,'reason':'No eligible item reference'});continue
            if not prediction or prediction['identity_status']!='candidate' or prediction.get('item_id')!=canonical:
                rejected.append({'expected_item_id':expected,'reason':'Unchanged acceptance gates not passed'});continue
            added.add(expected)
            evidence.append({'path':example['path'],'sha256':example['sha256'],
                'bounding_box':prediction['bounding_box'],'expected_item_id':expected,
                'canonical_item_id':canonical,'similarity':prediction['match_score'],
                'margin':prediction['score_margin'],'label_basis':example['validation'],
                'original_split':example['split'],'used_for_new_identity_calibration':True,
                'quantity':truth.get('quantity')})
    next_config=copy.deepcopy(current)
    next_config['supported_item_ids']=sorted(set(current.get('supported_item_ids',[]))|added)
    combined=current.get('expanded_labeled_evidence',[])+evidence
    next_config['expanded_labeled_evidence']=list({(e['sha256'],tuple(e['bounding_box']),e['expected_item_id']):e for e in combined}.values())
    next_config['expanded_labeled_positive_icon_count']=len(next_config['expanded_labeled_evidence'])
    report={'added_supported_item_ids':sorted(added),'evidence':evidence,'rejected':rejected,
            'thresholds_changed':False,'independent_holdout_for_new_ids':False,
            'calibration_promoted':bool(promote and added)}
    if promote and added: L.atomic_json(calibration_path,next_config)
    L.atomic_json(L.ROOT/'reports/fandom_recognition/labeled_calibration.json',report)
    return report

def evaluate_expanded_calibration(compatibility,calibration_path=DEFAULT_TEMPLATES/'calibration.json'):
    """Measure final calibration on the same real screenshots, without relabeling them."""
    from item_recognition.validate import assign
    recognizer=ItemRecognizer(calibration_path=calibration_path)
    truth={str((PROJECT/e['path']).resolve()):e for e in
           json.loads((DEFAULT_TEMPLATES/'ground_truth.json').read_text('utf-8'))['examples']}
    truth_by_hash={e['sha256']:e for e in truth.values()}
    counts=Counter();labeled=Counter();cases=[];unresolved=[];violations=[]
    consumed={e['path'] for e in recognizer.calibration.get('expanded_labeled_evidence',[])}
    for case in compatibility['cases']:
        path=Path(case['screenshot']);before=json.loads(Path(case['output']).read_text('utf-8'))
        if file_hash(path)!=case['sha256']: raise ValueError('Comparison screenshot changed: '+str(path))
        final=recognizer.analyze_screenshot(path)
        entries=final.get('rewards',[])+final.get('unknown_rewards',[])
        counts.update(status_counts(final))
        by_box={tuple(r['bounding_box']):r for r in entries}
        for previous in before.get('rewards',[])+before.get('unknown_rewards',[]):
            after=by_box.get(tuple(previous['bounding_box']))
            if after is None or after.get('quantity')!=previous.get('quantity'):
                violations.append({'screenshot':str(path),'reason':'Reward geometry or quantity changed'})
            elif previous['identity_status']=='matched' and (after['identity_status']!='matched' or
                    after['item_id']!=recognizer.identity_map.get(previous['item_id'],previous['item_id'])):
                violations.append({'screenshot':str(path),'reason':'Previously verified identity changed'})
        example=truth.get(str(path.resolve())) or truth_by_hash.get(case['sha256'])
        if example:
            if file_hash(path)!=example['sha256']: raise ValueError('Ground-truth source changed')
            pairs,_=assign(entries,example.get('rewards',[]))
            for label,prediction in pairs:
                if not label.get('item_id'): continue
                labeled['known_human_labels']+=1
                expected=recognizer.identity_map.get(label['item_id'],label['item_id'])
                correct=prediction and prediction['identity_status']=='matched' and prediction['item_id']==expected
                labeled['correct_matched_labels' if correct else 'abstained_or_wrong_labels']+=1
                if prediction and prediction['identity_status']=='matched' and prediction['item_id']!=expected:
                    violations.append({'screenshot':str(path),'reason':'Incorrect human-labeled identity'})
        unknown=[r for r in entries if r['identity_status']!='matched' and r.get('visual_state')!='unrevealed']
        if unknown:
            unresolved.append({'screenshot':str(path),'rewards':[{'bounding_box':r['bounding_box'],
                'status':r['identity_status'],'reason':r.get('reason'),'candidates':r.get('candidates',[])[:2]} for r in unknown]})
        cases.append({'screenshot':str(path),'before':case['before'],'expanded_before_calibration':case['after'],
            'final':status_counts(final),'new_identity_calibration_screen':bool(example and example['path'] in consumed)})
    report={'passed':not violations,'screenshots':len(cases),'before':compatibility['before_status_counts'],
        'expanded_before_calibration':compatibility['after_status_counts'],'final':dict(counts),
        'human_labeled_accuracy_counts':dict(labeled),'cases':cases,'remaining_visible_unverified_rewards':unresolved,
        'violations':violations,'supported_item_ids':sorted(recognizer.supported_items),
        'supported_character_ids':sorted(recognizer.supported_characters),
        'calibration_evidence_screens_excluded_from_new_identity_holdout_claims':sorted(consumed),
        'reference_report':recognizer.reference_report(),'acquired_quantities_inferred':False,
        'note':'Before/after status counts measure recognition outputs; only existing human labels establish correctness.'}
    L.atomic_json(L.ROOT/'reports/fandom_recognition/manager_upgrade.json',report)
    return report

def check(snapshot,calibration_path=DEFAULT_TEMPLATES/'calibration.json',promote=True,additional_paths=()):
    cv2.setNumThreads(1)
    directory=L.ROOT/'reports/fandom_recognition';directory.mkdir(parents=True,exist_ok=True)
    current=json.loads(Path(calibration_path).read_text('utf-8'))
    original_path=L.ROOT/'backups/fandom-1.1-before/calibration.json'
    original=json.loads(original_path.read_text('utf-8')) if original_path.exists() else copy.deepcopy(current)
    snapshot=Path(snapshot)
    # SQLite backup preserves data but may change page headers and therefore the file hash.
    trusted_snapshot=original_path.with_name('library.sqlite3')
    expected_snapshot_hash=file_hash(trusted_snapshot) if trusted_snapshot.exists() else original.get('library_sha256')
    if expected_snapshot_hash!=file_hash(snapshot):
        raise ValueError('Snapshot does not match the preserved pre-import calibration')
    before_config=copy.deepcopy(original);before_config['library_sha256']=file_hash(snapshot)
    before_file=directory/'baseline_calibration.json';L.atomic_json(before_file,before_config)
    loader=ItemRecognizer._load_reference
    def snapshot_loader(instance,record):
        instance.library_root=L.ROOT
        return loader(instance,record)
    paths,duplicate_paths=unique_screenshots(evidence_paths(original)+list(additional_paths));old_results={}
    if not paths: raise ValueError('No saved recognition evidence available; calibration was not promoted')
    with patch.object(ItemRecognizer,'_load_reference',snapshot_loader):
        baseline=ItemRecognizer(library_path=snapshot,calibration_path=before_file)
        for path in paths: old_results[str(path)]=baseline.analyze_screenshot(path)
    del baseline;gc.collect()
    next_config=copy.deepcopy(original);next_config['library_sha256']=file_hash(L.DB_PATH)
    after_file=directory/'candidate_calibration.json';L.atomic_json(after_file,next_config)
    expanded=ItemRecognizer(calibration_path=after_file)
    supported=set(original.get('supported_item_ids',[])) | set(original.get('supported_character_ids',[]))
    violations=[];cases=[];verified=0;new_candidates=0;before_counts=Counter();after_counts=Counter()
    for path in paths:
        old=old_results[str(path)];new=expanded.analyze_screenshot(path)
        old_counts=status_counts(old);new_counts=status_counts(new)
        before_counts.update(old_counts);after_counts.update(new_counts)
        if old['status']=='error': violations.append({'screenshot':str(path),'reason':'Baseline recognition failed'})
        old_entries=old.get('rewards',[])+old.get('unknown_rewards',[])
        new_entries=new.get('rewards',[])+new.get('unknown_rewards',[])
        by_box={tuple(r['bounding_box']):r for r in new_entries}
        preserved=0
        for before in old_entries:
            after=by_box.get(tuple(before['bounding_box']))
            if before['identity_status']=='matched':
                verified+=1
                if not after or after['identity_status']!='matched' or after['item_id']!=before['item_id']:
                    violations.append({'screenshot':str(path),'box':before['bounding_box'],'before_id':before['item_id'],'after_id':after.get('item_id') if after else None,'reason':'Previously calibrated identity no longer preserved'})
                else: preserved+=1
            if after and before.get('quantity')!=after.get('quantity'):
                violations.append({'screenshot':str(path),'reason':'Quantity interpretation changed'})
            if after and after['identity_status']=='matched' and after['item_id'] not in supported:
                violations.append({'screenshot':str(path),'reason':'New identity was incorrectly marked calibrated'})
            if after and after['identity_status']=='candidate' and before['identity_status'] in ('unknown','ambiguous'):
                new_candidates+=1
        if len(new_entries)!=len(old_entries): violations.append({'screenshot':str(path),'reason':'Reward region/icon count changed'})
        if new['status']=='error': violations.append({'screenshot':str(path),'reason':'Expanded recognition failed'})
        out=directory/(hashlib.sha256(str(path).encode()).hexdigest()[:12]+'.json')
        L.atomic_json(out,new)
        cases.append({'screenshot':str(path),'sha256':file_hash(path),'old_icon_count':len(old_entries),'new_icon_count':len(new_entries),'verified_preserved':preserved,'before':old_counts,'after':new_counts,'output':str(out)})
    if not verified: violations.append({'reason':'No previously calibrated matches available for comparison'})
    reference_report=expanded.reference_report()
    if reference_report['errors']: violations.append({'reason':'Expanded references failed to load','errors':reference_report['errors']})
    report={'passed':not violations,'saved_screenshots_tested':len(paths),'previously_calibrated_matches_checked':verified,
            'new_uncalibrated_candidates':new_candidates,'violations':violations,'cases':cases,
            'original_database_sha256':original.get('library_sha256'),'expanded_database_sha256':next_config['library_sha256'],
            'baseline_snapshot_sha256':file_hash(snapshot),
            'duplicate_screenshot_paths_excluded':duplicate_paths,
            'before_status_counts':dict(before_counts),'after_status_counts':dict(after_counts),
            'thresholds_changed':False,'supported_ids_changed':False,'reference_report':reference_report,
            'calibration_promoted':False,'note':'Offline saved-image compatibility check. Newly imported identities remain uncalibrated.'}
    if not violations and promote:
        # Rechecking an import must not erase later independently reviewed calibration.
        promoted=copy.deepcopy(current);promoted['library_sha256']=next_config['library_sha256']
        imports=promoted.setdefault('compatible_library_imports',[])
        entry={'source':'Fandom Library 1.1','before_sha256':original.get('library_sha256'),'after_sha256':next_config['library_sha256'],'validation_report':str(directory/'compatibility.json'),'verified_matches_preserved':verified}
        imports[:]=[value for value in imports if value.get('after_sha256')!=entry['after_sha256']]
        imports.append(entry)
        L.atomic_json(calibration_path,promoted);report['calibration_promoted']=True
    L.atomic_json(directory/'compatibility.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','reference_report')},indent=2))
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--no-promote',action='store_true')
    p.add_argument('--screenshot',type=Path,action='append',default=[],help='Additional real screenshot to compare; does not supply ground truth')
    args=p.parse_args();report=check(args.snapshot,promote=not args.no_promote,additional_paths=args.screenshot)
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__': main()
