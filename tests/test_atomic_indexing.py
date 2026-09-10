import sqlite3
import shlex
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adb_wrapper import ADBError, DeviceInfo
from database import Database
from file_indexer import (
    FileIndexer,
    IndexingCancelled,
    SCAN_COMPLETE_MARKER,
    SCAN_ERRORS_MARKER,
    SCAN_PROTOCOL_MARKER,
    SCAN_RECORD_MARKER,
    SCAN_UNSUPPORTED_MARKER,
)


OLD_FILES = [
    ("old.txt", "/storage/emulated/0/old.txt", 3, None, 0, ".txt"),
]


class FakeADB:
    def __init__(
        self,
        output="",
        records=None,
        error=None,
        scan_status=0,
        scan_errors="",
        structured_payload=None,
        structured_supported=True,
        stream_chunk_size=65536,
        include_errors_marker=True,
        include_scan_marker=True,
    ):
        self.output = output
        self.records = records or []
        self.error = error
        self.scan_status = scan_status
        self.scan_errors = scan_errors
        self.structured_payload = structured_payload
        self.structured_supported = structured_supported
        self.stream_chunk_size = stream_chunk_size
        self.include_errors_marker = include_errors_marker
        self.include_scan_marker = include_scan_marker
        self.storage_device_serials = []
        self.shell_device_serials = []
        self.shell_commands = []
        self.binary_shell_commands = []

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

    def shell_bytes(self, command, timeout=60, *, device_serial):
        self.shell_device_serials.append(device_serial)
        self.binary_shell_commands.append(command)
        return self._structured_output()

    def shell_stream(
        self,
        command,
        timeout=60,
        *,
        device_serial,
        cancel_check=None,
    ):
        self.shell_device_serials.append(device_serial)
        self.binary_shell_commands.append(command)
        payload = self._structured_output()
        for start in range(0, len(payload), self.stream_chunk_size):
            if cancel_check and cancel_check():
                return
            yield payload[start:start + self.stream_chunk_size]

    def _structured_output(self):
        if self.error:
            raise self.error
        if not self.structured_supported:
            return SCAN_UNSUPPORTED_MARKER.encode("ascii") + b"\0"
        if self.structured_payload is not None:
            return self.structured_payload

        fields = [SCAN_PROTOCOL_MARKER.encode("ascii")]
        for size, modified, path in self.records:
            fields.extend([
                SCAN_RECORD_MARKER.encode("ascii"),
                str(size).encode("ascii"),
                str(modified).encode("ascii"),
                path.encode("utf-8"),
            ])
        fields.extend([
            SCAN_ERRORS_MARKER.encode("ascii"),
            self.scan_errors.encode("utf-8"),
            SCAN_COMPLETE_MARKER.encode("ascii"),
            str(self.scan_status).encode("ascii"),
        ])
        return b"\0".join(fields) + b"\0"

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

    def assert_staging_empty(self):
        conn = sqlite3.connect(self.db_path)
        try:
            staged_files = conn.execute(
                "SELECT COUNT(*) FROM files_staging"
            ).fetchone()[0]
            staged_runs = conn.execute(
                "SELECT COUNT(*) FROM index_staging_runs"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((staged_files, staged_runs), (0, 0))

    def test_successfully_replaces_the_complete_index(self):
        adb = FakeADB(records=[
            (10, 1786528800, "/storage/emulated/0/new one.jpg"),
            (20, 1786528860, "/storage/emulated/0/new-two.pdf"),
        ])
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

    def test_structured_protocol_preserves_special_file_names(self):
        paths = [
            "/storage/emulated/0/report:",
            "/storage/emulated/0/line\nbreak.txt",
            "/storage/emulated/0/tab\tname.pdf",
            "/storage/emulated/0/- leading spaces -.jpg",
        ]
        adb = FakeADB(records=[
            (index + 1, 1786528800 + index, path)
            for index, path in enumerate(paths)
        ])
        self.indexer = FileIndexer(adb, self.db)

        count = self.indexer.index_device_sync("device-1")

        self.assertEqual(count, 4)
        indexed = self.db.search("device-1", "", limit=100)
        self.assertEqual({row["path"] for row in indexed}, set(paths))
        self.assertIn("-exec sh -c", adb.binary_shell_commands[0])

    def test_structured_protocol_parses_fields_split_across_chunks(self):
        path = "/storage/emulated/0/chunked file.txt"
        adb = FakeADB(
            records=[(123, 1786528800, path)],
            stream_chunk_size=1,
        )
        self.indexer = FileIndexer(adb, self.db)

        self.assertEqual(self.indexer.index_device_sync("device-1"), 1)
        self.assertEqual(
            [row["path"] for row in self.db.search("device-1", "chunked")],
            [path],
        )

    def test_streams_bounded_batches_while_live_index_remains_searchable(self):
        adb = FakeADB(records=[
            (index, 1786528800, f"/storage/emulated/0/new-{index}.txt")
            for index in range(5)
        ], stream_chunk_size=7)
        self.indexer = FileIndexer(adb, self.db)
        real_stage_batch = self.db.stage_files_batch
        observed_batch_sizes = []
        live_paths_during_staging = []
        new_paths_during_staging = []

        def observe_staging(scan_id, device_serial, files):
            result = real_stage_batch(scan_id, device_serial, files)
            observed_batch_sizes.append(len(files))
            live_paths_during_staging.append([
                row["path"]
                for row in self.db.search("device-1", "old")
            ])
            new_paths_during_staging.append([
                row["path"]
                for row in self.db.search("device-1", "new")
            ])
            return result

        with patch(
            "file_indexer.INDEX_STAGING_BATCH_SIZE",
            2,
        ), patch.object(
            self.db,
            "stage_files_batch",
            side_effect=observe_staging,
        ):
            count = self.indexer.index_device_sync("device-1")

        self.assertEqual(count, 5)
        self.assertEqual(observed_batch_sizes, [2, 2, 1])
        self.assertEqual(
            live_paths_during_staging,
            [[OLD_FILES[0][1]]] * 3,
        )
        self.assertEqual(new_paths_during_staging, [[], [], []])
        self.assertEqual(self.db.search("device-1", "old"), [])
        self.assert_staging_empty()

    def test_invalid_trailer_discards_already_committed_staging_batches(self):
        fields = [SCAN_PROTOCOL_MARKER.encode("ascii")]
        for index in range(3):
            fields.extend([
                SCAN_RECORD_MARKER.encode("ascii"),
                str(index).encode("ascii"),
                b"1786528800",
                f"/storage/emulated/0/new-{index}.txt".encode("utf-8"),
            ])
        fields.extend([
            SCAN_ERRORS_MARKER.encode("ascii"),
            b"",
        ])
        adb = FakeADB(
            structured_payload=b"\0".join(fields) + b"\0",
            stream_chunk_size=5,
        )
        self.indexer = FileIndexer(adb, self.db)

        with patch("file_indexer.INDEX_STAGING_BATCH_SIZE", 2):
            with self.assertRaisesRegex(ADBError, "invalid trailer"):
                self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()
        self.assert_staging_empty()

    def test_structured_protocol_skips_only_exact_protected_folders(self):
        adb = FakeADB(records=[
            (1, 1786528800, "/storage/emulated/0/Android/data/app/x.dat"),
            (2, 1786528800, "/storage/emulated/0/DCIM/.thumbnails/x.jpg"),
            (3, 1786528800, "/storage/emulated/0/Android/database/keep.db"),
            (4, 1786528800, "/storage/emulated/0/foo.thumbnails/keep.jpg"),
        ])
        self.indexer = FileIndexer(adb, self.db)

        self.assertEqual(self.indexer.index_device_sync("device-1"), 2)
        indexed = self.db.search("device-1", "", limit=100)
        self.assertEqual(
            {row["name"] for row in indexed},
            {"keep.db", "keep.jpg"},
        )

    def test_structured_protocol_rejects_truncated_record(self):
        payload = b"\0".join([
            SCAN_PROTOCOL_MARKER.encode("ascii"),
            SCAN_RECORD_MARKER.encode("ascii"),
            b"10",
        ]) + b"\0"
        self.indexer = FileIndexer(
            FakeADB(structured_payload=payload),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "truncated record"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_structured_protocol_rejects_path_outside_scan_root(self):
        self.indexer = FileIndexer(
            FakeADB(records=[(
                10,
                1786528800,
                "/storage/emulated/01/not-a-child.txt",
            )]),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "outside the scan root"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_legacy_ls_is_used_only_when_protocol_is_unavailable(self):
        output = "\n".join([
            "/storage/emulated/0:",
            "-rw-rw---- 1 user group 10 2026-08-12 10:00 legacy.txt",
        ])
        adb = FakeADB(
            output=output,
            structured_supported=False,
        )
        self.indexer = FileIndexer(adb, self.db)

        self.assertEqual(self.indexer.index_device_sync("device-1"), 1)
        self.assertEqual(
            [row["name"] for row in self.db.search("device-1", "legacy")],
            ["legacy.txt"],
        )
        self.assertEqual(len(adb.binary_shell_commands), 1)
        self.assertEqual(len(adb.shell_commands), 1)
        self.assertIn("ls -lR", adb.shell_commands[0])

    def test_structured_scan_command_quotes_the_root_path(self):
        scan_path = "/storage/card's files"
        command = FileIndexer._build_structured_scan_command(scan_path)

        self.assertIn(shlex.quote(scan_path), command)
        self.assertIn(SCAN_PROTOCOL_MARKER, command)
        self.assertIn(SCAN_COMPLETE_MARKER, command)

    def test_scan_failure_preserves_the_previous_index(self):
        self.indexer = FileIndexer(
            FakeADB(error=RuntimeError("scan failed")),
            self.db,
        )

        with self.assertRaisesRegex(RuntimeError, "scan failed"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_inaccessible_child_directories_do_not_discard_valid_results(self):
        self.indexer = FileIndexer(
            FakeADB(
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors=(
                    "find: /storage/emulated/0/Android/data: Permission denied"
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
        self.indexer = FileIndexer(
            FakeADB(
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors=(
                    "find: cannot open directory "
                    "'/storage/emulated/0/Android/data': Permission denied"
                ),
            ),
            self.db,
        )

        self.assertEqual(self.indexer.index_device_sync("device-1"), 1)

    def test_structured_scan_rejects_non_find_permission_diagnostic(self):
        self.indexer = FileIndexer(
            FakeADB(
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors=(
                    "ls: /storage/emulated/0/Android/data: Permission denied"
                ),
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "Permission denied"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_non_permission_scan_error_is_preserved_and_rejected(self):
        adb = FakeADB(
            records=[(
                10,
                1786528800,
                "/storage/emulated/0/visible.txt",
            )],
            scan_status=1,
            scan_errors=(
                "find: /storage/emulated/0/DCIM: Input/output error"
            ),
        )
        self.indexer = FileIndexer(adb, self.db)

        with self.assertRaisesRegex(ADBError, "Input/output error"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()
        self.assertIn("find", adb.binary_shell_commands[0])
        self.assertIn("stat -c", adb.binary_shell_commands[0])
        self.assertIn("\\000", adb.binary_shell_commands[0])
        self.assertNotIn("ls -lR", adb.binary_shell_commands[0])

    def test_status_one_without_remote_error_is_rejected(self):
        self.indexer = FileIndexer(
            FakeADB(
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
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
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors=(
                    "find: /storage/emulated/0: Permission denied"
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
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors=(
                    "find: /storage/emulated/01/private: Permission denied"
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
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=1,
                scan_errors="\n".join([
                    "find: /storage/emulated/0/Android/data: Permission denied",
                    "find: /storage/emulated/0/DCIM: Transport endpoint is not connected",
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
                records=[(
                    10,
                    1786528800,
                    "/storage/emulated/0/visible.txt",
                )],
                scan_status=0,
                scan_errors="find: unexpected diagnostic",
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
                structured_supported=False,
                include_errors_marker=False,
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "remote error marker"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_missing_scan_completion_marker_preserves_previous_index(self):
        self.indexer = FileIndexer(
            FakeADB(
                output="partial output",
                structured_supported=False,
                include_scan_marker=False,
            ),
            self.db,
        )

        with self.assertRaisesRegex(ADBError, "completion marker"):
            self.indexer.index_device_sync("device-1")

        self.assert_old_index_preserved()

    def test_database_failure_rolls_back_files_and_device_metadata(self):
        new_files = [
            ("new.txt", "/storage/emulated/0/new.txt", 5, None, 0, ".txt"),
        ]
        completed_callbacks = [0]

        def fail_before_commit(processed, total):
            if processed == total:
                completed_callbacks[0] += 1
                if completed_callbacks[0] == 2:
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
        self.indexer = FileIndexer(FakeADB(records=[(
            10,
            1786528800,
            "/storage/emulated/0/new.txt",
        )]), self.db)
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
        self.assert_staging_empty()


if __name__ == "__main__":
    unittest.main()
