import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_desktop_version import check_version


class DesktopVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.write_versions("1.2.3", "1.2.3", "1.2.3")

    def write_versions(self, tauri, cargo, lock):
        (self.path / "tauri.conf.json").write_text(json.dumps({"version": tauri}), encoding="utf-8")
        (self.path / "Cargo.toml").write_text(f'[package]\nname = "desktop"\nversion = "{cargo}"\n', encoding="utf-8")
        (self.path / "Cargo.lock").write_text(f'[[package]]\nname = "desktop"\nversion = "{lock}"\n', encoding="utf-8")

    def test_matching_release(self):
        self.assertEqual(check_version(self.path, "v1.2.3"), "1.2.3")

    def test_rejects_tag_that_would_publish_the_wrong_version(self):
        with self.assertRaises(ValueError):
            check_version(self.path, "v1.2.4")

    def test_rejects_stale_lockfile(self):
        self.write_versions("1.2.3", "1.2.3", "1.2.2")
        with self.assertRaises(ValueError):
            check_version(self.path)

    def test_rejects_stale_cargo_manifest(self):
        self.write_versions("1.2.3", "1.2.2", "1.2.3")
        with self.assertRaises(ValueError):
            check_version(self.path)

    def test_accepts_explicit_prerelease(self):
        self.write_versions("1.2.3-rc.1", "1.2.3-rc.1", "1.2.3-rc.1")
        self.assertEqual(check_version(self.path, "v1.2.3-rc.1"), "1.2.3-rc.1")

    def test_rejects_ambiguous_version(self):
        self.write_versions("01.2.3", "01.2.3", "01.2.3")
        with self.assertRaises(ValueError):
            check_version(self.path)
