import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adb_wrapper import ADBError, DeviceInfo
from database import Database
from file_indexer import (
    FileIndexer,
    IndexingCancelled,
    SCAN_COMPLETE_MARKER,
    SCAN_ERRORS_MARKER,
)


OLD_FILES = [
    ("old.txt", "/storage/emulated/0/old.txt", 3, None, 0, ".txt"),
]


class FakeADB:
    def __init__(
        self,
        output="",
        error=None,
        scan_status=0,
        scan_errors="",
        include_errors_marker=True,
        include_scan_marker=True,
    ):
        self.output = output
        self.error = error
        self.scan_status = scan_status
        self.scan_errors = scan_errors
        self.include_errors_marker = include_errors_marker
        self.include_scan_marker = include_scan_marker
        self.storage_device_serials = []
        self.shell_device_serials = []
        self.shell_commands = []

    def get_storage_paths(self, *, device_serial):
        self.storage_device_serials.append(device_serial)
        return ["/storage/emulated/0"]

    def shell(self, command, timeout=60, *, device_serial):
        self.shell_device_serials.append(device_serial)
        self.shell_commands.append(command)
        if self.error:
            raise self.error
        errors_payload = ""
        if self.include_errors_marker:
            errors_payload = (
                f"\n{SCAN_ERRORS_MARKER}{self.scan_errors}\n"
            )
        if self.include_scan_marker:
            return (
                f"{self.output}{errors_payload}"
                f"{SCAN_COMPLETE_MARKER}{self.scan_status}\n"
            )
        return f"{self.output}{errors_payload}"

    def get_devices(self):
        return [DeviceInfo("device-1", "device", model="Test Phone")]


class AtomicIndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "files.db")
        self.db = Database(self.db_path)
        self.db.replace_device_index("device-1", OLD_FILES, model="Old Phone")

    def tearDown(self):
        # A worker callback may release the test before the thread has returned
        # from its final bookkeeping. Join it before Windows removes the DB.
        indexer = getattr(self, "indexer", None)
        worker = getattr(indexer, "_current_thread", None)
        if worker:
            worker.join(timeout=2)
        self.temp_dir.cleanup()

    def assert_old_index_preserved(self):
        self.assertEqual(self.db.get_file_count("device-1"), 1)
        rows = self.db.search("device-1", "old")
        self.assertEqual([row["path"] for row in rows], [OLD_FILES[0][1]])

        conn = sqlite3.connect(self.db_path)
        try:
            device = conn.execute(
                "SELECT model, file_count FROM devices WHERE serial = ?",
                ("device-1",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(device, ("Old Phone", 1))

    def test_successfully_replaces_the_complete_index(self):
        output = "\n".join([
            "/storage/emulated/0:",
            "-rw-rw---- 1 user group 10 2026-08-12 10:00 new one.jpg",
            "-rw-rw---- 1 user group 20 2026-08-12 10:01 new-two.pdf",
        ])
        adb = FakeADB(output=output)
        self.indexer = FileIndexer(adb, self.db)

        count = self.indexer.index_device_sync("device-1")

        self.assertEqual(count, 2)
        self.assertEqual(self.db.get_file_count("device-1"), 2)
        self.assertEqual(self.db.search("device-1", "old"), [])
        self.assertEqual(
            {row["name"] for row in self.db.search("device-1", "new")},
            {"new one.jpg", "new-two.pdf"},
        )
        self.assertEqual(adb.storage_device_serials, ["device-1"])
        self.assertEqual(adb.shell_device_serials, ["device-1"])

    def test_scan_failure_preserves_the_previous_index(self):
        self.indexer = FileIndexer(
            FakeADB(error=RuntimeError("scan failed")),
            self.db,
        )

        with self.assertRaisesRegex(RuntimeError, "scan failed"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_inaccessible_child_directories_do_not_discard_valid_results(self):
        output = "\n".join([
            "/storage/emulated/0:",
            "-rw-rw---- 1 user group 10 2026-08-12 10:00 visible.txt",
        ])
        self.indexer = FileIndexer(
            FakeADB(
                output=output,
                scan_status=1,
                scan_errors=(
                    "ls: /storage/emulated/0/Android/data: Permission denied"
                ),
            ),
            self.db,
        )

        count = self.indexer.index_device_sync("device-1")

        self.assertEqual(count, 1)
        self.assertEqual(
            [row["path"] for row in self.db.search("device-1", "visible")],
            ["/storage/emulated/0/visible.txt"],
        )

    def test_gnu_style_permission_denied_child_error_is_allowed(self):
        output = "\n".join([
            "/storage/emulated/0:",
            "-rw-rw---- 1 user group 10 2026-08-12 10:00 visible.txt",
        ])
        self.indexer = FileIndexer(
            FakeADB(
                output=output,
                scan_status=1,
                scan_errors=(
                    "ls: cannot open directory "
                    "'/storage/emulated/0/Android/data': Permission denied"
                ),
            ),
            self.db,
        )

        self.assertEqual(self.indexer.index_device_sync("device-1"), 1)

    def test_non_permission_scan_error_is_preserved_and_rejected(self):
        adb = FakeADB(
            output="/storage/emulated/0:",
            scan_status=1,
            scan_errors=(
                "ls: /storage/emulated/0/DCIM: Input/output error"
            ),
        )
        self.indexer = FileIndexer(adb, self.db)

        with self.assertRaisesRegex(ADBError, "Input/output error"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()
        self.assertNotIn("2>/dev/null", adb.shell_commands[0])
        self.assertIn(SCAN_ERRORS_MARKER, adb.shell_commands[0])

    def test_status_one_without_remote_error_is_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="/storage/emulated/0:",
                scan_status=1,
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "no remote error output"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_permission_denied_for_scan_root_is_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="/storage/emulated/0:",
                scan_status=1,
                scan_errors=(
                    "ls: /storage/emulated/0: Permission denied"
                ),
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "Permission denied"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_permission_denied_for_sibling_path_is_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="/storage/emulated/0:",
                scan_status=1,
                scan_errors=(
                    "ls: /storage/emulated/01/private: Permission denied"
                ),
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "Permission denied"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_mixed_permission_and_transport_errors_are_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="/storage/emulated/0:",
                scan_status=1,
                scan_errors="\n".join([
                    "ls: /storage/emulated/0/Android/data: Permission denied",
                    "ls: /storage/emulated/0/DCIM: Transport endpoint is not connected",
                ]),
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "Transport endpoint"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_remote_error_with_zero_status_is_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="/storage/emulated/0:",
                scan_status=0,
                scan_errors="ls: unexpected diagnostic",
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "unexpected diagnostic"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_missing_remote_error_marker_preserves_previous_index(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="partial output",
                include_errors_marker=False,
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "remote error marker"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_missing_scan_completion_marker_preserves_previous_index(self):
        self.indexer = FileIndexer(
            FakeADB(output="partial output", include_scan_marker=False),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "completion marker"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_database_failure_rolls_back_files_and_device_metadata(self):
        new_files = [
            ("new.txt", "/storage/emulated/0/new.txt", 5, None, 0, ".txt"),
        ]

        def fail_before_commit(processed, total):
            if processed == total:
                raise RuntimeError("simulated write failure")

        with self.assertRaisesRegex(RuntimeError, "simulated write failure"):
            self.db.replace_device_index(
                "device-1",
                new_files,
                model="New Phone",
                progress_callback=fail_before_commit,
            )

        self.assert_old_index_preserved()
        self.assertEqual(self.db.search("device-1", "new"), [])

    def test_cancellation_during_replacement_rolls_back(self):
        new_files = [
            (f"new-{index}.txt", f"/new-{index}.txt", index, None, 0, ".txt")
            for index in range(3)
        ]

        def cancel_after_first_batch(processed, total):
            if processed:
                raise IndexingCancelled()

        with self.assertRaises(IndexingCancelled):
            self.db.replace_device_index(
                "device-1",
                new_files,
                model="New Phone",
                batch_size=1,
                progress_callback=cancel_after_first_batch,
            )

        self.assert_old_index_preserved()

    def test_cancelled_worker_reports_completion_without_replacing_index(self):
        output = "\n".join([
            "/storage/emulated/0:",
            "-rw-rw---- 1 user group 10 2026-08-12 10:00 new.txt",
        ])
        self.indexer = FileIndexer(FakeADB(output=output), self.db)
        cancelled = threading.Event()

        def request_cancel(message, current, total):
            if message.startswith("Preparing"):
                self.indexer.cancel()

        started = self.indexer.index_device(
            "device-1",
            progress_callback=request_cancel,
            cancelled_callback=cancelled.set,
        )

        self.assertTrue(started)
        self.assertTrue(cancelled.wait(timeout=2))
        self.indexer._current_thread.join(timeout=2)
        self.assertFalse(self.indexer._current_thread.is_alive())
        self.assert_old_index_preserved()


if __name__ == "__main__":
    unittest.main()
