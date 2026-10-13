"""Batch regressions use synthetic saved images and an offline fake recognizer."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli import analyze_paths


class FakeRecognizer:
    """Tests processing bookkeeping; this does not demonstrate recognition accuracy."""
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def analyze_screenshot(self, path, debug_path=None):
        path = Path(path)
        self.calls.append(path)
        result = {'schema_version': 1, 'screenshot': str(path), 'stage': 'Synthetic Stage',
                  'screenshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'status': 'complete', 'rewards': [], 'unknown_rewards': [],
                  'warnings': [], 'totals': {}}
        try:
            with Image.open(path) as image:
                if self.fail_first and len(self.calls) == 1:
                    result.update(status='error', warnings=['Synthetic transient processing failure'])
                    return result
                if debug_path:
                    Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
                    image.convert('RGB').save(debug_path)
        except OSError:
            result.update(status='error', warnings=['Unreadable fixture image'])
            return result
        result.update(rewards=[{'item_id': 'synthetic-item', 'item_name': 'Synthetic Reward',
            'identity_status': 'matched', 'quantity_status': 'verified', 'quantity': 2}],
            totals={'synthetic-item': 2})
        return result


class BatchProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nbr-batch-test-')
        self.root = Path(self.temp.name)
        self.input = self.root / 'inputs'
        self.input.mkdir()
        self.output = self.root / 'outputs'
        self.first = self.input / 'first.png'
        self.second = self.input / 'second.png'
        Image.new('RGB', (64, 64), (35, 135, 220)).save(self.first)
        self.second.write_bytes(self.first.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_bytes_different_paths_are_processed_once_per_batch(self):
        recognizer = FakeRecognizer()
        before = {p: p.read_bytes() for p in (self.first, self.second)}
        results = analyze_paths([self.first, self.second], self.output, recognizer)
        self.assertEqual(len(recognizer.calls), 1)
        self.assertEqual([r['status'] for r in results], ['complete', 'duplicate'])
        self.assertEqual(results[1]['rewards'], [])
        self.assertEqual(results[1]['totals'], {})
        self.assertEqual(results[0]['screenshot_sha256'], results[1]['screenshot_sha256'])
        self.assertTrue(results[1]['warnings'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        manifest = json.loads((self.output / 'processing_manifest.json').read_text())
        self.assertEqual(len(manifest['processed']), 1)
        self.assertEqual(json.loads((self.output / 'batch_results.json').read_text()), results)

    def test_second_invocation_uses_persistent_hash_safeguard(self):
        first = FakeRecognizer()
        analyze_paths([self.first], self.output, first)
        second = FakeRecognizer()
        results = analyze_paths([self.second], self.output, second)
        self.assertEqual(second.calls, [])
        self.assertEqual(results[0]['status'], 'duplicate')
        self.assertEqual(results[0]['rewards'], [])
        self.assertEqual(results[0]['totals'], {})
        self.assertEqual(results[0]['duplicate_of'], str(self.first.resolve()))

    def test_force_reanalysis_still_deduplicates_within_batch(self):
        recognizer = FakeRecognizer()
        analyze_paths([self.first], self.output, recognizer)
        forced = analyze_paths([self.first, self.second], self.output, recognizer, force=True)
        self.assertEqual(len(recognizer.calls), 2)
        self.assertEqual([r['status'] for r in forced], ['complete', 'duplicate'])
        self.assertEqual(forced[1]['totals'], {})

    def test_changed_source_bytes_are_processed_as_new_content(self):
        recognizer = FakeRecognizer()
        before = analyze_paths([self.first], self.output, recognizer)[0]
        Image.new('RGB', (64, 64), (190, 25, 75)).save(self.first)
        after = analyze_paths([self.first], self.output, recognizer)[0]
        self.assertEqual(len(recognizer.calls), 2)
        self.assertNotEqual(before['screenshot_sha256'], after['screenshot_sha256'])
        self.assertEqual(after['status'], 'complete')
        self.assertEqual(len(json.loads((self.output / 'processing_manifest.json').read_text())['processed']), 2)

    def test_failed_processing_is_retryable_without_force(self):
        recognizer = FakeRecognizer(fail_first=True)
        failed = analyze_paths([self.first], self.output, recognizer)[0]
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['totals'], {})
        recovered = analyze_paths([self.first], self.output, recognizer)[0]
        self.assertEqual(recovered['status'], 'complete')
        self.assertEqual(len(recognizer.calls), 2)

    def test_corrupted_image_is_not_credited_and_can_be_repaired(self):
        corrupt = self.input / 'corrupted.png'
        corrupt.write_bytes(b'invalid image bytes')
        recognizer = FakeRecognizer()
        failed = analyze_paths([corrupt], self.output, recognizer)[0]
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['rewards'], [])
        self.assertEqual(failed['totals'], {})
        Image.new('RGB', (64, 64), 'green').save(corrupt)
        repaired = analyze_paths([corrupt], self.output, recognizer)[0]
        self.assertEqual(repaired['status'], 'complete')
        self.assertEqual(len(recognizer.calls), 2)

    def test_string_input_paths_cannot_be_overwritten_by_debug_output(self):
        self.output.mkdir()
        digest = hashlib.sha256(self.first.read_bytes()).hexdigest()
        collision = self.output / ('first-' + digest[:16] + '.debug.png')
        Image.new('RGB', (64, 64), 'red').save(collision)
        original = collision.read_bytes()
        with self.assertRaises(ValueError):
            analyze_paths([str(self.first), str(collision)], self.output, FakeRecognizer())
        self.assertEqual(collision.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
