"""Calibrate on manually labeled saved screens; evaluate disjoint holdout examples."""
import json
from pathlib import Path
from collections import Counter
if __package__:
    from .recognizer import ItemRecognizer,image_rgb,file_hash,DEFAULT_TEMPLATES,PROJECT
else:
    from recognizer import ItemRecognizer,image_rgb,file_hash,DEFAULT_TEMPLATES,PROJECT

ROOT=Path(__file__).resolve().parent

def intersection(a,b):
    x=max(a[0],b[0]); y=max(a[1],b[1]); right=min(a[0]+a[2],b[0]+b[2]); bottom=min(a[1]+a[3],b[1]+b[3])
    overlap=max(0,right-x)*max(0,bottom-y)
    return overlap/(a[2]*a[3]+b[2]*b[3]-overlap) if overlap else 0

def assign(predictions,truth):
    remaining=list(predictions); assigned=[]
    for t in truth:
        ranked=sorted(remaining,key=lambda r:-intersection(r['bounding_box'],t['bounding_box']))
        p=ranked[0] if ranked and intersection(ranked[0]['bounding_box'],t['bounding_box'])>.5 else None
        if p is not None: remaining.remove(p)
        assigned.append((t,p))
    return assigned,remaining

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def calibrate(examples):
    recognizer=ItemRecognizer(calibration_path=None)
    positive=[]; negative=[]; margins=[]; supported=set(); evidence=[]
    for example in examples:
        if example['split']!='calibration': continue
        if file_hash(example['path'])!=example['sha256']: raise ValueError('Ground-truth source changed: '+example['path'])
        result=recognizer.analyze_screenshot(example['path'])
        pairs,extra=assign(result['rewards']+result['unknown_rewards'],example['rewards'])
        for truth,pred in pairs:
            if pred is None: continue
            expected=truth['item_id']; best=pred.get('candidates',[None])[0] if pred.get('candidates') else None
            if expected and best and best['item_id']==expected:
                positive.append(pred['match_score']); margins.append(pred['score_margin']); supported.add(expected)
            elif pred['match_score'] is not None: negative.append(pred['match_score'])
            evidence.append({'screenshot_sha256':example['sha256'],'bounding_box':pred['bounding_box'],'expected_item_id':expected,'top_candidate_id':best['item_id'] if best else None,'similarity':pred['match_score'],'margin':pred['score_margin']})
    if not positive: raise ValueError('No correct labeled calibration icons')
    low=min(positive); high=max(negative,default=0)
    # Require separation in actual calibration evidence. Do not lower a threshold to force mistakes.
    threshold=(low+high)/2 if low>high else low
    threshold=max(.75,threshold)
    margin=max(.01,min(.04,min(margins)*.8))
    calibration={'schema_version':1,'library_sha256':file_hash(recognizer.db_path),'threshold':round(threshold,6),'margin':round(margin,6),'supported_item_ids':sorted(supported),
                 'score_kind':'Masked color and structural similarity; not a calibrated probability',
                 'scope':'Only the named item subset and inspected NB:R acquired-reward geometry; unknown quantities excluded.',
                 'calibration_screenshots':[e['path'] for e in examples if e['split']=='calibration'],
                 'positive_icon_count':len(positive),'negative_icon_count':len(negative),'minimum_positive_similarity':low,
                 'maximum_negative_similarity':high,'minimum_correct_margin':min(margins),'evidence':evidence}
    write(DEFAULT_TEMPLATES/'calibration.json',calibration)
    return calibration

def evaluate():
    dataset=json.loads((DEFAULT_TEMPLATES/'ground_truth.json').read_text('utf-8'))
    for example in dataset['examples']:
        example['path']=str(PROJECT/example['path'])
    examples=dataset['examples']
    calibration_path=DEFAULT_TEMPLATES/'calibration.json'
    # Routine validation must preserve reviewed groups, character gates and
    # additive calibration. Initial calibration remains available via calibrate.
    calibration=json.loads(calibration_path.read_text('utf-8')) if calibration_path.exists() else calibrate(examples)
    consumed={str((PROJECT/e['path']).resolve()) for e in calibration.get('expanded_labeled_evidence',[])}
    recognizer=ItemRecognizer(calibration_path=DEFAULT_TEMPLATES/'calibration.json')
    database_before=file_hash(recognizer.db_path)
    overall=Counter(); heldout=Counter(); reports=[]
    for i,example in enumerate(examples):
        before=file_hash(example['path'])
        if before!=example['sha256']: raise ValueError('Ground truth screenshot hash mismatch')
        target=PROJECT/'runtime/item_recognition/validation'/ (str(i).zfill(2)+'-'+Path(example['path']).stem)
        result=recognizer.analyze_screenshot(example['path'],debug_path=target.with_suffix('.debug.png'))
        write(target.with_suffix('.json'),result)
        counts=Counter(); counts['screenshots']=1
        truth=example.get('rewards',[])
        pairs,extra=assign(result['rewards']+result['unknown_rewards'],truth)
        counts['reward_icons_evaluated']=len(truth)
        for t,p in pairs:
            if t['item_id'] is None: counts['library_missing_icons']+=1
            else: counts['supported_labeled_icons']+=1
            if p is None: counts['missed_icons']+=1; continue
            counts['detected_icons']+=1
            state=p['identity_status']
            if state=='matched':
                if p['item_id']==t['item_id']: counts['correctly_identified_icons']+=1
                else: counts['incorrectly_identified_icons']+=1
            elif state=='candidate': counts['candidate_matches']+=1
            elif state=='ambiguous': counts['ambiguous_matches']+=1
            else: counts['unknown_matches']+=1
            if t['item_id'] is None and state in ('unknown','ambiguous'): counts['correct_unknown_abstentions']+=1
            if p['quantity'] is not None:
                if t['quantity'] is None: counts['invented_quantities']+=1
                else:
                    counts['quantity_fields_evaluated']+=1
                    if p['quantity']==t['quantity']: counts['correct_quantities']+=1
            if t.get('quantity') is not None and p['item_id']==t['item_id'] and p['quantity']==t['quantity'] and state=='matched': counts['end_to_end_correct_rewards']+=1
        counts['extra_icon_detections']=len(extra) if truth else 0
        if not truth: counts['unrevealed_unknown_tiles']=sum(p.get('visual_state')=='unrevealed' for p in extra)
        negative=example.get('expected_status') or example['screen_type']!='acquired' or not truth
        if negative and not truth:
            counts['negative_screenshots']=1
            counts['false_item_identities_on_negative_screens']=sum(r['identity_status'] in ('matched','candidate') for r in result['rewards'])
        if before!=file_hash(example['path']): raise AssertionError('Input image mutated')
        overall.update(counts)
        if example['split']=='holdout' and str(Path(example['path']).resolve()) not in consumed: heldout.update(counts)
        reports.append({'screenshot':example['path'],'split':example['split'],'used_for_additive_calibration':str(Path(example['path']).resolve()) in consumed,'ground_truth_screen_type':example['screen_type'],'actual_screen_type':result['screen_type'],'status':result['status'],'counts':dict(counts),'json_path':str(target.with_suffix('.json')),'debug_path':str(target.with_suffix('.debug.png'))})
    unchanged=database_before==file_hash(recognizer.db_path)
    if not unchanged: raise AssertionError('Library DB changed')
    quantity=json.loads((DEFAULT_TEMPLATES/'quantity/manifest.json').read_text('utf-8'))
    quantity_report=[]
    from quantity import EVIDENCE
    for evidence in EVIDENCE:
        if evidence['quantity'] is None: continue
        from PIL import Image
        source=PROJECT/evidence['source']
        with Image.open(source) as im: reading=recognizer.quantity_reader.read(im.crop(tuple(evidence['box'])))
        quantity_report.append({'field':evidence['id'],'scope':evidence['field'],'expected':evidence['quantity'],'observed':reading['quantity'],'status':reading['quantity_status'],'correct':reading['quantity']==evidence['quantity']})
    supported=heldout['supported_labeled_icons']
    verified=heldout['correctly_identified_icons']+heldout['incorrectly_identified_icons']
    # Explicit zeros are essential to keep failures, abstention, and missing evidence distinguishable.
    for counter in (overall,heldout):
        for key in ('incorrectly_identified_icons','ambiguous_matches','unknown_matches','missed_icons','candidate_matches','invented_quantities','quantity_fields_evaluated','correct_quantities','end_to_end_correct_rewards','extra_icon_detections'):
            counter.setdefault(key,0)
    report={'actual_results_screenshots_tested':sum(e['screen_type']!='not_results' for e in examples),
            'actual_acquired_screenshots_tested':sum(bool(e.get('rewards')) for e in examples),
            'all_examples':dict(overall),'heldout_examples':dict(heldout),'per_screenshot':reports,
            'heldout_supported_identification_accuracy':heldout['correctly_identified_icons']/supported if supported else None,
            'heldout_accepted_identity_precision':heldout['correctly_identified_icons']/verified if verified else None,
            'drop_quantity_accuracy':None,'drop_quantity_accuracy_reason':'No explicit acquired-icon quantities are shown in labeled real screenshots. Quantity must remain unknown.',
            'quantity_fields':quantity_report,'summary_quantity_resubstitution_correct':sum(x['correct'] for x in quantity_report),
            'summary_quantity_resubstitution_total':len(quantity_report),'summary_quantity_generalization_accuracy':None,
            'summary_quantity_scope_note':'These six explicit condition/summary numeric fields were also used to derive glyphs. This is a real-source reproduction check, not held-out drop quantity accuracy.',
            'library_database_unchanged':unchanged,'reference_report':recognizer.reference_report(),'calibration':calibration,
            'real_vs_synthetic':'All counts here use saved real-game screenshots. Software regression tests are reported separately.'}
    write(PROJECT/'runtime/item_recognition/reports/validation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k in ('actual_results_screenshots_tested','actual_acquired_screenshots_tested','all_examples','heldout_examples','heldout_supported_identification_accuracy','drop_quantity_accuracy','library_database_unchanged')},indent=2))
    return report

if __name__=='__main__': evaluate()
