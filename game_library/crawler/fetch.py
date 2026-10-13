"""Resumable, scoped public GachaWiki reference collector (no account endpoints)."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, http.client, json, os, posixpath, re, sqlite3, ssl, threading, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, unquote
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://gachawiki.net'
HOME = ORIGIN + '/naruto-blazing'
IMG_RE = re.compile(r'/naruto-blazing/[^\s"\x27`<>\\]+?\.(?:webp|png|jpe?g|gif|svg|avif)', re.I)
DATA_RE = re.compile(r'/data/naruto-blazing/[^\s"\x27`<>\\]+?\.json')
EXCLUDED = re.compile(r'(?:supabase|profile|auth|account|community|login)', re.I)
LOCK = threading.RLock()
RATE_LOCK = threading.Lock()
NEXT_REQUEST = 0.0
LOCAL = threading.local()

def now():
    return datetime.now(timezone.utc).isoformat()

def normalize_url(value, base=HOME):
    u = urlsplit(urljoin(base, value))
    if u.scheme != 'https' or u.netloc.lower() != 'gachawiki.net' or u.username or u.password:
        return None
    checked_path=posixpath.normpath(unquote(u.path))
    if not (checked_path=='/naruto-blazing' or checked_path.startswith('/naruto-blazing/') or checked_path.startswith('/data/naruto-blazing/') or checked_path.startswith('/assets/')):
        return None
    if re.search(r'/(?:profile|auth|account|community|login)(?:/|$)',checked_path,re.I):
        return None
    return urlunsplit(('https', 'gachawiki.net', quote(u.path, safe='/:%-._~'), u.query, ''))

def walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values(): yield from walk(v)
    elif isinstance(value, list):
        for v in value: yield from walk(v)

def slug(value):
    return re.sub(r'[^a-z0-9]+', '-', re.sub(r"['.]", '', str(value)).lower()).strip('-')

def request(url, headers):
    global NEXT_REQUEST
    with RATE_LOCK:
        delay = max(0, NEXT_REQUEST - time.monotonic())
        NEXT_REQUEST = max(NEXT_REQUEST, time.monotonic()) + .25
    if delay: time.sleep(delay)
    if not getattr(LOCAL, 'connection', None):
        LOCAL.connection = http.client.HTTPSConnection('gachawiki.net', timeout=35, context=ssl.create_default_context())
    conn = LOCAL.connection
    u = urlsplit(url)
    try:
        conn.request('GET', u.path + ('?' + u.query if u.query else ''), headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict((k.lower(), v) for k,v in resp.getheaders()), data
    except Exception:
        conn.close(); LOCAL.connection = None
        raise

class Collector:
    def __init__(self, update=False, workers=4):
        self.update, self.workers = update, max(1, min(4, workers))
        self.started = now(); self.stats = Counter(); self.logical = {}; self.seen = set(); self.data = {}
        for name in ['data/raw', 'reports', 'assets/thumbnails']:
            (ROOT / name).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(ROOT/'data/crawl_state.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS resources(url TEXT PRIMARY KEY, entry TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS logical_pages(url TEXT PRIMARY KEY, source TEXT)')
        self.entries = {url:json.loads(entry) for url,entry in self.db.execute('SELECT url,entry FROM resources')}
        retired=[]
        for url in list(self.entries):
            if '/assets/assets/' in url or url.endswith('/effects/.webp') or url.endswith('/menu/mode-nwus.webp') or re.search(r'/icons/(?:short|mid|long|vast)\.webp$',url):
                retired.append(self.entries.pop(url)); self.db.execute('DELETE FROM resources WHERE url=?',(url,))
        if retired:
            audit=ROOT/'reports/retired_discovery_candidates.json'
            previous=json.loads(audit.read_text(encoding='utf8')) if audit.exists() else []
            unique={v['url']:v for v in previous+retired}
            audit.write_text(json.dumps(list(unique.values()),indent=2),encoding='utf8')
            self.db.commit()
        self.logical = dict(self.db.execute('SELECT url,source FROM logical_pages'))
        self.image_paths={e['sha256']:e['path'] for e in self.entries.values()
                          if e.get('kind')=='image' and e.get('status')=='ok' and e.get('path') and (ROOT/e['path']).is_file()}
        self.done = 0

    def record(self, entry):
        with LOCK:
            self.entries[entry['url']] = entry
            self.db.execute('INSERT OR REPLACE INTO resources VALUES (?,?)', (entry['url'], json.dumps(entry, ensure_ascii=False)))
            self.db.commit(); self.done += 1
            if self.done % 50 == 0:
                self.snapshot()
                print(json.dumps({'processed':self.done,'known':len(self.entries),'run_counts':dict(self.stats)}), flush=True)
        return entry

    def seed(self, url, source, kind, path):
        url = normalize_url(url)
        if not url or url in self.entries or not source.exists(): return
        target = ROOT/path; target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        preserved=self.preserve_source_version(target,data) if kind in ['json','script','style','html'] else None
        target.write_bytes(data)
        entry={'url':url,'path':path,'status':'ok','kind':kind,'sha256':hashlib.sha256(data).hexdigest(),
                     'mime':'application/json' if kind=='json' else 'text/javascript' if kind=='script' else 'text/css' if kind=='style' else 'text/html',
                     'collected_at':now(),'http_status':200,'reused_probe':True,'attempts':0}
        if preserved: entry['previous_versions']=[preserved]
        self.record(entry)

    def preserve_source_version(self, target, replacement):
        if not target.is_file(): return None
        original=target.read_bytes()
        if original==replacement: return None
        digest=hashlib.sha256(original).hexdigest()
        path=f'data/history/{digest}{target.suffix}'
        archived=ROOT/path; archived.parent.mkdir(parents=True,exist_ok=True)
        if not archived.exists():
            tmp=archived.with_name(archived.name+'.'+str(threading.get_ident())+'.part')
            tmp.write_bytes(original); os.replace(tmp,archived)
        return {'path':path,'sha256':digest,'archived_at':now()}

    def fetch(self, resource):
        url, kind = resource
        with LOCK:
            old = self.entries.get(url)
        cached=bool(old and old.get('status')=='ok' and old.get('path') and (ROOT/old['path']).is_file())
        if cached and not self.update and not old.get('update_failed'):
            if kind=='image' and not old.get('thumbnail_path') and old.get('image_processing_error'):
                self.thumbnail_fallback(old)
                self.record(old)
            with LOCK: self.stats['skipped'] += 1
            return old
        headers = {'User-Agent':'NBR-Game-Reference-Library/1.0 (owner-authorized public reference collection)', 'Accept-Encoding':'identity'}
        if cached:
            if old.get('etag'): headers['If-None-Match']=old['etag']
            if old.get('last_modified'): headers['If-Modified-Since']=old['last_modified']
        entry = {'url':url,'kind':kind,'status':'failed','collected_at':now()}
        for attempt in range(3):
            entry['attempts']=attempt+1
            try:
                status, hdr, data = request(url, headers)
                entry.update(http_status=status, mime=hdr.get('content-type','').split(';')[0], etag=hdr.get('etag'),last_modified=hdr.get('last-modified'))
                if status==304 and old and old.get('path') and (ROOT/old['path']).is_file():
                    entry=old.copy(); entry.update(checked_at=now(),http_status=304)
                    entry.pop('update_error',None); entry.pop('update_failed',None)
                    with LOCK: self.stats['unchanged']+=1
                    return self.record(entry)
                if status in [401,403]:
                    entry['error']='Public resource access restricted; no bypass attempted'; break
                if status==429 or status>=500:
                    retry=hdr.get('retry-after','')
                    time.sleep(min(30,float(retry) if retry.isdigit() else 2**(attempt+1)))
                    entry['error']=f'HTTP {status}'; continue
                if 300<=status<400:
                    location=normalize_url(hdr.get('location',''),url)
                    if not location:
                        entry['error']='Redirect outside authorized scope'; break
                    status,hdr,data=request(location,headers); entry['redirect_url']=location
                    if status!=200: entry['error']=f'Redirect HTTP {status}'; break
                    entry.update(http_status=status,mime=hdr.get('content-type','').split(';')[0])
                if status!=200:
                    entry['error']=f'HTTP {status}'; break
                digest=hashlib.sha256(data).hexdigest()
                entry.update(sha256=digest,bytes=len(data),status='ok')
                p=urlsplit(url).path
                if kind=='style' and entry['mime']!='text/css':
                    entry.update(status='failed',error='Response is not a stylesheet'); break
                if kind=='image':
                    if not entry['mime'].startswith('image/'):
                        entry.update(status='failed',error='Response is not an image'); break
                    ext=Path(p).suffix.lower() or '.bin'
                    category='characters' if '/ninjas/' in p or '/cutins/' in p else 'items' if any(v in p for v in ['/lb/','/abilities/','/story/units/']) else 'stages' if any(v in p for v in ['/missions/','/events/','/maps/','/story/','/road/']) else 'interface' if any(v in p for v in ['/ui/','/menu/','/icons/','/rarity/','/tiers/','/effects/','/affiliations/','/modes/']) else 'miscellaneous'
                    with LOCK:
                        path=self.image_paths.setdefault(digest,f'assets/{category}/{digest}{ext}')
                elif kind=='json':
                    json.loads(data.decode('utf-8-sig'))
                    filename=Path(p).name
                    if urlsplit(url).query or p.rsplit('/',1)[0]!='/data/naruto-blazing':
                        filename=Path(p).stem+'-'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.json'
                    path='data/raw/'+filename
                elif kind in ['script','style']: path='data/raw/'+Path(p).name
                else: path='data/raw/source.html' if url==HOME else 'data/raw/page-'+digest[:20]+'.html'
                target=ROOT/path; target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                    if kind in ['json','script','style','html']:
                        preserved=self.preserve_source_version(target,data)
                        if preserved:
                            entry['previous_versions']=list(old.get('previous_versions',[])) if old else []
                            if not any(v['sha256']==preserved['sha256'] for v in entry['previous_versions']):
                                entry['previous_versions'].append(preserved)
                    tmp=target.with_name(target.name+'.'+str(threading.get_ident())+'.part'); tmp.write_bytes(data); os.replace(tmp,target)
                if old and old.get('previous_versions') and 'previous_versions' not in entry:
                    entry['previous_versions']=list(old['previous_versions'])
                entry['path']=path
                if kind=='image' and ext!='.svg':
                    try:
                        with Image.open(target) as im:
                            entry.update(width=im.width,height=im.height,image_format=im.format)
                            gray=im.convert('L').resize((9,8),Image.Resampling.LANCZOS)
                            pixels=list(gray.get_flattened_data() if hasattr(gray,'get_flattened_data') else gray.getdata()); bits=0
                            for y in range(8):
                                for x in range(8): bits=(bits<<1)|int(pixels[y*9+x]>pixels[y*9+x+1])
                            entry['perceptual_hash']=f'{bits:016x}'
                            thumb=im.convert('RGBA'); thumb.thumbnail((192,192),Image.Resampling.LANCZOS)
                            thumb_path=f'assets/thumbnails/{digest}.webp'
                            if not (ROOT/thumb_path).exists():
                                thumb_target=ROOT/thumb_path
                                thumb_tmp=thumb_target.with_name(thumb_target.name+'.'+str(threading.get_ident())+'.part')
                                thumb.save(thumb_tmp,'WEBP',quality=86); os.replace(thumb_tmp,thumb_target)
                            entry['thumbnail_path']=thumb_path
                    except Exception as exc:
                        entry['image_processing_error']=str(exc)
                        self.thumbnail_fallback(entry)
                entry.pop('error',None)
                with LOCK: self.stats['updated' if old and old.get('sha256')!=digest else 'successful']+=1
                return self.record(entry)
            except (ValueError,OSError,http.client.HTTPException) as exc:
                entry['status']='failed'
                entry['error']=str(exc)
                if attempt<2: time.sleep(2**attempt)
        if cached and (ROOT/old['path']).is_file():
            failure={k:entry.get(k) for k in ['collected_at','attempts','http_status','error']}
            entry=old.copy()
            entry.update(update_failed=True,update_error=failure.get('error') or 'Update failed',
                         last_update_attempt=failure,checked_at=now())
            with LOCK: self.stats['update_failed']+=1
        with LOCK: self.stats['failed']+=1
        return self.record(entry)

    def thumbnail_fallback(self, entry):
        """Preserve decodable originals when the WebP thumbnail encoder rejects a source."""
        try:
            with Image.open(ROOT/entry['path']) as im:
                thumb=im.convert('RGBA'); thumb.thumbnail((192,192),Image.Resampling.LANCZOS)
                path=f'assets/thumbnails/{entry["sha256"]}.png'
                target=ROOT/path; tmp=target.with_name(target.name+'.'+str(threading.get_ident())+'.part')
                thumb.save(tmp,'PNG'); os.replace(tmp,target)
                entry['thumbnail_path']=path
                entry.pop('image_processing_error',None)
                entry['thumbnail_format']='PNG encoder fallback'
        except Exception as exc:
            entry['image_processing_error']=str(exc)

    def run_batch(self, resources):
        tasks=[]
        for url,kind in resources:
            url=normalize_url(url)
            if url and url not in self.seen:
                self.seen.add(url); tasks.append((url,kind))
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(self.fetch,tasks))

    def logical_page(self, value, source):
        if not value.startswith('#/naruto-blazing'): return
        if EXCLUDED.search(value) or '$' in value: return
        self.logical[ORIGIN+'/'+value]=source

    def discover_scripts(self):
        self.seed(HOME,ROOT/'source_probe.html','html','data/raw/source.html')
        self.run_batch([(HOME,'html')])
        html=(ROOT/'data/raw/source.html').read_text(encoding='utf8') if (ROOT/'data/raw/source.html').exists() else ''
        script_paths=re.findall(r'<script[^>]+src="([^"]+)"',html)
        seeds=[]
        for p in script_paths:
            url=normalize_url(p)
            if url:
                self.seed(url,ROOT/'data/site_app.js','script','data/raw/'+Path(p).name); seeds.append((url,'script'))
        self.run_batch(seeds)
        main='\n'.join((ROOT/e['path']).read_text(encoding='utf8') for e in self.entries.values() if e['kind']=='script' and e['status']=='ok' and e['url'] in [normalize_url(p) for p in script_paths])
        for m in re.findall(r'["\x27`]([^"\x27`]*naruto-blazing[^"\x27`]*\.js)["\x27`]',main):
            u=normalize_url(m,ORIGIN+'/' if m.startswith('assets/') else ORIGIN+'/assets/index.js')
            if u: self.seed(u,ROOT/'data/naruto_app.js','script','data/raw/'+Path(urlsplit(u).path).name); seeds.append((u,'script'))
        self.run_batch(seeds)
        pending=[e for e in self.entries.values() if e['kind']=='script' and e['status']=='ok']
        scanned=set()
        while pending:
            todo=[]
            for entry in pending:
                if entry['url'] in scanned: continue
                scanned.add(entry['url'])
                text=(ROOT/entry['path']).read_text(encoding='utf8')
                imports=re.findall(r'(?:from|import\()\s*["\x27`]([^"\x27`]+\.js)',text)
                for value in imports:
                    # Shared root is already present; avoid unrelated app/account modules.
                    if EXCLUDED.search(value) or (not re.search(r'naruto|Ninja|Mission|Blazing',value,re.I)): continue
                    url=normalize_url(value,entry['url'])
                    if not url: continue
                    if 'NinjaCollection' in value:
                        self.seed(url,ROOT/'data/collection_app.js','script','data/raw/'+Path(value).name)
                    todo.append((url,'script'))
            pending=self.run_batch(todo)
        styles=self.stylesheet_urls(html,[(e['url'],(ROOT/e['path']).read_text(encoding='utf8')) for e in self.entries.values() if e['kind']=='script' and e['status']=='ok'])
        self.run_batch([(url,'style') for url in styles])
        return [(ROOT/e['path']).read_text(encoding='utf8') for e in self.entries.values() if e['kind'] in ['script','style'] and e['status']=='ok']

    @staticmethod
    def stylesheet_urls(html, modules):
        urls=set()
        for tag in re.findall(r'<link\b[^>]*>',html,re.I):
            attrs={name.lower():(double or single) for name,double,single in re.findall(r'([\w-]+)\s*=\s*(?:"([^"]*)"|\x27([^\x27]*)\x27)',tag)}
            if 'stylesheet' in attrs.get('rel','').lower().split() and attrs.get('href'):
                url=normalize_url(attrs['href'])
                if url and urlsplit(url).path.endswith('.css'): urls.add(url)
        for base,text in modules:
            for value in re.findall(r'["\x27`]([^"\x27`]*naruto-blazing[^"\x27`]*\.css)["\x27`]',text):
                url=normalize_url(value,ORIGIN+'/' if value.startswith('assets/') else base)
                if url and not EXCLUDED.search(url): urls.add(url)
        return sorted(urls)

    def dynamic_images(self, scripts, payloads):
        images=set(); combined='\n'.join(scripts)
        for text in scripts:
            images.update(m for m in IMG_RE.findall(text) if '${' not in m)
            # Literal arguments to source-defined image helpers.
            helpers=re.findall(r'([A-Za-z_$][\w$]*)=e=>`(/naruto-blazing/(?:ui(?:/story)?|menu)/)\$\{e\}\.webp`',text)
            for helper,prefix in helpers:
                for argument in re.findall(re.escape(helper)+r'\(`[A-Za-z0-9_-]+`\)',text):
                    images.add(prefix+argument.split('`')[1]+'.webp')
            # Resolve base variables used by literal template paths.
            for var,prefix in re.findall(r'([A-Za-z_$][\w$]*)=`(/naruto-blazing/[^`]+)`',text):
                for suffix in re.findall(r'\$\{'+re.escape(var)+r'\}(/[^`]+\.webp)',text):
                    if '${' not in suffix: images.add(prefix+suffix)
        # Source-defined finite values and conditional helper call arguments.
        for prefix,values in [('/rarity/',['3','4','5','6','6-ba','7','7-ba']),('/icons/',['heart','skill','body','bravery','wisdom']),('/tiers/',['ss','s','a','b','c']),('/ui/',['special-reward','clear-complete','drop-complete']),('/ui/story/',['roll-1','roll-2','roll-5','roll-6']),('/ui/summon/',['single','multi'])]:
            for value in values: images.add('/naruto-blazing'+prefix+value+'.webp')
        for name in ['ninjutsu','secret']: images.add('/naruto-blazing/ui/jutsu-'+name+'.webp')
        for name in ['story','emergency','road','trials','showdown','castle','ninja-trial','panel']:
            # Only finite menu ids explicitly instantiated in the source.
            if re.search(r'id:`'+re.escape(name)+r'`',combined): images.add('/naruto-blazing/menu/mode-'+name+'.webp')
        for m in re.finditer(r'\bIn=`([^`]+)`',combined):
            for effect in m.group(1).split('.'):
                if slug(effect): images.add('/naruto-blazing/effects/'+('perfect-dodge' if effect=='Dodge' else slug(effect))+'.webp')
        for d in payloads:
            for value in walk(d):
                if isinstance(value,str):
                    images.update(IMG_RE.findall(value))
                if isinstance(value,dict):
                    for aff in value.get('affiliations',[]): images.add('/naruto-blazing/affiliations/'+slug(aff)+'.webp')
                    for effect_key in ['effect','fieldEffect','buddyEffect']:
                        if isinstance(value.get(effect_key),str) and slug(value[effect_key]): images.add('/naruto-blazing/effects/'+slug(value[effect_key])+'.webp')
                    if isinstance(value.get('stars'),int) and 3<=value['stars']<=7:
                        images.add('/naruto-blazing/rarity/'+str(value['stars'])+('-ba' if value.get('blazingAwakened') else '')+'.webp')
            for ninja in d.get('ninjas',[]) if isinstance(d,dict) else []:
                if isinstance(ninja,dict) and 'forms' in ninja: images.add('/naruto-blazing/cutins/'+ninja['id']+'.webp')
        return images

    def snapshot(self):
        resources=sorted(self.entries.values(),key=lambda e:e['url'])
        groups={}
        for entry in resources:
            if entry['kind']=='image' and entry['status']=='ok': groups.setdefault(entry['sha256'],[]).append(entry)
        for group in groups.values():
            for entry in group[1:]:
                entry['duplicate_of']=group[0]['url']
                if entry['path']!=group[0]['path']:
                    entry['preserved_original_path']=entry['path']
                    entry['path']=group[0]['path']
        payload={'source':HOME,'started_at':self.started,'updated_at':now(),'resources':resources,'logical_pages':[{'url':u,'source_url':s} for u,s in sorted(self.logical.items())]}
        target=ROOT/'data/manifest.json'; tmp=target.with_suffix('.json.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8'); os.replace(tmp,target)
        counts=Counter((e['kind']+'_'+e['status']) for e in resources)
        report={'source':HOME,'started_at':self.started,'finished_at':now(),'resource_counts':dict(counts),'logical_pages_discovered':len(self.logical),'logical_pages_processed':len(self.logical),'logical_page_method':'SPA routes enumerated from client router and complete public datasets; single HTTP shell, no duplicate shell fetches','run_counts':dict(self.stats),'exact_duplicate_urls':sum(len(g)-1 for g in groups.values()),'unique_original_images':len(groups),'failures':[{'url':e['url'],'error':e.get('update_error') or e.get('error'),'http_status':e.get('last_update_attempt',{}).get('http_status') if e.get('update_error') else e.get('http_status'),'cached_available':e['status']=='ok'} for e in resources if e['status']!='ok' or e.get('update_error')],'excluded':['account/authentication/profile/community RPC endpoints','all non-Naruto-Blazing website data','external source links (preserved, not crawled)']}
        (ROOT/'reports/crawl.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')

    def run(self):
        scripts=self.discover_scripts()
        endpoints=set()
        for text in scripts: endpoints.update(DATA_RE.findall(text))
        for name in ['ninjas','missions','story']:
            self.seed(ORIGIN+'/data/naruto-blazing/'+name+'.json',ROOT/('data/'+name+'_probe.json'),'json','data/raw/'+name+'.json')
        pending=[(u,'json') for u in endpoints]; processed_json=set()
        while pending:
            entries=self.run_batch(pending); pending=[]
            for entry in entries:
                if entry['status']!='ok' or entry['kind']!='json': continue
                if entry['url'] in processed_json: continue
                processed_json.add(entry['url'])
                d=json.loads((ROOT/entry['path']).read_text(encoding='utf-8-sig')); self.data[entry['url']]=d
                for value in walk(d):
                    if isinstance(value,str):
                        linked=normalize_url(value,entry['url'])
                        if linked and urlsplit(linked).path.startswith('/data/naruto-blazing/') and urlsplit(linked).path.endswith('.json'):
                            pending.append((linked,'json'))
                        for endpoint in DATA_RE.findall(value): pending.append((endpoint,'json'))
                        if value.startswith('#/naruto-blazing'): self.logical_page(value,entry['url'])
            self.snapshot()
        # Story chapters are client modal state, not distinct routes; enumerate only router-supported pages.
        self.logical={}
        self.db.execute('DELETE FROM logical_pages')
        for part in ['','/ninjas','/missions','/missions/story','/missions/emergency','/missions/road','/missions/trials','/missions/ninja-trial','/missions/panel','/tower','/compare']:
            self.logical_page('#/naruto-blazing'+part,HOME)
        for url,d in self.data.items():
            for ninja in d.get('ninjas',[]):
                if isinstance(ninja,dict) and 'forms' in ninja: self.logical_page('#/naruto-blazing/ninjas/'+quote(ninja['id']),url)
            for mission in d.get('missions',[]):
                if isinstance(mission,dict) and 'id' in mission: self.logical_page('#/naruto-blazing/missions/emergency/'+quote(str(mission['id'])),url)
        for u,s in self.logical.items(): self.db.execute('INSERT OR REPLACE INTO logical_pages VALUES (?,?)',(u,s))
        self.db.commit()
        images=self.dynamic_images(scripts,list(self.data.values()))
        direct_references={}
        for entry in list(self.entries.values()):
            if entry['status']=='ok' and entry['kind'] in ['script','style','json']:
                text=(ROOT/entry['path']).read_text(encoding='utf8')
                for value in IMG_RE.findall(text):
                    if '${' not in value:
                        image_url=normalize_url(value)
                        if image_url: direct_references.setdefault(image_url,[]).append(entry['url'])
        print(json.dumps({'phase':'images','datasets':len(self.data),'discovered_image_urls':len(images),'logical_pages':len(self.logical)}),flush=True)
        self.snapshot()
        entries=self.run_batch([(u,'image') for u in sorted(images)])
        for entry in entries:
            entry['discovery_method']='source_literal' if entry['url'] in direct_references else 'client_helper'
            entry['referenced_by']=direct_references.get(entry['url'],[ORIGIN+'/assets/naruto-blazing-DJrTTTQw.js',ORIGIN+'/assets/NinjaCollection-CqPN8ZEq.js'])
            with LOCK:
                self.entries[entry['url']]=entry
                self.db.execute('INSERT OR REPLACE INTO resources VALUES (?,?)',(entry['url'],json.dumps(entry,ensure_ascii=False)))
        self.db.commit()
        self.snapshot()
        print(json.dumps({'phase':'complete','report':'reports/crawl.json','run_counts':dict(self.stats)}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--update',action='store_true',help='Revalidate cached resources using ETag/Last-Modified'); p.add_argument('--resume',action='store_true',help='Reuse successful cached resources and retry failures (default)'); p.add_argument('--workers',type=int,default=4)
    a=p.parse_args(); Collector(a.update,a.workers).run()

if __name__=='__main__': main()
