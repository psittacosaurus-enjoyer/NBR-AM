"""Read-only library reference evidence; no device access."""
import sys, json
from pathlib import Path
from collections import Counter
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'game_library'))
from library import ReferenceLibrary
from PIL import Image,ImageDraw
import numpy as np
import cv2

out=Path(__file__).parent
lib=ReferenceLibrary(ROOT/'game_library/data/library.sqlite3')
chars=[]
for page in range(1,4):
    chars += [lib.get(r['id']) for r in lib.search(record_type='character',page=page,limit=120)['records']]
roles=Counter((a['role'],a['width'],a['height']) for r in chars for a in r['assets'])
def brief(r):
    return {k:r[k] for k in ('id','display_name','game_id','rarity','element','source_key','verification_status')}
def asset_brief(a):
    return {k:a[k] for k in ('role','local_image_path','source_image_url','width','height','sha256')}
sheet=Image.new('RGB',(720,230),'#ddd');draw=ImageDraw.Draw(sheet)
initial=Image.open(out/'initial_acc1_results.png').convert('RGB')
portrait=initial.crop((461,1266,602,1410))
sheet.paste(portrait.resize((144,144)),(0,25));draw.text((0,175),'Live conditions (not acquired)',fill='black')
kush=[]
for i,r in enumerate([r for r in chars if 'Kushina' in r['display_name']]):
    cards=[a for a in r['assets'] if a['role']=='card' and a['status']=='ok']
    b=brief(r);b['assets']=[asset_brief(a) for a in cards];kush.append(b)
    if cards:
        im=Image.open(ROOT/'game_library'/cards[0]['local_image_path']).convert('RGBA')
        bg=Image.new('RGBA',im.size,'white');bg.alpha_composite(im)
        sheet.paste(bg.convert('RGB').resize((144,144)),((i+1)*144,25))
    draw.text(((i+1)*144,175),r['game_id']+' '+str(r['rarity'])+' '+str(r['element']),fill='black')
    draw.text(((i+1)*144,194),r['metadata'].get('title','')[:22],fill='black')
sheet.save(out/'portrait_library_contact.png')
items=[]
for r in lib.item_references():
    if 'Large Sphere' not in r['display_name']:continue
    b=brief(r);m=r['metadata'];b.update(original=m.get('original'),source_identifiers=m.get('source_identifiers'),element_code=m.get('element_code'),assets=[asset_brief(a) for a in r['assets']],occurrence_count=len(m.get('occurrences',[])))
    items.append(b)
evidence={'character_record_count':len(chars),'asset_role_dimensions':[{ 'role':k[0],'width':k[1],'height':k[2],'count':v} for k,v in roles.items()],'portrait_comparison':kush,'large_sphere_records':items}
samples=[]
files=sorted((ROOT/'runtime/loot/diagnostics').glob('20261013_022720*.png'))
if files:
    shot=Image.open(files[0]).convert('RGB')
    target=np.asarray(shot.crop((57,411,191,550)))
    pixels=cv2.resize(target,(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)/255
    ranked=[]
    for r in chars:
        candidates=[]
        for a in r['assets']:
            url=a['source_image_url']
            eligible=a['role']=='card' or (a['role']=='icon' and ('/missions/card-' in url or '/story/units/' in url))
            if not eligible or a['status']!='ok':continue
            path=ROOT/'game_library'/a['local_image_path']
            rgba=np.asarray(Image.open(path).convert('RGBA'));yy,xx=np.where(rgba[:,:,3]>16)
            rgba=rgba[yy.min():yy.max()+1,xx.min():xx.max()+1]
            ref=cv2.resize(rgba[:,:,:3],(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)/255
            mask=cv2.resize(rgba[:,:,3],(64,64),interpolation=cv2.INTER_AREA).astype(np.float32)[:,:,None]/255
            weight=mask.sum()*3
            mse=(((ref-pixels)**2)*mask).sum()/weight
            m1=(ref*mask).sum()/weight;m2=(pixels*mask).sum()/weight
            v1=ref-m1;v2=pixels-m2
            corr=(v1*v2*mask).sum()/np.sqrt((v1*v1*mask).sum()*(v2*v2*mask).sum())
            score=.55*(1-np.sqrt(mse))+.45*max(0,corr)
            candidates.append({'score':round(float(score),6),'reference_path':str(path),'asset':asset_brief(a)})
        if candidates:ranked.append(dict(brief(r),**max(candidates,key=lambda x:x['score'])))
    ranked.sort(key=lambda x:-x['score'])
    samples.append({'screenshot':str(files[0]),'box':[57,411,134,139],'comparison_method':'same .55 masked color + .45 spatial correlation as baseline recognizer; independent character-card extension for audit only','top_character_records':ranked[:5]})
    sheet.paste(shot.crop((57,411,191,550)).resize((144,144)),(0,25));draw.text((0,175),'Saved acquired slot 0',fill='black')
    sheet.save(out/'portrait_library_contact.png')
evidence['saved_acquired_portrait_trace']=samples
evidence['recommendations']=['No name-only grouping: preserve rarity and element where explicitly present.', 'Same unit ID + same rarity + nonconflicting element is strong identity evidence; road/story 11813 and11823 images are also byte-identical.', 'Character card assets plus icon assets under missions/card- and story/units/ provide portrait coverage; exclude abilities/materials/cutin/art.', 'Completion conditions portrait is not acquired loot; saved acquired slot 0 is eligible.']
(out/'reference_evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf8')
print(json.dumps({'characters':len(chars),'character_trace':samples},indent=2))
