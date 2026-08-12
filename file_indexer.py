"""
File indexer for scanning and indexing Android device files.
"""
import os
import threading
from typing import Callable, Optional, List
from datetime import datetime

from adb_wrapper import ADBWrapper, FileInfo, get_adb
from database import Database, get_database
from config import SCAN_PATHS


class IndexingCancelled(Exception):
    """Raised internally to roll back a cancelled index replacement."""


class FileIndexer:
    """Indexes files from Android device into the database."""
    
    def __init__(self, adb: Optional[ADBWrapper] = None, db: Optional[Database] = None):
        self.adb = adb or get_adb()
        self.db = db or get_database()
        self._indexing = False
        self._cancel_requested = False
        self._current_thread: Optional[threading.Thread] = None
    
    @property
    def is_indexing(self) -> bool:
        """Check if indexing is in progress."""
        return self._indexing
    
    def cancel(self):
        """Request cancellation of current indexing."""
        self._cancel_requested = True
    
    def index_device(
        self,
        device_serial: str,
        paths: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        complete_callback: Optional[Callable[[int], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
        cancelled_callback: Optional[Callable[[], None]] = None
    ) -> bool:
        """
        Start indexing device files in background.
        
        Args:
            device_serial: Device to index
            paths: Paths to scan (default: SCAN_PATHS from config)
            progress_callback: Called with (status_message, current, total)
            complete_callback: Called with total file count when done
            error_callback: Called with the exception when indexing fails
            cancelled_callback: Called after cancellation

        Returns:
            True if an indexing worker was started, otherwise False.
        """
        if self._indexing:
            return False

        requested_paths = list(paths) if paths is not None else None

        def report_cancelled():
            if progress_callback:
                progress_callback(
                    "Cancelled - previous index preserved", 0, 100
                )
            if cancelled_callback:
                cancelled_callback()
        
        def do_index():
            try:
                self.adb.select_device(device_serial)

                scan_paths = requested_paths
                if scan_paths is None:
                    scan_paths = self.adb.get_storage_paths()
                    if not scan_paths:
                        scan_paths = ["/storage/emulated/0"]
                
                all_files = []
                
                # Scan each path
                for i, scan_path in enumerate(scan_paths):
                    if self._cancel_requested:
                        break
                    
                    if progress_callback:
                        progress_callback(
                            f"Scanning {scan_path}...", 
                            int(i / len(scan_paths) * 50),
                            100
                        )
                    
                    # Get files from device
                    files = self._scan_path(scan_path, progress_callback)
                    all_files.extend(files)
                
                if self._cancel_requested:
                    report_cancelled()
                    return
                
                # Prepare the complete replacement before touching the current
                # database index.
                if progress_callback:
                    progress_callback(
                        f"Preparing {len(all_files)} files...", 75, 100
                    )
                
                # Prepare data for batch insert
                batch_data = []
                for f in all_files:
                    if self._cancel_requested:
                        report_cancelled()
                        return

                    ext = os.path.splitext(f.name)[1].lower() if f.name else ""
                    modified_str = f.modified.isoformat() if f.modified else None
                    batch_data.append((
                        f.name,
                        f.path,
                        f.size,
                        modified_str,
                        1 if f.is_dir else 0,
                        ext
                    ))
                
                if self._cancel_requested:
                    report_cancelled()
                    return

                # Fetch metadata before opening the replacement transaction so
                # an ADB failure cannot affect the previous index.
                devices = self.adb.get_devices()
                model = next(
                    (d.model for d in devices if d.serial == device_serial),
                    ""
                )

                def on_replace_progress(processed: int, total: int):
                    if self._cancel_requested:
                        raise IndexingCancelled()

                    if progress_callback:
                        progress = 75 + int(
                            (processed / max(total, 1)) * 20
                        )
                        progress_callback(
                            f"Saving to database... ({processed}/{total})",
                            progress,
                            100
                        )

                indexed_count = self.db.replace_device_index(
                    device_serial,
                    batch_data,
                    model=model,
                    batch_size=5000,
                    progress_callback=on_replace_progress,
                )
                
                if progress_callback:
                    progress_callback(
                        f"Done! {indexed_count} files indexed.", 100, 100
                    )
                
                if complete_callback:
                    complete_callback(indexed_count)

            except IndexingCancelled:
                report_cancelled()
            except Exception as e:
                if progress_callback:
                    progress_callback(
                        f"Error: {str(e)}. Previous index preserved.",
                        0,
                        100
                    )
                if error_callback:
                    error_callback(e)
            finally:
                self._indexing = False
        
        # Start in background thread
        self._indexing = True
        self._cancel_requested = False
        self._current_thread = threading.Thread(target=do_index, daemon=True)
        try:
            self._current_thread.start()
        except Exception:
            self._indexing = False
            raise

        return True
    
    def _scan_path(
        self, 
        path: str, 
        progress_callback: Optional[Callable] = None
    ) -> List[FileInfo]:
        """Scan a single path on the device with file sizes using ls -lR (fast)."""
        files = []
        
        # Use ls -lR for fast recursive listing with sizes. Let failures reach
        # the worker so it can report them and preserve the previous index.
        cmd = f'ls -lR "{path}" 2>/dev/null'
        output = self.adb.shell(cmd, timeout=180)

        current_dir = path
        lines = output.split('\n')

        for line in lines:
            if self._cancel_requested:
                break

            line = line.rstrip()

            # Empty line
            if not line:
                continue

            # Directory header (e.g., "/sdcard/Pictures:")
            if line.endswith(':'):
                current_dir = line[:-1]
                continue

            # Skip "total" lines and special entries
            if line.startswith('total ') or line.startswith('d') or line.startswith('l'):
                continue

            # Skip certain system paths
            if any(skip in current_dir for skip in ['/Android/data', '/.thumbnails']):
                continue

            # Parse file line: -rw-rw---- 1 owner group size date time name
            # Example: -rw-rw---- 1 u0_a123 u0_a123 12345 2024-01-15 10:30 photo.jpg
            if line.startswith('-'):
                parts = line.split(None, 7)
                if len(parts) >= 8:
                    try:
                        size = int(parts[4])
                    except ValueError:
                        size = 0

                    # Parse date/time
                    try:
                        date_str = f"{parts[5]} {parts[6]}"
                        modified = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                    except (ValueError, IndexError):
                        modified = None

                    # split(None, 7) keeps spaces in the filename.
                    name = parts[7]

                    if name and name not in ['.', '..']:
                        file_path = f"{current_dir}/{name}"
                        files.append(FileInfo(
                            name=name,
                            path=file_path,
                            size=size,
                            modified=modified,
                            is_dir=False
                        ))

        return files
    
    def index_device_sync(
        self,
        device_serial: str,
        paths: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> int:
        """
        Synchronous version of index_device.
        Returns total file count.
        """
        result = [0]
        errors = []
        cancelled = [False]
        done_event = threading.Event()
        
        def on_complete(count):
            result[0] = count
            done_event.set()

        def on_error(error):
            errors.append(error)
            done_event.set()

        def on_cancelled():
            cancelled[0] = True
            done_event.set()

        started = self.index_device(
            device_serial, 
            paths, 
            progress_callback, 
            on_complete,
            on_error,
            on_cancelled,
        )

        if not started:
            raise RuntimeError("Indexing is already in progress")

        done_event.wait()
        if self._current_thread:
            self._current_thread.join()

        if errors:
            raise errors[0]
        if cancelled[0]:
            raise IndexingCancelled("Indexing was cancelled")

        return result[0]


# Singleton instance
_indexer: Optional[FileIndexer] = None


def get_indexer() -> FileIndexer:
    """Get the global file indexer instance."""
    global _indexer
    if _indexer is None:
        _indexer = FileIndexer()
    return _indexer
