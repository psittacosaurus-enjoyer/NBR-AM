"""Scoped stylesheet discovery and cross-category original-byte deduplication."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from crawler import fetch


class StylesheetDiscoveryTests(unittest.TestCase):
    def test_stylesheets_include_bootstrap_and_named_naruto_module_only(self):
        html='<link crossorigin href="/assets/index-main.css" rel="stylesheet"><link rel="icon" href="/assets/icon.png"><link rel="stylesheet" href="https://other.example/app.css">'
        modules=[(fetch.ORIGIN+'/assets/index-main.js','["assets/naruto-blazing-game.css","assets/another-game.css","assets/naruto-blazing-profile.css"]')]
        self.assertEqual(fetch.Collector.stylesheet_urls(html,modules),[
            fetch.ORIGIN+'/assets/index-main.css',fetch.ORIGIN+'/assets/naruto-blazing-game.css'])

    def test_css_only_collects_scoped_naruto_image_references(self):
        c=fetch.Collector.__new__(fetch.Collector)
        images=c.dynamic_images(['.one{background:url("/naruto-blazing/ui/panel.webp")} .two{background:url(/another-game/image.webp)}'],[])
        self.assertIn('/naruto-blazing/ui/panel.webp',images)
        self.assertNotIn('/another-game/image.webp',images)

    def test_concurrent_categories_and_restart_share_one_original_file(self):
        output=io.BytesIO(); Image.new('RGB',(24,24),(1,25,240)).save(output,'PNG'); data=output.getvalue()
        with tempfile.TemporaryDirectory(prefix='nbr-crawl-dedup-test-') as temporary,patch.object(fetch,'ROOT',Path(temporary)):
            c=fetch.Collector()
            try:
                with patch.object(fetch,'request',return_value=(200,{'content-type':'image/png'},data)):
                    entries=c.run_batch([(fetch.HOME+'/ninjas/a/card.png','image'),(fetch.HOME+'/missions/drop.png','image')])
                self.assertEqual(entries[0]['path'],entries[1]['path'])
                self.assertEqual(len([p for p in Path(temporary).glob('assets/*/*.png') if p.parent.name!='thumbnails']),1)
            finally: c.db.close()
            resumed=fetch.Collector()
            try:
                with patch.object(fetch,'request',return_value=(200,{'content-type':'image/png'},data)):
                    third=resumed.fetch((fetch.HOME+'/ui/icon.png','image'))
                self.assertEqual(third['path'],entries[0]['path'])
                self.assertEqual(len([p for p in Path(temporary).glob('assets/*/*.png') if p.parent.name!='thumbnails']),1)
            finally: resumed.db.close()


if __name__=='__main__': unittest.main()
