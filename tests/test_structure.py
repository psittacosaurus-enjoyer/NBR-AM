"""Offline relocation checks: imports, launch compatibility and saved assets only."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
MODULE_ALIASES = (
    ("farm", "automation.controller"),
    ("bluestacks", "automation.adb"),
    ("manager_gui", "app.gui.window"),
    ("manager_theme", "app.gui.theme"),
    ("manager_art", "app.gui.art"),
    ("manager_loot", "app.gui.loot"),
    ("manager_backend", "app.backend.controller"),
    ("loot_tracking", "app.backend.loot"),
    ("process_state", "app.core.process_state"),
)


class StructureTests(unittest.TestCase):
    def python(self, arguments, *, root_first=False):
        """A fresh process and foreign cwd expose hidden import/path assumptions."""
        environment = os.environ.copy()
        paths = (ROOT, SOURCE) if root_first else (SOURCE, ROOT)
        environment["PYTHONPATH"] = os.pathsep.join(map(str, paths))
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["QT_QPA_PLATFORM"] = "offscreen"
        with tempfile.TemporaryDirectory(prefix="nbr-structure-") as directory:
            return subprocess.run(
                [sys.executable, "-B", *arguments], cwd=directory,
                env=environment, capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def test_legacy_imports_share_modules_and_patched_globals(self):
        for legacy_name, canonical_name in MODULE_ALIASES:
            with self.subTest(module=legacy_name):
                legacy = importlib.import_module(legacy_name)
                canonical = importlib.import_module(canonical_name)
                self.assertIs(legacy, canonical)
                marker = object()
                with patch.object(legacy, "_structure_test_marker", marker, create=True):
                    self.assertIs(canonical._structure_test_marker, marker)

    def test_root_entry_point_imports_share_implementations(self):
        # With the repository first, these imports exercise the root wrappers.
        result = self.python(["-c", """
import farm
import manager_gui
from automation import controller
from app.gui import window
assert farm is controller
assert manager_gui is window
print('root aliases share implementations')
"""], root_first=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("root aliases share implementations", result.stdout)

    def test_canonical_imports_can_precede_legacy_imports(self):
        result = self.python(["-c", """
import importlib
import json
import sys
for legacy_name, canonical_name in json.loads(sys.argv[1]):
    canonical = importlib.import_module(canonical_name)
    assert importlib.import_module(legacy_name) is canonical, legacy_name
print('canonical aliases share implementations')
""", json.dumps(MODULE_ALIASES)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("canonical aliases share implementations", result.stdout)

    def test_project_and_manager_assets_resolve_from_foreign_cwd(self):
        result = self.python(["-c", """
import json
import farm
import manager_theme
print(json.dumps({'root': str(farm.ROOT), 'runtime': str(farm.RUNTIME),
                  'catalog': str(farm.CATALOG), 'style': manager_theme.RETRO_STYLE}))
"""])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        resolved = json.loads(result.stdout)
        self.assertEqual(Path(resolved["root"]), ROOT)
        self.assertEqual(Path(resolved["runtime"]), ROOT / "runtime")
        self.assertTrue(Path(resolved["catalog"]).is_file())
        self.assertNotIn("@ASSET@", resolved["style"])
        assets = re.findall(r'url\([\"\']?([^\"\')]+)', resolved["style"])
        self.assertTrue(assets, "The manager stylesheet should resolve its arrow assets")
        for asset in assets:
            with self.subTest(asset=asset):
                path = Path(asset)
                self.assertTrue(path.is_absolute())
                self.assertTrue(path.is_relative_to(ROOT / "assets" / "manager"))
                self.assertTrue(path.is_file())

    def test_root_and_legacy_controller_help_launch_from_foreign_cwd(self):
        # argparse exits for --help before selecting a target or reading live state.
        for script in (ROOT / "farm.py", SOURCE / "farm.py"):
            with self.subTest(script=script):
                result = self.python([str(script), "--help"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("usage:", result.stdout)
                self.assertIn("--stage", result.stdout)
                self.assertIn("resolve-missing-evidence", result.stdout)

    def test_manager_entry_points_preserve_main_exit_status(self):
        from app.gui import window

        for script in (ROOT / "manager_gui.py", SOURCE / "manager_gui.py"):
            with self.subTest(script=script), \
                 patch.object(window, "main", return_value=7) as main:
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(stopped.exception.code, 7)
                main.assert_called_once_with()

    def test_catalogs_and_detector_resolve_saved_detection_assets(self):
        import farm

        catalogs = sorted((ROOT / "assets" / "patterns").glob("*.json"))
        self.assertTrue(catalogs)
        references = set()
        for catalog in catalogs:
            data = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertTrue(data["states"], catalog)
            for state in data["states"]:
                for anchor in state["anchors"]:
                    references.update((anchor["template"], *anchor.get("alternatives", ())))
        self.assertTrue(references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue((ROOT / reference).is_file())

        # Detector loads local catalog/images without constructing a device.
        with patch.object(farm, "_PROCESS_INSTANCE", None), \
             patch.object(farm, "Device", side_effect=AssertionError("No device access permitted")):
            for stage in farm.STAGE_IDS:
                with self.subTest(stage=stage):
                    detector = farm.Detector(stage)
                    self.assertTrue(detector.templates)
                    self.assertTrue(detector.catalog.is_file())
                    self.assertFalse(detector.changed())


if __name__ == "__main__":
    unittest.main()
