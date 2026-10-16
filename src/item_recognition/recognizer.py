"""Deterministic offline item recognition. Never controls the game or writes library data."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, UnidentifiedImageError
try:
    from .reward_identity import character_references, canonical_groups
    from .reward_layout import unrevealed_label
except ImportError:
    from reward_identity import character_references, canonical_groups
    from reward_layout import unrevealed_label

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]
DEFAULT_LIBRARY=PROJECT/'runtime/library'
DEFAULT_TEMPLATES=PROJECT/'assets/item_recognition'
_MATCH_BATCH_SIZE=32

def image_rgb(value):
    if isinstance(value,Image.Image): return np.asarray(value.convert('RGB'))
    if isinstance(value,np.ndarray):
        if value.ndim==2: return cv2.cvtColor(value,cv2.COLOR_GRAY2RGB)
        if value.ndim!=3 or value.shape[2] not in (3,4): raise ValueError('Expected RGB/RGBA image')
        return value[:,:,:3].astype(np.uint8)
    with Image.open(value) as im: return np.asarray(im.convert('RGB'))

def crop(rgb,box):
    x,y,w,h=map(int,box)
    if x<0 or y<0 or w<=0 or h<=0 or x+w>rgb.shape[1] or y+h>rgb.shape[0]:
        raise ValueError('Bounding box outside screenshot')
    return rgb[y:y+h,x:x+w]

def file_hash(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _library_module():
    # Load the existing source without creating a bytecode file in the reference library.
    file=PROJECT/'src/game_library/library.py'
    spec=importlib.util.spec_from_file_location('_nbr_readonly_reference_library',file)
    mod=importlib.util.module_from_spec(spec)
    old=sys.dont_write_bytecode; sys.dont_write_bytecode=True
    try: spec.loader.exec_module(mod)
    finally: sys.dont_write_bytecode=old
    return mod

class ItemRecognizer:
    def __init__(self,library_path=DEFAULT_LIBRARY,calibration_path='auto',templates_dir=None,include_characters=True,reviewed_path='auto'):
        location=Path(library_path).resolve()
        self.library_root=location if location.is_dir() else location.parent.parent if location.parent.name=='data' else location.parent
        self.db_path=location/'data/library.sqlite3' if location.is_dir() else location
        self.templates_dir=Path(templates_dir or DEFAULT_TEMPLATES)
        self.references=[]; self.character_references=[]; self.reference_errors=[]; self.excluded_references=[]; self.library_reference_count=0; self._images=[]
        self.threshold=.88; self.margin=.04; self.calibration=None; calibration_file_hash=None
        if calibration_path=='auto':
            available=DEFAULT_TEMPLATES/'calibration.json'
            calibration_path=None
            if available.exists():
                stored=json.loads(available.read_text('utf-8'))
                if stored.get('library_sha256')==file_hash(self.db_path): calibration_path=available
        if calibration_path:
            self.calibration=json.loads(Path(calibration_path).read_text('utf-8'))
            calibration_file_hash=file_hash(calibration_path)
            self.threshold=float(self.calibration['threshold']); self.margin=float(self.calibration['margin'])
            if not 0<=self.threshold<=1 or not 0<=self.margin<=1: raise ValueError('Invalid calibration bounds')
            if self.calibration.get('library_sha256') and self.calibration['library_sha256']!=file_hash(self.db_path):
                raise ValueError('Calibration belongs to another library revision; validate again or disable calibration')
        self.supported_items=set((self.calibration or {}).get('supported_item_ids',[]))
        self.supported_characters=set((self.calibration or {}).get('supported_character_ids',[]))
        self.supported=self.supported_items | self.supported_characters
        self.identity_thresholds=(self.calibration or {}).get('per_identity_thresholds',{})
        for limits in self.identity_thresholds.values():
            if any(not 0<=float(limits.get(k,default))<=1 for k,default in (('threshold',self.threshold),('margin',self.margin))):
                raise ValueError('Invalid per-identity calibration bounds')
        lib=_library_module()
        with lib.ReferenceLibrary(self.db_path) as source:
            for record in source.item_references():
                self.library_reference_count+=1
                original=record.get('metadata',{}).get('original',{})
                combat=all(k in original for k in ('hp','attack'))
                enemy_art=any('/enemies/' in a['source_image_url'] or '/bosses/' in a['source_image_url'] or '/ninjas/' in a['source_image_url'] for a in record['assets'])
                if combat or enemy_art:
                    self.excluded_references.append({'item_id':record['id'],'item_name':record['display_name'],'reason':'Combat enemy/character evidence in source metadata; excluded conservatively without changing the library.'})
                    continue
                self._load_reference(record); self.references.append(record)
            if include_characters:
                for record in character_references(source):
                    self._load_reference(record); self.character_references.append(record)
        self._records={r['id']:r for r in self.references+self.character_references}
        reviewed,reviewed_hash=self._reviewed_rewards(reviewed_path,calibration_file_hash)
        base_groups=(self.calibration or {}).get('canonical_groups',[]) if (self.calibration or {}).get('schema_version',1)>=2 else []
        self.identity_map,self.canonical_groups=canonical_groups(list(self._records.values()),
            base_groups+reviewed['canonical_groups'],self.supported)
        for group in reviewed['canonical_groups']:
            canonical=group['canonical_item_id']
            if canonical in reviewed['supported_item_ids'] and all(self.identity_map.get(mid)==canonical for mid in group['member_ids']):
                self.supported_items.add(canonical)
        self.supported=self.supported_items | self.supported_characters
        self.reference_snapshot={'library_sha256':file_hash(self.db_path),
            'calibration_sha256':hashlib.sha256(json.dumps(self.calibration,sort_keys=True).encode()).hexdigest() if self.calibration else None,
            'reviewed_rewards_sha256':reviewed_hash,
            'item_reference_records':len(self.references),'character_reference_records':len(self.character_references),
            'item_reference_images':sum(r['record_type']=='item' for r in self._images),
            'character_reference_images':sum(r['record_type']=='character' for r in self._images)}
        if self._images:
            self._pixels=np.stack([r['pixels'] for r in self._images])
            self._mask=np.stack([r['mask'] for r in self._images])[:,:,:,None]
            self._weight=self._mask.sum(axis=(1,2,3))*3
        else: self._pixels=None
        self.profile=None
        profile_path=self.templates_dir/'profiles.json'
        if profile_path.exists(): self.profile=json.loads(profile_path.read_text('utf-8'))
        try:
            if __package__:
                from .quantity import QuantityReader
            else:
                from quantity import QuantityReader
            self.quantity_reader=QuantityReader(self.templates_dir/'quantity')
        except (ImportError,OSError,ValueError): self.quantity_reader=None

    def _reviewed_rewards(self,path,calibration_file_hash):
        """Add reviewed aliases only to the exact library/calibration they validated.

        This source-controlled supplement never changes thresholds or the live
        calibration file. Invalid or stale declarations cannot promote identities.
        """
        empty={'canonical_groups':[],'supported_item_ids':[]}
        if not self.calibration or not calibration_file_hash or path is None: return empty,None
        path=DEFAULT_TEMPLATES/'reviewed_rewards.json' if path=='auto' else Path(path)
        try:
            raw=path.read_bytes(); reviewed=json.loads(raw)
            if (not isinstance(reviewed,dict) or reviewed.get('schema_version')!=1
                    or reviewed.get('library_sha256')!=file_hash(self.db_path)
                    or reviewed.get('base_calibration_sha256')!=calibration_file_hash): return empty,None
            groups=reviewed.get('canonical_groups'); supported=reviewed.get('supported_item_ids')
            if (not isinstance(groups,list) or not isinstance(supported,list)
                    or any(not isinstance(rid,str) for rid in supported)): return empty,None
            valid=[]
            for group in groups:
                if not isinstance(group,dict): continue
                canonical=group.get('canonical_item_id'); members=group.get('member_ids')
                if (not isinstance(canonical,str) or not isinstance(members,list) or len(members)<2
                        or any(not isinstance(mid,str) or mid not in self._records for mid in members)
                        or len(set(members))!=len(members)
                        or canonical not in members or self._records[canonical]['record_type']!='item'
                        or group.get('allow_alternate_names') is not True
                        or not isinstance(group.get('source_card_id'),str) or not group['source_card_id'].isdigit()
                        or not group.get('evidence')): continue
                # The existing identity policy verifies every member's source
                # card and rejects conflicting element, rarity, type or game ID.
                mapping,details=canonical_groups(list(self._records.values()),[group],self.supported)
                evidence=details.get(canonical,{}).get('evidence',[])
                if (all(mapping.get(mid)==canonical for mid in members)
                        and any(e.get('kind')=='reviewed_calibration' and e.get('source_card_id')==group['source_card_id'] for e in evidence)):
                    valid.append(group)
            if not valid: return empty,None
            return {'canonical_groups':valid,'supported_item_ids':supported},hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError,TypeError): return empty,None

    def _load_reference(self,record):
        paths=list(dict.fromkeys(str((self.library_root/a['local_image_path']).resolve()) for a in record['assets'] if a['status']=='ok' and a['local_image_path']))
        record['reference_paths']=paths
        for path in paths:
            try:
                with Image.open(path) as im:
                    rgba=np.asarray(im.convert('RGBA')); rgb=rgba[:,:,:3]; alpha=rgba[:,:,3]
                # Some source cards contain opaque edge artifacts that distort
                # automatic trimming. Reviewed bounds apply to exact bytes only.
                reviewed=(self.calibration or {}).get('reference_bounds',{})
                bounds=reviewed.get(file_hash(path)) if reviewed else None
                if bounds is not None:
                    if not isinstance(bounds,list) or len(bounds)!=4 or any(type(v) is not int for v in bounds):
                        raise ValueError('Invalid reviewed reference bounds')
                    rgb=crop(rgb,bounds); alpha=crop(alpha,bounds)
                # Keep frame, rarity and element marks. Remove only transparent margins.
                yy,xx=np.where(alpha>16)
                if not len(xx): raise ValueError('Fully transparent reference')
                box=(int(xx.min()),int(yy.min()),int(xx.max()+1),int(yy.max()+1))
                rgb=rgb[box[1]:box[3],box[0]:box[2]]; alpha=alpha[box[1]:box[3],box[0]:box[2]]
                pixels=cv2.resize(rgb,(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)/255
                mask=cv2.resize(alpha,(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)/255
                self._images.append(dict(item_id=record['id'],item_name=record['display_name'],record_type=record['record_type'],path=path,pixels=pixels,mask=mask,original=rgb,transparent=bool(np.any(alpha<255)),width=rgb.shape[1],height=rgb.shape[0]))
            except (OSError,ValueError,UnidentifiedImageError) as e:
                self.reference_errors.append({'path':path,'record_id':record['id'],'error':str(e)})

    def reference_report(self):
        hashes={file_hash(r['path']) for r in self._images}
        item_images=[r for r in self._images if r['record_type']=='item']
        asset_sources={a['local_image_path']:a for record in self.references for a in record['assets'] if a.get('local_image_path')}
        fandom_paths={str((self.library_root/path).resolve()) for path,a in asset_sources.items()
                      if any(s.get('source_name')=='Fandom' for s in a.get('source_urls',[]))}
        return {'library_api_verified_records_with_images':self.library_reference_count,'verified_item_records':len(self.references),'verified_character_records':len(self.character_references),
                'loaded_item_reference_images':sum(r['record_type']=='item' for r in self._images),'loaded_character_reference_images':sum(r['record_type']=='character' for r in self._images),
                'loaded_fandom_item_images':sum(r['path'] in fandom_paths for r in item_images),
                'item_identities_with_multiple_images':sum(len(r['reference_paths'])>1 for r in self.references),
                'item_reference_max_dimensions':[max((r['width'] for r in item_images),default=0),max((r['height'] for r in item_images),default=0)],
                'library_apis':['ReferenceLibrary.item_references()','ReferenceLibrary.search(record_type=character, status=source_verified/manually_verified, page=..., limit=120)','ReferenceLibrary.get(record_id)'],
                'canonical_groups':[g for g in self.canonical_groups.values() if len(g['member_ids'])>1],
                'excluded_non_item_records':len(self.excluded_references),'excluded_references':self.excluded_references,'loaded_reference_images':len(self._images),
                'unique_reference_image_contents':len(hashes),'images_with_transparency':sum(r['transparent'] for r in self._images),
                'errors':self.reference_errors,'verification_basis':'Source/manual verification of library labels; screenshot accuracy evaluated separately.'}

    def match_icon(self,image_or_path):
        result={'item_id':None,'item_name':None,'match_score':None,'identity_status':'unknown','candidates':[],
                'score_margin':None,'confidence':{'calibrated':False,'score_kind':'similarity, not probability','threshold':self.threshold,'required_margin':self.margin}}
        try:
            rgb=image_rgb(image_or_path)
            alpha=None
            if isinstance(image_or_path,np.ndarray) and image_or_path.ndim==3 and image_or_path.shape[2]==4:
                alpha=image_or_path[:,:,3]
            elif isinstance(image_or_path,Image.Image):
                if 'A' in image_or_path.getbands(): alpha=np.asarray(image_or_path.getchannel('A'))
            elif not isinstance(image_or_path,np.ndarray):
                with Image.open(image_or_path) as im:
                    if 'A' in im.getbands(): alpha=np.asarray(im.getchannel('A'))
            if alpha is not None and np.any(alpha<255):
                yy,xx=np.where(alpha>16)
                if not len(xx): raise ValueError('Fully transparent icon')
                rgb=rgb[yy.min():yy.max()+1,xx.min():xx.max()+1]
        except (OSError,ValueError,UnidentifiedImageError) as e:
            result['reason']='Invalid icon: '+str(e); return result
        if self._pixels is None or min(rgb.shape[:2])<8 or float(rgb.std())<4:
            result['reason']='Missing references or blank/uninformative icon'; return result
        pixels=cv2.resize(rgb,(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)/255
        # Masked color error preserves element and rarity differences; spatial correlation measures structure.
        # Bound temporary arrays independently of library size. Keep every
        # score so aliases and competing identities still rank globally.
        scores=np.empty(len(self._images),dtype=np.float32)
        for start in range(0,len(self._images),_MATCH_BATCH_SIZE):
            end=start+_MATCH_BATCH_SIZE
            references=self._pixels[start:end]; mask=self._mask[start:end]; weight=self._weight[start:end]
            mse=((references-pixels)**2*mask).sum(axis=(1,2,3))/weight
            color=np.clip(1-np.sqrt(mse),0,1)
            mean_ref=(references*mask).sum(axis=(1,2,3))/weight
            mean_crop=(pixels*mask).sum(axis=(1,2,3))/weight
            a=references-mean_ref[:,None,None,None]; b=pixels-mean_crop[:,None,None,None]
            numerator=(a*b*mask).sum(axis=(1,2,3))
            denominator=np.sqrt((a*a*mask).sum(axis=(1,2,3))*(b*b*mask).sum(axis=(1,2,3)))
            correlation=np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>1e-8)
            scores[start:end]=.55*color+.45*np.clip(correlation,0,1)
        best_by_item={}
        for i,r in enumerate(self._images):
            canonical=self.identity_map[r['item_id']]; record=self._records[canonical]
            group=self.canonical_groups[canonical]
            candidate={'item_id':canonical,'item_name':record['display_name'],'record_type':record['record_type'],
                       'rarity':record.get('rarity'),'element':record.get('element'),'game_id':record.get('game_id'),
                       'canonical_group':canonical,'member_ids':group['member_ids'],
                       'reference_item_id':r['item_id'],'match_score':round(float(scores[i]),6),'reference_path':r['path']}
            if canonical not in best_by_item or candidate['match_score']>best_by_item[canonical]['match_score']:
                best_by_item[canonical]=candidate
        ranked=sorted(best_by_item.values(),key=lambda r:(-r['match_score'],r['item_id']))
        best=ranked[0]; gap=best['match_score']-ranked[1]['match_score'] if len(ranked)>1 else 1.0
        result.update(match_score=best['match_score'],candidates=ranked[:5],score_margin=round(gap,6))
        # Calibrated source aliases may no longer be exposed as separate records.
        # Keep their gates, taking the stricter value when member limits disagree.
        limits=[self.identity_thresholds[mid] for mid in best['member_ids'] if mid in self.identity_thresholds]
        threshold=max((float(value.get('threshold',self.threshold)) for value in limits),default=self.threshold)
        margin=max((float(value.get('margin',self.margin)) for value in limits),default=self.margin)
        result['confidence'].update(threshold=threshold,required_margin=margin)
        if best['match_score']<threshold: result['reason']='Similarity below acceptance threshold'; return result
        if gap<margin:
            result['identity_status']='ambiguous'; result['reason']='Competing item identities are too similar'; return result
        supported=self.supported_characters if best['record_type']=='character' else self.supported_items
        calibrated=bool(supported.intersection(best['member_ids']))
        result.update({k:v for k,v in best.items() if k not in ('match_score','reference_path')})
        result.update(identity_status='matched' if calibrated else 'candidate')
        result['confidence']['calibrated']=calibrated
        result['reason']='Accepted within validated reward subset' if calibrated else 'Uncalibrated candidate; manual review required'
        return result

    def _anchor_score(self,rgb,path,box):
        current=crop(rgb,box)
        with Image.open(self.templates_dir/path) as im: expected=np.asarray(im.convert('RGB'))
        current=cv2.resize(current,(expected.shape[1],expected.shape[0]))
        a=cv2.cvtColor(current,cv2.COLOR_RGB2GRAY); b=cv2.cvtColor(expected,cv2.COLOR_RGB2GRAY)
        return float(cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)[0,0])

    def detect_reward_region(self,rgb):
        if not self.profile: return None
        p=self.profile
        if abs(rgb.shape[1]/rgb.shape[0]-p['width']/p['height'])>.015: return None
        normalized=cv2.resize(rgb,(p['width'],p['height']),interpolation=cv2.INTER_AREA)
        if self._anchor_score(normalized,p['header']['path'],p['header']['box'])<p['anchor_threshold']: return None
        phases=[(self._anchor_score(normalized,a['path'],a['box']),name) for name,a in p['phases'].items()]
        score,phase=max(phases)
        if score<p['anchor_threshold']: return None
        return {'screen_type':phase,'normalized':normalized,'region':p.get('reward_region') if phase=='acquired' else None,
                'scale_x':rgb.shape[1]/p['width'],'scale_y':rgb.shape[0]/p['height'],'anchor_score':round(score,6)}

    def detect_icons(self,rgb,region):
        """Contour proposals for explicit regions when a validated grid profile isn't available."""
        area=crop(rgb,region); gray=cv2.cvtColor(area,cv2.COLOR_RGB2GRAY)
        edges=cv2.Canny(gray,70,150); edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        contours,_=cv2.findContours(edges,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        boxes=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if min(w,h)>=40 and .7<w/h<1.4 and max(w,h)<=min(area.shape[:2])*.5:
                boxes.append([region[0]+x,region[1]+y,w,h])
        return sorted(boxes,key=lambda b:(b[1],b[0]))

    def _grid(self,rgb):
        p=self.profile; found=[]
        for box in p['grid_boxes']:
            tile=crop(rgb,box)
            # Package backs have card-like borders too; exclude them before identity matching.
            placeholder=unrevealed_label(tile,p,self.templates_dir)
            if placeholder:
                found.append((box,'unrevealed',{'identity_status':'unknown','item_id':None,'item_name':None,'match_score':None,'candidates':[], 'reason':'Unopened reward package: '+placeholder}))
            elif self._has_card_frame(tile):
                found.append((box,'icon',self.match_icon(tile)))
        return found

    @staticmethod
    def _has_card_frame(tile):
        gray=cv2.cvtColor(tile,cv2.COLOR_RGB2GRAY).astype(np.float32)
        gx=np.abs(cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3))
        gy=np.abs(cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3))
        h,w=gray.shape
        right=float((gx[20:h-14,w-17:w-1]>110).mean(axis=0).max())
        bottom=float((gy[h-16:h-1,20:w-10]>110).mean(axis=1).max())
        return right>.68 and bottom>.68

    def analyze_screenshot(self,screenshot_path,reward_region=None,icon_boxes=None,quantity_boxes=None,debug_path=None,**legacy_metadata):
        # Older collectors can lazily load this newer API while farming runs.
        # Accept their unused attribution argument without identifying a mission.
        unexpected=set(legacy_metadata)-{'stage'}
        if unexpected:
            raise TypeError('Unexpected recognition argument: '+', '.join(sorted(unexpected)))
        path=Path(screenshot_path)
        result={'schema_version':1,'screenshot':str(path.resolve()),'screenshot_sha256':None,'status':'error',
                'screen_type':'unknown','reward_region':None,'rewards':[],'unknown_rewards':[],'warnings':[],
                'totals':{},'summary_fields':[],'diagnostics':{'quantity_defaults_used':False,'input_controls_used':False,
                    'reference_snapshot':dict(self.reference_snapshot)}}
        try:
            raw=path.read_bytes(); result['screenshot_sha256']=hashlib.sha256(raw).hexdigest()
            with Image.open(io.BytesIO(raw)) as im: rgb=np.asarray(im.convert('RGB'))
        except (OSError,ValueError,UnidentifiedImageError) as e:
            result['warnings'].append('Cannot load screenshot: '+str(e)); return result
        context=self.detect_reward_region(rgb) if reward_region is None and icon_boxes is None else None
        work=rgb; sx=sy=1.0; proposals=[]
        if context:
            result['screen_type']=context['screen_type']
            work=context['normalized']; sx=context['scale_x']; sy=context['scale_y']
            result['diagnostics']['screen_anchor_score']=context['anchor_score']
            if context['screen_type']=='summary':
                result['status']='no_acquired_rewards'
                result['warnings'].append('Special reward conditions/summary are not an acquired-drop grid. No acquired items credited.')
                if self.quantity_reader:
                    for field in self.profile.get('summary_fields',[]):
                        reading=self.quantity_reader.read(crop(work,field['box']))
                        result['summary_fields'].append({'field':field['name'],'scope':field['scope'],'bounding_box':self._scaled(field['box'],sx,sy),**reading})
                if debug_path: self._debug(rgb,result,debug_path)
                return result
            reward_region=context['region']; proposals=self._grid(work)
        elif icon_boxes is not None:
            try: proposals=[(list(box),'icon',self.match_icon(crop(rgb,box))) for box in icon_boxes]
            except (ValueError,TypeError) as e:
                result['warnings'].append('Invalid icon bounds: '+str(e)); return result
            result['screen_type']='explicit_regions'
        elif reward_region is not None:
            try: proposals=[(box,'icon',self.match_icon(crop(rgb,box))) for box in self.detect_icons(rgb,reward_region)]
            except ValueError as e: result['warnings'].append(str(e)); return result
            result['screen_type']='explicit_reward_region'
        else:
            result['status']='unsupported_screen'
            result['warnings'].append('No validated results-screen layout matched; specify reward_region/icon_boxes for offline inspection.')
            if debug_path: self._debug(rgb,result,debug_path)
            return result
        result['reward_region']=self._scaled(reward_region,sx,sy) if reward_region else None
        for i,(box,kind,identity) in enumerate(proposals):
            quantity={'quantity':None,'quantity_status':'unknown','score':None,'reason':'No explicit quantity is displayed or no validated quantity box was supplied.'}
            qb=quantity_boxes.get(i) if isinstance(quantity_boxes,dict) else quantity_boxes[i] if quantity_boxes and i<len(quantity_boxes) else None
            if qb is not None and self.quantity_reader:
                try: quantity=self.quantity_reader.read(crop(work,qb))
                except (ValueError,OSError) as e: quantity['reason']=str(e)
            entry={**identity,'quantity':quantity.get('quantity') if quantity.get('quantity_status')=='verified' else None,
                   'quantity_status':quantity.get('quantity_status','unknown'),'quantity_score':quantity.get('score'),
                   'quantity_reason':quantity.get('reason'),'bounding_box':self._scaled(box,sx,sy),'reward_scope':'acquired',
                   'visual_state':kind,'quantity_bounding_box':self._scaled(qb,sx,sy) if qb is not None else None}
            if identity['identity_status'] in ('matched','candidate'): result['rewards'].append(entry)
            else: result['unknown_rewards'].append(entry)
            if entry['identity_status']=='matched' and entry['quantity_status']=='verified':
                result['totals'][entry['item_id']]=result['totals'].get(entry['item_id'],0)+entry['quantity']
        all_rewards=result['rewards']+result['unknown_rewards']
        result['status']='complete' if all_rewards and all(r['identity_status']=='matched' and r['quantity_status']=='verified' for r in all_rewards) else 'partial' if all_rewards else 'no_visible_rewards'
        if any(r['quantity'] is None for r in all_rewards): result['warnings'].append('Unknown quantities are excluded from numerical totals. Stars indicate rarity, not quantity.')
        if any(r['identity_status']=='candidate' for r in all_rewards): result['warnings'].append('Uncalibrated identities remain review candidates.')
        if any(r['visual_state']=='unrevealed' for r in all_rewards): result['warnings'].append('Unopened reward packages hide item identities; no item guessed from package color.')
        if debug_path: self._debug(rgb,result,debug_path)
        return result

    @staticmethod
    def _scaled(box,sx,sy): return [round(box[0]*sx),round(box[1]*sy),round(box[2]*sx),round(box[3]*sy)]

    def _debug(self,rgb,result,path):
        image=Image.fromarray(rgb); draw=ImageDraw.Draw(image)
        if result['reward_region']:
            x,y,w,h=result['reward_region']; draw.rectangle((x,y,x+w,y+h),outline='cyan',width=3)
        for r in result['rewards']+result['unknown_rewards']:
            x,y,w,h=r['bounding_box']; state=r['identity_status']
            color='lime' if state=='matched' else 'orange' if state in ('candidate','ambiguous') else 'red'
            draw.rectangle((x,y,x+w,y+h),outline=color,width=3)
            label=(r['item_name'] or state).replace('Awakening Scroll ','Scroll ').replace('Gama-chan ','Gama ').replace('"','')
            # Labels occupy the gap above cards, leaving element badges and rarity stars visible.
            while label and draw.textbbox((0,0),label)[2]>w-5: label=label[:-1]
            top=max(24,y-23)
            draw.rectangle((x,top,x+w,y-2),fill='black'); draw.text((x+2,top),label,fill=color)
            detail=state+' | qty '+str(r['quantity'] if r['quantity'] is not None else '?')
            draw.text((x+2,top+10),detail,fill=color)
        for field in result.get('summary_fields',[]):
            x,y,w,h=field['bounding_box']; accepted=field['quantity_status']=='verified'
            color='lime' if accepted else 'orange'
            draw.rectangle((x,y,x+w,y+h),outline=color,width=2)
            label=field['scope']+': '+str(field['quantity'] if accepted else '?')
            left=max(0,x-190); top=max(25,y-18)
            draw.rectangle((left,top,x+w,top+16),fill='black'); draw.text((left+2,top+2),label,fill=color)
        draw.rectangle((0,0,image.width,22),fill='black'); draw.text((5,4),result['status']+' | green: matched / orange: review / red: unknown / qty ?: unknown',fill='white')
        target=Path(path).resolve()
        if target==Path(result['screenshot']).resolve(): raise ValueError('Debug output must not overwrite input')
        target.parent.mkdir(parents=True,exist_ok=True); image.save(target)
