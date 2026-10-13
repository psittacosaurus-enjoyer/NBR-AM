"""Local HTTP integration tests run on an ephemeral loopback port and isolated DB."""
import contextlib
import http.client
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library as L
import server


class BrowserAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-browser-test-')
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/library.sqlite3'
        connect = L.connect
        references = L.ReferenceLibrary
        with contextlib.closing(connect(self.path)) as db:
            L.initialize(db)
            self.rid, _ = L.upsert_record(db, record_type='item', source_key='scroll',
                display_name='Heart Scroll', source_url='https://gachawiki.net/naruto-blazing',
                category='reward', element='Heart', game_id='123')
            L.index(db)
            db.commit()
        browser = Path(__file__).resolve().parents[1] / 'browser'
        shutil.copytree(browser, self.root / 'browser')
        self.connections = []
        def fixture_connection(*args, **kwargs):
            if kwargs.get('readonly', False):
                db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
            else:
                db = sqlite3.connect(self.path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            self.connections.append(db)
            return db
        self.patches = [patch.object(L, 'ROOT', self.root),
                       patch.object(L, 'connect', side_effect=fixture_connection),
                       patch.object(L, 'ReferenceLibrary', side_effect=lambda *args, **kwargs: references(self.path))]
        for p in self.patches:
            p.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        for p in reversed(self.patches):
            p.stop()
        for db in self.connections:
            db.close()
        self.temp.cleanup()

    def request(self, path, method='GET', obj=None, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        headers = dict(headers or {})
        body = raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            conn.close()

    def test_visual_browser_and_javascript_are_served(self):
        status, headers, html = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(b'<html', html.lower())
        self.assertIn('Content-Security-Policy', headers)
        for file in (self.root / 'browser').glob('*.js'):
            with self.subTest(file=file.name):
                status, _, body = self.request('/browser/' + file.name)
                self.assertEqual(status, 200)
                self.assertEqual(body, file.read_bytes())

    def test_search_details_stats_and_empty_item_references(self):
        status, _, raw = self.request('/api/records?q=Heart&type=item&element=Heart&limit=1')
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['records'][0]['id'], self.rid)
        status, _, raw = self.request('/api/records/' + self.rid)
        self.assertEqual((status, json.loads(raw)['game_id']), (200, '123'))
        status, _, raw = self.request('/api/stats')
        self.assertEqual((status, json.loads(raw)['by_type']), (200, {'item': 1}))
        status, _, raw = self.request('/api/item-references')
        self.assertEqual((status, json.loads(raw)), (200, []))

    def test_manual_edits_persist_and_become_searchable(self):
        status, _, raw = self.request('/api/records/' + self.rid, 'PATCH',
            {'display_name': 'Verified Reward', 'notes': 'Blue screenshot',
             'tags': ['manual'], 'verification_status': 'manually_verified'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['display_name'], 'Verified Reward')
        status, _, raw = self.request('/api/records?q=Blue&status=manually_verified')
        self.assertEqual((status, json.loads(raw)['total']), (200, 1))
        status, _, raw = self.request('/api/records/' + self.rid)
        self.assertEqual(json.loads(raw)['notes'], 'Blue screenshot')

    def test_invalid_requests_and_missing_records_return_expected_errors(self):
        for path, expected in (('/api/records/missing', 404), ('/api/unknown', 404),
                               ('/api/records?page=invalid', 400), ('/assets/../../outside', 404)):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], expected)
        self.assertEqual(self.request('/api/records/' + self.rid, 'PATCH', {'tags': 'bad'})[0], 400)
        self.assertEqual(self.request('/api/records/' + self.rid, 'PATCH', raw=b'{broken')[0], 400)
        self.assertEqual(self.request('/api/records/missing', 'PATCH', {'notes': 'missing'})[0], 404)

    def test_remote_host_and_cross_origin_changes_are_rejected(self):
        self.assertEqual(self.request('/api/stats', headers={'Host': 'remote.example'})[0], 403)
        self.assertEqual(self.request('/api/records/' + self.rid, 'PATCH', {'notes': 'should fail'},
                                      headers={'Origin': 'https://remote.example'})[0], 403)
        status, _, raw = self.request('/api/records/' + self.rid)
        self.assertEqual((status, json.loads(raw)['notes']), (200, ''))


if __name__ == '__main__':
    unittest.main()
