import os
import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from adb_wrapper import ADBWrapper
from path_utils import (
    available_download_path,
    cached_download_path,
    sanitize_windows_filename,
)
from ui.main_window import MainWindow


class ImmediateThread:
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class ImmediateRoot:
    def after(self, delay, callback):
        callback()


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class RemotePathHandlingTests(unittest.TestCase):
    def setUp(self):
        self.adb = ADBWrapper(adb_path=sys.executable)

    def test_delete_shell_quotes_android_path(self):
        remote_path = "/sdcard/quote' $(touch injected) ; report.txt"

        with patch.object(self.adb, "shell", return_value="") as shell:
            self.assertTrue(self.adb.delete_file(remote_path))

        shell.assert_called_once_with(
            f"rm -f {shlex.quote(remote_path)} 2>&1"
        )

    def test_delete_rejects_empty_and_relative_paths(self):
        with patch.object(self.adb, "shell", return_value="") as shell:
            self.assertFalse(self.adb.delete_file(""))
            self.assertFalse(self.adb.delete_file("--help"))

        shell.assert_not_called()

    def test_legacy_listing_helpers_quote_scan_paths(self):
        remote_path = "/sdcard/folder's reports"

        with patch.object(self.adb, "shell", return_value="") as shell:
            self.adb.list_files_fast(remote_path)
            self.adb.list_files_detailed(remote_path)

        fast_command = shell.call_args_list[0].args[0]
        detailed_command = shell.call_args_list[1].args[0]
        self.assertIn(shlex.quote(remote_path), fast_command)
        self.assertIn(shlex.quote(remote_path), detailed_command)


class WindowsPathHandlingTests(unittest.TestCase):
    def test_invalid_windows_characters_are_replaced(self):
        self.assertEqual(
            sanitize_windows_filename('folder\\photo:2026?".jpg'),
            "folder_photo_2026__.jpg",
        )

    def test_reserved_and_empty_windows_names_are_replaced(self):
        self.assertEqual(sanitize_windows_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_windows_filename("... "), "file")
        self.assertEqual(sanitize_windows_filename("\n\t"), "__")

    def test_long_unicode_filename_stays_within_utf16_limit(self):
        result = sanitize_windows_filename(f"{'😀' * 200}.jpg")
        utf16_units = len(result.encode("utf-16-le")) // 2

        self.assertLessEqual(utf16_units, 180)
        self.assertTrue(result.endswith(".jpg"))

    def test_batch_paths_do_not_overwrite_existing_or_reserved_names(self):
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "report.txt").write_text("existing", encoding="utf-8")
            reserved = set()

            first = available_download_path(temp_dir, "report.txt", reserved)
            second = available_download_path(temp_dir, "report.txt", reserved)

            self.assertEqual(Path(first).name, "report (1).txt")
            self.assertEqual(Path(second).name, "report (2).txt")
            self.assertEqual(
                Path(temp_dir, "report.txt").read_text(encoding="utf-8"),
                "existing",
            )

    def test_android_name_cannot_escape_download_directory(self):
        with TemporaryDirectory() as temp_dir:
            local_path = available_download_path(
                temp_dir,
                r"..\..\outside?.txt",
            )

            self.assertEqual(
                os.path.commonpath([temp_dir, local_path]),
                os.path.abspath(temp_dir),
            )
            self.assertEqual(Path(local_path).parent, Path(temp_dir))

    def test_cache_paths_are_stable_and_distinguish_remote_files(self):
        with TemporaryDirectory() as temp_dir:
            first = cached_download_path(
                temp_dir,
                "photo?.jpg",
                "device-1\0/sdcard/a/photo?.jpg",
            )
            repeated = cached_download_path(
                temp_dir,
                "photo?.jpg",
                "device-1\0/sdcard/a/photo?.jpg",
            )
            second = cached_download_path(
                temp_dir,
                "photo?.jpg",
                "device-1\0/sdcard/b/photo?.jpg",
            )

            self.assertEqual(first, repeated)
            self.assertNotEqual(first, second)
            self.assertEqual(Path(first).parent, Path(temp_dir))
            self.assertNotIn("?", Path(first).name)

    def test_cache_identity_survives_a_maximum_length_filename(self):
        with TemporaryDirectory() as temp_dir:
            filename = "." + ("a" * 179)
            first = cached_download_path(temp_dir, filename, "first")
            second = cached_download_path(temp_dir, filename, "second")

            self.assertNotEqual(first, second)
            self.assertLessEqual(
                len(Path(first).name.encode("utf-16-le")) // 2,
                180,
            )

    def test_batch_download_integration_uses_safe_unique_paths(self):
        with TemporaryDirectory() as temp_dir:
            existing_path = Path(temp_dir, "report_.txt")
            existing_path.write_text("keep", encoding="utf-8")

            window = MainWindow.__new__(MainWindow)
            window.file_list = Mock()
            window.file_list.get_selected_files.return_value = [
                {"name": "report?.txt", "path": "/a/report?.txt"},
                {"name": "report?.txt", "path": "/b/report?.txt"},
            ]
            window.adb = Mock()
            window.adb.pull_file.side_effect = [True, False]
            window.root = ImmediateRoot()
            window.status_var = FakeVar()

            with patch(
                "ui.main_window.filedialog.askdirectory",
                return_value=temp_dir,
            ), patch(
                "ui.main_window.threading.Thread",
                ImmediateThread,
            ), patch("ui.main_window.messagebox.showwarning") as warning:
                window._pull_selected()

            local_paths = [
                Path(call.args[1]).name
                for call in window.adb.pull_file.call_args_list
            ]
            self.assertEqual(local_paths, ["report_ (1).txt", "report_ (2).txt"])
            self.assertEqual(existing_path.read_text(encoding="utf-8"), "keep")
            self.assertEqual(
                window.status_var.value,
                "Downloaded 1 file(s); 1 failed",
            )
            warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
