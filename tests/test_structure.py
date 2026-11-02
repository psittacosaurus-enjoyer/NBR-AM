"""Offline structure checks: canonical imports, entry points and saved assets."""
from __future__ import annotations

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
CANONICAL_MODULES = (
    "automation.controller", "automation.adb", "app.gui.window", "app.gui.theme",
    "app.gui.art", "app.gui.loot", "app.backend.controller", "app.backend.loot",
    "app.core.process_state",
)


class StructureTests(unittest.TestCase):
    def python(self, arguments, *, root_first=False, source_only=False):
        """A fresh process and foreign cwd expose hidden import/path assumptions."""
        environment = os.environ.copy()
        paths = ((SOURCE,) if source_only else
                 (ROOT, SOURCE) if root_first else (SOURCE, ROOT))
        environment["PYTHONPATH"] = os.pathsep.join(map(str, paths))
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["QT_QPA_PLATFORM"] = "offscreen"
        with tempfile.TemporaryDirectory(prefix="nbr-structure-") as directory:
            return subprocess.run(
                [sys.executable, "-B", *arguments], cwd=directory,
                env=environment, capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def test_canonical_modules_import_from_source_only(self):
        result = self.python(["-c", """
import importlib
import json
import sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
print('canonical modules import without root entry points')
""", json.dumps(CANONICAL_MODULES)], source_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("canonical modules import without root entry points", result.stdout)

    def test_root_entry_point_imports_share_implementations(self):
        # With the repository first, these imports exercise the root entry points.
        result = self.python(["-c", """
import farm
import manager_gui
from automation import controller
from app.gui import window
assert farm is controller
assert manager_gui is window
print('root entry points share implementations')
"""], root_first=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("root entry points share implementations", result.stdout)

    def test_canonical_imports_can_precede_root_entry_points(self):
        result = self.python(["-c", """
from automation import controller
from app.gui import window
import farm
import manager_gui
assert farm is controller
assert manager_gui is window
print('canonical imports share entry point implementations')
"""], root_first=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("canonical imports share entry point implementations", result.stdout)

    def test_project_and_manager_assets_resolve_from_foreign_cwd(self):
        result = self.python(["-c", """
import json
from automation import controller as farm
from app.gui import theme
print(json.dumps({'root': str(farm.ROOT), 'runtime': str(farm.RUNTIME),
                  'catalog': str(farm.CATALOG), 'style': theme.RETRO_STYLE}))
"""], source_only=True)
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

    def test_root_and_source_controller_help_launch_from_foreign_cwd(self):
        # argparse exits for --help before selecting a target or reading live state.
        for script in (ROOT / "farm.py", SOURCE / "farm.py"):
            with self.subTest(script=script):
                result = self.python([str(script), "--help"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("usage:", result.stdout)
                self.assertIn("--stage", result.stdout)
                self.assertIn("resolve-missing-evidence", result.stdout)

    def test_manager_entry_point_preserves_main_exit_status(self):
        from app.gui import window

        with patch.object(window, "main", return_value=7) as main:
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / "manager_gui.py"), run_name="__main__")
            self.assertEqual(stopped.exception.code, 7)
            main.assert_called_once_with()

    def test_catalogs_and_detector_resolve_saved_detection_assets(self):
        from automation import controller as farm

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
