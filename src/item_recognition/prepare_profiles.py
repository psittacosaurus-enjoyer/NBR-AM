"""Build only offline layout anchors from visually inspected existing results screenshots."""
import json
import hashlib
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[1]

def build():
    directory=PROJECT/'assets/item_recognition'; directory.mkdir(exist_ok=True)
    evidence=[]
    def save(path,name,box):
        with Image.open(path) as im:
            if im.size!=(900,1600): raise ValueError('Unexpected source geometry')
            im.crop((box[0],box[1],box[0]+box[2],box[1]+box[3])).save(directory/(name+'.png'))
        evidence.append({'source':path.resolve().relative_to(PROJECT).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'box':box,'template':name+'.png','verification':'Visually inspected saved Mission Results screenshot; no live game access.'})
        return {'path':name+'.png','box':box}
    first=PROJECT/'assets/reference_images/starting_point.png'
    header=save(first,'mission-results',[22,27,520,78])
    acquired=save(first,'acquired-header',[240,287,420,53])
    summary=save(PROJECT/'tests/fixtures/results_next.png','summary-header',[228,300,452,55])
    boxes=[[57+162*c,411+162*r,134,139] for r in range(3) for c in range(5)]
    placeholders=[]
    sealed=PROJECT/'tests/fixtures/rich_b/reward_reveal.png'
    for i,label in [(0,'green sealed package'),(3,'silver sealed package'),(4,'brown sealed package'),(8,'Lucky Drop unopened chest')]:
        entry=save(sealed,'placeholder-'+str(i),boxes[i]); entry['label']=label; placeholders.append(entry)
    lucky=save(PROJECT/'tests/fixtures/item_recognition/bravery_lucky_unrevealed.png',
               'placeholder-lucky-back',[381,411,134,139])
    lucky['label']='Unrevealed Lucky Drop'; placeholders.append(lucky)
    profile={'version':1,'width':900,'height':1600,'anchor_threshold':.94,'header':header,
             'phases':{'acquired':acquired,'summary':summary},
             'reward_region':[48,402,800,735],'grid_boxes':boxes,'proposal_threshold':.78,
             'placeholders':placeholders,'placeholder_threshold':.92,
             'summary_fields':[{'name':'first_special_reward_amount','scope':'condition_display','box':[786,489,86,25]},
                               {'name':'second_special_reward_amount','scope':'condition_display','box':[786,617,86,26]},
                               {'name':'ryo_earned','scope':'run_summary_currency','box':[561,923,161,36]}],
             'geometry_basis':'Upper acquired-reward grid inspected in existing 900x1600 All Scrolls A and Getting Rich Quick B screenshots; lower completion conditions excluded.',
             'quantity_policy':'No quantity text shown beside acquired icons. Never treat rarity stars or repeated slots as a numerical total.',
             'evidence':evidence}
    (directory/'profiles.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    print(json.dumps({'templates':len(evidence),'profile':str(directory/'profiles.json')}))

if __name__=='__main__': build()
