"""
File indexer for scanning and indexing Android device files.
"""
import os
import posixpath
import re
import shlex
import threading
from typing import Callable, Optional, List
from datetime import datetime

from adb_wrapper import ADBError, ADBWrapper, FileInfo, get_adb
from database import Database, get_database
from device_mutation import (
    DeviceMutationCoordinator,
    get_device_mutation_coordinator,
)
from config import SCAN_PATHS


class IndexingCancelled(Exception):
    """Raised internally to roll back a cancelled index replacement."""


SCAN_COMPLETE_MARKER = "__ANDROID_EVERYTHING_SCAN_COMPLETE__="
SCAN_ERRORS_MARKER = "__ANDROID_EVERYTHING_SCAN_ERRORS__="


_PERMISSION_DENIED_PATTERNS = (
    re.compile(
        r"^ls:\s+cannot open directory\s+(?P<path>.+):\s*"
        r"Permission denied\s*$"
    ),
    re.compile(
        r"^ls:\s+(?P<path>.+):\s*Permission denied\s*$"
    ),
)


class FileIndexer:
    """Indexes files from Android device into the database."""
    
    def __init__(
        self,
        adb: Optional[ADBWrapper] = None,
        db: Optional[Database] = None,
        mutation_coordinator: Optional[DeviceMutationCoordinator] = None,
    ):
        self.adb = adb or get_adb()
        self.db = db or get_database()
        self.mutation_coordinator = (
            mutation_coordinator or get_device_mutation_coordinator()
        )
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
            device_lock = None
            try:
                # Hold this lock from the first ADB read through the atomic
                # database commit. A delete for the same device must therefore
                # happen entirely before or entirely after this snapshot.
                device_lock = self.mutation_coordinator.acquire(device_serial)

                if self._cancel_requested:
                    report_cancelled()
                    return

                scan_paths = requested_paths
                if scan_paths is None:
                    scan_paths = self.adb.get_storage_paths(
                        device_serial=device_serial,
                    )
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
                    files = self._scan_path(
                        device_serial,
                        scan_path,
                        progress_callback,
                    )
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
                if device_lock is not None:
                    self.mutation_coordinator.release(device_lock)
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
        device_serial: str,
        path: str,
        progress_callback: Optional[Callable] = None
    ) -> List[FileInfo]:
        """Scan a single path on the device with file sizes using ls -lR (fast)."""
        files = []
        
        # Recursive ls commonly returns 1 when Android denies access to a
        # protected child directory even though the accessible listing is
        # complete and usable. Preserve its remote stderr separately so that
        # only that specific partial-failure case can be accepted. The
        # sentinels also prevent an interrupted ADB transport from being
        # mistaken for a completed scan.
        quoted_path = shlex.quote(path)
        cmd = (
            f"if ! ls -ld {quoted_path} >/dev/null; then exit 2; fi; "
            "exec 3>&1; "
            f"scan_errors=$(ls -lR {quoted_path} 2>&1 1>&3); "
            "scan_status=$?; "
            "exec 3>&-; "
            f"printf '\n{SCAN_ERRORS_MARKER}%s\n' \"$scan_errors\"; "
            f"printf '\n{SCAN_COMPLETE_MARKER}%s\n' \"$scan_status\"; "
            "exit 0"
        )
        output = self.adb.shell(
            cmd,
            timeout=180,
            device_serial=device_serial,
        )

        scan_payload, marker, status_output = output.rpartition(
            SCAN_COMPLETE_MARKER
        )
        if not marker:
            raise ADBError(
                "ADB scan ended before the completion marker was received"
            )

        status_text = status_output.strip()
        if not re.fullmatch(r"\d+", status_text):
            raise ADBError("ADB scan returned an invalid completion status")
        scan_status = int(status_text)

        listing, errors_marker, scan_errors = scan_payload.rpartition(
            SCAN_ERRORS_MARKER
        )
        if not errors_marker:
            raise ADBError(
                "ADB scan ended before the remote error marker was received"
            )

        listing = listing.rstrip("\r\n")
        scan_errors = scan_errors.strip("\r\n")
        self._validate_scan_errors(path, scan_status, scan_errors)

        # Even an explicitly permitted partial failure must return a usable
        # listing; otherwise replacing the previous index would lose data.
        if scan_status == 1 and not listing.strip():
            raise ADBError("Android file listing failed without returning data")

        output = listing

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

    @staticmethod
    def _validate_scan_errors(
        scan_path: str,
        scan_status: int,
        scan_errors: str,
    ):
        """Accept only explicit permission failures for scanned subfolders."""
        error_lines = [
            line.strip()
            for line in scan_errors.splitlines()
            if line.strip()
        ]

        if scan_status == 0:
            if error_lines:
                raise ADBError(
                    "Android file listing reported unexpected remote error "
                    f"output: {FileIndexer._format_scan_errors(error_lines)}"
                )
            return

        if scan_status != 1:
            detail = FileIndexer._format_scan_errors(error_lines)
            message = (
                f"Android file listing failed with exit code {scan_status}"
            )
            if detail:
                message += f": {detail}"
            raise ADBError(message)

        if not error_lines or not all(
            FileIndexer._is_permission_denied_child(scan_path, line)
            for line in error_lines
        ):
            detail = FileIndexer._format_scan_errors(error_lines)
            if not detail:
                detail = "no remote error output"
            raise ADBError(
                "Android file listing failed; only explicit Permission denied "
                "errors for child directories are allowed: "
                f"{detail}"
            )

    @staticmethod
    def _is_permission_denied_child(scan_path: str, error_line: str) -> bool:
        """Return whether an ls diagnostic names a child of ``scan_path``."""
        denied_path = None
        for pattern in _PERMISSION_DENIED_PATTERNS:
            match = pattern.fullmatch(error_line)
            if match:
                denied_path = match.group("path").strip()
                break

        if denied_path is None:
            return False

        if (
            len(denied_path) >= 2
            and denied_path[0] == denied_path[-1]
            and denied_path[0] in ("'", '"')
        ):
            denied_path = denied_path[1:-1]

        normalized_root = posixpath.normpath(scan_path)
        normalized_denied = posixpath.normpath(denied_path)
        if (
            not normalized_root.startswith("/")
            or not normalized_denied.startswith("/")
        ):
            return False

        child_prefix = (
            normalized_root
            if normalized_root.endswith("/")
            else normalized_root + "/"
        )
        return (
            normalized_denied != normalized_root
            and normalized_denied.startswith(child_prefix)
        )

    @staticmethod
    def _format_scan_errors(error_lines: List[str]) -> str:
        """Return bounded remote diagnostics suitable for the UI and logs."""
        detail = " | ".join(error_lines)
        if len(detail) > 500:
            return detail[:497] + "..."
        return detail
    
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
