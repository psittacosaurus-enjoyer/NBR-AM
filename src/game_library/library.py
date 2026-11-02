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
    CREATE TABLE IF NOT EXISTS record_sources (
      source_name TEXT NOT NULL, source_key TEXT NOT NULL, record_id TEXT REFERENCES records(id),
      source_url TEXT NOT NULL, display_name TEXT, game_id TEXT, category TEXT, element TEXT,
      rarity TEXT, metadata TEXT NOT NULL, verification_status TEXT NOT NULL, content_hash TEXT,
      PRIMARY KEY(source_name,source_key));
    CREATE TABLE IF NOT EXISTS asset_sources (
      source_image_url TEXT PRIMARY KEY, asset_id TEXT REFERENCES assets(id), source_name TEXT,
      source_page_url TEXT, metadata TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS identity_aliases (
      alias_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL REFERENCES records(id),
      evidence TEXT NOT NULL, CHECK(alias_id!=canonical_id));
    CREATE TABLE IF NOT EXISTS record_asset_sources (
      record_id TEXT REFERENCES records(id), asset_id TEXT REFERENCES assets(id), role TEXT,
      source_image_url TEXT NOT NULL, PRIMARY KEY(record_id,asset_id,role,source_image_url));
    CREATE TABLE IF NOT EXISTS asset_preferences (
      record_id TEXT REFERENCES records(id), asset_id TEXT REFERENCES assets(id), fidelity_score REAL,
      basis TEXT NOT NULL, PRIMARY KEY(record_id,asset_id));
    CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(record_id UNINDEXED, search_text, tokenize='unicode61');
    ''')
    migrate_aliases(db)
    db.commit()

def migrate_aliases(db):
    """Permit historical IDs to redirect without retaining duplicate records.

    The caller owns the transaction; read-only consumers never migrate schemas.
    """
    if not db.in_transaction:
        db.execute('BEGIN')
    if any(row[3] == 'alias_id' for row in db.execute('PRAGMA foreign_key_list(identity_aliases)')):
        db.execute('CREATE TABLE identity_aliases_new (alias_id TEXT PRIMARY KEY, '
                   'canonical_id TEXT NOT NULL REFERENCES records(id), evidence TEXT NOT NULL, '
                   'CHECK(alias_id!=canonical_id))')
        db.execute('INSERT INTO identity_aliases_new SELECT * FROM identity_aliases')
        db.execute('DROP TABLE identity_aliases')
        db.execute('ALTER TABLE identity_aliases_new RENAME TO identity_aliases')
    db.execute('CREATE TABLE IF NOT EXISTS record_asset_sources ('
               'record_id TEXT REFERENCES records(id),asset_id TEXT REFERENCES assets(id),role TEXT,'
               'source_image_url TEXT NOT NULL,PRIMARY KEY(record_id,asset_id,role,source_image_url))')

def resolve_id(db, rid):
    if not has_table(db, 'identity_aliases'):
        return rid
    seen = set()
    while True:
        if rid in seen:
            raise ValueError('Cyclic library identity aliases')
        seen.add(rid)
        alias = db.execute('SELECT canonical_id FROM identity_aliases WHERE alias_id=?', (rid,)).fetchone()
        if alias is None:
            return rid
        rid = alias[0]

def _source_record(db, rid, record):
    """Keep source-specific facts separately from the authoritative identity."""
    db.execute('INSERT INTO record_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?) '
               'ON CONFLICT(source_name,source_key) DO UPDATE SET record_id=excluded.record_id,'
               'source_url=excluded.source_url,display_name=excluded.display_name,game_id=excluded.game_id,'
               'category=excluded.category,element=excluded.element,rarity=excluded.rarity,'
               'metadata=excluded.metadata,verification_status=excluded.verification_status,content_hash=excluded.content_hash',
               ('Source record', record['id'], rid, record['source_url'], record['display_name'],
                record.get('game_id'), record.get('category'), record.get('element'), record.get('rarity'),
                record['metadata'], record['verification_status'], record.get('content_hash')))

def record_asset_urls(db, record_id, asset_id, role=None):
    if not has_table(db, 'record_asset_sources'):
        return []
    query = 'SELECT DISTINCT source_image_url FROM record_asset_sources WHERE record_id=? AND asset_id=?'
    params = [record_id, asset_id]
    if role is not None:
        query += ' AND role=?'
        params.append(role)
    return [row[0] for row in db.execute(query + ' ORDER BY source_image_url', params)]

def link_asset(db, record_id, asset_id, role, source_image_url):
    record_id = resolve_id(db, record_id)
    if (db.execute('SELECT 1 FROM record_assets WHERE record_id=? AND asset_id=? AND role=?',
                   (record_id, asset_id, role)).fetchone() and not record_asset_urls(db, record_id, asset_id, role)):
        original = db.execute('SELECT source_image_url FROM assets WHERE id=?', (asset_id,)).fetchone()
        if original:
            db.execute('INSERT OR IGNORE INTO record_asset_sources VALUES(?,?,?,?)',
                       (record_id, asset_id, role, original[0]))
    db.execute('INSERT OR IGNORE INTO record_assets VALUES(?,?,?)', (record_id, asset_id, role))
    db.execute('INSERT OR IGNORE INTO record_asset_sources VALUES(?,?,?,?)',
               (record_id, asset_id, role, source_image_url))

def upsert_asset(db, asset):
    """Reuse exact content and split a changed source from shared old bytes."""
    url = asset['source_image_url']
    previous = db.execute('SELECT a.* FROM assets a JOIN asset_sources s ON s.asset_id=a.id '
                          'WHERE s.source_image_url=?', (url,)).fetchone()
    if previous is None:
        previous = db.execute('SELECT * FROM assets WHERE source_image_url=?', (url,)).fetchone()
    if previous is not None and previous['status'] == 'ok' and asset.get('status') != 'ok':
        return previous['id']
    changed = previous is not None and previous['sha256'] != asset.get('sha256')
    if changed:
        # Older databases may not yet have contextual links. Seed their original
        # URL before moving only the associations belonging to this source.
        db.execute('INSERT OR IGNORE INTO record_asset_sources '
                   'SELECT ra.record_id,ra.asset_id,ra.role,a.source_image_url FROM record_assets ra '
                   'JOIN assets a ON a.id=ra.asset_id WHERE a.id=? AND NOT EXISTS '
                   '(SELECT 1 FROM record_asset_sources s WHERE s.record_id=ra.record_id '
                   'AND s.asset_id=ra.asset_id AND s.role=ra.role)', (previous['id'],))
        urls = {previous['source_image_url'], *(row[0] for row in db.execute(
            'SELECT source_image_url FROM record_asset_sources WHERE asset_id=?', (previous['id'],)))}
        for old_url in urls:
            db.execute('INSERT OR IGNORE INTO asset_sources VALUES(?,?,?,?,?)',
                       (old_url, previous['id'], 'GachaWiki' if 'gachawiki.net' in old_url else 'Fandom', '', '{}'))
    match = None
    if asset.get('status') == 'ok' and asset.get('sha256'):
        match = db.execute('SELECT * FROM assets WHERE status="ok" AND sha256=? ORDER BY id LIMIT 1',
                           (asset['sha256'],)).fetchone()
    if match is None and previous is not None and not changed:
        match = previous
    if match is None:
        if changed and previous['source_image_url'] == url:
            other = db.execute('SELECT s.source_image_url FROM asset_sources s WHERE s.asset_id=? '
                               'AND s.source_image_url!=? AND NOT EXISTS '
                               '(SELECT 1 FROM assets a WHERE a.source_image_url=s.source_image_url) '
                               'ORDER BY s.source_image_url LIMIT 1', (previous['id'], url)).fetchone()
            if other is not None:
                db.execute('UPDATE assets SET source_image_url=? WHERE id=?', (other[0], previous['id']))
            else:
                # A single-source asset can safely retain its ID as its source updates.
                fields = ('local_image_path','thumbnail_path','sha256','width','height',
                          'perceptual_hash','status','collected_at')
                db.execute('UPDATE assets SET '+','.join(field+'=?' for field in fields)+' WHERE id=?',
                           [asset.get(field) for field in fields] + [previous['id']])
                match = db.execute('SELECT * FROM assets WHERE id=?', (previous['id'],)).fetchone()
        values = dict(asset)
        attempt = 0
        while db.execute('SELECT 1 FROM assets WHERE id=?', (values['id'],)).fetchone():
            attempt += 1
            suffix = '' if attempt == 1 else '#' + str(attempt)
            values['id'] = stable_id('asset', url + '#' + str(asset.get('sha256')) + suffix)
        fields = ('id','source_image_url','original_filename','local_image_path','thumbnail_path','sha256',
                  'width','height','perceptual_hash','status','collected_at')
        if match is None:
            db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)', [values.get(k) for k in fields])
            match = db.execute('SELECT * FROM assets WHERE id=?', (values['id'],)).fetchone()
    aid = match['id']
    if changed and previous['id'] != aid:
        affected = [tuple(row) for row in db.execute('SELECT record_id,role FROM record_asset_sources '
                                                    'WHERE asset_id=? AND source_image_url=?', (previous['id'], url))]
        for rid, role in affected:
            db.execute('INSERT OR IGNORE INTO record_assets VALUES(?,?,?)', (rid, aid, role))
            db.execute('INSERT OR IGNORE INTO record_asset_sources VALUES(?,?,?,?)', (rid, aid, role, url))
            db.execute('INSERT INTO asset_preferences SELECT record_id,?,fidelity_score,basis '
                       'FROM asset_preferences WHERE record_id=? AND asset_id=? '
                       'ON CONFLICT(record_id,asset_id) DO UPDATE SET '
                       'fidelity_score=MAX(asset_preferences.fidelity_score,excluded.fidelity_score)',
                       (aid, rid, previous['id']))
            db.execute('DELETE FROM record_asset_sources WHERE record_id=? AND asset_id=? AND role=? '
                       'AND source_image_url=?', (rid, previous['id'], role, url))
            if not record_asset_urls(db, rid, previous['id'], role):
                db.execute('DELETE FROM record_assets WHERE record_id=? AND asset_id=? AND role=?',
                           (rid, previous['id'], role))
            if not db.execute('SELECT 1 FROM record_assets WHERE record_id=? AND asset_id=?',
                              (rid, previous['id'])).fetchone():
                db.execute('DELETE FROM asset_preferences WHERE record_id=? AND asset_id=?', (rid, previous['id']))
    db.execute('INSERT INTO asset_sources VALUES(?,?,?,?,?) ON CONFLICT(source_image_url) '
               'DO UPDATE SET asset_id=excluded.asset_id',
               (url, aid, 'GachaWiki' if 'gachawiki.net' in url else 'Fandom', '', '{}'))
    if changed and previous['id'] != aid:
        # Keep the surviving row's representative URL associated with its own bytes.
        other = db.execute('SELECT source_image_url FROM asset_sources WHERE asset_id=? '
                           'ORDER BY source_image_url LIMIT 1', (previous['id'],)).fetchone()
        if other and previous['source_image_url'] == url:
            db.execute('UPDATE assets SET source_image_url=? WHERE id=?', (other[0], previous['id']))
        if not other and not db.execute('SELECT 1 FROM record_assets WHERE asset_id=? UNION ALL '
                                        'SELECT 1 FROM asset_preferences WHERE asset_id=?',
                                        (previous['id'], previous['id'])).fetchone():
            db.execute('DELETE FROM assets WHERE id=?', (previous['id'],))
    return aid

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
    canonical = resolve_id(db, rid)
    if canonical != rid:
        previous = db.execute('SELECT content_hash FROM record_sources WHERE source_name=? AND source_key=?',
                              ('Source record', rid)).fetchone()
        _source_record(db, canonical, dict(payload, id=rid, metadata=json.dumps(payload['metadata'], ensure_ascii=False), content_hash=digest))
        return canonical, 'unchanged' if previous and previous[0] == digest else 'updated'
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

def select_query(db):
    """Use the record's icon URLs while still reading pre-migration libraries."""
    if not has_table(db, 'record_asset_sources'):
        return SELECT
    scope = 's.record_id=ra.record_id AND s.asset_id=x.id AND s.role=ra.role'
    eligible = ('(EXISTS (SELECT 1 FROM record_asset_sources s WHERE '+scope+
                ' AND s.source_image_url NOT LIKE \'%/abilities/%\') OR '
                '(NOT EXISTS (SELECT 1 FROM record_asset_sources s WHERE '+scope+
                ') AND x.source_image_url NOT LIKE \'%/abilities/%\'))')
    return SELECT.replace("x.source_image_url NOT LIKE '%/abilities/%'", eligible)

def index(db):
    db.execute('DELETE FROM record_search')
    for row in db.execute(select_query(db)).fetchall():
        r = effective(row)
        filenames = [x[0] for x in db.execute('SELECT original_filename FROM assets JOIN record_assets ON assets.id=asset_id WHERE record_id=?', (r['id'],))]
        terms = [r['display_name'], r['game_id'], r['category'], r['element'], r['rarity'], r['notes'], *r['tags'], *filenames, r['source_key'], json.dumps(r['metadata'], ensure_ascii=False)]
        if has_table(db,'record_sources'):
            terms.extend(str(x) for row in db.execute('SELECT display_name,game_id,metadata FROM record_sources WHERE record_id=?',(r['id'],)) for x in row if x)
        db.execute('INSERT INTO record_search VALUES(?,?)', (r['id'], ' '.join(str(x) for x in terms if x is not None)))

def search(db, q='', record_type='', category='', element='', rarity='', status='', page=1, limit=60):
    page, limit = max(1,int(page)), min(120,max(1,int(limit)))
    clauses, params = [], []
    if has_table(db,'identity_aliases'):
        clauses.append('r.id NOT IN (SELECT alias_id FROM identity_aliases)')
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
    rows = db.execute(select_query(db) + where + ' ORDER BY COALESCE(a.display_name,r.display_name) COLLATE NOCASE,r.id LIMIT ? OFFSET ?', params+[limit,(page-1)*limit]).fetchall()
    records=[effective(r) for r in rows]
    if has_table(db,'asset_preferences'):
        for r in records:
            preferred=db.execute('SELECT x.local_image_path,x.thumbnail_path FROM asset_preferences p JOIN assets x ON x.id=p.asset_id WHERE p.record_id=? AND x.status="ok" ORDER BY p.fidelity_score DESC,x.id LIMIT 1',(r['id'],)).fetchone()
            if preferred: r['local_image_path'],r['thumbnail_path']=preferred
    return dict(records=records, total=total, page=page, limit=limit)

def get_record(db, rid):
    canonical = resolve_id(db, rid)
    row = db.execute(select_query(db)+' WHERE r.id=?', (canonical,)).fetchone()
    if row is None:
        return None
    r = effective(row)
    r['canonical_id']=canonical
    members=[canonical]
    if has_table(db,'identity_aliases'):
        members.extend(x[0] for x in db.execute('SELECT alias_id FROM identity_aliases WHERE canonical_id=? ORDER BY alias_id',(canonical,)))
    r['aliases']=[mid for mid in members if mid!=canonical]
    slots=','.join('?' for _ in members)
    r['assets']=[dict(x) for x in db.execute('SELECT x.*,ra.role FROM assets x JOIN record_assets ra ON ra.asset_id=x.id WHERE ra.record_id IN ('+slots+') ORDER BY ra.role,x.id',members)]
    seen=set();r['assets']=[a for a in r['assets'] if not ((a['id'],a['role']) in seen or seen.add((a['id'],a['role'])))]
    priorities={}
    if has_table(db,'asset_preferences'):
        priorities={x[0]:x[1] for x in db.execute('SELECT asset_id,fidelity_score FROM asset_preferences WHERE record_id=?',(canonical,))}
    r['assets'].sort(key=lambda a:(-priorities.get(a['id'],0),a['role'],a['id']))
    for a in r['assets']:
        contextual = {url for member in members for url in record_asset_urls(db, member, a['id'], a['role'])}
        a['source_image_urls'] = sorted(contextual) or [a['source_image_url']]
        a['source_urls']=[{'source_name':'GachaWiki' if 'gachawiki.net' in a['source_image_url'] else 'Fandom','source_url':a['source_image_url']}]
        if has_table(db,'asset_sources'):
            a['source_urls'].extend(dict(s) for s in db.execute('SELECT source_name,source_image_url source_url,source_page_url,metadata FROM asset_sources WHERE asset_id=?',(a['id'],)))
    downloaded=next((a for a in r['assets'] if a['status']=='ok' and a['local_image_path']),None)
    if downloaded and priorities:
        r['local_image_path']=downloaded['local_image_path'];r['thumbnail_path']=downloaded['thumbnail_path']
    r['sources']=[]
    if has_table(db,'record_sources'):
        for s in db.execute('SELECT * FROM record_sources WHERE record_id IN ('+slots+') ORDER BY source_name,source_key',members):
            value=dict(s);value['metadata']=json.loads(value['metadata']);r['sources'].append(value)
    r['related'] = [dict(x) for x in db.execute('''SELECT r.id,r.record_type,COALESCE(a.display_name,r.display_name) display_name,l.kind
       FROM relationships l JOIN records r ON r.id=CASE WHEN l.from_id=? THEN l.to_id ELSE l.from_id END
       LEFT JOIN annotations a ON a.record_id=r.id WHERE l.from_id=? OR l.to_id=? ORDER BY r.display_name LIMIT 200''',(canonical,canonical,canonical))]
    return r

def annotate(db, rid, changes):
    rid = resolve_id(db, rid)
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
    canonical_filter=' WHERE id NOT IN (SELECT alias_id FROM identity_aliases)' if has_table(db,'identity_aliases') else ''
    by_type = dict(db.execute('SELECT record_type,count(*) FROM records'+canonical_filter+' GROUP BY record_type'))
    facets = {}
    for name,expr in [('categories','COALESCE(a.category,r.category)'),('elements','r.element'),('rarities','r.rarity'),('statuses','COALESCE(a.verification_status,r.verification_status)')]:
        facets[name] = [x[0] for x in db.execute('SELECT DISTINCT '+expr+' FROM records r LEFT JOIN annotations a ON a.record_id=r.id WHERE '+expr+' IS NOT NULL ORDER BY 1')]
    return dict(total_records=sum(by_type.values()), total_assets=db.execute("SELECT count(*) FROM assets WHERE status='ok'").fetchone()[0], by_type=by_type, facets=facets)

def export(db, directory=ROOT/'catalogs'):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    for kind,filename in [('character','characters'),('item','items'),('stage','stages'),('interface','interface'),('miscellaneous','miscellaneous')]:
        extra=' AND id NOT IN (SELECT alias_id FROM identity_aliases)' if has_table(db,'identity_aliases') else ''
        records=[get_record(db,row[0]) for row in db.execute('SELECT id FROM records WHERE record_type=?'+extra+' ORDER BY id',(kind,)).fetchall()]
        atomic_json(directory/(filename+'.json'),records)
    atomic_json(directory/'assets.json',[dict(r) for r in db.execute('SELECT * FROM assets ORDER BY id')])

def atomic_json(path, obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
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
            extra=' AND r.id NOT IN (SELECT alias_id FROM identity_aliases)' if has_table(self.db,'identity_aliases') else ''
            rows=self.db.execute(select_query(self.db)+" WHERE r.record_type='item' AND COALESCE(a.verification_status,r.verification_status)=?"+extra,(status,)).fetchall()
            for row in rows:
                r=get_record(self.db,row['id'])
                r['artwork_assets']=[a for a in r['assets'] if a.get('role')=='artwork']
                r['assets']=[a for a in r['assets'] if a.get('role')!='artwork']
                r['reference_paths']=list(dict.fromkeys(str((ROOT/x['local_image_path']).resolve()) for x in r['assets'] if x['status']=='ok' and x['local_image_path']))
                if r['reference_paths']: yield r
    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

def has_table(db,name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
