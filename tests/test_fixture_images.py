"""Fixture caching must not hide edited inputs or share mutable test pixels."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from fixture_images import FixtureImages


class FixtureImagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cache = FixtureImages(max_bytes=2 * 8 * 8 * 3)

    def image(self, name, value):
        path = self.directory / (name + ".png")
        self.assertTrue(cv2.imwrite(str(path), np.full((8, 8, 3), value, dtype=np.uint8)))
        return path

    def test_decode_is_reused_but_mutations_never_escape_the_caller(self):
        path = self.image("original", 32)
        with patch.object(cv2, "imread", wraps=cv2.imread) as read:
            first = self.cache.load(path)
            first[:] = 0
            second = self.cache.load(path)
            self.assertEqual(read.call_count, 1)
        self.assertTrue(second.flags.writeable)
        self.assertTrue(np.all(second == 32))
        self.assertFalse(np.shares_memory(first, second))

    def test_changed_fixture_is_decoded_again_and_deleted_fixture_fails(self):
        path = self.image("edited", 32)
        self.cache.load(path)
        previous = path.stat().st_mtime_ns
        self.image("edited", 64)
        os.utime(path, ns=(previous + 1_000_000_000, previous + 1_000_000_000))
        self.assertTrue(np.all(self.cache.load(path) == 64))
        path.unlink()
        with self.assertRaisesRegex(AssertionError, "Cannot read image fixture"):
            self.cache.load(path)

    def test_flags_have_distinct_entries_and_lru_evicts_within_byte_budget(self):
        first, second, third = (self.image(name, value) for name, value in (("one", 1), ("two", 2), ("three", 3)))
        with patch.object(cv2, "imread", wraps=cv2.imread) as read:
            self.cache.load(first)
            self.cache.load(second)
            self.cache.load(first)  # Keep the first input; evict the second.
            self.cache.load(third)
            self.cache.load(first)
            self.assertEqual(read.call_count, 3)
            self.cache.load(second)
            self.assertEqual(read.call_count, 4)
            self.assertEqual(self.cache.load(second, cv2.IMREAD_GRAYSCALE).shape, (8, 8))
            self.assertEqual(read.call_count, 5)
        self.assertLessEqual(self.cache._bytes, self.cache.max_bytes)

    def test_corrupt_and_oversized_images_do_not_create_cache_entries(self):
        corrupt = self.directory / "broken.png"
        corrupt.write_bytes(b"invalid image")
        with self.assertRaisesRegex(AssertionError, "Cannot decode image fixture"):
            self.cache.load(corrupt)
        cache = FixtureImages(max_bytes=1)
        path = self.image("large", 128)
        with patch.object(cv2, "imread", wraps=cv2.imread) as read:
            cache.load(path)
            cache.load(path)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(cache._bytes, 0)


if __name__ == "__main__":
    unittest.main()
