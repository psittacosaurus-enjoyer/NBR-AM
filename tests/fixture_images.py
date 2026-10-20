"""Bounded decoding cache for saved screenshots; every caller owns its pixels.

Use this only for test inputs. Production template loading and hot reload checks
must continue to exercise the actual image reader.
"""
from collections import OrderedDict
from pathlib import Path

import cv2


class FixtureImages:
    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._images = OrderedDict()
        self._bytes = 0

    def load(self, path, flags=cv2.IMREAD_COLOR):
        path = Path(path).resolve()
        try:
            stat = path.stat()
        except OSError as exc:
            raise AssertionError(f"Cannot read image fixture: {path}") from exc
        key = (path, flags)
        signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        cached = self._images.pop(key, None)
        if cached is not None:
            self._bytes -= cached[1].nbytes
        if cached is not None and cached[0] == signature:
            image = cached[1]
        else:
            image = cv2.imread(str(path), flags)
            if image is None:
                raise AssertionError(f"Cannot decode image fixture: {path}")
            image.setflags(write=False)
        if image.nbytes <= self.max_bytes:
            while self._images and self._bytes + image.nbytes > self.max_bytes:
                _, (_, evicted) = self._images.popitem(last=False)
                self._bytes -= evicted.nbytes
            self._images[key] = (signature, image)
            self._bytes += image.nbytes
        return image.copy()

    def clear(self):
        self._images.clear()
        self._bytes = 0


_fixtures = FixtureImages()
load_fixture = _fixtures.load
