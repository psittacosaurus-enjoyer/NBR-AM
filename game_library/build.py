"""Extract all downloaded public catalogs, retaining source evidence and manual labels."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import re
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import library as L

BASE='https://gachawiki.net'
HOME=BASE+'/naruto-blazing'
IMAGE=re.compile(r'\.(?:png|webp|jpe?g|gif|avif|svg)(?:\?.*)?$',re.I)

def walk(obj,path='$'):
    if isinstance(obj,dict):
        yield path,obj
        for key,value in obj.items():
            yield from walk(value,path+'.'+str(key))
    elif isinstance(obj,list):
        for n,value in enumerate(obj): yield from walk(value,path+'['+str(n)+']')

def images(obj,role='image'):
    if isinstance(obj,dict):
        for key,value in obj.items(): yield from images(value,key)
    elif isinstance(obj,list):
        for value in obj: yield from images(value,role)
    elif isinstance(obj,str) and IMAGE.search(obj) and (obj.startswith('/naruto-blazing/') or obj.startswith(HOME+'/')):
        yield urljoin(BASE,obj),role

def name(obj):
    n=obj.get('name') or obj.get('title')
    if not n: return None
    if obj.get('name') and obj.get('title') and obj['title']!=obj['name']:
        return str(obj['name'])+' "'+str(obj['title'])+'"'
    return str(n)

def identity(n):
    return re.sub(r'\s+',' ',n.replace('“','"').replace('”','"')).strip().casefold()

class Builder:
    def __init__(self,root=L.ROOT,db_path=None):
        self.root=Path(root)
        self.db=L.connect(db_path or self.root/'data/library.sqlite3')
        L.initialize(self.db)
        self.counts=collections.Counter()
        self.errors=[]
        self.assets={}
        self.linked=set()
        self.characters={}
        self.character_names=collections.defaultdict(list)
        self.items={}
        self.logical=set([HOME])
        self.seen_records=set()

    def record(self,kind,key,n,source,**kw):
        rid,state=L.upsert_record(self.db,record_type=kind,source_key=key,display_name=n,source_url=source,**kw)
        self.counts[state]+=1
        self.seen_records.add(rid)
        return rid

    def relate(self,origin,target,kind):
        if origin and target and origin!=target:
            self.db.execute('INSERT OR IGNORE INTO relationships VALUES(?,?,?)',(origin,target,kind))

    def link(self,rid,obj,only_direct=False):
        for url,role in images(obj):
            aid=self.assets.get(url)
            if aid:
                self.db.execute('INSERT OR IGNORE INTO record_assets VALUES(?,?,?)',(rid,aid,role))
                self.linked.add(aid)

    def provenance(self,source,path,obj):
        return dict(source_data_url=source,json_pointer=path,original=obj,
                    verification_basis='Values explicitly supplied by GachaWiki; not screenshot-tested.')

    def import_assets(self,manifest):
        for a in manifest.get('resources',[]):
            if a.get('kind')!='image': continue
            url=a['url']; aid=L.stable_id('asset',url); self.assets[url]=aid
            path=a.get('path') if a.get('status')=='ok' else None
            if path and not (self.root/path).is_file():
                self.errors.append(dict(source=url,error='Manifest image file missing',path=path)); path=None
            values=(aid,url,Path(urlsplit(url).path).name,path,a.get('thumbnail_path'),a.get('sha256'),a.get('width'),a.get('height'),a.get('perceptual_hash'),a.get('status'),a.get('collected_at') or L.now())
            self.db.execute('''INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_image_url) DO UPDATE SET
              local_image_path=excluded.local_image_path,thumbnail_path=excluded.thumbnail_path,sha256=excluded.sha256,
              width=excluded.width,height=excluded.height,perceptual_hash=excluded.perceptual_hash,status=excluded.status''',values)

    def ninjas(self,data,source):
        for i,n in enumerate(data.get('ninjas',[])):
            if not isinstance(n,dict) or not n.get('id') or not n.get('name'):
                self.errors.append(dict(source=source,path=f'$.ninjas[{i}]',error='Missing ninja identifier/name')); continue
            page=HOME+'/ninjas/'+str(n['id']); self.logical.add(page)
            forms=n.get('forms') or [n]
            for j,f in enumerate(forms):
                if not isinstance(f,dict):
                    self.errors.append(dict(source=source,error='Malformed character form',path=f'$.ninjas[{i}].forms[{j}]')); continue
                card=f.get('cardNo')
                key=str(n['id'])+':'+(str(card) if card is not None else str(f.get('stars','unknown'))+':'+str(f.get('blazingAwakened',False)))
                m=self.provenance(source,f'$.ninjas[{i}].forms[{j}]',f)
                m.update(ninja_id=n['id'],character_name=n['name'],title=n.get('title'),roster_metadata={k:v for k,v in n.items() if k!='forms'},upcoming=n.get('upcoming'),game_id_kind='cardNo' if card is not None else None)
                affiliations=n.get('affiliations',[])
                if not isinstance(affiliations,list): affiliations=[]
                rid=self.record('character',key,name(n),page,game_id=card,category='ninja',element=f.get('element',n.get('element')),rarity=f.get('stars'),metadata=m,tags=[n['id']]+affiliations)
                self.characters[(n['id'],str(f.get('stars')))] = rid
                self.character_names[(identity(name(n)),str(f.get('stars')))].append(rid)
                # Forms carry their own correct artwork; avoid putting every form card on each record.
                self.link(rid,f)
                cutin={'cutin':'/naruto-blazing/cutins/'+str(n['id'])+'.webp'}
                # The public client constructs cut-in paths from the explicit ninja slug.
                self.link(rid,cutin)
                if not f.get('card'): self.link(rid,{'card':n.get('card'),'art':n.get('art')})
                if f.get('awakening') and not isinstance(f['awakening'],dict):
                    self.errors.append(dict(source=source,error='Malformed awakening metadata',path=f'$.ninjas[{i}].forms[{j}].awakening'))
                elif f.get('awakening'):
                    for k,item in enumerate(f['awakening'].get('materials',[])):
                        target=self.drop(item,source,f'$.ninjas[{i}].forms[{j}].awakening.materials[{k}]',page)
                        self.relate(rid,target,'awakening_material')

    def drop(self,obj,source,path,page,origin=None,relation='reward'):
        if not isinstance(obj,dict): return None
        kind=obj.get('kind')
        if kind in ('ninja','character'):
            target=self.characters.get((obj.get('ninja'),str(obj.get('stars'))))
            if not target and obj.get('name'):
                # Structured names/title can match roster; never use asset filename as identity.
                candidates=self.character_names.get((identity(name(obj)),str(obj.get('stars'))),[])
                if len(candidates)==1: target=candidates[0]
            if not target and name(obj):
                key='unit:'+str(obj['unit']) if obj.get('unit') is not None else 'drop:'+identity(name(obj))+':'+str(obj.get('stars'))
                target=self.record('character',key,name(obj),page,game_id=obj.get('unit'),category='drop_character',element=obj.get('element'),rarity=obj.get('stars'),metadata=self.provenance(source,path,obj))
            if target:
                self.link(target,obj); self.relate(origin,target,relation)
            return target
        if kind=='boss':
            target=self.record('miscellaneous','boss:'+identity(name(obj)),name(obj),page,category='boss',metadata=self.provenance(source,path,obj))
            self.link(target,obj); self.relate(origin,target,'boss'); return target
        n=name(obj)
        if not n: return None
        stars=obj.get('stars',obj.get('rarity'))
        original_name=n
        # Stars spelled out in a source label are explicit metadata, not inferred from pixels/filenames.
        suffix=re.search(r'\s*\(\s*★([1-7])\s*\)\s*$',n)
        if suffix:
            if stars is None: stars=int(suffix.group(1))
            n=n[:suffix.start()].strip()
        element=obj.get('element')
        # Items from client numeric element codes remain numeric unless source explicitly names it.
        # Infer ONLY a named element explicitly present in the supplied item name/title.
        named=next((e for e in ('Heart','Skill','Body','Bravery','Wisdom') if kind not in ('character_crystal','boss') and re.search(r'\b'+e+r'\b',n)),None)
        key=identity(n)+'|'+str(stars)+'|'+str(named or element)
        if key not in self.items:
            meta=self.provenance(source,path,obj)
            meta.update(occurrences=[],source_identifiers=[],original_names=[],game_id_kind='unit' if obj.get('unit') is not None else 'id' if obj.get('id') is not None else None,element_code=element if isinstance(element,int) else None)
            self.items[key]=dict(n=n,source=page,stars=stars,element=named or element,category=kind or 'item',meta=meta,objects=[])
        info=self.items[key]
        occurrence=dict(source_url=page,source_data_url=source,json_pointer=path,record=obj)
        if occurrence not in info['meta']['occurrences']: info['meta']['occurrences'].append(occurrence)
        for ident in (obj.get('unit'),obj.get('id')):
            if ident is not None and str(ident) not in info['meta']['source_identifiers']: info['meta']['source_identifiers'].append(str(ident))
        if original_name not in info['meta']['original_names']: info['meta']['original_names'].append(original_name)
        info['objects'].append(obj)
        target=L.stable_id('item',key)
        self.seen_records.add(target)
        # Create preliminary record now for foreign key relationships; consolidate metadata at the end.
        if self.db.execute('SELECT 1 FROM records WHERE id=?',(target,)).fetchone() is None:
            self.record('item',key,n,page,category=info['category'],element=info['element'],rarity=stars,metadata=info['meta'])
        self.link(target,obj); self.relate(origin,target,relation)
        return target

    def stage(self,key,title,page,source,path,obj,category,difficulty=None,game_id=None,image=None):
        meta=self.provenance(source,path,obj); meta['difficulty']=difficulty
        rid=self.record('stage',key,title,page,game_id=game_id,category=category,element=obj.get('element'),metadata=meta)
        self.logical.add(page)
        self.link(rid,{'image':image} if image else {k:obj[k] for k in ('image','banner','icon') if obj.get(k)})
        for subpath,sub in walk(obj,path):
            if sub.get('name') and (sub.get('icon') or sub.get('kind') in ('ninja','item','scroll','crystal','gama','sphere','currency')):
                self.drop(sub,source,subpath,page,origin=rid,relation='boss' if sub.get('kind')=='boss' else 'reward')
        for slug in obj.get('ninjas',[]) if isinstance(obj.get('ninjas'),list) else []:
            for (nid,stars),target in self.characters.items():
                if nid==slug: self.relate(rid,target,'featured_character')
        return rid

    def missions(self,data,source):
        for i,m in enumerate(data.get('missions',[])):
            if not isinstance(m,dict) or not m.get('id') or not m.get('title'):
                self.errors.append(dict(source=source,error='Malformed mission',path=f'$.missions[{i}]')); continue
            page=HOME+'/missions/emergency/'+str(m['id'])
            parent=self.stage('mission:'+str(m['id']),m['title'],page,source,f'$.missions[{i}]',m,m.get('type','mission'),game_id=m.get('area'),image=m.get('banner'))
            for j,s in enumerate(m.get('stages',[])):
                if not isinstance(s,dict): continue
                rank=s.get('rank'); title=s.get('title') or m['title']
                rid=self.stage('mission:'+str(m['id'])+':'+str(rank or j),title+' ['+str(rank or j)+']',page,source,f'$.missions[{i}].stages[{j}]',s,m.get('type','mission'),difficulty=rank,game_id=s.get('id'),image=m.get('banner'))
                self.relate(parent,rid,'mission_stage')

    def story(self,data,source):
        for group in ('story','gaiden'):
            for i,ch in enumerate(data.get(group,[])):
                if not isinstance(ch,dict): continue
                page=HOME+'/missions/'+('story' if group=='gaiden' else group)
                parent=self.stage(group+':chapter:'+str(ch.get('no',i+1)),ch.get('title') or 'Unknown chapter',page,source,f'$.{group}[{i}]',ch,group,image=ch.get('image'))
                for difficulty in ('normal','hard'):
                    block=ch.get(difficulty) or {}
                    for j,m in enumerate(block.get('missions',[])):
                        if not isinstance(m,dict) or not m.get('id') or not m.get('title'):
                            self.errors.append(dict(source=source,error='Malformed story mission')); continue
                        rid=self.stage('stage:'+str(m['id']),m['title']+' ['+difficulty+']',page,source,f'$.{group}[{i}].{difficulty}.missions[{j}]',m,group,difficulty=difficulty,game_id=m['id'],image=ch.get('image'))
                        self.relate(parent,rid,'chapter_stage')

    def other(self,data,source,filename):
        if filename=='mission-maps.json':
            for slug,ranks in data.items():
                if not isinstance(ranks,dict): continue
                page=HOME+'/missions/emergency/'+slug
                for rank,maps in ranks.items():
                    rid=L.stable_id('stage','mission:'+slug+':'+str(rank))
                    row=self.db.execute('SELECT metadata FROM records WHERE id=?',(rid,)).fetchone()
                    if row:
                        meta=json.loads(row[0]); meta['battle_maps']=dict(source_data_url=source,json_pointer='$.'+slug+'.'+rank,maps=maps)
                        self.db.execute('UPDATE records SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),rid))
                        for p,obj in walk(maps):
                            if obj.get('background'): self.link(rid,{'background':obj['background']})
        if filename=='ninja-trials.json':
            for group in ('trials','panels'):
                page=HOME+'/missions/'+('ninja-trial' if group=='trials' else 'panel')
                for i,trial in enumerate(data.get(group,[])):
                    if not isinstance(trial,dict): continue
                    title=trial.get('title') or trial.get('name') or 'Unknown trial'
                    parent=self.stage('achievement:'+str(trial.get('id',group+str(i))),title,page,source,f'$.{group}[{i}]',trial,'ninja_trial' if group=='trials' else 'panel_mission',image=trial.get('badge'))
                    for p,obj in walk(trial,f'$.{group}[{i}]'):
                        if obj.get('id') is not None and obj.get('text'):
                            rid=self.stage('achievement:'+str(obj['id']),obj['text'],page,source,p,obj,'ninja_trial' if group=='trials' else 'panel_mission',game_id=obj['id'],image=trial.get('badge'))
                            self.relate(parent,rid,'trial_task')
        if filename=='limit-break.json':
            # Labels come directly from public client helper pr() and catalog element keys.
            basis='Public Naruto module pr() names elemental crystals, books, own5/own6 and ultimate.'
            self.drop({'name':'Ultimate Crystal','kind':'crystal','icon':data.get('ultimate'),'label_source':basis},source,'$.ultimate',HOME)
            for element,values in data.get('elements',{}).items():
                if not isinstance(values,dict): continue
                for field,stars in [('crystal',None),('scroll3',3),('scroll4',4)]:
                    if not values.get(field): continue
                    obj={'name':element+' Crystal' if field=='crystal' else 'Awakening Scroll "'+element+' Book"','kind':'crystal' if field=='crystal' else 'scroll','element':element,'icon':values[field],'label_source':basis,'trial':values.get('trial')}
                    if stars is not None: obj['stars']=stars
                    self.drop(obj,source,'$.elements.'+element+'.'+field,HOME)
            for slug,values in data.get('ninjas',{}).items():
                if not isinstance(values,dict): continue
                for stars in ('5','6'):
                    if not values.get(stars): continue
                    target=self.characters.get((slug,stars))
                    row=self.db.execute('SELECT metadata,display_name FROM records WHERE id=?',(target,)).fetchone() if target else None
                    if row:
                        title=json.loads(row['metadata']).get('title')
                        element=json.loads(row['metadata']).get('original',{}).get('element')
                        item=self.drop({'name':'"'+str(title)+'" '+chr(9733)+stars,'kind':'character_crystal','stars':int(stars),'element':element,'icon':values[stars],'character':slug,'label_source':basis},source,'$.ninjas.'+slug+'.'+stars,HOME+'/ninjas/'+slug)
                        self.relate(target,item,'limit_break_material')
        if filename=='road.json':
            page=HOME+'/missions/road'
            self.stage('road:'+str(data.get('road')), 'Ninja Road '+str(data.get('no','')),page,source,'$',data,'ninja_road',game_id=data.get('road'))
            for i,m in enumerate(data.get('maps',[])):
                if not isinstance(m,dict): continue
                n=name(m) or ('Map '+str(m.get('no',i+1)))
                rid=self.stage('road:map:'+str(m.get('map',m.get('no',i+1))),n,page,source,f'$.maps[{i}]',m,'ninja_road',game_id=m.get('map'),image=m.get('background'))
        # Every named visual object outside core catalogs remains available; classify conservatively.
        for path,obj in walk(data):
            if not name(obj) or not list(images(obj)): continue
            if obj.get('kind') in ('scroll','crystal','gama','sphere','item','currency','ninja'):
                self.drop(obj,source,path,HOME)
            else:
                # Avoid duplicating entire huge containers under generic records.
                direct={k:v for k,v in obj.items() if isinstance(v,str) and IMAGE.search(v)}
                if not direct: continue
                # Enemy repeats on many maps become one visual identity; stats remain in battle maps.
                key='visual:'+identity(name(obj))+':'+str(obj.get('element'))+':'+str(sorted(direct.values()))
                rid=self.record('miscellaneous',key,name(obj),HOME,category='enemy' if '/enemies/' in str(direct) or '/bosses/' in str(direct) else filename.removesuffix('.json'),element=obj.get('element'),metadata=self.provenance(source,path,obj))
                self.link(rid,direct)

    def run(self):
        manifest=json.loads((self.root/'data/manifest.json').read_text('utf-8'))
        resources=manifest.get('resources',[])
        catalogs=[]
        for resource in resources:
            if resource.get('kind')!='json' or resource.get('status')!='ok': continue
            try:
                data=json.loads((self.root/resource['path']).read_text('utf-8-sig'))
                if not isinstance(data,dict):
                    self.errors.append(dict(source=resource['url'],error='Expected object catalog')); continue
                catalogs.append((Path(urlsplit(resource['url']).path).name,resource['url'],data))
            except (OSError,ValueError) as e: self.errors.append(dict(source=resource['url'],error=str(e)))
        started=L.now()
        before={r[0]:r[1] for r in self.db.execute('SELECT id,content_hash FROM records')}
        before_dates={r[0]:r[1] for r in self.db.execute('SELECT id,updated_at FROM records')}
        annotated_assets=self.db.execute('SELECT * FROM record_assets WHERE record_id IN (SELECT record_id FROM annotations)').fetchall()
        with self.db:
            self.import_assets(manifest)
            # Rebuild source relationships safely within transaction; annotations and originals untouched.
            self.db.execute('DELETE FROM relationships'); self.db.execute('DELETE FROM record_assets')
            for filename,source,data in catalogs:
                if filename=='ninjas.json':
                    try: self.ninjas(data,source)
                    except (TypeError,ValueError,KeyError,AttributeError) as e:
                        self.errors.append(dict(source=source,error=type(e).__name__+': '+str(e)))
            # Mission-map relationship processing requires mission records first.
            catalogs.sort(key=lambda x: (0 if x[0]=='missions.json' else 1 if x[0]=='story.json' else 2,x[0]))
            for filename,source,data in catalogs:
                try:
                    if filename=='missions.json': self.missions(data,source)
                    elif filename=='story.json': self.story(data,source)
                    elif filename!='ninjas.json': self.other(data,source,filename)
                except (TypeError,ValueError,KeyError,AttributeError) as e:
                    self.errors.append(dict(source=source,error=type(e).__name__+': '+str(e)))
            # Attach statistical tables and damage calculations to correct form records.
            supplements={filename:(source,data) for filename,source,data in catalogs}
            for (slug,stars),rid in self.characters.items():
                row=self.db.execute('SELECT metadata FROM records WHERE id=?',(rid,)).fetchone()
                meta=json.loads(row[0]); additions={}
                for filename in ('stat-curves.json','tower.json','limit-break.json'):
                    if filename not in supplements: continue
                    url,data=supplements[filename]
                    value=data.get(slug) if filename=='stat-curves.json' else data.get('ninjas',{}).get(slug) if isinstance(data.get('ninjas'),dict) else next((n for n in data.get('ninjas',[]) if n.get('id')==slug),None)
                    if value is not None: additions[filename]={'source_data_url':url,'data':value}
                if additions:
                    meta['supplemental_metadata']=additions
                    self.db.execute('UPDATE records SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),rid))
            for key,info in self.items.items():
                ids=info['meta']['source_identifiers']
                rid=self.record('item',key,info['n'],info['source'],game_id=ids[0] if len(ids)==1 else None,category=info['category'],element=info['element'],rarity=info['stars'],metadata=info['meta'])
                for obj in info['objects']: self.link(rid,obj)
            for a in resources:
                aid=self.assets.get(a.get('url'))
                if not aid or aid in self.linked: continue
                path=urlsplit(a['url']).path
                known_interface=any('/'+part+'/' in path for part in ('ui','icons','rarity','effects','abilities','affiliations','tiers','menu','modes'))
                kind='interface' if known_interface else 'miscellaneous'
                # Filename is explicitly marked as a source filename, not a known display/game identity.
                rid=self.record(kind,'asset:'+a['url'],Path(path).name,HOME,category=path.split('/')[2] if len(path.split('/'))>3 else 'miscellaneous',verification_status='unidentified',metadata=dict(source_image_url=a['url'],classification_basis='Inferred from the public asset directory',display_name_basis='Original filename; canonical game label unknown'))
                self.link(rid,{'image':a['url']})
            # Remove obsolete generated records only; all annotated/manual records survive rebuilds.
            for rid, in self.db.execute('SELECT id FROM records WHERE id NOT IN (SELECT record_id FROM annotations)').fetchall():
                if rid not in self.seen_records:
                    self.db.execute('DELETE FROM relationships WHERE from_id=? OR to_id=?',(rid,rid))
                    self.db.execute('DELETE FROM record_assets WHERE record_id=?',(rid,))
                    self.db.execute('DELETE FROM records WHERE id=?',(rid,))
            for record_id,asset_id,role in annotated_assets:
                if record_id not in self.seen_records:
                    self.db.execute('INSERT OR IGNORE INTO record_assets VALUES(?,?,?)',(record_id,asset_id,role))
            # Final hashes include supplementary metadata; count records once, rather than extraction occurrences.
            for row in self.db.execute('SELECT id,metadata,display_name,game_id,category,element,rarity FROM records').fetchall():
                digest=hashlib.sha256(json.dumps(dict(row),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                date=before_dates.get(row['id'],L.now()) if before.get(row['id'])==digest else L.now()
                self.db.execute('UPDATE records SET content_hash=?,updated_at=? WHERE id=?',(digest,date,row['id']))
            after={r[0]:r[1] for r in self.db.execute('SELECT id,content_hash FROM records')}
            self.counts=collections.Counter(new=sum(k not in before for k in after),updated=sum(k in before and before[k]!=v for k,v in after.items()),unchanged=sum(before.get(k)==v for k,v in after.items()),removed=sum(k not in after for k in before))
            L.index(self.db)
            summary=L.stats(self.db)
            summary.update(changes=dict(self.counts),extraction_failures=self.errors,logical_pages=sorted(self.logical),started_at=started,finished_at=L.now())
            self.db.execute('INSERT INTO builds(started_at,finished_at,summary) VALUES(?,?,?)',(started,summary['finished_at'],json.dumps(summary)))
        L.export(self.db,self.root/'catalogs')
        L.atomic_json(self.root/'reports/build.json',summary)
        print(json.dumps({k:v for k,v in summary.items() if k not in ('logical_pages','facets','extraction_failures')},ensure_ascii=True))
        return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-only',action='store_true')
    args=parser.parse_args()
    if args.export_only:
        with closing(L.connect(readonly=True)) as db: L.export(db)
    else:
        builder=Builder()
        try: builder.run()
        finally: builder.db.close()

if __name__=='__main__': main()
