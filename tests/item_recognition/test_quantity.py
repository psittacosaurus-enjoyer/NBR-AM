"""Saved screenshot verification and separately identified synthetic rejection tests."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[2] / 'src/item_recognition'
sys.path.insert(0,str(ROOT))
import quantity


class SavedQuantityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader=quantity.QuantityReader()
        cls.manifest=json.loads((quantity.DEFAULT_TEMPLATE_DIR/'manifest.json').read_text(encoding='utf8'))

    def test_all_manually_inspected_quantity_fields(self):
        for field in self.manifest['evidence']:
            if field['quantity'] is None: continue
            with self.subTest(field=field['id']):
                result=self.reader.read(quantity.PROJECT/field['source'],field['box'])
                self.assertEqual(result['quantity'],field['quantity'])
                self.assertEqual(result['quantity_status'],'verified')
                self.assertGreaterEqual(result['score'],quantity.MIN_SCORE)
                self.assertEqual(set(result),{'quantity','quantity_status','score','method','reason'})

    def test_groundtruth_provenance_matches_original_source_bytes(self):
        for field in self.manifest['evidence']:
            self.assertEqual(hashlib.sha256((quantity.PROJECT/field['source']).read_bytes()).hexdigest(),field['source_sha256'])
            self.assertEqual(field['label_status'],'manually_checked_saved_screenshot')
        self.assertEqual(self.manifest['unsupported_digits'],['8','9'])

    def test_crop_inputs_accept_pil_rgb_numpy_and_path(self):
        path=quantity.DEFAULT_TEMPLATE_DIR/'evidence-rich_reward_2.png'
        with Image.open(path) as im:
            rgb=im.convert('RGB')
            for image in [path,rgb,np.asarray(rgb)]:
                self.assertEqual(self.reader.read(image)['quantity'],500)

    def test_no_quantity_is_not_assumed_to_be_one(self):
        result=self.reader.read(Image.new('RGB',(100,40),'black'))
        self.assertIsNone(result['quantity'])
        self.assertEqual(result['quantity_status'],'unknown')

    def test_full_screenshot_requires_explicit_quantity_region(self):
        result=self.reader.read(quantity.PROJECT/'tests/fixtures/results_next.png')
        self.assertIsNone(result['quantity'])
        self.assertIn('localized',result['reason'])

    def test_truncated_or_out_of_bounds_quantity_remains_unknown(self):
        source=quantity.PROJECT/'tests/fixtures/rich_b/results_next.png'
        # Numeral glyph 1 starts at x573; this cuts through the last zero.
        for box in [[561,918,710,961],[-1,0,100,40],[0,0,901,40],[10,10,10,20]]:
            with self.subTest(box=box):
                result=self.reader.read(source,box)
                self.assertIsNone(result['quantity'])
                self.assertEqual(result['quantity_status'],'unknown')

    def test_corrupt_or_malformed_inputs_remain_unknown(self):
        cases=[ROOT/'does-not-exist.png',np.zeros((0,10,3),dtype=np.uint8),np.zeros((20,20),dtype=np.uint8),np.zeros((20,20,3),dtype=float),None]
        with tempfile.TemporaryDirectory() as directory:
            corrupt=Path(directory)/'corrupt.png';corrupt.write_bytes(b'not an image');cases.append(corrupt)
            for value in cases:
                with self.subTest(type=type(value).__name__):
                    self.assertEqual(self.reader.read(value)['quantity_status'],'unknown')

    def test_missing_and_checksum_invalid_templates_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            self.assertEqual(quantity.QuantityReader(path).read(Image.new('RGB',(50,30),'black'))['quantity_status'],'unknown')
            glyph=path/'bad.png';Image.new('L',(48,64),255).save(glyph)
            (path/'manifest.json').write_text(json.dumps({'glyphs':[{'path':'bad.png','sha256':'wrong','character':'0','profile':'test'}]}))
            self.assertFalse(quantity.QuantityReader(path).templates)


class SyntheticQuantityTests(unittest.TestCase):
    """Synthetic cases are software tests, never counted as real-screen accuracy."""
    def setUp(self): self.reader=quantity.QuantityReader()

    @staticmethod
    def rendered(text):
        image=Image.new('RGB',(220,52),(25,25,25))
        font=ImageFont.truetype('C:/Windows/Fonts/timesbd.ttf',30)
        ImageDraw.Draw(image).text((8,5),text,font=font,fill='white')
        return image

    def test_percent_nonquantity_and_unsupported_digits_are_rejected(self):
        for text in ['20%','NONE','2.5','8','9','x','100/200']:
            with self.subTest(text=text):
                result=self.reader.read(self.rendered(text))
                self.assertIsNone(result['quantity'])
                self.assertIn(result['quantity_status'],['unknown','ambiguous'])

    def test_altered_font_does_not_claim_saved_screen_accuracy(self):
        result=self.reader.read(self.rendered('20000'))
        self.assertIsNone(result['quantity'])

    def test_competing_digit_templates_report_ambiguity(self):
        # An intentionally impossible synthetic template set uses one bitmap for
        # two digit labels. The reader must not choose an arbitrary quantity.
        with Image.open(quantity.DEFAULT_TEMPLATE_DIR/'evidence-scroll_ryo.png') as im:
            parts=quantity._segments(quantity._white_mask(np.asarray(im.convert('RGB'))))
        original=parts[1]['mask']
        normalized=quantity._normalize(original)
        rgb=np.full((original.shape[0]+10,original.shape[1]+10,3),20,dtype=np.uint8)
        rgb[5:-5,5:-5][original.astype(bool)]=255
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'same.png';Image.fromarray(normalized*255).save(path)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            (root/'manifest.json').write_text(json.dumps({'glyphs':[{'path':'same.png','sha256':digest,'character':char,'profile':'synthetic'} for char in ['6','0']]}))
            result=quantity.QuantityReader(root).read(rgb)
        self.assertIsNone(result['quantity'])
        self.assertEqual(result['quantity_status'],'ambiguous')

    def test_disconnected_noise_is_not_a_quantity(self):
        image=np.zeros((40,100,3),dtype=np.uint8)
        image[10,10]=255;image[15,30]=255
        self.assertIsNone(self.reader.read(image)['quantity'])


if __name__=='__main__': unittest.main()
