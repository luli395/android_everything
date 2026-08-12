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
        complete_callback: Optional[Callable[[int], None]] = None
    ):
        """
        Start indexing device files in background.
        
        Args:
            device_serial: Device to index
            paths: Paths to scan (default: SCAN_PATHS from config)
            progress_callback: Called with (status_message, current, total)
            complete_callback: Called with total file count when done
        """
        if self._indexing:
            return
        
        # Auto-detect storage paths if not provided
        if paths is None:
            paths = self.adb.get_storage_paths()
            if not paths:
                paths = ["/storage/emulated/0"]  # Fallback
        
        def do_index():
            self._indexing = True
            self._cancel_requested = False
            
            try:
                self.adb.select_device(device_serial)
                
                # Clear existing files for this device
                if progress_callback:
                    progress_callback("Clearing old index...", 0, 100)
                
                self.db.clear_device_files(device_serial)
                
                all_files = []
                
                # Scan each path
                for i, scan_path in enumerate(paths):
                    if self._cancel_requested:
                        break
                    
                    if progress_callback:
                        progress_callback(
                            f"Scanning {scan_path}...", 
                            int(i / len(paths) * 50), 
                            100
                        )
                    
                    # Get files from device
                    files = self._scan_path(scan_path, progress_callback)
                    all_files.extend(files)
                
                if self._cancel_requested:
                    if progress_callback:
                        progress_callback("Cancelled", 0, 100)
                    return
                
                # Insert into database
                if progress_callback:
                    progress_callback(f"Indexing {len(all_files)} files...", 75, 100)
                
                # Prepare data for batch insert
                batch_data = []
                for f in all_files:
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
                
                # Insert in batches
                batch_size = 5000
                for i in range(0, len(batch_data), batch_size):
                    if self._cancel_requested:
                        break
                    
                    batch = batch_data[i:i + batch_size]
                    self.db.insert_files_batch(device_serial, batch)
                    
                    if progress_callback:
                        progress = 75 + int((i / len(batch_data)) * 20)
                        progress_callback(
                            f"Saving to database... ({i}/{len(batch_data)})",
                            progress,
                            100
                        )
                
                # Update device info
                devices = self.adb.get_devices()
                model = next(
                    (d.model for d in devices if d.serial == device_serial), 
                    ""
                )
                self.db.update_device_info(device_serial, model, len(all_files))
                
                if progress_callback:
                    progress_callback(f"Done! {len(all_files)} files indexed.", 100, 100)
                
                if complete_callback:
                    complete_callback(len(all_files))
                    
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Error: {str(e)}", 0, 100)
            finally:
                self._indexing = False
        
        # Start in background thread
        self._current_thread = threading.Thread(target=do_index, daemon=True)
        self._current_thread.start()
    
    def _scan_path(
        self, 
        path: str, 
        progress_callback: Optional[Callable] = None
    ) -> List[FileInfo]:
        """Scan a single path on the device with file sizes using ls -lR (fast)."""
        files = []
        
        try:
            # Use ls -lR for fast recursive listing with sizes
            # Format: -rw-rw---- 1 u0_a123 u0_a123 12345 2024-01-15 10:30 filename
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
                        
                        name = parts[7]
                        
                        # Handle filenames with spaces (take rest of line after time)
                        if len(parts) > 8:
                            # Reconstruct filename
                            idx = line.find(parts[6]) + len(parts[6]) + 1
                            if idx < len(line):
                                name = line[idx:]
                        
                        if name and name not in ['.', '..']:
                            file_path = f"{current_dir}/{name}"
                            files.append(FileInfo(
                                name=name,
                                path=file_path,
                                size=size,
                                modified=modified,
                                is_dir=False
                            ))
                    
        except Exception as e:
            print(f"Error scanning {path}: {e}")
        
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
        done_event = threading.Event()
        
        def on_complete(count):
            result[0] = count
            done_event.set()
        
        self.index_device(
            device_serial, 
            paths, 
            progress_callback, 
            on_complete
        )
        
        done_event.wait()
        return result[0]


# Singleton instance
_indexer: Optional[FileIndexer] = None


def get_indexer() -> FileIndexer:
    """Get the global file indexer instance."""
    global _indexer
    if _indexer is None:
        _indexer = FileIndexer()
    return _indexer
