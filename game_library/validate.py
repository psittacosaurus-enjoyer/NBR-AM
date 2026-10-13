"""Audit source associations, originals, thumbnails, exports, and database integrity offline."""
import hashlib
import json
from collections import Counter
from contextlib import closing
from pathlib import Path
from PIL import Image
import library as L

def validate(root=L.ROOT):
    root=Path(root); failures=[]; checks=Counter()
    with closing(L.connect(root/'data/library.sqlite3',readonly=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': failures.append('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall(): failures.append('Broken foreign keys')
        checks['database_integrity']=1
        stats=L.stats(db)
        for a in db.execute("SELECT * FROM assets WHERE status='ok'"):
            file=root/a['local_image_path']
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=a['sha256']:
                failures.append('Missing or changed original: '+a['source_image_url']); continue
            checks['original_hashes']+=1
            thumb=root/(a['thumbnail_path'] or '__missing__')
            try:
                with Image.open(thumb) as img:
                    img.verify()
                checks['thumbnails']+=1
            except (OSError,ValueError): failures.append('Missing or invalid thumbnail: '+str(thumb))
        roster=json.loads((root/'data/raw/ninjas.json').read_text('utf-8'))
        for n in roster['ninjas']:
            for form in n['forms']:
                key=n['id']+':'+str(form['cardNo'])
                r=L.get_record(db,L.stable_id('character',key))
                if not r or r['game_id']!=str(form['cardNo']) or r['rarity']!=str(form['stars']) or r['element']!=form['element']:
                    failures.append('Character source field mismatch: '+key); continue
                card=form.get('card')
                if card and 'https://gachawiki.net'+card not in [a['source_image_url'] for a in r['assets']]:
                    failures.append('Missing character-card association: '+key)
                if r['metadata']['original']!=form: failures.append('Character metadata altered: '+key)
                checks['character_forms_source_checked']+=1
        items=[L.get_record(db,r[0]) for r in db.execute("SELECT id FROM records WHERE record_type='item'")]
        for r in items:
            source_icons=set()
            for occurrence in r['metadata'].get('occurrences',[]):
                record=occurrence['record']; icon=record.get('icon')
                if icon: source_icons.add('https://gachawiki.net'+icon)
            actual={a['source_image_url'] for a in r['assets']}
            if not source_icons.issubset(actual): failures.append('Item source icon association mismatch: '+r['id'])
            checks['items_source_checked']+=1
        for kind,file in [('character','characters'),('item','items'),('stage','stages'),('interface','interface'),('miscellaneous','miscellaneous')]:
            data=json.loads((root/'catalogs'/ (file+'.json')).read_text('utf-8'))
            if len(data)!=stats['by_type'].get(kind,0): failures.append('Export count mismatch: '+file)
            checks['exports_checked']+=1
        source_less=db.execute("SELECT count(*) FROM records WHERE source_url IS NULL OR source_url='' OR verification_status='manually_verified'").fetchone()[0]
        # No crawler marks records manually verified. User annotations are stored separately.
        if source_less: failures.append('Missing provenance or crawler-created manual verification')
        checks['record_provenance']=stats['total_records']
        unique=db.execute("SELECT count(DISTINCT sha256) FROM assets WHERE status='ok'").fetchone()[0]
        statuses=dict(db.execute('SELECT verification_status,count(*) FROM records GROUP BY verification_status'))
        groups={}
        for row in db.execute("SELECT * FROM assets WHERE status='ok' ORDER BY source_image_url"):
            groups.setdefault(row['sha256'],[]).append(dict(row))
        duplicate_groups=[{'sha256':digest,'canonical_path':rows[0]['local_image_path'],'source_urls':[a['source_image_url'] for a in rows]} for digest,rows in groups.items() if len(rows)>1]
        L.atomic_json(root/'reports/exact_duplicates.json',{'duplicate_urls':stats['total_assets']-unique,'groups':duplicate_groups,'policy':'Original bytes preserved; equivalent source URLs share one content-addressed original.'})
        phashes=[(digest,rows[0]) for digest,rows in groups.items() if rows[0]['perceptual_hash']]
        similar=[]
        for i,(first,a) in enumerate(phashes):
            for second,b in phashes[i+1:]:
                distance=(int(a['perceptual_hash'],16)^int(b['perceptual_hash'],16)).bit_count()
                if distance<=3:
                    similar.append({'first_sha256':first,'second_sha256':second,'first_path':a['local_image_path'],'second_path':b['local_image_path'],'hamming_distance':distance})
        L.atomic_json(root/'reports/perceptual_similarity.json',{'algorithm':'64-bit difference hash','maximum_distance':3,'pairs':similar,'policy':'Review suggestions only. No identity merges or deletions; a similar silhouette can be a different item.'})
        checks['exact_duplicate_groups']=len(duplicate_groups)
        checks['perceptual_review_pairs']=len(similar)
    result=dict(passed=not failures,checks=dict(checks),failures=failures,counts=stats,
                unique_original_images=unique,exact_duplicate_image_urls=stats['total_assets']-unique,
                verification_counts=statuses,validated_at=L.now())
    L.atomic_json(root/'reports/validation.json',result)
    print(json.dumps(result,ensure_ascii=True))
    return result

if __name__=='__main__':
    raise SystemExit(0 if validate()['passed'] else 1)
