import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adb_wrapper import DeviceInfo, FileInfo
from database import Database
from device_mutation import (
    DeviceMutationCoordinator,
    delete_device_paths,
)
from file_indexer import FileIndexer


DEVICE_SERIAL = "device-1"
REMOTE_PATH = "/storage/emulated/0/photo.jpg"


class CoordinatedADB:
    def __init__(self):
        self.remote_file_exists = True
        self.delete_called = threading.Event()

    def get_storage_paths(self, *, device_serial):
        return ["/storage/emulated/0"]

    def get_devices(self):
        return [DeviceInfo(DEVICE_SERIAL, "device", model="Test Phone")]

    def delete_file(self, path, *, device_serial):
        self.delete_called.set()
        if device_serial != DEVICE_SERIAL or path != REMOTE_PATH:
            return False
        self.remote_file_exists = False
        return True


class SnapshotPausingIndexer(FileIndexer):
    def __init__(self, *args, snapshot_ready, continue_indexing, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_ready = snapshot_ready
        self.continue_indexing = continue_indexing

    def _scan_path(self, device_serial, path, progress_callback=None):
        files = []
        if self.adb.remote_file_exists:
            files.append(FileInfo(
                name="photo.jpg",
                path=REMOTE_PATH,
                size=100,
                modified=None,
                is_dir=False,
            ))
        self.snapshot_ready.set()
        if not self.continue_indexing.wait(timeout=2):
            raise RuntimeError("test did not release the index snapshot")
        return files


class DeviceMutationTests(unittest.TestCase):
    def test_delete_waits_for_index_commit_then_removes_the_stale_snapshot(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(str(Path(temp_dir) / "files.db"))
            adb = CoordinatedADB()
            coordinator = DeviceMutationCoordinator()
            snapshot_ready = threading.Event()
            continue_indexing = threading.Event()
            indexing_complete = threading.Event()
            indexing_errors = []
            deleted_paths = []

            indexer = SnapshotPausingIndexer(
                adb,
                db,
                coordinator,
                snapshot_ready=snapshot_ready,
                continue_indexing=continue_indexing,
            )
            self.assertTrue(indexer.index_device(
                DEVICE_SERIAL,
                complete_callback=lambda count: indexing_complete.set(),
                error_callback=indexing_errors.append,
            ))
            self.assertTrue(snapshot_ready.wait(timeout=2))

            delete_attempted = threading.Event()

            def delete_file():
                delete_attempted.set()
                deleted_paths.extend(delete_device_paths(
                    adb,
                    db,
                    coordinator,
                    DEVICE_SERIAL,
                    [REMOTE_PATH],
                ))

            delete_thread = threading.Thread(target=delete_file)
            delete_thread.start()
            self.assertTrue(delete_attempted.wait(timeout=2))

            # The indexer owns the same per-device lock while its stale
            # snapshot is paused, so remote deletion cannot begin yet.
            try:
                self.assertFalse(adb.delete_called.wait(timeout=0.1))
            finally:
                continue_indexing.set()

            self.assertTrue(indexing_complete.wait(timeout=2))
            indexer._current_thread.join(timeout=2)
            delete_thread.join(timeout=2)

            self.assertFalse(indexer._current_thread.is_alive())
            self.assertFalse(delete_thread.is_alive())
            self.assertEqual(indexing_errors, [])
            self.assertEqual(deleted_paths, [REMOTE_PATH])
            self.assertFalse(adb.remote_file_exists)
            self.assertEqual(db.get_file_count(DEVICE_SERIAL), 0)
            self.assertEqual(db.search(DEVICE_SERIAL, "photo"), [])


if __name__ == "__main__":
    unittest.main()
