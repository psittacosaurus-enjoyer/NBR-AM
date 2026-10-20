"""Run focused offline regressions without launching the manager or game.

Examples:
    .venv/Scripts/python.exe -B scripts/test_offline.py
    .venv/Scripts/python.exe -B scripts/test_offline.py --repeat 3 --shuffle-seed 42
    .venv/Scripts/python.exe -B scripts/test_offline.py --suite controller
    .venv/Scripts/python.exe -B scripts/test_offline.py test_ninja_road.NinjaRoadTests
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        super().startTest(test)
        self._started = time.perf_counter()

    def stopTest(self, test):
        self.timings.append((time.perf_counter() - self._started, test.id()))
        super().stopTest(test)


def load_suite(suite_name, names):
    loader = unittest.TestLoader()
    if names:
        return loader.loadTestsFromNames(names)
    if suite_name == "ninja":
        return loader.loadTestsFromNames([
            "test_fixture_images", "test_ninja_detection", "test_ninja_road", "test_ninja_boss_swap",
        ])
    directories = {
        "controller": [ROOT / "tests"],
        "library": [ROOT / "tests/game_library"],
        "recognition": [ROOT / "tests/item_recognition"],
        "all": [ROOT / "tests", ROOT / "tests/game_library", ROOT / "tests/item_recognition"],
    }
    # Each directory has its own discovery root, as in the documented commands.
    return unittest.TestSuite(
        unittest.TestLoader().discover(str(path), pattern="test_*.py")
        for path in directories[suite_name]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tests", nargs="*", help="optional dotted unittest modules, classes or methods")
    parser.add_argument("--suite", choices=("ninja", "controller", "library", "recognition", "all"), default="ninja")
    parser.add_argument("--repeat", type=positive_integer, default=1)
    parser.add_argument("--shuffle-seed", type=int, help="reproducibly reorder tests; each repeat adds 1 to the seed")
    parser.add_argument("--slowest", type=positive_integer, default=5, help="show this many slowest tests")
    parser.add_argument("--cv-threads", type=positive_integer, default=1, help="OpenCV thread limit for this test process")
    parser.add_argument("--failfast", action="store_true")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true")
    verbosity.add_argument("-v", "--verbose", action="store_true")
    arguments = parser.parse_args(argv)

    sys.dont_write_bytecode = True
    for path in (ROOT, ROOT / "src", ROOT / "tests"):
        sys.path.insert(0, str(path))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import cv2

    # Tiny template comparisons spend more time scheduling large OpenCV pools
    # than computing. Keep this local to offline tests and avoid parallel suites.
    timings = []
    started = time.perf_counter()
    total = 0
    for repeat in range(arguments.repeat):
        suite = load_suite(arguments.suite, arguments.tests)
        # Controller imports set their own default; honor the test override
        # after all selected modules have been imported.
        cv2.setNumThreads(arguments.cv_threads)
        if arguments.shuffle_seed is not None:
            seed = arguments.shuffle_seed + repeat
            ordered = list(cases(suite))
            random.Random(seed).shuffle(ordered)
            suite = unittest.TestSuite(ordered)
            print(f"Shuffle seed: {seed}", file=sys.stderr, flush=True)
        count = suite.countTestCases()
        if not count:
            parser.error("no tests selected")
        print(f"Offline run {repeat + 1}/{arguments.repeat}: {count} tests, OpenCV threads={cv2.getNumThreads()}",
              file=sys.stderr, flush=True)
        result = unittest.TextTestRunner(
            resultclass=TimedResult,
            verbosity=0 if arguments.quiet else 2 if arguments.verbose else 1,
            failfast=arguments.failfast,
        ).run(suite)
        timings.extend(result.timings)
        total += result.testsRun
        if not result.wasSuccessful():
            return 1
    print(f"\n{total} tests passed in {time.perf_counter() - started:.3f}s", file=sys.stderr)
    print("Slowest tests (including setup and cleanup):", file=sys.stderr)
    for duration, name in sorted(timings, reverse=True)[:arguments.slowest]:
        print(f"  {duration:.3f}s  {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
