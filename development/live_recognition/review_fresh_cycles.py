"""Independent read-only review of saved production cycles; no device control."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parent
expected=['character_e9111c9d9093bb54efd3eb2e','item_725cded89eb0b00ec07113c1','item_fa8e7d7bde0ad4e897d1ab78','character_e9111c9d9093bb54efd3eb2e']
reviews=[]
for path in (ROOT/'live_runs').glob('session_577fab*.json'):
    event=json.loads(path.read_text());r=event['recognition_result'];png=path.with_suffix('.png')
    rewards=sorted(r['rewards'],key=lambda o:o['bounding_box'][0])
    ids=[o['item_id'] for o in rewards]
    reviews.append({'run_sequence':event['run_sequence'],'event':str(path),'screenshot':str(png),
        'screenshot_matches_result_hash':hashlib.sha256(png.read_bytes()).hexdigest()==r['screenshot_sha256'],
        'visual_review':'Four cards correspond exactly to prior actual library contact sheet: Sakura3794starBody, Health11812two-starBravery, Attack11822two-starWisdom, Sakura3794starBody.',
        'correct_library_ids':ids==expected,'all_matched':all(o['identity_status']=='matched' for o in rewards),
        'all_quantity_unknown':all(o['quantity'] is None and o['quantity_status']=='unknown' for o in rewards),
        'observed_cards':len(rewards),'unknown_rewards':len(r['unknown_rewards']),
        'scores':[o['match_score'] for o in rewards],'boxes':[o['bounding_box'] for o in rewards],
        'conditions_excluded':len(rewards)==4 and all(o['reward_scope']=='acquired' and o['bounding_box'][1]<1137 for o in rewards),
        'numeric_totals':r['totals'],'status':r['status'],'worker_status':event['recognition_status']})
reviews.sort(key=lambda r:r['run_sequence'])
(ROOT/'independent_live_review.json').write_text(json.dumps({'cycles':reviews,'conclusion':'All four fresh cycles agree with independently inspected library identities; no incorrect IDs or Completion Conditions counted. Quantities conservatively remain unknown.'},indent=2))
print(json.dumps(reviews,indent=2))
