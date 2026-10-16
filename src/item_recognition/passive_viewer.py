"""Local saved-results viewer. Reads farmer output; never captures or controls a device."""
from __future__ import annotations

import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote, urlsplit


PAGE = """<!doctype html><meta charset="utf-8"><title>NBR saved-results viewer</title>
<style>body{font:16px system-ui;background:#142031;color:#eef3fa;margin:24px}
main{display:flex;gap:24px;align-items:flex-start}img{max-width:450px;width:45vw}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:850px}p{color:#becce0}</style>
<h1>Saved-results viewer</h1><p>Passive: existing farmer metadata and retained results only.
Refreshes every 5 seconds. This is not a live device screen.</p><p id="status">Loading…</p>
<main><div><p id="age"></p><img id="capture" hidden alt="Retained farming results"></div>
<pre id="details"></pre></main><script>
let previous=null;
async function refresh(){try{
 const r=await fetch('/snapshot',{cache:'no-store'}),s=await r.json();
 document.getElementById('status').textContent=s.status+' — '+(s.detail||'');
 document.getElementById('details').textContent=JSON.stringify(s,null,2);
 document.getElementById('age').textContent=s.image_id?
  'Saved capture age: '+Math.round(s.image_age_seconds)+' seconds (file time)':'';
 const image=document.getElementById('capture');image.hidden=!s.image_id;
 if(s.image_id&&s.image_id!==previous)image.src='/image/'+encodeURIComponent(s.image_id);
 previous=s.image_id||null;
}catch(e){document.getElementById('status').textContent='Unavailable: '+e.message;}}
refresh();setInterval(refresh,5000);
</script>"""


def read_snapshot(root):
    """Return metadata and the sole allowed image; failed reads never create files."""
    root = Path(root).resolve()
    answer = {'status': 'Unavailable', 'detail': 'Saved results are unavailable.',
              'image_id': None, 'image_age_seconds': None}
    try:
        progress = json.loads((root / 'runtime/universal.json').read_text('utf-8'))
        answer['farmer'] = {key: progress.get(key) for key in
                            ('worker_pid', 'screen', 'heartbeat', 'counts', 'blocked')}
        database = root / 'runtime/loot/loot.sqlite3'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            session = db.execute('SELECT * FROM sessions ORDER BY tracking_started DESC LIMIT 1').fetchone()
            if session is None:
                answer['detail'] = 'No recorded loot session.'
                return answer, None
            answer['session'] = dict(session)
            rows = db.execute('SELECT * FROM completions WHERE session_id=? '
                              'ORDER BY run_sequence DESC LIMIT 5', (session['session_id'],)).fetchall()
            records = []
            selected = None
            for row in rows:
                record = dict(row)
                screenshot = record.pop('screenshot_path', None)
                record['result'] = json.loads(record.pop('result_json'))
                record['warnings'] = json.loads(record.pop('warnings_json'))
                records.append(record)
                if selected is None and screenshot:
                    path = Path(screenshot).resolve()
                    allowed = (root / 'runtime/loot/diagnostics').resolve()
                    if path.parent == allowed and path.suffix.lower() == '.png' and path.is_file():
                        selected = path
                        answer.update(image_id=record['completion_id'],
                                      image_age_seconds=max(0, time.time() - path.stat().st_mtime),
                                      image_completed_at=record['completed_at'])
            answer.update(status='Available', detail='Latest saved loot session; retained image may be older.',
                          records=records)
            return answer, selected
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        answer.update(status='Unavailable', detail=str(exc), image_id=None)
        return answer, None


def handler_for(root):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            route = urlsplit(self.path).path
            body, content_type, status = b'Not found', 'text/plain', 404
            if route == '/':
                body, content_type, status = PAGE.encode('utf-8'), 'text/html; charset=utf-8', 200
            elif route == '/snapshot':
                snapshot, _ = read_snapshot(root)
                body = json.dumps(snapshot).encode('utf-8')
                content_type, status = 'application/json', 200
            elif route.startswith('/image/'):
                snapshot, path = read_snapshot(root)
                if path and unquote(route[7:]) == snapshot.get('image_id'):
                    try:
                        body, content_type, status = path.read_bytes(), 'image/png', 200
                    except OSError:
                        body, status = b'Saved image unavailable', 503
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    with HTTPServer(('127.0.0.1', args.port), handler_for(args.root)) as server:
        print(f'Saved-results viewer: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever(poll_interval=.5)


if __name__ == '__main__':
    main()
