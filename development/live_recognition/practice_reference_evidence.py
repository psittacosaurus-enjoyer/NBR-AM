"""Read-only visual ground truth for acc1 saved Practice results."""
import sys,json,sqlite3
from pathlib import Path
from PIL import Image,ImageDraw
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'game_library'))
from library import ReferenceLibrary
lib=ReferenceLibrary(ROOT/'game_library/data/library.sqlite3')
shot=next((ROOT/'runtime/loot/diagnostics').glob('20261013_030903*.png'))
db=sqlite3.connect((ROOT/'runtime/loot/loot.sqlite3').as_uri()+'?mode=ro',uri=True)
result=json.loads(db.execute('SELECT result_json FROM completions WHERE screenshot_path=?',(str(shot),)).fetchone()[0])
observations=sorted(result['rewards']+result['unknown_rewards'],key=lambda r:r['bounding_box'][0])
sheet=Image.new('RGB',(6*150,4*190),'white');draw=ImageDraw.Draw(sheet);im=Image.open(shot)
evidence=[]
for row,o in enumerate(observations):
    x,y,w,h=o['bounding_box'];sheet.paste(im.crop((x,y,x+w,y+h)).resize((144,144)),(0,row*190))
    draw.text((0,row*190+148),'Live slot '+str(row),fill='black')
    seen=set();records=[]
    for c in o['candidates'][:3]:
        ids=c.get('member_ids',[c['item_id']])
        for rid in ids:
            if rid in seen:continue
            seen.add(rid);r=lib.get(rid)
            b={k:r[k] for k in ('id','display_name','game_id','rarity','element','source_key')}
            m=r['metadata'];b['original']=m.get('original');b['source_identifiers']=m.get('source_identifiers')
            b['assets']=[{k:a[k] for k in ('role','local_image_path','source_image_url','width','height','sha256')} for a in r['assets'] if a['role']=='card' or (a['role']=='icon' and not any(p in a['source_image_url'] for p in ('/abilities/','/materials/')))]
            records.append(b)
    for col,r in enumerate(records[:5],1):
        path=ROOT/'game_library'/r['assets'][0]['local_image_path']
        asset=Image.open(path).convert('RGBA').resize((144,144));bg=Image.new('RGBA',(144,144),'white');bg.alpha_composite(asset)
        sheet.paste(bg.convert('RGB'),(col*150,row*190));draw.text((col*150,row*190+148),str(r['game_id'])+' '+str(r['rarity'])+' '+str(r['element']),fill='black');draw.text((col*150,row*190+165),r['display_name'][:23],fill='black')
    evidence.append({'slot':row,'box':o['bounding_box'],'stored_status':o['identity_status'],'stored_score':o['match_score'],'library_records':records})
sheet.save(Path(__file__).parent/'practice_library_contact.png')
payload={'screenshot':str(shot),'screenshot_sha256':result['screenshot_sha256'],'observations':evidence,
         'visual_ground_truth':[
             {'slot':0,'id':'character_e9111c9d9093bb54efd3eb2e','name':'Sakura Haruno "Dancing Cherry Blossoms"','game_id':'379','rarity':'4','element':'Body'},
             {'slot':1,'id':'item_725cded89eb0b00ec07113c1','aliases':['item_2fe513792b516fa05b67582e','item_7f8b1b8b9fd7aa1f1793cbd9'],'name':'Health Boost "Health and Endurance"','game_id':'11812','rarity':'2','element':'4'},
             {'slot':2,'id':'item_fa8e7d7bde0ad4e897d1ab78','aliases':['item_485e45d483552d942c933c5a','item_819bb9bdea8ff6fb7f887055'],'name':'Attack Boost "Sphere of Strength"','game_id':'11822','rarity':'2','element':'5'},
             {'slot':3,'id':'character_e9111c9d9093bb54efd3eb2e','name':'Sakura Haruno "Dancing Cherry Blossoms"','game_id':'379','rarity':'4','element':'Body','overlay':'Lucky Drop'}],
         'quantity':'All four acquired cards are observed icons; no explicit numeric quantities are printed. Four/five stars are rarity, not quantity.'}
cycles=[]
for n,path,raw in db.execute('SELECT run_sequence,screenshot_path,result_json FROM completions WHERE session_id=? ORDER BY run_sequence',('session_94c68fea9ee71e3d2da0cb4a',)):
    r=json.loads(raw)
    if 'rewards' not in r:continue
    cycles.append({'run_sequence':n,'screenshot_path':path,'stored_observations':[{'box':o['bounding_box'],'identity_status':o['identity_status'],'score':o['match_score'],'top_id':o['candidates'][0]['item_id']} for o in sorted(r['rewards']+r['unknown_rewards'],key=lambda a:a['bounding_box'][0])]})
payload['repeated_cycles']=cycles
(Path(__file__).parent/'practice_reference_evidence.json').write_text(json.dumps(payload,indent=2),encoding='utf8')
print(json.dumps(cycles,indent=2))
