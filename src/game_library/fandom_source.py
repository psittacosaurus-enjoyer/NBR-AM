"""Public MediaWiki material collector; stages source facts for the existing library.

Uses ordinary anonymous api.php endpoints and source-associated original images.
It never writes the reference database or interacts with any game/account.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,quote,urlsplit
from urllib.request import Request,urlopen
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]/'runtime'/'library'
ORIGIN='https://naruto-blazing.fandom.com'
API=ORIGIN+'/api.php'
MATERIAL_TYPES={'Limit Break','Blazing Awakening','Awakening','Enhancement Material','Boosting','Spending'}
LOCK=threading.RLock()
RATE_LOCK=threading.Lock()
NEXT=0.

def timestamp(): return datetime.now(timezone.utc).isoformat()
def page_url(title): return ORIGIN+'/wiki/'+quote(title.replace(' ','_'),safe='():_')
def api_url(**params): return API+'?'+urlencode(params)
def files_in(value): return list(dict.fromkeys(re.findall(r'\[\[File:([^|\]]+)',value,re.I)))
def links_in(value):
    links=[]
    for title in re.findall(r'\[\[([^|\]]+)',value):
        if not re.match(r'(?:File|Category|Template):',title,re.I): links.append(title.strip())
    for title in re.findall(r'\[\[File:[^\]]*?\|link=([^\]]+)\]\]',value,re.I):
        if not re.match(r'(?:File|Category|Template):',title,re.I):links.append(title.strip())
    return list(dict.fromkeys(links))
def plain(value):
    value=re.sub(r'\[\[File:[^\]]*\]\]','',value,flags=re.I)
    value=re.sub(r'\[\[([^|\]]+)\|([^\]]+)\]\]',r'\2',value)
    value=re.sub(r'\[\[([^\]]+)\]\]',r'\1',value)
    value=re.sub(r'<br\s*/?>','; ',value,flags=re.I)
    value=re.sub(r'<[^>]*>','',value)
    return html.unescape(value).replace("'''",'').strip()

def extract_encyclopedia(wikitext,source_url=None):
    """The explicit encyclopedia No. column verifies card numbers, never filenames."""
    records=[];excluded=Counter()
    for chunk in re.split(r'\n\|-\s*\n',wikitext):
        cells=[line[1:].strip() for line in chunk.splitlines() if line.startswith('|') and not line.startswith('|}')]
        if len(cells)<6 or not cells[0].isdigit(): continue
        no,picture,name,rarity,element,kind=cells[:6]
        if kind not in MATERIAL_TYPES:
            excluded[kind]+=1;continue
        names=links_in(name)
        if len(names)!=1:
            excluded['unresolved_material_name']+=1;continue
        title=names[0]
        element_match=re.search(r'link=Category:(Heart|Skill|Body|Bravery|Wisdom)\b',element)
        records.append({'source_key':'fandom:card_no:'+no,'title':title,'source_url':page_url(title),'game_id':no,
                        'category':kind,'element':element_match.group(1) if element_match else None,
                        'rarity':rarity.count('★') or None,'description':None,'images':[],
                        'metadata':{'game_id_namespace':'card_no','game_id_evidence':'Encyclopedia explicit No. column',
                                    'encyclopedia_url':source_url or page_url('Encyclopedia'),'encyclopedia_display_name':plain(name),
                                    'source_type':kind,'rarity_raw':rarity,'image_filenames':files_in(picture),
                                    'field_status':'source_verified','classification_status':'source_type_preserved'},
                        'related_links':[page_url('Encyclopedia')]})
    return records,dict(excluded)

def material_parameters(wikitext):
    params={}
    for match in re.finditer(r'^\|([\w-]+)\s*=\s*(.*?)(?=\n\|[\w-]+\s*=|\n\}\}|\Z)',wikitext,re.M|re.S):
        params[match.group(1)]=match.group(2).strip()
    return params

def canonical_material_name(record):
    metadata=record['metadata'];params=metadata.get('material_parameters',{})
    raw=metadata.get('fandom_page_title') or metadata.get('raw_page_title') or record['title']
    metadata['raw_page_title']=raw
    base,subtitle=plain(params.get('mat-name','')),plain(params.get('mat-title',''))
    name=(base+' "'+subtitle+'"') if base and subtitle else metadata.get('encyclopedia_display_name') or raw
    record['title']=re.sub(r'\s*\(★\d+\)\s*$','',name).strip()
    if '\ufffd' in raw or '\u00e2\u02dc' in raw or '\u00e2\u0098' in raw:
        metadata['encoding_issue_detected']=True
    return record

def enrich_material(record,page):
    revisions=page.get('revisions',[])
    wiki=revisions[0].get('slots',{}).get('main',{}).get('*','') if revisions else ''
    params=material_parameters(wiki)
    record['metadata'].update(fandom_page_id=page.get('pageid'),fandom_page_title=page.get('title'),material_parameters=params,
                              page_source_status='missing' if 'missing' in page else 'retrieved')
    discrepancies=[]
    if params.get('card-no') and params['card-no'].strip()!=record['game_id']:
        discrepancies.append({'field':'card_no','encyclopedia':record['game_id'],'material_page':params['card-no']})
    if params.get('rarity') and params['rarity'].count('★')!=record['rarity']:
        discrepancies.append({'field':'rarity','encyclopedia':record['rarity'],'material_page':params['rarity'].count('★')})
    material_element=re.search(r'\{\{(Heart|Skill|Body|Bravery|Wisdom) Materials\b',wiki)
    if material_element and record['element'] and material_element.group(1)!=record['element']:
        discrepancies.append({'field':'element','encyclopedia':record['element'],'material_page':material_element.group(1)})
    if discrepancies: record['metadata']['source_discrepancies']=discrepancies
    description=plain(params.get('additional-information',''))
    if description and description not in ['N/A','None','???']: record['description']=description
    if params.get('how-obtain'): record['metadata']['obtain']=plain(params['how-obtain'])
    links=[]
    for key in ['how-obtain','additional-information','awaken-thum']+[k for k in params if k.startswith('material-')]:
        if key in params: links.extend(links_in(params[key]))
    record['related_links']=list(dict.fromkeys(record['related_links']+[page_url(t) for t in links]))
    # Only main material icon and artwork; adjacent previous/next cards and banners
    # are not images of this identity and are intentionally excluded.
    image_roles={name:'card' for name in record['metadata']['image_filenames']}
    for key,role in [('mat-image','card'),('mat-artwork','artwork')]:
        for name in files_in(params.get(key,'')): image_roles[name]=role
    record['metadata']['image_roles']=image_roles
    return canonical_material_name(record)

class FandomCollector:
    def __init__(self,update=False,workers=4):
        self.update=update;self.workers=max(1,min(4,workers));self.stats=Counter();self.started=timestamp()
        self.folder=ROOT/'data'/'fandom';self.folder.mkdir(parents=True,exist_ok=True)
        (ROOT/'reports').mkdir(parents=True,exist_ok=True)
        self.cache_path=self.folder/'resources.json'
        self.resources=json.loads(self.cache_path.read_text(encoding='utf8')) if self.cache_path.exists() else {}
        self.sha_paths={}
        existing=ROOT/'data'/'manifest.json'
        if existing.exists():
            for row in json.loads(existing.read_text(encoding='utf8')).get('resources',[]):
                if row.get('kind')=='image' and row.get('status')=='ok' and row.get('path') and (ROOT/row['path']).is_file():
                    self.sha_paths[row['sha256']]=row['path']
        for row in self.resources.values():
            if row.get('kind')=='image' and row.get('status')=='ok' and row.get('path') and (ROOT/row['path']).is_file():
                self.sha_paths[row['sha256']]=row['path']

    def save_cache(self):
        with LOCK:
            tmp=self.cache_path.with_suffix('.json.tmp');tmp.write_text(json.dumps(self.resources,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(self.cache_path)

    def allowed(self,url,kind):
        u=urlsplit(url)
        if u.scheme!='https' or u.username or u.password: return False
        if kind=='api': return u.netloc=='naruto-blazing.fandom.com' and u.path=='/api.php'
        namespaces=('/naruto-blazing/','/naruto-shippuden-ultimate-ninja-blazing/')
        return u.netloc in ['static.wikia.nocookie.net','vignette.wikia.nocookie.net','images.wikia.com','naruto-blazing.fandom.com'] and (u.path.startswith(namespaces) or u.netloc=='naruto-blazing.fandom.com')

    def native_image_request(self,url,headers):
        """Windows curl uses the normal OS trust store; TLS verification stays enabled."""
        original_url=url+('&' if '?' in url else '?')+'format=original'
        with tempfile.TemporaryDirectory(prefix='native-image-',dir=self.folder) as temporary:
            folder=Path(temporary).resolve()
            if not folder.is_relative_to(self.folder.resolve()):raise OSError('Unexpected temporary directory location')
            body=folder/'body.bin';dump=folder/'headers.txt'
            command=['curl.exe','--silent','--show-error','--location','--proto','=https','--proto-redir','=https',
                     '--connect-timeout','15','--max-time','35','--dump-header',str(dump),'--output',str(body),
                     '--write-out','%{http_code}\n%{url_effective}\n%{content_type}',
                     '--header','Accept: image/png']
            for key,value in headers.items():command.extend(['--header',key+': '+value])
            command.append(original_url)
            result=subprocess.run(command,capture_output=True,text=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise OSError('Native HTTPS request failed: '+result.stderr.strip())
            pieces=result.stdout.splitlines();status=int(pieces[0]);effective=pieces[1]
            if not self.allowed(effective,'image'):raise ValueError('Redirect outside authorized official image namespace')
            blocks=[b for b in re.split(r'\r?\n\r?\n',dump.read_text(encoding='utf8',errors='replace')) if b.strip()]
            response_headers={}
            for line in blocks[-1].splitlines()[1:]:
                if ':' in line:
                    key,value=line.split(':',1);response_headers[key.lower()]=value.strip()
            if status!=200:raise HTTPError(url,status,'Native HTTPS response',response_headers,None)
            return body.read_bytes(),response_headers,status,effective

    def fetch(self,url,kind='api'):
        global NEXT
        if not self.allowed(url,kind): return {'url':url,'status':'failed','error':'Outside authorized public material source scope','kind':kind}
        with LOCK: old=self.resources.get(url)
        cached=bool(old and old.get('status')=='ok' and old.get('path') and (ROOT/old['path']).is_file())
        if cached and not self.update and not old.get('update_error'):
            with LOCK:self.stats['skipped']+=1
            return old
        headers={'User-Agent':'NBR-Reference-Library/1.1 (authorized anonymous public material integration)','Accept-Encoding':'identity'}
        if cached:
            if old.get('etag'):headers['If-None-Match']=old['etag']
            if old.get('last_modified'):headers['If-Modified-Since']=old['last_modified']
        row={'url':url,'kind':kind,'status':'failed','collected_at':timestamp()}
        for attempt in range(3):
            row['attempts']=attempt+1
            with RATE_LOCK:
                delay=max(0,NEXT-time.monotonic());NEXT=max(NEXT,time.monotonic())+.5
            if delay:time.sleep(delay)
            try:
                if kind=='image':
                    data,response_headers,status,effective=self.native_image_request(url,headers)
                    row.update(transport='Windows native HTTPS curl with certificate verification',original_format_request_url=effective)
                else:
                    response=urlopen(Request(url,headers=headers),timeout=35)
                    if not self.allowed(response.url,kind):raise ValueError('Redirect outside permitted public source scope')
                    data=response.read();response_headers={k.lower():v for k,v in response.headers.items()};status=response.status
                mime=response_headers.get('content-type','').split(';')[0]
                row.update(http_status=status,mime=mime,etag=response_headers.get('etag'),last_modified=response_headers.get('last-modified'))
                if kind=='api':
                    payload=json.loads(data)
                    if 'error' in payload:raise ValueError('Public API error: '+str(payload['error']))
                    extension='.json'
                else:
                    if not mime.startswith('image/'):raise ValueError('Image URL returned a nonimage response')
                    with Image.open(io.BytesIO(data)) as im:
                        extension='.'+im.format.lower().replace('jpeg','jpg')
                        gray=im.convert('L').resize((9,8),Image.Resampling.LANCZOS)
                        pixels=list(gray.get_flattened_data() if hasattr(gray,'get_flattened_data') else gray.getdata());bits=0
                        for y in range(8):
                            for x in range(8):bits=(bits<<1)|int(pixels[y*9+x]>pixels[y*9+x+1])
                        row.update(width=im.width,height=im.height,format=im.format,has_alpha='A' in im.mode or 'transparency' in im.info,perceptual_hash=f'{bits:016x}')
                digest=hashlib.sha256(data).hexdigest()
                with LOCK:
                    if kind=='image' and digest in self.sha_paths:
                        path=self.sha_paths[digest];self.stats['exact_duplicate_storage_avoided']+=1
                        row['reused_existing_path']=True
                    else:
                        path=f'data/fandom/{"images" if kind=="image" else "raw"}/{digest}{extension}'
                        if kind=='image':self.sha_paths[digest]=path
                    target=ROOT/path;target.parent.mkdir(parents=True,exist_ok=True)
                    if not target.exists():target.write_bytes(data)
                row.update(status='ok',sha256=digest,sha1=hashlib.sha1(data).hexdigest(),path=path,bytes=len(data));row.pop('error',None)
                with LOCK:self.stats['successful']+=1
                break
            except HTTPError as exc:
                row.update(http_status=exc.code,error=f'HTTP {exc.code}')
                if exc.code==304 and cached:
                    row=old.copy();row.update(checked_at=timestamp(),http_status=304);row.pop('update_error',None)
                    with LOCK:self.stats['unchanged']+=1
                    break
                if exc.code in [401,403]:
                    row['error']='Public access blocked; no challenge bypass, authentication or proxy attempted';break
                if exc.code==429 or exc.code>=500:
                    retry=exc.headers.get('Retry-After','');time.sleep(min(30,int(retry) if retry.isdigit() else 2**attempt));continue
                break
            except (OSError,URLError,ValueError) as exc:
                row['error']=str(exc)
                if attempt<2:time.sleep(2**attempt)
        if row['status']!='ok':
            with LOCK:self.stats['failed']+=1
            if cached:
                failure=row;row=old.copy();row.update(update_error=failure.get('error'),last_update_attempt=failure,checked_at=timestamp())
        with LOCK:self.resources[url]=row
        self.save_cache()
        return row

    def api(self,**params):
        row=self.fetch(api_url(**params))
        return json.loads((ROOT/row['path']).read_bytes()) if row['status']=='ok' else None

    def seed_primary_probes(self):
        for title in ['Limit_Break','Encyclopedia']:
            path=self.folder/(title+'.json')
            if not path.exists():continue
            url=api_url(action='parse',format='json',page=title,prop='text|wikitext|images|links')
            if url not in self.resources:
                data=path.read_bytes();json.loads(data)
                self.resources[url]={'url':url,'kind':'api','status':'ok','path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(data).hexdigest(),'http_status':200,'collected_at':timestamp(),'bytes':len(data),'reused_authorized_probe':True}
        self.save_cache()

    def run(self):
        self.seed_primary_probes()
        primary={}
        for title in ['Limit_Break','Encyclopedia']:
            primary[title]=self.api(action='parse',format='json',page=title,prop='text|wikitext|images|links')
        if not all(primary.values()):
            self.write_report([],{},'blocked_primary_sources');return []
        records,excluded=extract_encyclopedia(primary['Encyclopedia']['parse']['wikitext']['*'])
        # Preserve explicit associations from the requested Limit Break guide.
        lb=primary['Limit_Break']['parse']['wikitext']['*']
        lb_links={plain(t) for t in re.findall(r'\[\[File:[^\]]*?\|link=([^\]]+)\]\]',lb)}
        for row in records:
            if row['title'] in lb_links:row['related_links'].append(page_url('Limit Break'));row['metadata']['limit_break_guide_associated']=True
        by_title={row['title']:row for row in records}
        batches=[records[i:i+25] for i in range(0,len(records),25)]
        def pages(batch):
            return self.api(action='query',format='json',prop='revisions',rvprop='content',rvslots='main',redirects=1,titles='|'.join(r['title'] for r in batch))
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            payloads=list(pool.map(pages,batches))
        for batch,payload in zip(batches,payloads):
            if not payload:
                for row in batch:row['metadata']['page_source_status']='unavailable'
                continue
            redirects={r['to']:r['from'] for r in payload['query'].get('redirects',[])}
            for page in payload['query']['pages'].values():
                title=page['title'];original=redirects.get(title,title)
                if original in by_title:enrich_material(by_title[original],page)
        for row in records:
            if 'image_roles' not in row['metadata']:row['metadata']['image_roles']={name:'card' for name in row['metadata']['image_filenames']}
        self.export(records)
        filenames=sorted({name for row in records for name in row['metadata']['image_roles']})
        imageinfo={}
        for i in range(0,len(filenames),50):
            payload=self.api(action='query',format='json',prop='imageinfo',iiprop='url|size|mime|sha1',titles='|'.join('File:'+name for name in filenames[i:i+50]))
            if not payload:continue
            for page in payload['query']['pages'].values():
                if page.get('imageinfo'):imageinfo[page['title'][5:]]=page['imageinfo'][0]
        urls=sorted({info['url'] for info in imageinfo.values()})
        print(json.dumps({'phase':'material_images','source_records':len(records),'original_image_urls':len(urls),'material_pages_with_infobox':sum(bool(r['metadata'].get('material_parameters')) for r in records)}),flush=True)
        downloads={}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for index,row in enumerate(pool.map(lambda url:self.fetch(url,'image'),urls),1):
                downloads[row['url']]=row
                if index%50==0:print(json.dumps({'images_processed':index,'total':len(urls),'counts':dict(self.stats)}),flush=True)
        for row in records:
            for filename,role in row['metadata']['image_roles'].items():
                info=imageinfo.get(filename)
                if not info:
                    row['metadata'].setdefault('missing_source_images',[]).append(filename);continue
                downloaded=downloads.get(info['url'])
                if not downloaded or downloaded['status']!='ok':
                    row['metadata'].setdefault('unavailable_image_urls',[]).append(info['url']);continue
                row['images'].append({**{k:downloaded.get(k) for k in ['url','path','width','height','sha256','perceptual_hash','format','has_alpha']},
                                      'source_page_url':row['source_url'],'original_filename':filename,'role':role,
                                      'file_page_url':info.get('descriptionurl',page_url('File:'+filename)),
                                      'source_image_sha1':info.get('sha1'),'original_format_request_url':downloaded.get('original_format_request_url'),
                                      'original_byte_checksum_verified':downloaded.get('sha1')==info.get('sha1') if downloaded.get('sha1') else None,
                                      'reused_existing_path':downloaded.get('reused_existing_path',False)})
        self.export(records);self.write_report(records,excluded,'complete')
        return records

    def export(self,records):
        path=self.folder/'source_records.json';tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(path)

    def write_report(self,records,excluded,status):
        report={'source':ORIGIN,'status':status,'started_at':self.started,'finished_at':timestamp(),'source_records_examined':len(records),
                'records_with_images':sum(bool(r['images']) for r in records),'record_image_associations':sum(len(r['images']) for r in records),
                'unique_original_image_hashes':len({i['sha256'] for r in records for i in r['images']}),'category_counts':dict(Counter(r['category'] for r in records)),
                'excluded_encyclopedia_rows':excluded,'run_counts':dict(self.stats),
                'source_discrepancies':[{'source_key':r['source_key'],'values':r['metadata']['source_discrepancies']} for r in records if r['metadata'].get('source_discrepancies')],
                'missing_source_images':[{'source_key':r['source_key'],'files':r['metadata']['missing_source_images']} for r in records if r['metadata'].get('missing_source_images')],
                'source_image_checksum_mismatches':[{'source_key':r['source_key'],'url':i['url']} for r in records for i in r['images'] if i.get('original_byte_checksum_verified') is False],
                'failures':[r for r in self.resources.values() if r['status']!='ok' or r.get('update_error')],
                'access_method':'ordinary anonymous public MediaWiki api.php and API-associated original image URLs; no authentication/challenge bypass/proxies',
                'scope':'Material rows only; no character roster recrawl, private data, game interaction or reference database writes.'}
        (ROOT/'reports'/'fandom_crawl.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({k:report[k] for k in ['status','source_records_examined','records_with_images','record_image_associations','unique_original_image_hashes','run_counts']}),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--update',action='store_true');parser.add_argument('--resume',action='store_true');parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();FandomCollector(args.update,args.workers).run()

if __name__=='__main__':main()
