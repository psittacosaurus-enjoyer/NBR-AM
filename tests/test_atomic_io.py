"""Isolated snapshot persistence tests; no live runtime or device access."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core import atomic_io


def windows_error(code):
    error = PermissionError("fixture Windows contention")
    error.winerror = code
    return error


class AtomicIoFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nbr-atomic-io-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "snapshot.json"

    def tearDown(self):
        self.temporary.cleanup()


class AtomicJsonTests(AtomicIoFixture):
    def test_round_trip_uses_json_semantics_and_utf8(self):
        atomic_io.atomic_json(self.path, {"name": "奖励", "values": (1, 2)}, indent=2)
        self.assertEqual(atomic_io.read_json_file(self.path), {"name": "奖励", "values": [1, 2]})
        self.assertIn("奖励", self.path.read_text("utf-8"))
        self.assertIn("\n  ", self.path.read_text("utf-8"))
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_invalid_input_keeps_previous_snapshot(self):
        atomic_io.atomic_json(self.path, {"old": True})
        for invalid in (float("nan"), float("inf"), object()):
            with self.subTest(value=type(invalid).__name__):
                with self.assertRaises((ValueError, TypeError)):
                    atomic_io.atomic_json(self.path, invalid)
                self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})
                self.assertFalse(list(self.root.glob("*.tmp")))

    def test_fsync_or_validation_failure_keeps_previous_snapshot(self):
        atomic_io.atomic_json(self.path, {"old": True})
        with patch.object(atomic_io.os, "fsync", side_effect=OSError("fixture disk failure")):
            with self.assertRaisesRegex(OSError, "fixture disk failure"):
                atomic_io.atomic_json(self.path, {"new": True})
        with patch.object(atomic_io, "read_json_file", return_value={"wrong": True}):
            with self.assertRaisesRegex(OSError, "verification failed"):
                atomic_io.atomic_json(self.path, {"new": True})
        self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_transient_windows_contention_retries_then_publishes(self):
        atomic_io.atomic_json(self.path, {"old": True})
        original_replace = os.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                calls = 0
                def attempt_replace(source, destination):
                    nonlocal calls
                    calls += 1
                    if calls < 3:
                        raise windows_error(code)
                    return original_replace(source, destination)
                with (patch.object(atomic_io, "_WINDOWS", True),
                      patch.object(atomic_io.os, "replace", side_effect=attempt_replace) as replace,
                      patch.object(atomic_io.time, "sleep") as sleep):
                    temp = self.root / "replacement.tmp"
                    temp.write_text('{"new": true}', encoding="utf-8")
                    atomic_io.replace_atomic(temp, self.path)
                    self.assertEqual(replace.call_count, 3)
                    self.assertEqual(sleep.call_count, 2)
                    self.assertEqual(sleep.call_args.args, (.05,))
                self.assertEqual(atomic_io.read_json_file(self.path), {"new": True})

    def test_exhausted_windows_contention_keeps_destination_and_cleans_temp(self):
        atomic_io.atomic_json(self.path, {"old": True})
        error = windows_error(5)
        with (patch.object(atomic_io, "_WINDOWS", True),
              patch.object(atomic_io, "_open_binary_reader", side_effect=lambda path: Path(path).open("rb")),
              patch.object(atomic_io.os, "replace", side_effect=error) as replace,
              patch.object(atomic_io.time, "sleep") as sleep):
            with self.assertRaises(PermissionError) as raised:
                atomic_io.atomic_json(self.path, {"new": True})
        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 20)
        self.assertEqual(sleep.call_count, 19)
        self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_unrelated_or_nonwindows_errors_are_not_retried(self):
        errors = [(True, windows_error(112)), (True, PermissionError("plain access denial")),
                  (False, windows_error(5)), (True, FileNotFoundError("missing source"))]
        for windows, error in errors:
            with self.subTest(windows=windows, error=str(error)):
                with (patch.object(atomic_io, "_WINDOWS", windows),
                      patch.object(atomic_io.os, "replace", side_effect=error) as replace,
                      patch.object(atomic_io.time, "sleep") as sleep):
                    with self.assertRaises(type(error)) as raised:
                        atomic_io.replace_atomic("source", "destination")
                self.assertIs(raised.exception, error)
                replace.assert_called_once()
                sleep.assert_not_called()

    def test_cleanup_failure_does_not_mask_replace_failure(self):
        atomic_io.atomic_json(self.path, {"old": True})
        error = OSError("fixture replace failure")
        original_unlink = Path.unlink
        with (patch.object(atomic_io.os, "replace", side_effect=error),
              patch.object(Path, "unlink", side_effect=OSError("fixture cleanup failure"))):
            with self.assertRaisesRegex(OSError, "fixture replace failure") as raised:
                atomic_io.atomic_json(self.path, {"new": True})
        self.assertIs(raised.exception, error)
        self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})
        for temp in self.root.glob("*.tmp"):
            original_unlink(temp)

    def test_read_preserves_missing_and_malformed_errors(self):
        with self.assertRaises(FileNotFoundError):
            atomic_io.read_json_file(self.path)
        self.path.write_text("invalid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            atomic_io.read_json_file(self.path)
        with patch.object(atomic_io, "_open_binary_reader", side_effect=PermissionError("fixture access denied")):
            with self.assertRaises(PermissionError):
                atomic_io.read_json_file(self.path)

    def test_exclusive_temp_creation_keeps_a_colliding_file(self):
        atomic_io.atomic_json(self.path, {"old": True})
        collision = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.fixture.tmp")
        collision.write_text("owned by another writer", encoding="utf-8")
        with patch.object(atomic_io.uuid, "uuid4") as uuid4:
            uuid4.return_value.hex = "fixture"
            with self.assertRaises(FileExistsError):
                atomic_io.atomic_json(self.path, {"new": True})
        self.assertEqual(collision.read_text("utf-8"), "owned by another writer")
        self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})

    def test_simultaneous_writers_and_readers_see_complete_snapshots(self):
        atomic_io.atomic_json(self.path, {"writer": -1, "sequence": 0, "payload": "x" * 2048})
        start = threading.Barrier(6)
        done = threading.Event()

        def writer(number):
            start.wait(timeout=5)
            for sequence in range(30):
                atomic_io.atomic_json(self.path, {"writer": number, "sequence": sequence, "payload": "x" * 2048})

        def reader():
            start.wait(timeout=5)
            count = 0
            while not done.is_set() or count < 30:
                value = atomic_io.read_json_file(self.path)
                self.assertIn(value["writer"], range(-1, 4))
                self.assertIn(value["sequence"], range(30))
                self.assertEqual(value["payload"], "x" * 2048)
                count += 1

        with ThreadPoolExecutor(max_workers=6) as pool:
            readers = [pool.submit(reader) for _ in range(2)]
            writers = [pool.submit(writer, number) for number in range(4)]
            try:
                for future in writers:
                    future.result(timeout=15)
            finally:
                done.set()
            for future in readers:
                future.result(timeout=5)
        self.assertFalse(list(self.root.glob("*.tmp")))


@unittest.skipUnless(os.name == "nt", "requires real Windows file sharing")
class WindowsSharingTests(AtomicIoFixture):
    def open_without_delete_sharing(self):
        handle = atomic_io._create_file(str(self.path), 0x80000000, 0x1 | 0x2,
                                        None, 3, 0x80, None)
        if handle == atomic_io._INVALID_HANDLE:
            raise atomic_io.ctypes.WinError(atomic_io.ctypes.get_last_error())
        return handle

    def test_delete_sharing_reader_allows_snapshot_to_be_renamed(self):
        atomic_io.atomic_json(self.path, {"old": True})
        with atomic_io._open_binary_reader(self.path) as stream:
            os.replace(self.path, self.root / "previous.json")
            self.assertEqual(json.loads(stream.read().decode("utf-8")), {"old": True})
        self.assertEqual(atomic_io.read_json_file(self.root / "previous.json"), {"old": True})

    def test_real_conflicting_reader_releases_and_writer_recovers(self):
        atomic_io.atomic_json(self.path, {"old": True})
        handle = self.open_without_delete_sharing()
        blocked = threading.Event()
        original_replace = os.replace

        def observed_replace(source, destination):
            try:
                return original_replace(source, destination)
            except OSError:
                blocked.set()
                raise

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(atomic_io.os, "replace", side_effect=observed_replace):
                future = pool.submit(atomic_io.atomic_json, self.path, {"new": True})
                try:
                    self.assertTrue(blocked.wait(3), "writer never encountered the held handle")
                    self.assertEqual(json.loads(self.path.read_text("utf-8")), {"old": True})
                finally:
                    atomic_io._close_handle(handle)
                future.result(timeout=3)
        self.assertEqual(atomic_io.read_json_file(self.path), {"new": True})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_real_permanent_conflicting_reader_preserves_destination(self):
        atomic_io.atomic_json(self.path, {"old": True})
        handle = self.open_without_delete_sharing()
        try:
            with patch.object(atomic_io.time, "sleep") as sleep:
                with self.assertRaises(PermissionError) as raised:
                    atomic_io.atomic_json(self.path, {"new": True})
            self.assertIn(raised.exception.winerror, {5, 32, 33})
            self.assertEqual(sleep.call_count, 19)
            self.assertEqual(atomic_io.read_json_file(self.path), {"old": True})
            self.assertFalse(list(self.root.glob("*.tmp")))
        finally:
            atomic_io._close_handle(handle)


if __name__ == "__main__":
    unittest.main()
