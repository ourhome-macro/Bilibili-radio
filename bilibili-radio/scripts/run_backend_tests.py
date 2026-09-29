"""Run backend tests with a temporary default database; never use listener data."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


def main() -> int:
    backend = Path(__file__).resolve().parents[1] / "py-radio"
    sys.path.insert(0, str(backend))
    with tempfile.TemporaryDirectory(prefix="bilibili-radio-tests-") as data_dir:
        os.environ.update(
            APP_DATA_DIR=data_dir,
            APP_RUNTIME="desktop",
            AUTH_MODE="disabled",
            ALLOW_INSECURE_LOCAL_AUTH="",
        )
        os.environ.pop("SESSION_COOKIE_SECURE", None)
        # Freeze database.DEFAULT_DB_PATH in the temporary directory before tests
        # inspect data-directory resolution with their own environment settings.
        import app  # noqa: F401

        os.environ.pop("APP_DATA_DIR")
        suite = unittest.defaultTestLoader.discover(str(backend / "tests"))
        if suite.countTestCases() == 0:
            raise RuntimeError("No backend tests were discovered")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
