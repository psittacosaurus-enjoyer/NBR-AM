"""SQLite reference library. No dependency on, or writes to, farming software."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'library'
DB_PATH = ROOT / 'data' / 'library.sqlite3'
TYPES = ('character', 'item', 'stage', 'interface', 'miscellaneous')
STATUSES = ('source_verified', 'inferred', 'unidentified', 'manually_verified')

def now():
    return datetime.now(timezone.utc).isoformat()

def stable_id(kind, key):
    return kind + '_' + hashlib.sha256(str(key).encode()).hexdigest()[:24]

def connect(path=DB_PATH, readonly=False):
    path = Path(path)
    if readonly:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS records (
      id TEXT PRIMARY KEY, record_type TEXT NOT NULL, source_key TEXT NOT NULL,
      display_name TEXT NOT NULL, game_id TEXT, source_url TEXT NOT NULL,
      category TEXT, element TEXT, rarity TEXT, verification_status TEXT NOT NULL,
      metadata TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', content_hash TEXT NOT NULL,
      collected_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(record_type,source_key));
    CREATE TABLE IF NOT EXISTS annotations (
      record_id TEXT PRIMARY KEY REFERENCES records(id), display_name TEXT,
      category TEXT, tags TEXT, notes TEXT, verification_status TEXT, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assets (
      id TEXT PRIMARY KEY, source_image_url TEXT UNIQUE NOT NULL, original_filename TEXT NOT NULL,
      local_image_path TEXT, thumbnail_path TEXT, sha256 TEXT, width INTEGER, height INTEGER,
      perceptual_hash TEXT, status TEXT, collected_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS record_assets (
      record_id TEXT REFERENCES records(id), asset_id TEXT REFERENCES assets(id), role TEXT,
      PRIMARY KEY(record_id,asset_id,role));
    CREATE TABLE IF NOT EXISTS relationships (
      from_id TEXT REFERENCES records(id), to_id TEXT REFERENCES records(id), kind TEXT,
      PRIMARY KEY(from_id,to_id,kind));
    CREATE TABLE IF NOT EXISTS builds (
      id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, summary TEXT);
    CREATE INDEX IF NOT EXISTS records_type ON records(record_type);
    CREATE INDEX IF NOT EXISTS records_category ON records(category);
    CREATE INDEX IF NOT EXISTS asset_hash ON assets(sha256);
    CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(record_id UNINDEXED, search_text, tokenize='unicode61');
    ''')
    db.commit()

def upsert_record(db, *, record_type, source_key, display_name, source_url, game_id=None,
                  category=None, element=None, rarity=None, metadata=None,
                  verification_status='source_verified', tags=None):
    if record_type not in TYPES or verification_status not in STATUSES:
        raise ValueError('Invalid record type or status')
    rid = stable_id(record_type, source_key)
    payload = dict(record_type=record_type, source_key=str(source_key), display_name=display_name,
                   game_id=None if game_id is None else str(game_id), source_url=source_url,
                   category=category, element=None if element is None else str(element),
                   rarity=None if rarity is None else str(rarity), verification_status=verification_status,
                   metadata=metadata or {}, tags=tags or [])
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = db.execute('SELECT content_hash FROM records WHERE id=?', (rid,)).fetchone()
    timestamp = now()
    values = [rid] + [payload[k] for k in ('record_type','source_key','display_name','game_id','source_url','category','element','rarity','verification_status')]
    values += [json.dumps(payload['metadata'], ensure_ascii=False), json.dumps(payload['tags']), digest, timestamp, timestamp]
    db.execute('''INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      display_name=excluded.display_name,game_id=excluded.game_id,source_url=excluded.source_url,
      category=excluded.category,element=excluded.element,rarity=excluded.rarity,
      verification_status=excluded.verification_status,metadata=excluded.metadata,tags=excluded.tags,
      content_hash=excluded.content_hash,updated_at=CASE WHEN records.content_hash!=excluded.content_hash
      THEN excluded.updated_at ELSE records.updated_at END''', values)
    return rid, 'new' if not old else 'unchanged' if old[0] == digest else 'updated'

def effective(row):
    r = dict(row)
    for key in ('display_name','category','tags','verification_status'):
        manual = r.pop('manual_' + key, None)
        if manual is not None:
            r[key] = manual
    r['notes'] = r.pop('manual_notes', None) or ''
    r['metadata'] = json.loads(r['metadata'])
    r['tags'] = json.loads(r['tags'])
    return r

SELECT = '''SELECT r.*,a.display_name manual_display_name,a.category manual_category,
 a.tags manual_tags,a.notes manual_notes,a.verification_status manual_verification_status,
 (SELECT local_image_path FROM assets x JOIN record_assets ra ON x.id=ra.asset_id
  WHERE ra.record_id=r.id AND x.status='ok' AND (r.record_type!='character' OR ra.role IN ('card','art','icon'))
  AND (r.record_type!='character' OR ra.role!='icon' OR x.source_image_url NOT LIKE '%/abilities/%')
  ORDER BY CASE ra.role WHEN 'card' THEN 0 WHEN 'icon' THEN 1 WHEN 'image' THEN 2 ELSE 3 END, x.id LIMIT 1) local_image_path,
 (SELECT thumbnail_path FROM assets x JOIN record_assets ra ON x.id=ra.asset_id
  WHERE ra.record_id=r.id AND x.status='ok' AND (r.record_type!='character' OR ra.role IN ('card','art','icon'))
  AND (r.record_type!='character' OR ra.role!='icon' OR x.source_image_url NOT LIKE '%/abilities/%')
  ORDER BY CASE ra.role WHEN 'card' THEN 0 WHEN 'icon' THEN 1 WHEN 'image' THEN 2 ELSE 3 END, x.id LIMIT 1) thumbnail_path
 FROM records r LEFT JOIN annotations a ON a.record_id=r.id'''

def index(db):
    db.execute('DELETE FROM record_search')
    for row in db.execute(SELECT).fetchall():
        r = effective(row)
        filenames = [x[0] for x in db.execute('SELECT original_filename FROM assets JOIN record_assets ON assets.id=asset_id WHERE record_id=?', (r['id'],))]
        terms = [r['display_name'], r['game_id'], r['category'], r['element'], r['rarity'], r['notes'], *r['tags'], *filenames, r['source_key'], json.dumps(r['metadata'], ensure_ascii=False)]
        db.execute('INSERT INTO record_search VALUES(?,?)', (r['id'], ' '.join(str(x) for x in terms if x is not None)))

def search(db, q='', record_type='', category='', element='', rarity='', status='', page=1, limit=60):
    page, limit = max(1,int(page)), min(120,max(1,int(limit)))
    clauses, params = [], []
    if q.strip():
        # Literal token prefix queries, never accept raw FTS syntax from the user.
        query = ' AND '.join('"' + token.replace('"','""') + '"*' for token in q.split())
        clauses.append('r.id IN (SELECT record_id FROM record_search WHERE record_search MATCH ?)')
        params.append(query)
    for expr,value in [('r.record_type',record_type),('COALESCE(a.category,r.category)',category),
                       ('r.element',element),('r.rarity',rarity),('COALESCE(a.verification_status,r.verification_status)',status)]:
        if value:
            clauses.append(expr + '=?')
            params.append(value)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = db.execute('SELECT count(*) FROM records r LEFT JOIN annotations a ON a.record_id=r.id' + where, params).fetchone()[0]
    rows = db.execute(SELECT + where + ' ORDER BY COALESCE(a.display_name,r.display_name) COLLATE NOCASE,r.id LIMIT ? OFFSET ?', params+[limit,(page-1)*limit]).fetchall()
    return dict(records=[effective(r) for r in rows], total=total, page=page, limit=limit)

def get_record(db, rid):
    row = db.execute(SELECT+' WHERE r.id=?', (rid,)).fetchone()
    if row is None:
        return None
    r = effective(row)
    r['assets'] = [dict(x) for x in db.execute('SELECT x.*,ra.role FROM assets x JOIN record_assets ra ON ra.asset_id=x.id WHERE ra.record_id=? ORDER BY ra.role,x.id',(rid,))]
    r['related'] = [dict(x) for x in db.execute('''SELECT r.id,r.record_type,COALESCE(a.display_name,r.display_name) display_name,l.kind
       FROM relationships l JOIN records r ON r.id=CASE WHEN l.from_id=? THEN l.to_id ELSE l.from_id END
       LEFT JOIN annotations a ON a.record_id=r.id WHERE l.from_id=? OR l.to_id=? ORDER BY r.display_name LIMIT 200''',(rid,rid,rid))]
    return r

def annotate(db, rid, changes):
    if db.execute('SELECT 1 FROM records WHERE id=?',(rid,)).fetchone() is None:
        raise KeyError(rid)
    if set(changes) - {'display_name','category','tags','notes','verification_status'}:
        raise ValueError('Unsupported annotation field')
    if 'verification_status' in changes and changes['verification_status'] not in STATUSES:
        raise ValueError('Invalid verification status')
    if 'tags' in changes and (not isinstance(changes['tags'],list) or not all(isinstance(t,str) for t in changes['tags'])):
        raise ValueError('Tags must be a list of strings')
    for key in ('display_name','category','notes'):
        if key in changes and not isinstance(changes[key],str):
            raise ValueError(key+' must be a string')
    if 'display_name' in changes and not changes['display_name'].strip():
        raise ValueError('Display name cannot be empty')
    with db:
        db.execute('INSERT OR IGNORE INTO annotations(record_id,updated_at) VALUES(?,?)',(rid,now()))
        for key,value in changes.items():
            if key=='tags': value=json.dumps(value)
            db.execute('UPDATE annotations SET '+key+'=?,updated_at=? WHERE record_id=?',(value,now(),rid))
        index(db)
    return get_record(db,rid)

def stats(db):
    by_type = dict(db.execute('SELECT record_type,count(*) FROM records GROUP BY record_type'))
    facets = {}
    for name,expr in [('categories','COALESCE(a.category,r.category)'),('elements','r.element'),('rarities','r.rarity'),('statuses','COALESCE(a.verification_status,r.verification_status)')]:
        facets[name] = [x[0] for x in db.execute('SELECT DISTINCT '+expr+' FROM records r LEFT JOIN annotations a ON a.record_id=r.id WHERE '+expr+' IS NOT NULL ORDER BY 1')]
    return dict(total_records=sum(by_type.values()), total_assets=db.execute("SELECT count(*) FROM assets WHERE status='ok'").fetchone()[0], by_type=by_type, facets=facets)

def export(db, directory=ROOT/'catalogs'):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    for kind,filename in [('character','characters'),('item','items'),('stage','stages'),('interface','interface'),('miscellaneous','miscellaneous')]:
        records=[get_record(db,row[0]) for row in db.execute('SELECT id FROM records WHERE record_type=? ORDER BY id',(kind,)).fetchall()]
        atomic_json(directory/(filename+'.json'),records)
    atomic_json(directory/'assets.json',[dict(r) for r in db.execute('SELECT * FROM assets ORDER BY id')])

def atomic_json(path, obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

class ReferenceLibrary:
    """Read-only lookup interface; verified references include source or manual verification."""
    def __init__(self,path=DB_PATH):
        self.db=connect(path,readonly=True)
    def search(self,**kwargs): return search(self.db,**kwargs)
    def get(self,record_id): return get_record(self.db,record_id)
    def item_references(self,manually_verified_only=False):
        statuses=('manually_verified',) if manually_verified_only else ('source_verified','manually_verified')
        for status in statuses:
            rows=self.db.execute(SELECT+" WHERE r.record_type='item' AND COALESCE(a.verification_status,r.verification_status)=?",(status,)).fetchall()
            for row in rows:
                r=get_record(self.db,row['id'])
                r['reference_paths']=list(dict.fromkeys(str((ROOT/x['local_image_path']).resolve()) for x in r['assets'] if x['status']=='ok' and x['local_image_path']))
                if r['reference_paths']: yield r
    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
