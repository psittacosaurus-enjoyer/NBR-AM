"""Small offline fixtures for fingerprint-bound reviewed reward supplements."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageEnhance

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/item_recognition'))
sys.path.insert(0,str(ROOT/'src/game_library'))
import library as L
import recognizer as recognition
from recognizer import ItemRecognizer


def icon(color):
    image=Image.new('RGB',(72,72),(22,28,38)); draw=ImageDraw.Draw(image)
    draw.rectangle((6,7,65,65),fill=color,outline=(248,223,148),width=4)
    draw.polygon((36,12,59,37,36,60,13,37),fill=(234,211,177))
    draw.line((18,49,53,24),fill=(25,43,60),width=6)
    return image


class ReviewedRewardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nbr-reviewed-rewards-')
        self.root=Path(self.temp.name); self.path=self.root/'data/library.sqlite3'
        self.db=L.connect(self.path); L.initialize(self.db)
        self.heart=icon((184,49,35)); self.other=icon((45,160,105))
        self.canonical=self.reference('heart','Limit Break Crystal "Heart Crystal"',self.heart,
            '/missions/card-0947.webp',game_id='947',rarity='4',category='item')
        self.alias=self.reference('alias','Heart Crystal',self.heart,'/lb/card-0947.webp',category='crystal')
        self.unrelated=self.reference('other','Other Crystal',self.other,'/missions/card-0950.webp',game_id='950')
        self.db.commit(); self.db.close()
        self.calibration=self.root/'calibration.json'
        self.base={'schema_version':2,'threshold':.75,'margin':.04,'supported_item_ids':[]}
        self.write_calibration()
        self.supplement=self.root/'reviewed_rewards.json'
        self.reviewed={'schema_version':1,'library_sha256':self.digest(self.path),
            'base_calibration_sha256':self.digest(self.calibration),
            'supported_item_ids':[self.canonical],
            'canonical_groups':[{'canonical_item_id':self.canonical,'member_ids':[self.canonical,self.alias],
                'source_card_id':'947','allow_alternate_names':True,
                'evidence':{'basis':'Reviewed explicit source card 947 with compatible metadata'}}]}
        self.write_supplement()

    def tearDown(self): self.temp.cleanup()

    @staticmethod
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

    def reference(self,key,name,image,source,game_id=None,rarity=None,category='item'):
        rid,_=L.upsert_record(self.db,record_type='item',source_key=key,display_name=name,
            source_url='https://example.test/records/'+key,game_id=game_id,
            element='Heart',rarity=rarity,category=category)
        relative='assets/'+key+'.png'; target=self.root/relative; target.parent.mkdir(exist_ok=True)
        image.save(target); aid=L.stable_id('asset',key)
        self.db.execute('''INSERT INTO assets(id,source_image_url,original_filename,local_image_path,
            sha256,width,height,status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)''',
            (aid,'https://example.test'+source,target.name,relative,self.digest(target),72,72,'ok',L.now()))
        self.db.execute('INSERT INTO record_assets VALUES(?,?,?)',(rid,aid,'icon'))
        return rid

    def write_calibration(self): self.calibration.write_text(json.dumps(self.base),encoding='utf-8')
    def write_supplement(self): self.supplement.write_text(json.dumps(self.reviewed),encoding='utf-8')

    def recognizer(self,**kwargs):
        return ItemRecognizer(library_path=self.path,calibration_path=self.calibration,
            templates_dir=self.root/'templates',include_characters=False,
            reviewed_path=kwargs.pop('reviewed_path',self.supplement),**kwargs)

    def test_reviewed_duplicate_is_one_matched_identity_without_relaxing_gates(self):
        baseline=self.recognizer(reviewed_path=None).match_icon(self.heart)
        self.assertEqual(baseline['identity_status'],'ambiguous')
        recognized=self.recognizer(); result=recognized.match_icon(self.heart)
        self.assertEqual(result['identity_status'],'matched')
        self.assertEqual(result['item_id'],self.canonical)
        self.assertEqual(set(result['member_ids']),{self.canonical,self.alias})
        self.assertEqual(result['confidence']['threshold'],.75)
        self.assertEqual(result['confidence']['required_margin'],.04)
        self.assertEqual(recognized.reference_snapshot['reviewed_rewards_sha256'],self.digest(self.supplement))
        self.assertEqual(self.digest(self.calibration),self.reviewed['base_calibration_sha256'])

    def test_unrelated_requested_support_cannot_bypass_reviewed_group(self):
        self.reviewed['supported_item_ids'].append(self.unrelated); self.write_supplement()
        result=self.recognizer().match_icon(self.other)
        self.assertEqual(result['item_id'],self.unrelated)
        self.assertEqual(result['identity_status'],'candidate')
        self.assertNotEqual(result['item_id'],self.canonical)

    def test_stricter_alias_threshold_and_margin_are_preserved(self):
        self.base['per_identity_thresholds']={self.alias:{'threshold':.999,'margin':.065}}
        self.write_calibration(); self.reviewed['base_calibration_sha256']=self.digest(self.calibration)
        self.write_supplement()
        result=self.recognizer().match_icon(ImageEnhance.Brightness(self.heart).enhance(.6))
        self.assertEqual(result['identity_status'],'unknown')
        self.assertEqual(result['confidence']['threshold'],.999)
        self.assertEqual(result['confidence']['required_margin'],.065)

    def test_stale_library_or_calibration_fingerprint_is_ignored(self):
        for field in ('library_sha256','base_calibration_sha256'):
            with self.subTest(field=field):
                original=self.reviewed[field]; self.reviewed[field]='0'*64; self.write_supplement()
                recognized=self.recognizer()
                self.assertEqual(recognized.match_icon(self.heart)['identity_status'],'ambiguous')
                self.assertIsNone(recognized.reference_snapshot['reviewed_rewards_sha256'])
                self.reviewed[field]=original

    def test_no_base_calibration_prevents_supplemental_promotion(self):
        recognized=ItemRecognizer(library_path=self.path,calibration_path=None,
            templates_dir=self.root/'templates',include_characters=False,reviewed_path=self.supplement)
        self.assertEqual(recognized.match_icon(self.heart)['identity_status'],'ambiguous')
        self.assertEqual(recognized.threshold,.88)

    def test_malformed_or_unvalidated_declarations_are_ignored(self):
        for mutation in ('missing_member','wrong_source_card','duplicate_member','no_evidence','no_review'):
            with self.subTest(mutation=mutation):
                value=copy.deepcopy(self.reviewed); group=value['canonical_groups'][0]
                if mutation=='missing_member': group['member_ids'].append('absent')
                elif mutation=='wrong_source_card': group['source_card_id']='948'
                elif mutation=='duplicate_member': group['member_ids']=[self.canonical,self.canonical]
                elif mutation=='no_evidence': group['evidence']={}
                else: group['allow_alternate_names']=False
                self.supplement.write_text(json.dumps(value),encoding='utf-8')
                self.assertEqual(self.recognizer().match_icon(self.heart)['identity_status'],'ambiguous')
        for text in ('{broken','[]',json.dumps(dict(self.reviewed,canonical_groups='bad'))):
            with self.subTest(text=text):
                self.supplement.write_text(text,encoding='utf-8')
                self.assertEqual(self.recognizer().match_icon(self.heart)['identity_status'],'ambiguous')

    def test_existing_supported_identity_stays_matched(self):
        self.base['supported_item_ids']=[self.unrelated]; self.write_calibration()
        self.reviewed['base_calibration_sha256']=self.digest(self.calibration); self.write_supplement()
        self.assertEqual(self.recognizer().match_icon(self.other)['identity_status'],'matched')

    def test_auto_supplement_obeys_fingerprints(self):
        with patch.object(recognition,'DEFAULT_TEMPLATES',self.root):
            self.assertEqual(self.recognizer(reviewed_path='auto').match_icon(self.heart)['identity_status'],'matched')
            self.reviewed['base_calibration_sha256']='0'*64; self.write_supplement()
            self.assertEqual(self.recognizer(reviewed_path='auto').match_icon(self.heart)['identity_status'],'ambiguous')


if __name__=='__main__': unittest.main()
