"""Incremental source preservation regressions; all network requests are offline fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from crawler import fetch


class IncrementalPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nbr-crawl-update-test-')
        self.root=Path(self.temp.name)
        self.patch=patch.object(fetch,'ROOT',self.root); self.patch.start()
        self.collectors=[]
        self.url=fetch.ORIGIN+'/data/naruto-blazing/ninjas.json'
        self.initial=b'{"ninjas":[{"id":"known","name":"Original"}]}'

    def tearDown(self):
        for c in self.collectors: c.db.close()
        self.patch.stop(); self.temp.cleanup()

    def collector(self,**kwargs):
        c=fetch.Collector(**kwargs); self.collectors.append(c); return c

    def original(self):
        c=self.collector()
        with patch.object(fetch,'request',return_value=(200,{'content-type':'application/json','etag':'old-tag','last-modified':'Mon, 12 Oct 2026 20:00:00 GMT'},self.initial)):
            return c.fetch((self.url,'json'))

    def test_cached_json_survives_failed_update_and_reports_error(self):
        old=self.original(); c=self.collector(update=True)
        with patch.object(fetch,'request',side_effect=OSError('offline')) as request,patch.object(fetch.time,'sleep'):
            saved=c.fetch((self.url,'json'))
        self.assertEqual(request.call_count,3)
        self.assertEqual(saved['status'],'ok')
        self.assertEqual(saved['path'],old['path'])
        self.assertEqual(saved['sha256'],old['sha256'])
        self.assertEqual((self.root/saved['path']).read_bytes(),self.initial)
        self.assertTrue(saved['update_failed'])
        self.assertEqual(saved['update_error'],'offline')
        self.assertEqual(saved['last_update_attempt']['attempts'],3)
        c.snapshot()
        report=json.loads((self.root/'reports/crawl.json').read_text())
        self.assertEqual(report['resource_counts']['json_ok'],1)
        self.assertEqual(report['failures'][0]['error'],'offline')
        self.assertTrue(report['failures'][0]['cached_available'])
        resumed=self.collector()
        self.assertEqual(resumed.entries[self.url]['status'],'ok')
        with patch.object(fetch,'request',return_value=(304,{},b'')) as request:
            recovered=resumed.fetch((self.url,'json'))
        self.assertEqual(request.call_count,1)
        self.assertNotIn('update_error',recovered)
        self.assertNotIn('update_failed',recovered)

    def test_changed_raw_sources_archive_original_bytes_and_keep_version_history(self):
        old=self.original(); c=self.collector(update=True)
        replacement=b'{"ninjas":[{"id":"known","name":"Updated"}]}'
        with patch.object(fetch,'request',return_value=(200,{'content-type':'application/json','etag':'new-tag'},replacement)):
            updated=c.fetch((self.url,'json'))
        self.assertEqual(updated['path'],old['path'])
        self.assertEqual((self.root/updated['path']).read_bytes(),replacement)
        historical=self.root/'data/history'/f'{hashlib.sha256(self.initial).hexdigest()}.json'
        self.assertEqual(historical.read_bytes(),self.initial)
        self.assertEqual(updated['previous_versions'][0]['path'],historical.relative_to(self.root).as_posix())
        with patch.object(fetch,'request',return_value=(200,{'content-type':'application/json'},replacement)):
            unchanged=c.fetch((self.url,'json'))
        self.assertEqual(len(unchanged['previous_versions']),1)
        script_url=fetch.ORIGIN+'/assets/NinjaCollection.js'
        first=b'const original = 1;'
        with patch.object(fetch,'request',return_value=(200,{'content-type':'text/javascript'},first)):
            c.fetch((script_url,'script'))
        with patch.object(fetch,'request',return_value=(200,{'content-type':'text/javascript'},b'const updated = 2;')):
            script=c.fetch((script_url,'script'))
        self.assertEqual((self.root/script['previous_versions'][0]['path']).read_bytes(),first)

    def test_missing_cached_file_never_sends_validators(self):
        old=self.original(); (self.root/old['path']).unlink()
        c=self.collector(update=True)
        with patch.object(fetch,'request',return_value=(200,{'content-type':'application/json'},self.initial)) as request:
            repaired=c.fetch((self.url,'json'))
        headers=request.call_args.args[1]
        self.assertNotIn('If-None-Match',headers)
        self.assertNotIn('If-Modified-Since',headers)
        self.assertEqual(repaired['status'],'ok')
        self.assertEqual((self.root/repaired['path']).read_bytes(),self.initial)


if __name__=='__main__': unittest.main()
