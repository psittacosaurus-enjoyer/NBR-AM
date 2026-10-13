"""Conservative local numeral OCR from manually inspected saved-image glyphs.

Run this file with --build-templates to recreate the documented local evidence.
No game, ADB, farming process, network request, or library mutation is involved.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import cv2
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
DEFAULT_TEMPLATE_DIR=PROJECT/'assets/item_recognition/quantity'
MIN_SCORE=.935
MIN_MARGIN=.055
METHOD='localized_screenshot_glyph_templates_v1'

# These labels were checked against the full originals and magnified crops.
# Auxiliary nonquantity fields supply glyph shapes; they are not reward quantities.
EVIDENCE=[
    {'id':'scroll_reward_1','source':'tests/fixtures/results_next.png','box':[786,489,871,512],'text':'x20000','quantity':20000,'field':'special_condition_reward','profile':'reward_small'},
    {'id':'scroll_reward_2','source':'tests/fixtures/results_next.png','box':[786,617,871,642],'text':'x25000','quantity':25000,'field':'special_condition_reward','profile':'reward_small'},
    {'id':'scroll_ryo','source':'tests/fixtures/results_next.png','box':[610,918,726,961],'text':'6000','quantity':6000,'field':'ryo_earned','profile':'earned'},
    {'id':'rich_reward_1','source':'tests/fixtures/rich_b/results_next.png','box':[786,489,871,512],'text':'x30000','quantity':30000,'field':'special_condition_reward','profile':'reward_small'},
    {'id':'rich_reward_2','source':'tests/fixtures/rich_b/results_next.png','box':[801,617,871,642],'text':'x500','quantity':500,'field':'special_condition_reward','profile':'reward_small'},
    {'id':'rich_ryo','source':'tests/fixtures/rich_b/results_next.png','box':[561,918,726,961],'text':'100000','quantity':100000,'field':'ryo_earned','profile':'earned'},
    {'id':'aux_exp','source':'tests/fixtures/results_next.png','box':[610,990,726,1040],'text':'4500','quantity':None,'field':'experience_glyph_evidence_only','profile':'earned'},
    {'id':'aux_next_rank','source':'tests/fixtures/results_next.png','box':[690,1064,802,1098],'text':'26070','quantity':None,'field':'rank_progress_glyph_evidence_only','profile':'rank'},
]

def _white_mask(rgb):
    values=rgb.astype(np.int16)
    return ((values.min(axis=2)>=180)&((values.max(axis=2)-values.min(axis=2))<=45)).astype(np.uint8)

def _segments(mask):
    """Projection groups reunite thin white strokes split within an outlined zero."""
    occupied=np.flatnonzero(mask.any(axis=0))
    groups=[]
    for x in occupied:
        if groups and int(x)-groups[-1][-1]<=2: groups[-1].append(int(x))
        else: groups.append([int(x)])
    result=[]
    for group in groups:
        left,right=group[0],group[-1]+1
        rows=np.flatnonzero(mask[:,left:right].any(axis=1))
        if not len(rows): continue
        top,bottom=int(rows[0]),int(rows[-1])+1
        glyph=mask[top:bottom,left:right]
        if int(glyph.sum())<4: continue
        result.append({'box':[left,top,right,bottom],'mask':glyph,'height':bottom-top,'width':right-left})
    return result

def _normalize(mask):
    # Preserve aspect ratio; stretching slender "1" into a wide digit is unsafe.
    height,width=mask.shape
    scaled_width=max(1,min(44,round(width*48/height)))
    resized=cv2.resize(mask.astype(np.float32),(scaled_width,48),interpolation=cv2.INTER_AREA)>=.45
    canvas=np.zeros((64,48),dtype=np.uint8)
    left=(48-scaled_width)//2
    canvas[8:56,left:left+scaled_width]=resized.astype(np.uint8)
    return canvas

def _similarity(left,right):
    union=int(left.sum())+int(right.sum())
    return 2*int(np.logical_and(left,right).sum())/union if union else 0.

def _unknown(reason,score=None,status='unknown'):
    return {'quantity':None,'quantity_status':status,'score':score,'method':METHOD,'reason':reason}

class QuantityReader:
    def __init__(self,template_dir=None):
        self.template_dir=Path(template_dir) if template_dir else DEFAULT_TEMPLATE_DIR
        self.templates=[]
        try:
            manifest=json.loads((self.template_dir/'manifest.json').read_text(encoding='utf8'))
            self.provenance=manifest
            for row in manifest.get('glyphs',[]):
                path=self.template_dir/row['path']
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=row['sha256']: continue
                with Image.open(path) as im: mask=np.asarray(im.convert('L'))>127
                if mask.shape!=(64,48) or row['character'] not in '0123456789x': continue
                self.templates.append((row['character'],row['profile'],mask,row))
        except (OSError,ValueError,KeyError):
            self.provenance={}

    def read(self,image_or_path,box=None):
        if not self.templates: return _unknown('No valid local digit templates are available.')
        try:
            if isinstance(image_or_path,(str,Path)):
                with Image.open(image_or_path) as im: rgb=np.asarray(im.convert('RGB')).copy()
            elif isinstance(image_or_path,Image.Image): rgb=np.asarray(image_or_path.convert('RGB')).copy()
            else:
                rgb=np.asarray(image_or_path)
                if rgb.ndim!=3 or rgb.shape[2]!=3 or rgb.dtype!=np.uint8:
                    return _unknown('Expected an RGB uint8 image or a readable image file.')
            if rgb.ndim!=3 or rgb.shape[2]!=3 or not rgb.shape[0] or not rgb.shape[1]:
                return _unknown('The image is empty or malformed.')
            if box is not None:
                if len(box)!=4 or any(not isinstance(v,(int,np.integer)) for v in box): return _unknown('The quantity ROI is invalid.')
                left,top,right,bottom=map(int,box)
                if not (0<=left<right<=rgb.shape[1] and 0<=top<bottom<=rgb.shape[0]): return _unknown('The quantity ROI is missing or outside the image.')
                rgb=rgb[top:bottom,left:right]
            if rgb.shape[0]>96 or rgb.shape[1]>512: return _unknown('Provide one explicit localized quantity field, not a whole screen.')
            mask=_white_mask(rgb)
            if not mask.any(): return _unknown('No visible white quantity glyphs are present; no default quantity is assumed.')
            ys,xs=np.where(mask)
            if xs.min()<2 or ys.min()<2 or xs.max()>mask.shape[1]-3 or ys.max()>mask.shape[0]-3:
                return _unknown('Visible glyphs touch the ROI boundary; a truncated quantity cannot be verified.')
            glyphs=_segments(mask)
            if not glyphs or len(glyphs)>12: return _unknown('The field does not contain one supported quantity.')
            tall=max(g['height'] for g in glyphs)
            if tall<10 or tall>55 or any(g['height']<7 for g in glyphs): return _unknown('Glyphs are too small, damaged, or not a supported quantity line.')
            candidates=[]
            for profile in sorted({t[1] for t in self.templates}):
                text=[];scores=[];margins=[]
                for index,glyph in enumerate(glyphs):
                    sample=_normalize(glyph['mask'])
                    per_character={}
                    for character,p,reference,_ in self.templates:
                        if p!=profile or (character=='x' and index!=0): continue
                        per_character[character]=max(per_character.get(character,0),_similarity(sample,reference))
                    ranked=sorted(per_character.items(),key=lambda pair:pair[1],reverse=True)
                    if not ranked: break
                    character,score=ranked[0]
                    margin=score-(ranked[1][1] if len(ranked)>1 else 0.)
                    text.append(character);scores.append(score);margins.append(margin)
                if len(text)==len(glyphs): candidates.append((''.join(text),min(scores),min(margins),profile))
            if not candidates: return _unknown('No supported glyph profile matches the field.')
            candidates.sort(key=lambda item:item[1],reverse=True)
            text,score,margin,profile=candidates[0]
            # A multiplier is permitted only as the first glyph; punctuation and
            # percentages lack matching templates and therefore fail this gate.
            numeric=text[1:] if text.startswith('x') else text
            if not numeric.isdigit() or not numeric or (len(numeric)>1 and numeric.startswith('0')):
                return _unknown('The field is nonnumeric, percent, incomplete, or not an explicit quantity.',score)
            if score<MIN_SCORE:
                return _unknown('At least one glyph is unsupported, damaged, or below the calibrated match threshold.',score)
            if margin<MIN_MARGIN:
                return _unknown('Two digit identities are too similar to verify this quantity.',score,'ambiguous')
            for alternative,other_score,_,_ in candidates[1:]:
                if other_score>=MIN_SCORE and alternative!=text and score-other_score<MIN_MARGIN:
                    return _unknown('Competing glyph profiles disagree on the quantity.',score,'ambiguous')
            return {'quantity':int(numeric),'quantity_status':'verified','score':round(score,6),'method':METHOD,
                    'reason':f'Every visible glyph matches the inspected local {profile} profile; this verifies numerals, not item identity.'}
        except (OSError,ValueError,TypeError,cv2.error) as exc:
            return _unknown('The quantity image could not be decoded or processed: '+str(exc))

def build_templates(project_root=None,template_dir=None):
    project=Path(project_root) if project_root else PROJECT
    destination=Path(template_dir) if template_dir else DEFAULT_TEMPLATE_DIR
    destination.mkdir(parents=True,exist_ok=True)
    evidence=[];glyphs=[];seen=set()
    for field in EVIDENCE:
        source=project/field['source'];raw=source.read_bytes()
        with Image.open(source) as im: crop=im.convert('RGB').crop(field['box'])
        crop_path='evidence-'+field['id']+'.png';crop.save(destination/crop_path)
        mask=_white_mask(np.asarray(crop));parts=_segments(mask)
        if len(parts)!=len(field['text']):
            raise ValueError(f"Inspected label {field['id']} has {len(field['text'])} glyphs but segmentation found {len(parts)}; refusing automatic labelling.")
        field_row={**field,'source_sha256':hashlib.sha256(raw).hexdigest(),'crop_path':crop_path,
                   'label_status':'manually_checked_saved_screenshot','source_image_size':list(Image.open(source).size)}
        evidence.append(field_row)
        for index,(character,part) in enumerate(zip(field['text'],parts)):
            normalized=_normalize(part['mask']);key=(character,field['profile'],normalized.tobytes())
            if key in seen: continue
            seen.add(key)
            filename=f"glyph-{field['profile']}-{character}-{len(glyphs):03d}.png"
            Image.fromarray(normalized*255).save(destination/filename)
            glyphs.append({'character':character,'profile':field['profile'],'path':filename,
                           'sha256':hashlib.sha256((destination/filename).read_bytes()).hexdigest(),
                           'evidence_id':field['id'],'glyph_index':index,'glyph_box_in_crop':part['box']})
    manifest={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'method':METHOD,
              'threshold':MIN_SCORE,'minimum_character_margin':MIN_MARGIN,'evidence':evidence,'glyphs':glyphs,
              'supported_digits':sorted({g['character'] for g in glyphs if g['character'].isdigit()}),
              'unsupported_digits':['8','9'],'limitations':['Calibration uses these saved screens only, not the live game.','Unseen fonts, missing digits, screen scales, percentages and truncated fields must remain unknown.','A tight numeric crop does not provide semantic context; caller must supply only an explicit quantity field.','Item identity status is independent of quantity numeral status.']}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    reader=QuantityReader(destination);calibration=[]
    for field in evidence:
        if field['quantity'] is None: continue
        result=reader.read(destination/field['crop_path'])
        calibration.append({'evidence_id':field['id'],'expected':field['quantity'],'result':result})
    (destination/'calibration.json').write_text(json.dumps(calibration,indent=2),encoding='utf8')
    if any(c['result']['quantity']!=c['expected'] or c['result']['quantity_status']!='verified' for c in calibration):
        raise ValueError('A inspected numeric field failed calibrated local-template recognition; inspect calibration.json before using this profile.')
    return {'glyph_templates':len(glyphs),'quantity_fields_checked':len(calibration),'template_dir':str(destination)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-templates',action='store_true');parser.add_argument('--image');parser.add_argument('--box',nargs=4,type=int)
    args=parser.parse_args()
    if args.build_templates: print(json.dumps(build_templates(),indent=2))
    elif args.image: print(json.dumps(QuantityReader().read(args.image,args.box),indent=2))
    else: parser.print_help()

if __name__=='__main__': main()
