import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config
from database import Database
from version import __version__


class ReleaseReadinessTests(unittest.TestCase):
    def test_version_matches_first_release(self):
        self.assertEqual(__version__, "0.1.0")

    def test_windows_data_directory_uses_local_app_data(self):
        with patch.object(config.os, "name", "nt"), patch.dict(
            os.environ,
            {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"},
            clear=False,
        ):
            os.environ.pop("ANDROID_EVERYTHING_DATA_DIR", None)
            self.assertEqual(
                config.get_app_data_dir(),
                r"C:\Users\Test\AppData\Local\AndroidEverything",
            )

    def test_data_directory_override_is_supported(self):
        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"ANDROID_EVERYTHING_DATA_DIR": temp_dir},
            clear=False,
        ):
            self.assertEqual(config.get_app_data_dir(), os.path.abspath(temp_dir))

    def test_database_creates_its_parent_directory(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "nested" / "files.db"
            Database(str(db_path))
            self.assertTrue(db_path.is_file())


if __name__ == "__main__":
    unittest.main()
