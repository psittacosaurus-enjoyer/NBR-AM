"""Loopback-only visual browser and annotation API; run with python server.py."""
import argparse
import json
import mimetypes
from pathlib import Path
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
if __package__:
    from . import library as L
else:
    import library as L

BROWSER_ROOT = Path(__file__).resolve().parents[2] / 'assets' / 'library_browser'

class Handler(BaseHTTPRequestHandler):
    def json(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(raw)

    def log_message(self,fmt,*args): pass

    def safe_request(self):
        # Reject DNS rebinding and cross-site annotation requests; never bind a public interface.
        host=self.headers.get('Host','').split(':')[0].lower()
        if host not in ('127.0.0.1','localhost'):
            self.json({'error':'Local host required'},403); return False
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
            self.json({'error':'Same origin required'},403); return False
        return True

    def do_GET(self):
        if not self.safe_request(): return
        u=urlsplit(self.path); path=unquote(u.path)
        try:
            if path.startswith('/api/'):
                with closing(L.connect(readonly=True)) as db:
                    if path=='/api/stats': return self.json(L.stats(db))
                    if path=='/api/records':
                        p={k:v[0] for k,v in parse_qs(u.query).items()}
                        return self.json(L.search(db,q=p.get('q',''),record_type=p.get('type',''),category=p.get('category',''),element=p.get('element',''),rarity=p.get('rarity',''),status=p.get('status',''),page=p.get('page',1),limit=p.get('limit',60)))
                    if path.startswith('/api/records/'):
                        r=L.get_record(db,path.rsplit('/',1)[1])
                        return self.json(r if r else {'error':'Record not found'},200 if r else 404)
                    if path=='/api/item-references':
                        with L.ReferenceLibrary() as refs:
                            return self.json(list(refs.item_references(parse_qs(u.query).get('manual',['0'])[0]=='1')))
                    return self.json({'error':'Not found'},404)
            if path in ('/','/browser','/browser/'): path='/browser/index.html'
            if not (path.startswith('/browser/') or path.startswith('/assets/')):
                return self.json({'error':'Not found'},404)
            base = BROWSER_ROOT if path.startswith('/browser/') else L.ROOT
            relative = path[len('/browser/'):] if path.startswith('/browser/') else path.lstrip('/')
            file=(base/relative).resolve()
            if not file.is_relative_to(base.resolve()) or not file.is_file():
                return self.json({'error':'Not found'},404)
            body=file.read_bytes(); self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(file)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(body))); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'")
            self.send_header('Cache-Control','public, max-age=3600' if path.startswith('/assets/') else 'no-cache')
            self.end_headers(); self.wfile.write(body)
        except (ValueError,TypeError) as e: self.json({'error':str(e)},400)
        except Exception as e: self.json({'error':str(e)},500)

    def do_PATCH(self):
        if not self.safe_request(): return
        path=urlsplit(self.path).path
        if not path.startswith('/api/records/'): return self.json({'error':'Not found'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>65536: return self.json({'error':'Invalid body size'},400)
            if not self.headers.get('Content-Type','').startswith('application/json'): return self.json({'error':'JSON body required'},400)
            changes=json.loads(self.rfile.read(length))
            if not isinstance(changes,dict): raise ValueError('JSON object required')
            with closing(L.connect()) as db: r=L.annotate(db,path.rsplit('/',1)[1],changes)
            self.json(r)
        except KeyError: self.json({'error':'Record not found'},404)
        except (ValueError,TypeError) as e: self.json({'error':str(e)},400)

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if not L.DB_PATH.is_file(): parser.error('Build the database first with build.py')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print('NB:R library: http://127.0.0.1:'+str(args.port),flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
