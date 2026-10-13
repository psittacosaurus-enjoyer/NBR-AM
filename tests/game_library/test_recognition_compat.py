"""Fail-closed compatibility guards never analyze images or open a real library DB."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'src'))
sys.path.insert(0, str(PROJECT / 'src/game_library'))
import recognition_compat as compat


class RecognitionCompatibilityGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-compat-guard-test-')
        self.root = Path(self.temp.name)
        # These bytes are a fingerprint fixture, not a usable database.
        self.snapshot = self.root / 'before.sqlite3'
        self.snapshot.write_bytes(b'isolated snapshot fingerprint fixture')
        self.calibration_path = self.root / 'calibration.json'
        self.config = {'library_sha256': hashlib.sha256(self.snapshot.read_bytes()).hexdigest(),
                       'threshold': 0.88, 'margin': 0.04, 'supported_item_ids': ['item_fixture']}
        self.calibration_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.original = self.calibration_path.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_mismatch_rejects_before_recognizer_or_evidence_and_preserves_calibration(self):
        backup = self.root / 'backups/fandom-1.1-before/calibration.json'
        backup.parent.mkdir(parents=True)
        preserved = dict(self.config, library_sha256='0' * 64)
        backup.write_text(json.dumps(preserved), encoding='utf-8')
        preserved_bytes = backup.read_bytes()
        with patch.object(compat.L, 'ROOT', self.root), \
             patch.object(compat.cv2, 'setNumThreads'), \
             patch.object(compat, 'ItemRecognizer') as recognizer, \
             patch.object(compat, 'evidence_paths') as evidence:
            with self.assertRaisesRegex(ValueError, 'Snapshot does not match'):
                compat.check(self.snapshot, calibration_path=self.calibration_path, promote=True)
        recognizer.assert_not_called()
        evidence.assert_not_called()
        self.assertEqual(self.calibration_path.read_bytes(), self.original)
        self.assertEqual(backup.read_bytes(), preserved_bytes)
        self.assertFalse((self.root / 'reports/fandom_recognition/candidate_calibration.json').exists())

    def test_empty_evidence_rejects_without_recognizer_or_calibration_promotion(self):
        with patch.object(compat.L, 'ROOT', self.root), \
             patch.object(compat.cv2, 'setNumThreads'), \
             patch.object(compat, 'ItemRecognizer') as recognizer, \
             patch.object(compat, 'evidence_paths', return_value=[]) as evidence:
            with self.assertRaisesRegex(ValueError, 'No saved recognition evidence'):
                compat.check(self.snapshot, calibration_path=self.calibration_path, promote=True)
        evidence.assert_called_once()
        recognizer.assert_not_called()
        self.assertEqual(self.calibration_path.read_bytes(), self.original)
        self.assertFalse((self.root / 'reports/fandom_recognition/candidate_calibration.json').exists())

    def test_new_calibration_rejects_changed_human_labeled_source(self):
        screenshot=self.root/'reward.png';screenshot.write_bytes(b'changed screenshot')
        truth={'examples':[{'path':str(screenshot),'sha256':'0'*64,
            'screen_type':'acquired','split':'holdout','validation':'Human review',
            'rewards':[{'item_id':'item_new','identity_status':'manually_labeled'}]}]}
        (self.root/'ground_truth.json').write_text(json.dumps(truth),encoding='utf-8')
        fake=SimpleNamespace(supported_items=set())
        with patch.object(compat,'DEFAULT_TEMPLATES',self.root), \
             patch.object(compat,'ItemRecognizer',return_value=fake):
            with self.assertRaisesRegex(ValueError,'Ground-truth source changed'):
                compat.expand_labeled_calibration(self.calibration_path,promote=True)
        self.assertEqual(self.calibration_path.read_bytes(),self.original)

    def test_unverified_predictions_cannot_supply_calibration_labels(self):
        truth={'examples':[{'path':'missing.png','screen_type':'acquired',
            'rewards':[{'item_id':'item_new','identity_status':'candidate'}]}]}
        (self.root/'ground_truth.json').write_text(json.dumps(truth),encoding='utf-8')
        fake=SimpleNamespace(supported_items=set())
        with patch.object(compat,'DEFAULT_TEMPLATES',self.root), \
             patch.object(compat.L,'ROOT',self.root), \
             patch.object(compat,'ItemRecognizer',return_value=fake):
            report=compat.expand_labeled_calibration(self.calibration_path,promote=True)
        self.assertEqual(report['added_supported_item_ids'],[])
        self.assertFalse(report['calibration_promoted'])
        self.assertEqual(self.calibration_path.read_bytes(),self.original)

    def test_compatibility_recheck_preserves_later_reviewed_calibration(self):
        backup=self.root/'backups/fandom-1.1-before/calibration.json'
        backup.parent.mkdir(parents=True)
        backup.write_text(json.dumps(self.config),encoding='utf-8')
        current=dict(self.config,supported_item_ids=['item_fixture','item_reviewed_later'],
                     expanded_labeled_evidence=[{'label_basis':'Human review'}])
        self.calibration_path.write_text(json.dumps(current),encoding='utf-8')
        screenshot=self.root/'evidence.png';screenshot.write_bytes(b'fingerprint only')
        class Recognizer:
            def __init__(self,**kwargs): pass
            def _load_reference(self,record): pass
            def analyze_screenshot(self,path):
                return {'status':'ok','rewards':[{'bounding_box':[0,0,10,10],
                    'identity_status':'matched','item_id':'item_fixture','quantity':None}]}
            def reference_report(self): return {'errors':[]}
        with patch.object(compat.L,'ROOT',self.root), \
             patch.object(compat.L,'DB_PATH',self.snapshot), \
             patch.object(compat,'ItemRecognizer',Recognizer), \
             patch.object(compat,'evidence_paths',return_value=[screenshot]):
            compat.check(self.snapshot,self.calibration_path,promote=True)
            compat.check(self.snapshot,self.calibration_path,promote=True)
        promoted=json.loads(self.calibration_path.read_text('utf-8'))
        self.assertEqual(promoted['supported_item_ids'],current['supported_item_ids'])
        self.assertEqual(promoted['expanded_labeled_evidence'],current['expanded_labeled_evidence'])
        self.assertEqual(len(promoted['compatible_library_imports']),1)
        self.assertEqual(promoted['threshold'],self.config['threshold'])

    def test_byte_identical_screenshot_copies_are_counted_once_with_audit(self):
        first=self.root/'first.png';second=self.root/'second.png'
        first.write_bytes(b'same immutable evidence');second.write_bytes(first.read_bytes())
        paths,duplicates=compat.unique_screenshots([second,first,first])
        self.assertEqual(paths,[first])
        self.assertEqual(len(duplicates),1)
        self.assertEqual(duplicates[0]['duplicate_path'],str(second))

    def test_routine_validation_preserves_reviewed_calibration_and_excludes_consumed_holdout(self):
        from item_recognition import validate as validation
        screenshot=self.root/'evidence.png';screenshot.write_bytes(b'isolated screenshot fingerprint')
        config=dict(self.config,canonical_groups=[{'evidence':'Independent review'}],
            supported_character_ids=['character_reviewed'],
            expanded_labeled_evidence=[{'path':'evidence.png'}])
        self.calibration_path.write_text(json.dumps(config),encoding='utf-8')
        original=self.calibration_path.read_bytes()
        dataset={'examples':[{'path':'evidence.png','sha256':compat.file_hash(screenshot),
            'split':'holdout','screen_type':'not_results','rewards':[]}]}
        (self.root/'ground_truth.json').write_text(json.dumps(dataset),encoding='utf-8')
        quantity=self.root/'quantity';quantity.mkdir()
        (quantity/'manifest.json').write_text('{}',encoding='utf-8')
        recognizer=SimpleNamespace(db_path=self.snapshot,reference_report=lambda:{},
            analyze_screenshot=Mock(return_value={'screen_type':'not_results','status':'unsupported_screen',
                'rewards':[],'unknown_rewards':[]}))
        with patch.object(validation,'DEFAULT_TEMPLATES',self.root), \
             patch.object(validation,'PROJECT',self.root), \
             patch.object(validation,'ItemRecognizer',return_value=recognizer), \
             patch.object(validation,'calibrate') as calibrate, \
             patch.dict(sys.modules,{'quantity':SimpleNamespace(EVIDENCE=[])}), \
             patch('builtins.print'):
            report=validation.evaluate()
        calibrate.assert_not_called()
        self.assertEqual(self.calibration_path.read_bytes(),original)
        self.assertEqual(report['heldout_examples'].get('screenshots',0),0)
        self.assertTrue(report['per_screenshot'][0]['used_for_additive_calibration'])


if __name__ == '__main__':
    unittest.main()
