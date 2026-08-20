import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adb_wrapper import ADBError, ADBWrapper
from database import Database
from file_indexer import FileIndexer


OLD_FILES = [
    ("old.txt", "/storage/emulated/0/old.txt", 3, None, 0, ".txt"),
]


class ADBCommandStatusTests(unittest.TestCase):
    def setUp(self):
        # A real existing executable satisfies ADBWrapper's constructor check;
        # subprocess.run is mocked in every test, so Python is never invoked as
        # though it were ADB.
        self.adb = ADBWrapper(adb_path=sys.executable)

    @patch("adb_wrapper.subprocess.run")
    def test_zero_exit_code_is_success_even_with_stderr_output(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["adb", "devices"], 0, "List of devices attached\n", "warning"
        )

        result = self.adb._run_command(["devices"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "warning")

    @patch("adb_wrapper.subprocess.run")
    def test_nonzero_exit_code_raises_adb_error(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["adb", "devices"], 1, "", "error: device offline"
        )

        with self.assertRaisesRegex(
            ADBError, r"exit code 1: error: device offline"
        ):
            self.adb._run_command(["devices"])

    @patch("adb_wrapper.subprocess.run")
    def test_shell_propagates_nonzero_exit_code(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["adb", "shell", "ls"], 2, "", "ls: inaccessible"
        )

        with self.assertRaisesRegex(ADBError, r"exit code 2"):
            self.adb.shell("ls /missing", device_serial="device-1")

    def test_device_specific_command_requires_an_explicit_serial(self):
        self.assertFalse(hasattr(self.adb, "current_device"))
        self.assertFalse(hasattr(self.adb, "select_device"))
        with self.assertRaises(TypeError):
            self.adb.shell("ls /sdcard")

    @patch("adb_wrapper.subprocess.run")
    def test_pull_uses_exit_code_instead_of_error_text(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                ["adb", "pull"], 0, "1 file pulled", "error-like filename"
            ),
            subprocess.CompletedProcess(
                ["adb", "pull"], 1, "", ""
            ),
        ]

        self.assertTrue(self.adb.pull_file(
            "/phone/good.txt",
            "good.txt",
            device_serial="device-1",
        ))
        self.assertFalse(self.adb.pull_file(
            "/phone/missing.txt",
            "missing.txt",
            device_serial="device-2",
        ))

        self.assertEqual(
            run.call_args_list[0].args[0][1:4],
            ["-s", "device-1", "pull"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0][1:4],
            ["-s", "device-2", "pull"],
        )

    @patch("adb_wrapper.subprocess.run")
    def test_delete_uses_remote_exit_code(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(["adb", "shell", "rm"], 0, "", ""),
            subprocess.CompletedProcess(
                ["adb", "shell", "rm"], 1, "", "Permission denied"
            ),
        ]

        self.assertTrue(self.adb.delete_file(
            "/phone/good.txt",
            device_serial="device-1",
        ))
        self.assertFalse(self.adb.delete_file(
            "/phone/protected.txt",
            device_serial="device-2",
        ))

        self.assertEqual(
            run.call_args_list[0].args[0][1:4],
            ["-s", "device-1", "shell"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0][1:4],
            ["-s", "device-2", "shell"],
        )

    @patch("adb_wrapper.subprocess.run")
    def test_failed_scan_preserves_the_existing_index(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["adb", "shell", "ls"], 1, "", "error: device offline"
        )

        with TemporaryDirectory() as temp_dir:
            db = Database(str(Path(temp_dir) / "files.db"))
            db.replace_device_index("device-1", OLD_FILES, model="Test Phone")
            indexer = FileIndexer(self.adb, db)

            with self.assertRaisesRegex(ADBError, r"device offline"):
                indexer.index_device_sync(
                    "device-1", paths=["/storage/emulated/0"]
                )

            self.assertEqual(db.get_file_count("device-1"), 1)
            self.assertEqual(
                [row["path"] for row in db.search("device-1", "old")],
                [OLD_FILES[0][1]],
            )


if __name__ == "__main__":
    unittest.main()
