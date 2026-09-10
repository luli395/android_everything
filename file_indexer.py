"""
File indexer for scanning and indexing Android device files.
"""
import os
import posixpath
import re
import shlex
import threading
from typing import Callable, Iterable, Iterator, Optional, List
from datetime import datetime

from adb_wrapper import (
    ADBCommandCancelled,
    ADBError,
    ADBWrapper,
    FileInfo,
    get_adb,
)
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
SCAN_PROTOCOL_MARKER = "__ANDROID_EVERYTHING_SCAN_V1__"
SCAN_UNSUPPORTED_MARKER = "__ANDROID_EVERYTHING_SCAN_UNSUPPORTED__"
SCAN_RECORD_MARKER = "F"
INDEX_STAGING_BATCH_SIZE = 1000
MAX_SCAN_FIELD_BYTES = 16 * 1024 * 1024


class StructuredScanUnavailable(Exception):
    """Raised when the device lacks commands required by scan protocol v1."""


_PERMISSION_DENIED_PATTERNS = (
    re.compile(
        r"^(?P<command>ls|find):\s+cannot open directory\s+(?P<path>.+):\s*"
        r"Permission denied\s*$"
    ),
    re.compile(
        r"^(?P<command>ls|find):\s+(?P<path>.+):\s*"
        r"Permission denied\s*$"
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
            staging_id = None
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
                
                staging_id = self.db.begin_index_staging(device_serial)
                staged_count = 0
                pending_batch = []

                def flush_pending_batch(progress_value: int):
                    nonlocal staged_count
                    if not pending_batch:
                        return

                    staged_count += self.db.stage_files_batch(
                        staging_id,
                        device_serial,
                        pending_batch,
                    )
                    pending_batch.clear()
                    if progress_callback:
                        progress_callback(
                            f"Staged {staged_count} files...",
                            progress_value,
                            100,
                        )

                # Parse each path as ADB produces records and persist bounded
                # batches immediately. The searchable table remains unchanged.
                for i, scan_path in enumerate(scan_paths):
                    if self._cancel_requested:
                        raise IndexingCancelled()
                    
                    if progress_callback:
                        progress_callback(
                            f"Scanning {scan_path}...", 
                            int(i / len(scan_paths) * 70),
                            100
                        )
                    
                    files = self._scan_path(
                        device_serial,
                        scan_path,
                        progress_callback,
                    )
                    stage_progress = int(
                        (i + 1) / max(len(scan_paths), 1) * 70
                    )
                    for file_info in files:
                        if self._cancel_requested:
                            raise IndexingCancelled()

                        extension = (
                            os.path.splitext(file_info.name)[1].lower()
                            if file_info.name
                            else ""
                        )
                        modified = (
                            file_info.modified.isoformat()
                            if file_info.modified
                            else None
                        )
                        pending_batch.append((
                            file_info.name,
                            file_info.path,
                            file_info.size,
                            modified,
                            1 if file_info.is_dir else 0,
                            extension,
                        ))
                        if len(pending_batch) >= INDEX_STAGING_BATCH_SIZE:
                            flush_pending_batch(stage_progress)

                    # Make progress durable even when each storage volume has
                    # fewer records than the normal batch size.
                    flush_pending_batch(stage_progress)

                if self._cancel_requested:
                    raise IndexingCancelled()

                if progress_callback:
                    progress_callback(
                        f"Preparing {staged_count} files...", 75, 100
                    )
                if self._cancel_requested:
                    raise IndexingCancelled()

                # Fetch metadata before opening the replacement transaction so
                # an ADB failure cannot affect the previous index.
                devices = self.adb.get_devices()
                model = next(
                    (d.model for d in devices if d.serial == device_serial),
                    ""
                )

                if progress_callback:
                    progress_callback(
                        "Publishing completed index...", 95, 100
                    )

                def before_publish_commit():
                    if self._cancel_requested:
                        raise IndexingCancelled()

                indexed_count = self.db.publish_staged_index(
                    staging_id,
                    device_serial,
                    model=model,
                    before_commit=before_publish_commit,
                )
                staging_id = None
                
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
                if staging_id is not None:
                    try:
                        self.db.discard_staged_index(staging_id)
                    except Exception:
                        # Preserve the original scan/cancellation result. Old
                        # staging runs are also reaped by a later scan.
                        pass
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
    ) -> Iterator[FileInfo]:
        """Scan a path with the NUL-delimited protocol, or a legacy fallback."""
        try:
            yield from self._scan_path_structured(device_serial, path)
        except StructuredScanUnavailable:
            yield from self._scan_path_legacy(device_serial, path)

    def _scan_path_structured(
        self,
        device_serial: str,
        path: str,
    ) -> Iterator[FileInfo]:
        """Stream exact file metadata using the NUL-delimited scan protocol."""
        command = self._build_structured_scan_command(path)
        shell_stream = getattr(self.adb, "shell_stream", None)
        if shell_stream is None:
            # Compatibility for third-party ADBWrapper substitutes. The real
            # wrapper always takes the bounded streaming path.
            chunks = iter((self.adb.shell_bytes(
                command,
                timeout=180,
                device_serial=device_serial,
            ),))
        else:
            chunks = shell_stream(
                command,
                timeout=180,
                device_serial=device_serial,
                cancel_check=lambda: self._cancel_requested,
            )

        try:
            try:
                yield from self._parse_structured_scan(path, chunks)
            except ADBCommandCancelled as error:
                raise IndexingCancelled() from error
        finally:
            close_stream = getattr(chunks, "close", None)
            if close_stream:
                close_stream()

    @staticmethod
    def _build_structured_scan_command(path: str) -> str:
        """Build the remote shell command for scan protocol version 1."""
        quoted_path = shlex.quote(path)
        worker_script = (
            "for scan_file do "
            "scan_metadata=$(stat -c '%s %Y' \"$scan_file\") || exit $?; "
            "scan_size=${scan_metadata%% *}; "
            "scan_mtime=${scan_metadata#* }; "
            f"printf '{SCAN_RECORD_MARKER}\\000%s\\000%s\\000%s\\000' "
            "\"$scan_size\" \"$scan_mtime\" \"$scan_file\"; "
            "done"
        )
        quoted_worker = shlex.quote(worker_script)
        return (
            "if ! command -v find >/dev/null 2>&1 || "
            "! command -v stat >/dev/null 2>&1; then "
            f"printf '{SCAN_UNSUPPORTED_MARKER}\\000'; exit 0; fi; "
            f"if ! ls -ld {quoted_path} >/dev/null; then exit 2; fi; "
            f"if ! stat -c '%s %Y' {quoted_path} >/dev/null 2>&1; then "
            f"printf '{SCAN_UNSUPPORTED_MARKER}\\000'; exit 0; fi; "
            f"printf '{SCAN_PROTOCOL_MARKER}\\000'; "
            "exec 3>&1; "
            f"scan_errors=$(find {quoted_path} -type f -exec sh -c "
            f"{quoted_worker} sh {{}} + 2>&1 1>&3); "
            "scan_status=$?; "
            "exec 3>&-; "
            f"printf '{SCAN_ERRORS_MARKER}\\000%s\\000' \"$scan_errors\"; "
            f"printf '{SCAN_COMPLETE_MARKER}\\000%s\\000' \"$scan_status\"; "
            "exit 0"
        )

    def _parse_structured_scan(
        self,
        scan_path: str,
        chunks: Iterable[bytes],
    ) -> Iterator[FileInfo]:
        """Incrementally validate and parse a NUL-delimited scan stream."""
        if isinstance(chunks, bytes):
            chunks = (chunks,)
        fields = iter(self._iter_nul_fields(chunks))
        protocol_marker = SCAN_PROTOCOL_MARKER.encode("ascii")
        unsupported_marker = SCAN_UNSUPPORTED_MARKER.encode("ascii")
        record_marker = SCAN_RECORD_MARKER.encode("ascii")
        errors_marker = SCAN_ERRORS_MARKER.encode("ascii")
        complete_marker = SCAN_COMPLETE_MARKER.encode("ascii")
        end_marker = object()

        if self._cancel_requested:
            raise IndexingCancelled()
        first_field = next(fields, end_marker)
        if first_field == unsupported_marker:
            if next(fields, end_marker) is not end_marker:
                raise ADBError(
                    "ADB structured scan returned invalid unsupported payload"
                )
            raise StructuredScanUnavailable()
        if first_field != protocol_marker:
            raise ADBError(
                "ADB structured scan returned an invalid protocol marker"
            )

        file_count = 0
        while True:
            if self._cancel_requested:
                raise IndexingCancelled()

            field = next(fields, end_marker)
            if field is end_marker:
                raise ADBError("ADB structured scan returned an invalid trailer")
            if field == errors_marker:
                break
            if field != record_marker:
                raise ADBError("ADB structured scan returned an invalid trailer")

            size_field = next(fields, end_marker)
            modified_field = next(fields, end_marker)
            path_field = next(fields, end_marker)
            if (
                size_field is end_marker
                or modified_field is end_marker
                or path_field is end_marker
            ):
                raise ADBError("ADB structured scan returned a truncated record")

            try:
                size_text = size_field.decode("ascii")
                modified_text = modified_field.decode("ascii")
            except UnicodeDecodeError as error:
                raise ADBError(
                    "ADB structured scan returned non-numeric metadata"
                ) from error

            if (
                not re.fullmatch(r"\d+", size_text)
                or not re.fullmatch(r"-?\d+", modified_text)
            ):
                raise ADBError(
                    "ADB structured scan returned invalid numeric metadata"
                )

            size = int(size_text)
            if size > 9223372036854775807:
                raise ADBError("ADB structured scan returned an invalid file size")

            remote_path = path_field.decode("utf-8", errors="replace")
            self._validate_structured_file_path(scan_path, remote_path)
            name = posixpath.basename(remote_path)
            if not name or self._should_skip_file(remote_path):
                continue

            try:
                modified = datetime.fromtimestamp(int(modified_text))
            except (OSError, OverflowError, ValueError):
                modified = None

            file_count += 1
            yield FileInfo(
                name=name,
                path=remote_path,
                size=size,
                modified=modified,
                is_dir=False,
            )

        scan_errors_field = next(fields, end_marker)
        received_complete_marker = next(fields, end_marker)
        status_field = next(fields, end_marker)
        if (
            scan_errors_field is end_marker
            or received_complete_marker != complete_marker
            or status_field is end_marker
            or next(fields, end_marker) is not end_marker
        ):
            raise ADBError("ADB structured scan returned an invalid trailer")

        scan_errors = scan_errors_field.decode("utf-8", errors="replace")
        try:
            status_text = status_field.decode("ascii")
        except UnicodeDecodeError as error:
            raise ADBError(
                "ADB scan returned an invalid completion status"
            ) from error
        if not re.fullmatch(r"\d+", status_text):
            raise ADBError("ADB scan returned an invalid completion status")

        scan_status = int(status_text)
        self._validate_scan_errors(
            scan_path,
            scan_status,
            scan_errors,
            allowed_commands={"find"},
        )
        if scan_status == 1 and file_count == 0:
            raise ADBError("Android file listing failed without returning data")

    def _iter_nul_fields(
        self,
        chunks: Iterable[bytes],
    ) -> Iterator[bytes]:
        """Yield complete NUL-delimited fields without buffering the scan."""
        pending = bytearray()
        for chunk in chunks:
            if self._cancel_requested:
                raise IndexingCancelled()
            if not isinstance(chunk, bytes):
                raise ADBError("ADB structured scan returned non-binary output")
            if not chunk:
                continue

            pending.extend(chunk)
            field_start = 0
            while True:
                delimiter_index = pending.find(0, field_start)
                if delimiter_index < 0:
                    if len(pending) - field_start > MAX_SCAN_FIELD_BYTES:
                        raise ADBError(
                            "ADB structured scan returned an oversized field"
                        )
                    break

                if delimiter_index - field_start > MAX_SCAN_FIELD_BYTES:
                    raise ADBError(
                        "ADB structured scan returned an oversized field"
                    )
                field = bytes(pending[field_start:delimiter_index])
                field_start = delimiter_index + 1
                yield field

            if field_start:
                del pending[:field_start]

        if pending:
            raise ADBError(
                "ADB structured scan ended without a complete NUL field"
            )

    @staticmethod
    def _validate_structured_file_path(scan_path: str, file_path: str):
        """Require every structured record to belong to the requested root."""
        normalized_root = posixpath.normpath(scan_path)
        normalized_file = posixpath.normpath(file_path)
        child_prefix = normalized_root.rstrip("/") + "/"
        if (
            not file_path
            or "\0" in file_path
            or not normalized_root.startswith("/")
            or not normalized_file.startswith(child_prefix)
        ):
            raise ADBError(
                "ADB structured scan returned a path outside the scan root"
            )

    @staticmethod
    def _should_skip_file(file_path: str) -> bool:
        """Return whether a file belongs to an intentionally skipped folder."""
        directory_parts = file_path.split("/")[1:-1]
        if ".thumbnails" in directory_parts:
            return True
        return any(
            directory_parts[index:index + 2] == ["Android", "data"]
            for index in range(len(directory_parts) - 1)
        )

    def _scan_path_legacy(
        self,
        device_serial: str,
        path: str,
    ) -> List[FileInfo]:
        """Scan with recursive ls when find/stat protocol support is absent."""
        files = []
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
        self._validate_scan_errors(
            path,
            scan_status,
            scan_errors,
            allowed_commands={"ls"},
        )

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
                        if self._should_skip_file(file_path):
                            continue
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
        allowed_commands=None,
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
            FileIndexer._is_permission_denied_child(
                scan_path,
                line,
                allowed_commands=allowed_commands,
            )
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
    def _is_permission_denied_child(
        scan_path: str,
        error_line: str,
        allowed_commands=None,
    ) -> bool:
        """Return whether a scan diagnostic names a child of ``scan_path``."""
        denied_path = None
        for pattern in _PERMISSION_DENIED_PATTERNS:
            match = pattern.fullmatch(error_line)
            if match:
                if (
                    allowed_commands is not None
                    and match.group("command") not in allowed_commands
                ):
                    return False
                denied_path = match.group("path").strip()
                break

        if denied_path is None:
            return False

        quote_pairs = {"'": "'", '"': '"', "‘": "’", "“": "”"}
        if (
            len(denied_path) >= 2
            and quote_pairs.get(denied_path[0]) == denied_path[-1]
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
