"""Add owner-requested public Fandom materials to the existing library transactionally."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from functools import lru_cache
from PIL import Image,ImageStat
try:
    from . import library as L
except ImportError:
    import library as L

def normalized(value):
    value=str(value or '').replace('“','"').replace('”','"').replace('’',"'")
    value=re.sub(r'\s*\([^)]*(?:★|â)[^)]*\)\s*$','',value)
    value=re.sub(r'\s*★\s*[1-7]\s*$','',value)
    return re.sub(r'\s+',' ',value).strip().casefold()

def family(category):
    return {'awakening':'scroll','limit break':'crystal','spending':'gama','boosting':'sphere','enhancement material':'ramen',
            'blazing awakening':'special','character_crystal':'crystal'}.get(str(category or '').casefold(),str(category or '').casefold())

def source_name(record):
    m=record.get('metadata') or {};params=m.get('material_parameters',{})
    if params.get('mat-name'):
        n=params['mat-name'];t=params.get('mat-title')
        return n+' "'+t+'"' if t and t not in ('N/A','-') else n
    return m.get('encyclopedia_display_name') or record.get('name') or record.get('title')

def fingerprint(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def backup_database(db_path,root):
    digest=fingerprint(db_path)
    target=Path(root)/'backups'/('fandom-1.1-'+digest[:16])/'library.sqlite3'
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        with closing(sqlite3.connect(db_path)) as source,closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    return str(target)

def dhash(image):
    gray=image.convert('L').resize((9,8),Image.Resampling.LANCZOS)
    p=list(gray.get_flattened_data() if hasattr(gray,'get_flattened_data') else gray.getdata());bits=0
    for y in range(8):
        for x in range(8): bits=(bits<<1)|int(p[y*9+x]>p[y*9+x+1])
    return f'{bits:016x}'

@lru_cache(maxsize=256)
def comparison_pixels(path):
    with Image.open(path) as image: return image.convert('RGBA').resize((32,32),Image.Resampling.LANCZOS)

def visual_distance(first,second):
    from PIL import ImageChops
    distance=sum(ImageStat.Stat(ImageChops.difference(comparison_pixels(first),comparison_pixels(second))).mean[:3])/(3*255)
    return distance

def namespace(existing):
    m=existing['metadata'];original=m.get('original',{})
    return m.get('game_id_namespace') or m.get('game_id_kind') or ('unit' if original.get('unit') is not None else 'card_no' if original.get('cardNo') is not None else None)

def compatible(existing,incoming):
    if existing.get('game_id') is not None and incoming.get('game_id') is not None:
        incoming_namespace=incoming.get('metadata',{}).get('game_id_namespace')
        if incoming_namespace and namespace(existing)==incoming_namespace and str(existing['game_id'])!=str(incoming['game_id']):
            return False
    for field in ('element','rarity'):
        a=existing.get(field);b=incoming.get(field)
        if field=='element':
            # Explicit named ramen/scroll occurrences in the Gacha client catalogs establish this enum.
            mapping={'1':'Heart','2':'Skill','3':'Body','4':'Bravery','5':'Wisdom'}
            a=mapping.get(str(a),a);b=mapping.get(str(b),b)
        if a is not None and b is not None and str(a).casefold()!=str(b).casefold(): return False
    categories={family(existing.get('category')),family(incoming.get('category'))}-{'','unit','item'}
    return len(categories)<=1 or categories=={'crystal','character_crystal'}

def names(existing):
    m=existing['metadata'];original=m.get('original',{})
    values=[existing['display_name'],original.get('name'),m.get('title')]
    if original.get('name') and original.get('title'): values.append(original['name']+' "'+original['title']+'"')
    values.extend(m.get('original_names',[]))
    return {normalized(v) for v in values if v}

def proven_identity(existing,incoming):
    if not compatible(existing,incoming): return False
    m=incoming.get('metadata') or {};incoming_id=incoming.get('game_id')
    ns=m.get('game_id_namespace')
    same_id=bool(incoming_id and existing.get('game_id') and ns and namespace(existing)==ns and str(incoming_id)==str(existing['game_id']))
    same_name=normalized(source_name(incoming)) in names(existing)
    params=m.get('material_parameters',{})
    if family(incoming.get('category'))=='crystal' and 'Limit Break Crystal' in str(params.get('mat-name','')):
        title=params.get('mat-title')
        if existing.get('category')=='character_crystal' and title:
            same_name=same_name or normalized('"'+title+'"') in names(existing)
        elif title in ('Heart','Skill','Body','Bravery','Wisdom'):
            same_name=same_name or normalized(title+' Crystal') in names(existing)
    # Missing rarity/element need supporting labeled imagery, never image equality as sole identity evidence.
    same_semantics=same_name and incoming.get('rarity') is not None and (incoming.get('element') is not None or existing.get('element') is None)
    return same_id or same_semantics

def provenance(db,record):
    rid=record['id'];key=record['source_key'];url=record['source_url']
    if 'gachawiki.net' not in url: return
    db.execute('''INSERT OR IGNORE INTO record_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
               ('GachaWiki',key,rid,url,record['display_name'],record['game_id'],record['category'],record['element'],record['rarity'],json.dumps(record['metadata'],ensure_ascii=False),record['verification_status'],record['content_hash']))

def preferred_score(asset,role):
    # Card fidelity is compared within the card variant. Portrait art is distinct and retained for browsing.
    area=(asset.get('width') or 1)*(asset.get('height') or 1)
    kind=asset.get('format','').upper()
    lossless=1 if kind=='PNG' or str(asset.get('local_image_path','')).lower().endswith('.png') else 0
    return (1e12 if role in ('card','icon','image') else 0)+area*10+lossless

def fill_missing_facts(db,rid,incoming):
    if incoming.get('metadata',{}).get('source_discrepancies'): return False
    row=dict(db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone())
    if row['verification_status'] not in ('source_verified','manually_verified'): return False
    updates={field:str(incoming[field]) for field in ('game_id','rarity','element') if row[field] is None and incoming.get(field) is not None}
    if not updates: return False
    metadata=json.loads(row['metadata'])
    metadata.setdefault('source_enrichment',{}).update({field:{'value':value,'source_url':incoming['source_url'],'source_key':incoming['source_key']} for field,value in updates.items()})
    if 'game_id' in updates: metadata['game_id_namespace']=incoming.get('metadata',{}).get('game_id_namespace')
    row.update(updates);row['metadata']=json.dumps(metadata,ensure_ascii=False)
    digest=hashlib.sha256(json.dumps({k:row[k] for k in ('id','game_id','rarity','element','metadata')},sort_keys=True).encode()).hexdigest()
    db.execute('UPDATE records SET game_id=?,rarity=?,element=?,metadata=?,content_hash=?,updated_at=? WHERE id=?',(row['game_id'],row['rarity'],row['element'],row['metadata'],digest,L.now(),rid))
    return True

def import_records(records,db_path=L.DB_PATH,root=L.ROOT,dry_run=False):
    root=Path(root);db_path=Path(db_path)
    report={'records_examined':len(records),'new_identities':0,'existing_identities_enriched':0,'exact_duplicates_avoided':0,
            'visually_similar_images_flagged':0,'unresolved_identity_conflicts':0,'usable_reference_images_added':0,
            'aliases_confirmed':0,'unchanged_source_records':0,'dry_run':dry_run,'conflicts':[],'similarities':[],'errors':[]}
    if dry_run:
        with closing(L.connect(db_path,readonly=True)) as original:
            db=sqlite3.connect(':memory:');original.backup(db);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
    else:
        report['backup']=backup_database(db_path,root)
        db=L.connect(db_path)
    L.initialize(db)
    existing={r['id']:dict(r) for r in db.execute('SELECT * FROM records WHERE record_type="item"')}
    for row in existing.values(): row['metadata']=json.loads(row['metadata'])
    manual={r['record_id']:dict(r) for r in db.execute('SELECT * FROM annotations')}
    calibrated=set()
    calibration_path=Path(__file__).resolve().parents[2]/'assets/item_recognition/calibration.json'
    if calibration_path.exists(): calibrated=set(json.loads(calibration_path.read_text('utf-8')).get('supported_item_ids',[]))
    all_assets=[dict(r) for r in db.execute("SELECT * FROM assets WHERE status='ok'")]
    by_hash={}
    for asset in all_assets: by_hash.setdefault(asset['sha256'],asset)
    enriched=set();mutated=False
    try:
        with db:
            for incoming in records:
                key=str(incoming['source_key']);title=source_name(incoming);url=incoming['source_url']
                payload_hash=hashlib.sha256(json.dumps(incoming,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                previous=db.execute("SELECT record_id,content_hash FROM record_sources WHERE source_name='Fandom' AND source_key=?",(key,)).fetchone()
                if previous and previous['content_hash']==payload_hash:
                    complete=proven_identity(existing[previous['record_id']],incoming)
                    for image in incoming.get('images',[]):
                        cached=db.execute('SELECT a.local_image_path FROM asset_sources s JOIN assets a ON a.id=s.asset_id WHERE s.source_image_url=? AND a.status="ok"',(image['url'],)).fetchone()
                        if not cached or not (root/cached[0]).exists(): complete=False;break
                    if complete:
                        if fill_missing_facts(db,previous['record_id'],incoming): mutated=True
                        report['unchanged_source_records']+=1;continue
                candidates=[r for r in existing.values() if proven_identity(r,incoming)]
                related=[r for r in existing.values() if normalized(title) in names(r) and not compatible(r,incoming)]
                source_discrepancies=incoming.get('metadata',{}).get('source_discrepancies',[])
                if source_discrepancies:
                    candidates=[]
                    report['conflicts'].append({'source_key':key,'reason':'Fandom encyclopedia and material page disagree; identity requires review','source_discrepancies':source_discrepancies})
                if candidates:
                    rid=min((r['id'] for r in candidates),key=lambda x:(x not in calibrated,x not in manual,x))
                    # A reviewed label is never discarded in favor of an unreviewed source identity.
                    reviewed=[r for r in candidates if r['id'] in manual]
                    if reviewed: rid=min((r['id'] for r in reviewed),key=lambda x:(x not in calibrated,x))
                    for candidate in candidates:
                        provenance(db,candidate)
                        other=candidate['id']
                        if other!=rid:
                            if other in manual and rid in manual and manual[other].get('display_name')!=manual[rid].get('display_name'):
                                report['conflicts'].append({'source_key':key,'record_ids':[rid,other],'reason':'Distinct manual labels; no automatic alias'})
                                continue
                            evidence=json.dumps({'kind':'verified_source_metadata','source_url':url,'source_key':key,'name':title,'rarity':incoming.get('rarity'),'element':incoming.get('element')})
                            db.execute('INSERT OR IGNORE INTO identity_aliases VALUES(?,?,?)',(other,rid,evidence));report['aliases_confirmed']+=1
                    enriched.add(rid)
                else:
                    rid=L.stable_id('item','fandom:'+key)
                    if rid not in existing:
                        canonical_category=family(incoming.get('category')) or 'item'
                        meta={'original':{'name':title,'kind':canonical_category,'stars':incoming.get('rarity'),'element':incoming.get('element')},'game_id_namespace':incoming.get('metadata',{}).get('game_id_namespace'),'source':'Fandom','source_data':incoming.get('metadata',{}),'description':incoming.get('description')}
                        meta['identity_review_required']=bool(source_discrepancies)
                        L.upsert_record(db,record_type='item',source_key='fandom:'+key,display_name=title,source_url=url,game_id=incoming.get('game_id'),category=canonical_category,element=incoming.get('element'),rarity=incoming.get('rarity'),metadata=meta,verification_status='inferred' if source_discrepancies else 'source_verified')
                        row=dict(db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone());row['metadata']=json.loads(row['metadata']);existing[rid]=row;report['new_identities']+=1
                    # Different documented rarities/elements are ordinary variants. Review is needed only
                    # when a conflicting claim also points to the same exact illustration or same scoped ID.
                    incoming_hashes={i.get('sha256') for i in incoming.get('images',[]) if i.get('sha256')}
                    conflicting=[]
                    for old in related:
                        same_scoped_id=namespace(old)==incoming.get('metadata',{}).get('game_id_namespace') and str(old.get('game_id'))==str(incoming.get('game_id')) and incoming.get('game_id') is not None
                        old_hashes={x[0] for x in db.execute('SELECT a.sha256 FROM assets a JOIN record_assets ra ON a.id=ra.asset_id WHERE ra.record_id=?',(old['id'],))}
                        if same_scoped_id or incoming_hashes.intersection(old_hashes): conflicting.append(old['id'])
                    if conflicting: report['conflicts'].append({'source_key':key,'record_ids':conflicting,'reason':'Conflicting identity facts share explicit scoped ID or exact image; distinct records retained'})
                # Preserve source-specific facts rather than replacing existing values or annotations.
                metadata=dict(incoming.get('metadata',{}));metadata['description']=incoming.get('description');metadata['original_title']=incoming.get('title')
                source_values=('Fandom',key,rid,url,title,None if incoming.get('game_id') is None else str(incoming['game_id']),incoming.get('category'),None if incoming.get('element') is None else str(incoming['element']),None if incoming.get('rarity') is None else str(incoming['rarity']),json.dumps(metadata,ensure_ascii=False),'source_verified',payload_hash)
                db.execute('''INSERT INTO record_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_name,source_key) DO UPDATE SET
                   record_id=excluded.record_id,source_url=excluded.source_url,display_name=excluded.display_name,game_id=excluded.game_id,
                   category=excluded.category,element=excluded.element,rarity=excluded.rarity,metadata=excluded.metadata,content_hash=excluded.content_hash''',source_values)
                fill_missing_facts(db,rid,incoming)
                for image in incoming.get('images',[]):
                    try:
                        source=Path(image['path']);source=source if source.is_absolute() else root/source
                        digest=fingerprint(source)
                        if image.get('sha256') and digest!=image['sha256']: raise ValueError('Source image hash mismatch')
                        role=image.get('role','icon');role='artwork' if role=='artwork' else 'card' if role=='card' else 'icon'
                        asset=by_hash.get(digest)
                        if asset: report['exact_duplicates_avoided']+=1
                        else:
                            with Image.open(source) as im:
                                w,h=im.size;format_name=im.format;perceptual=image.get('perceptual_hash') or dhash(im)
                            similar=[]
                            for old in all_assets:
                                if not old.get('perceptual_hash'): continue
                                distance=(int(perceptual,16)^int(old['perceptual_hash'],16)).bit_count()
                                if distance<=5:
                                    path=root/old['local_image_path']
                                    if path.exists():
                                        visual=visual_distance(source,path)
                                        if visual<=.12: similar.append({'incoming_url':image['url'],'existing_asset_id':old['id'],'hash_distance':distance,'color_distance':round(visual,6),'record_id':rid})
                            report['similarities'].extend(similar)
                            image_url=image['url'];aid=L.stable_id('asset',image_url)
                            local='assets/items/'+digest+source.suffix.lower();thumb='assets/thumbnails/'+digest+'.webp'
                            if not dry_run:
                                target=root/local;target.parent.mkdir(parents=True,exist_ok=True)
                                if not target.exists():
                                    try: os.link(source,target)
                                    except OSError: shutil.copyfile(source,target)
                                with Image.open(source) as im:
                                    preview=im.convert('RGBA');preview.thumbnail((192,192),Image.Resampling.LANCZOS)
                                    destination=root/thumb;destination.parent.mkdir(parents=True,exist_ok=True)
                                    if not destination.exists(): preview.save(destination,'WEBP',quality=88)
                            asset={'id':aid,'source_image_url':image_url,'original_filename':image.get('original_filename') or source.name,'local_image_path':local,'thumbnail_path':thumb,'sha256':digest,'width':w,'height':h,'perceptual_hash':perceptual,'status':'ok','collected_at':L.now(),'format':format_name}
                            db.execute('''INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_image_url) DO UPDATE SET local_image_path=excluded.local_image_path,thumbnail_path=excluded.thumbnail_path,sha256=excluded.sha256,width=excluded.width,height=excluded.height,perceptual_hash=excluded.perceptual_hash,status=excluded.status''',tuple(asset[k] for k in ('id','source_image_url','original_filename','local_image_path','thumbnail_path','sha256','width','height','perceptual_hash','status','collected_at')))
                            by_hash[digest]=asset;all_assets.append(asset);report['usable_reference_images_added']+=1
                        db.execute('INSERT OR IGNORE INTO record_assets VALUES(?,?,?)',(rid,asset['id'],role))
                        db.execute('INSERT OR REPLACE INTO asset_sources VALUES(?,?,?,?,?)',(image['url'],asset['id'],'Fandom',url,json.dumps(image,ensure_ascii=False)))
                        # Retain GachaWiki asset provenance even when Fandom pixels are byte-identical.
                        db.execute('INSERT OR IGNORE INTO asset_sources VALUES(?,?,?,?,?)',(asset['source_image_url'],asset['id'],'GachaWiki' if 'gachawiki.net' in asset['source_image_url'] else 'Fandom',url,'{}'))
                        score=preferred_score(asset,role)
                        db.execute('INSERT OR REPLACE INTO asset_preferences VALUES(?,?,?,?)',(rid,asset['id'],score,json.dumps({'criterion':'Variant role, original pixel area, then lossless encoding','role':role,'pixel_area':asset.get('width',0)*asset.get('height',0)})))
                    except (OSError,ValueError,KeyError) as error: report['errors'].append({'source_key':key,'image':image.get('url'),'error':str(error)})
                # Rank existing canonical references by the same fidelity rule, leaving all bytes intact.
                for old in db.execute('SELECT a.*,ra.role FROM assets a JOIN record_assets ra ON a.id=ra.asset_id WHERE ra.record_id=?',(rid,)).fetchall():
                    value=dict(old);score=preferred_score(value,value['role'])
                    db.execute('INSERT OR IGNORE INTO asset_preferences VALUES(?,?,?,?)',(rid,value['id'],score,'Existing source; same fidelity criterion'))
                mutated=True
            if mutated: L.index(db)
        report['existing_identities_enriched']=len(enriched)
        report['unresolved_identity_conflicts']=len(report['conflicts'])
        report['visually_similar_images_flagged']=len({s['incoming_url'] for s in report['similarities']})
        report['visually_similar_pairs']=len(report['similarities'])
        report['logical_item_count']=L.stats(db)['by_type'].get('item',0)
        if not dry_run:
            L.export(db,root/'catalogs');L.atomic_json(root/'reports/fandom_import.json',report)
        return report
    finally: db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--records',type=Path,default=L.ROOT/'data/fandom/source_records.json');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();records=json.loads(args.records.read_text('utf-8'))
    report=import_records(records,dry_run=args.dry_run)
    print(json.dumps({k:v for k,v in report.items() if k not in ('conflicts','similarities','errors')},indent=2))

if __name__=='__main__': main()
