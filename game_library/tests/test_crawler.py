"""Offline fixtures exercise the collector without touching the website."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import fetch


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-crawl-test-')
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(fetch, 'ROOT', self.root)
        self.root_patch.start()
        self.collectors = []
        output = io.BytesIO()
        Image.new('RGBA', (32, 24), (50, 100, 200, 255)).save(output, format='PNG')
        self.png = output.getvalue()
        self.url = fetch.ORIGIN + '/naruto-blazing/items/scroll.png'

    def tearDown(self):
        for c in self.collectors:
            c.db.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def collector(self, **kwargs):
        c = fetch.Collector(**kwargs)
        self.collectors.append(c)
        return c

    def fetch_image(self, c, url=None):
        with patch.object(fetch, 'request', return_value=(200, {'content-type': 'image/png', 'etag': 'v1'}, self.png)) as request:
            result = c.fetch((url or self.url, 'image'))
        return result, request

    def test_url_scope_and_fragment_normalization(self):
        self.assertEqual(fetch.normalize_url('/naruto-blazing/ninjas/123#detail'),
                         fetch.ORIGIN + '/naruto-blazing/ninjas/123')
        self.assertEqual(fetch.normalize_url('units?page=2', fetch.HOME + '/missions/'),
                         fetch.HOME + '/missions/units?page=2')
        for url in ('http://gachawiki.net/naruto-blazing', 'https://example.org/naruto-blazing',
                    'https://user:password@gachawiki.net/naruto-blazing',
                    '/another-game/ninjas', '/data/another-game/items.json',
                    '/naruto-blazing-other-game/ninjas'):
            with self.subTest(url=url):
                self.assertIsNone(fetch.normalize_url(url))

    def test_discovery_batches_dedup_urls_and_preserve_pagination(self):
        c = self.collector()
        urls = [(fetch.HOME + '/ninjas?page=1#top', 'html'),
                (fetch.HOME + '/ninjas?page=1', 'html'),
                (fetch.HOME + '/ninjas?page=2', 'html'),
                ('https://example.org/naruto-blazing', 'html')]
        with patch.object(c, 'fetch', side_effect=lambda resource: resource) as run:
            result = c.run_batch(urls)
            second = c.run_batch(urls)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(result), 2)
        self.assertEqual(second, [])
        self.assertEqual({u for u, _ in result}, {fetch.HOME + '/ninjas?page=1', fetch.HOME + '/ninjas?page=2'})

    def test_logical_routes_exclude_account_and_template_routes(self):
        c = self.collector()
        for route in ('#/naruto-blazing/ninjas/123', '#/naruto-blazing/ninjas/123',
                      '#/naruto-blazing/profile', '#/naruto-blazing/ninjas/${id}', '#/other-game'):
            c.logical_page(route, fetch.HOME)
        self.assertEqual(list(c.logical), [fetch.ORIGIN + '/#/naruto-blazing/ninjas/123'])

    def test_structured_nested_source_image_discovery(self):
        c = self.collector()
        fixture = {'missions': [{'reward': {'icon': '/naruto-blazing/story/units/scroll.png'}}],
                   'ninjas': [{'id': 'n123', 'forms': [], 'stars': 6,
                               'affiliations': ['Hidden Leaf']}]}
        images = c.dynamic_images([], [fixture])
        self.assertIn('/naruto-blazing/story/units/scroll.png', images)
        self.assertIn('/naruto-blazing/cutins/n123.webp', images)
        self.assertIn('/naruto-blazing/affiliations/hidden-leaf.webp', images)
        self.assertIn('/naruto-blazing/rarity/6.webp', images)
        self.assertEqual(list(fetch.walk({'a': ['one', {'b': 'two'}]}))[-1], 'two')

    def test_image_download_preserves_original_and_builds_thumbnail(self):
        c = self.collector()
        entry, request = self.fetch_image(c)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(entry['status'], 'ok')
        self.assertEqual(entry['sha256'], hashlib.sha256(self.png).hexdigest())
        self.assertEqual((self.root / entry['path']).read_bytes(), self.png)
        self.assertEqual((entry['width'], entry['height']), (32, 24))
        self.assertEqual(len(entry['perceptual_hash']), 16)
        with Image.open(self.root / entry['thumbnail_path']) as thumbnail:
            self.assertLessEqual(max(thumbnail.size), 192)

    def test_interrupted_collector_resumes_cached_download_without_http(self):
        first = self.collector()
        entry, _ = self.fetch_image(first)
        first.db.close()
        self.collectors.remove(first)
        resumed = self.collector()
        with patch.object(fetch, 'request', side_effect=AssertionError('cached file downloaded twice')):
            saved = resumed.fetch((self.url, 'image'))
        self.assertEqual(saved['sha256'], entry['sha256'])
        self.assertEqual(resumed.stats['skipped'], 1)

    def test_incremental_update_sends_etag_and_reuses_304(self):
        first = self.collector()
        entry, _ = self.fetch_image(first)
        updated = self.collector(update=True)
        with patch.object(fetch, 'request', return_value=(304, {}, b'')) as request:
            saved = updated.fetch((self.url, 'image'))
        self.assertEqual(request.call_args.args[1]['If-None-Match'], 'v1')
        self.assertEqual(saved['path'], entry['path'])
        self.assertEqual(saved['sha256'], entry['sha256'])
        self.assertEqual(saved['http_status'], 304)
        self.assertEqual(updated.stats['unchanged'], 1)

    def test_missing_cached_download_is_retrieved(self):
        c = self.collector()
        entry, _ = self.fetch_image(c)
        (self.root / entry['path']).unlink()
        repaired, request = self.fetch_image(c)
        self.assertEqual(request.call_count, 1)
        self.assertTrue((self.root / repaired['path']).exists())

    def test_exact_duplicate_urls_share_original_and_thumbnail(self):
        c = self.collector()
        one, _ = self.fetch_image(c)
        two, _ = self.fetch_image(c, fetch.HOME + '/items/another-name.png')
        self.assertEqual(one['path'], two['path'])
        self.assertEqual(one['thumbnail_path'], two['thumbnail_path'])
        c.snapshot()
        report = json.loads((self.root / 'reports/crawl.json').read_text())
        manifest = json.loads((self.root / 'data/manifest.json').read_text())
        self.assertEqual((report['exact_duplicate_urls'], report['unique_original_images']), (1, 1))
        self.assertEqual(sum('duplicate_of' in row for row in manifest['resources']), 1)

    def test_network_failures_are_bounded_and_persist_for_resume(self):
        c = self.collector()
        with patch.object(fetch, 'request', side_effect=OSError('offline')) as request, patch.object(fetch.time, 'sleep'):
            failed = c.fetch((self.url, 'image'))
        self.assertEqual(request.call_count, 3)
        self.assertEqual((failed['status'], failed['attempts']), ('failed', 3))
        resumed = self.collector()
        success, request = self.fetch_image(resumed)
        self.assertEqual(success['status'], 'ok')
        self.assertEqual(request.call_count, 1)

    def test_rate_limited_request_retries_then_succeeds(self):
        c = self.collector()
        responses = [(429, {'retry-after': '999'}, b''), (200, {'content-type': 'image/png'}, self.png)]
        with patch.object(fetch, 'request', side_effect=responses) as request, patch.object(fetch.time, 'sleep') as sleep:
            result = c.fetch((self.url, 'image'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_args.args[0], 30)

    def test_restricted_or_external_redirects_are_recorded_without_bypass(self):
        for response, expected in (((403, {}, b'private'), 'access restricted'),
                                   ((302, {'location': 'https://other.example/image.png'}, b''), 'outside authorized scope')):
            with self.subTest(response=response):
                c = self.collector()
                with patch.object(fetch, 'request', return_value=response) as request:
                    result = c.fetch((self.url, 'image'))
                self.assertEqual(result['status'], 'failed')
                self.assertIn(expected, result['error'])
                self.assertEqual(request.call_count, 1)

    def test_malformed_json_and_nonimage_responses_do_not_become_verified_resources(self):
        c = self.collector()
        with patch.object(fetch, 'request', return_value=(200, {'content-type': 'application/json'}, b'{broken')) as request, patch.object(fetch.time, 'sleep'):
            result = c.fetch((fetch.ORIGIN + '/data/naruto-blazing/broken.json', 'json'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(request.call_count, 3)
        with patch.object(fetch, 'request', return_value=(200, {'content-type': 'text/html'}, b'<h1>Error</h1>')):
            result = c.fetch((self.url, 'image'))
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('path', result)


if __name__ == '__main__':
    unittest.main()
