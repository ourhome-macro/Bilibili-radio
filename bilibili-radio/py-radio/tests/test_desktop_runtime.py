from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from database import BASE_DIR, resolve_data_dir


class DesktopDataDirTests(unittest.TestCase):
    def test_web_runtime_keeps_backend_local_data_dir(self):
        with patch.dict(os.environ, {"APP_RUNTIME": "", "APP_DATA_DIR": ""}, clear=False):
            self.assertEqual(resolve_data_dir(), BASE_DIR / "data")

    def test_explicit_app_data_dir_wins(self):
        configured = r"C:\Users\listener\AppData\Roaming\Bilibili Radio\data"
        self.assertEqual(
            resolve_data_dir(runtime="desktop", configured_data_dir=configured),
            Path(configured),
        )

    def test_desktop_runtime_uses_windows_appdata_by_default(self):
        self.assertEqual(
            resolve_data_dir(runtime="desktop", appdata=r"C:\Users\listener\AppData\Roaming"),
            Path(r"C:\Users\listener\AppData\Roaming") / "Bilibili Radio" / "data",
        )


class DesktopDownloadsDirTests(unittest.TestCase):
    def test_explicit_downloads_dir_wins(self):
        import app as app_module

        configured = r"D:\RadioDownloads"
        with patch.dict(os.environ, {"DOWNLOADS_DIR": configured}, clear=False):
            self.assertEqual(app_module._downloads_dir(), Path(configured))

    def test_windows_known_downloads_dir_wins_over_userprofile(self):
        import app as app_module

        known_downloads = Path(r"D:\SystemDownloads")
        with (
            patch.dict(os.environ, {"DOWNLOADS_DIR": "", "USERPROFILE": r"C:\Users\listener"}, clear=False),
            patch.object(app_module.os, "name", "nt"),
            patch.object(app_module, "_windows_known_downloads_dir", return_value=known_downloads),
        ):
            self.assertEqual(app_module._downloads_dir(), known_downloads)

    def test_windows_downloads_dir_falls_back_to_userprofile(self):
        import app as app_module

        with (
            patch.dict(os.environ, {"DOWNLOADS_DIR": "", "USERPROFILE": r"C:\Users\listener"}, clear=False),
            patch.object(app_module.os, "name", "nt"),
            patch.object(app_module, "_windows_known_downloads_dir", return_value=None),
        ):
            self.assertEqual(app_module._downloads_dir(), Path(r"C:\Users\listener") / "Downloads")


class DesktopBindConfigTests(unittest.TestCase):
    def test_desktop_runtime_defaults_to_loopback(self):
        import app as app_module

        with patch.dict(os.environ, {"APP_RUNTIME": "desktop"}, clear=False):
            self.assertEqual(app_module.resolve_bind_host(auth_enabled=False), "127.0.0.1")

    def test_bind_port_rejects_invalid_values(self):
        import app as app_module

        with patch.dict(os.environ, {"APP_BIND_PORT": "70000"}, clear=False):
            with self.assertRaises(RuntimeError):
                app_module.resolve_bind_port()

    def test_disabled_auth_rejects_non_loopback_bind_without_acknowledgement(self):
        import app as app_module

        with patch.dict(os.environ, {"ALLOW_INSECURE_LOCAL_AUTH": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                app_module.enforce_loopback_binding("0.0.0.0", auth_enabled=False)


if __name__ == "__main__":
    unittest.main()
