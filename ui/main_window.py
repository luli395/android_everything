"""
Main application window for Android Everything.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import logging
import threading
from typing import Callable, List, Optional
import os

from ui.styles import COLORS, FONTS, apply_dark_theme
from ui.file_list import FileListView

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT, SEARCH_DELAY_MS
from adb_wrapper import get_adb, ADBWrapper, DeviceInfo, ADBError
from file_indexer import FileIndexer
from search_engine import get_search_engine, SearchEngine
from database import SearchQueryError
from path_utils import (
    available_download_path,
    cached_download_path,
    sanitize_windows_filename,
)


logger = logging.getLogger(__name__)


class MainWindow:
    """Main application window."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(800, 600)
        
        # Set window icon (optional)
        try:
            self.root.iconbitmap(default="")
        except:
            pass
        
        # Apply theme
        apply_dark_theme(self.root)
        
        # Initialize local components even when ADB is not installed, so the
        # packaged application can still open and explain how to configure it.
        self.search_engine = get_search_engine()
        self.adb: Optional[ADBWrapper] = None
        self.indexer: Optional[FileIndexer] = None
        self._adb_error: Optional[str] = None
        try:
            self.adb = get_adb()
            self.indexer = FileIndexer(adb=self.adb, db=self.search_engine.db)
        except ADBError as e:
            self._adb_error = str(e)
        
        # State
        self._devices: List[DeviceInfo] = []
        self._current_device: Optional[str] = None
        self._search_timer: Optional[str] = None
        self._device_operation_count = 0
        
        # Build UI
        self._setup_ui()
        
        # Initial device check, or a non-fatal setup notice if ADB is missing.
        if self.adb:
            self.root.after(100, self._refresh_devices)
        else:
            self.refresh_btn.configure(state="disabled")
            self.index_btn.configure(state="disabled")
            self.status_var.set("ADB not found. Install Android Platform Tools, then restart the app.")
            self.root.after(100, self._show_adb_setup_notice)

    def _show_adb_setup_notice(self):
        """Explain missing ADB after the main window has initialized."""
        messagebox.showwarning(
            "ADB Setup Required",
            f"{self._adb_error}\n\nThe application can remain open, but device "
            "operations require ADB. Install Android Platform Tools and restart "
            "Android Everything.",
            parent=self.root,
        )
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        
        # Header
        self._create_header()
        
        # Main content area
        self._create_content()
        
        # Status bar
        self._create_statusbar()

    def _begin_device_operation(self):
        """Lock device selection while a device-bound operation is active."""
        self._device_operation_count += 1
        if self._device_operation_count == 1:
            self.device_combo.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")

    def _end_device_operation(self):
        """Release one device-operation lock and restore the controls."""
        if self._device_operation_count <= 0:
            logger.warning("Unbalanced device-operation unlock request")
            return

        self._device_operation_count -= 1
        if self._device_operation_count == 0:
            if self.adb:
                self.device_combo.configure(state="readonly")
                self.refresh_btn.configure(state="normal")
            else:
                self.device_combo.configure(state="disabled")
                self.refresh_btn.configure(state="disabled")

    def _start_device_worker(self, target: Callable[[], None]):
        """Run a background device task while holding the selector lock."""
        self._begin_device_operation()

        def run_target():
            try:
                target()
            except Exception as error:
                logger.exception("Background device operation failed")
                message = str(error)
                self.root.after(
                    0,
                    lambda: self._on_device_worker_failure(message),
                )

        try:
            threading.Thread(target=run_target, daemon=True).start()
        except Exception:
            self._end_device_operation()
            raise

    def _on_device_worker_failure(self, error: str):
        """Restore device controls after an unexpected worker failure."""
        self._end_device_operation()
        self.status_var.set(f"Device operation failed: {error}")
        messagebox.showerror(
            "Device Operation Error",
            f"The device operation failed.\n\n{error}",
            parent=self.root,
        )
    
    def _create_header(self):
        """Create the header with search bar and controls."""
        header = ttk.Frame(self.root, style="Card.TFrame", padding=15)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        header.columnconfigure(1, weight=1)
        
        # App title
        title_frame = ttk.Frame(header, style="Card.TFrame")
        title_frame.grid(row=0, column=0, sticky="w", padx=(0, 20))
        
        ttk.Label(
            title_frame,
            text="Android",
            style="Title.TLabel"
        ).pack(side="left")
        
        ttk.Label(
            title_frame,
            text="Everything",
            style="Title.TLabel",
            foreground=COLORS["text_primary"]
        ).pack(side="left")
        
        # Search bar
        search_frame = ttk.Frame(header, style="Card.TFrame")
        search_frame.grid(row=0, column=1, sticky="ew", padx=10)
        search_frame.columnconfigure(0, weight=1)
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_changed)
        
        self.search_entry = ttk.Entry(
            search_frame,
            textvariable=self.search_var,
            font=FONTS["heading"]
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", ipady=8)
        self.search_entry.bind("<Return>", lambda e: self._do_search())
        
        # Placeholder text
        self.search_entry.insert(0, "Search files...")
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self._search_has_focus = False
        
        # Extension filter
        ttk.Label(
            search_frame,
            text="Type:",
            style="Muted.TLabel"
        ).grid(row=0, column=1, padx=(10, 5))
        
        self.ext_var = tk.StringVar(value="All")
        self.ext_combo = ttk.Combobox(
            search_frame,
            textvariable=self.ext_var,
            values=["All"],
            width=10,
            state="readonly"
        )
        self.ext_combo.grid(row=0, column=2)
        self.ext_combo.bind("<<ComboboxSelected>>", lambda e: self._do_search())
        
        # Controls
        controls_frame = ttk.Frame(header, style="Card.TFrame")
        controls_frame.grid(row=0, column=2, sticky="e", padx=(20, 0))
        
        # Device selector
        ttk.Label(
            controls_frame,
            text="Device:",
            style="Muted.TLabel"
        ).pack(side="left", padx=(0, 5))
        
        self.device_var = tk.StringVar(value="No device")
        self.device_combo = ttk.Combobox(
            controls_frame,
            textvariable=self.device_var,
            values=[],
            width=20,
            state="readonly"
        )
        self.device_combo.pack(side="left", padx=(0, 10))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        
        # Refresh button
        self.refresh_btn = ttk.Button(
            controls_frame,
            text="🔄 Refresh",
            command=self._refresh_devices,
            width=10
        )
        self.refresh_btn.pack(side="left", padx=(0, 5))
        
        # Index button
        self.index_btn = ttk.Button(
            controls_frame,
            text="📥 Index",
            style="Accent.TButton",
            command=self._start_indexing,
            width=10
        )
        self.index_btn.pack(side="left")
    
    def _create_content(self):
        """Create the main content area with file list."""
        content = ttk.Frame(self.root)
        content.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        
        # File list
        self.file_list = FileListView(
            content,
            on_double_click=self._on_file_double_click,
            on_right_click=self._on_file_right_click
        )
        self.file_list.grid(row=0, column=0, sticky="nsew")
        
        # Context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.configure(
            bg=COLORS["bg_medium"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent"],
            activeforeground=COLORS["text_primary"],
            font=FONTS["body"]
        )
        self.context_menu.add_command(label="📥 Pull to PC", command=self._pull_selected)
        self.context_menu.add_command(label="📂 Show in Explorer", command=self._show_in_explorer)
        self.context_menu.add_command(label="📋 Copy Path", command=self._copy_path)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ Delete", command=self._delete_selected)
    
    def _create_statusbar(self):
        """Create the status bar."""
        statusbar = ttk.Frame(self.root, style="Card.TFrame", padding=8)
        statusbar.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 10))
        statusbar.columnconfigure(1, weight=1)
        
        # Status message
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            statusbar,
            textvariable=self.status_var,
            style="Muted.TLabel"
        ).grid(row=0, column=0, sticky="w")
        
        # Progress bar
        self.progress = ttk.Progressbar(
            statusbar,
            mode="determinate",
            length=200
        )
        self.progress.grid(row=0, column=1, sticky="e", padx=10)
        self.progress.grid_remove()  # Hidden by default
        
        # File count
        self.count_var = tk.StringVar(value="0 files")
        ttk.Label(
            statusbar,
            textvariable=self.count_var,
            style="Muted.TLabel"
        ).grid(row=0, column=2, sticky="e")
    
    def _on_search_focus_in(self, event):
        """Handle search entry focus in."""
        if not self._search_has_focus:
            self._search_has_focus = True
            if self.search_entry.get() == "Search files...":
                self.search_entry.delete(0, "end")
    
    def _on_search_focus_out(self, event):
        """Handle search entry focus out."""
        if not self.search_entry.get():
            self._search_has_focus = False
            self.search_entry.insert(0, "Search files...")
    
    def _on_search_changed(self, *args):
        """Handle search text change with debounce."""
        if self._search_timer:
            self.root.after_cancel(self._search_timer)
        
        self._search_timer = self.root.after(SEARCH_DELAY_MS, self._do_search)
    
    def _do_search(self):
        """Execute the search."""
        if not self._current_device:
            return
        
        query = self.search_var.get()
        if query == "Search files...":
            query = ""
        
        # Get extension filter
        ext_filter = None
        if self.ext_var.get() != "All":
            ext_filter = "." + self.ext_var.get().lower()
        
        # Search. Keep malformed/corrupt database queries from escaping the
        # Tkinter event callback and terminating the UI.
        try:
            results = self.search_engine.search(
                self._current_device,
                query,
                extension_filter=ext_filter
            )
        except SearchQueryError as error:
            logger.exception("Search query failed")
            self.file_list.clear()
            self.count_var.set("Search unavailable")
            self.status_var.set(str(error))
            return
        
        # Update file list
        self.file_list.set_files(results)
        self.count_var.set(f"{len(results):,} files")
        self.status_var.set("Ready")
    
    def _refresh_devices(self):
        """Refresh the list of connected devices."""
        if not self.adb:
            return
        
        try:
            self._devices = self.adb.get_devices()
            
            if self._devices:
                device_names = [
                    f"{d.model or d.serial} ({d.state})" 
                    for d in self._devices
                ]
                self.device_combo.configure(values=device_names)
                
                # Auto-select first device
                if not self._current_device or self._current_device not in [d.serial for d in self._devices]:
                    self.device_combo.current(0)
                    self._on_device_selected(None)
                
                self.status_var.set(f"{len(self._devices)} device(s) connected")
            else:
                self.device_combo.configure(values=["No device"])
                self.device_var.set("No device")
                self._current_device = None
                self.status_var.set("No devices found. Enable USB debugging on your phone.")
                
        except ADBError as e:
            self.status_var.set(f"ADB Error: {e}")
    
    def _on_device_selected(self, event):
        """Handle device selection."""
        idx = self.device_combo.current()
        if idx >= 0 and idx < len(self._devices):
            device = self._devices[idx]
            self._current_device = device.serial
            
            # Load file count
            count = self.search_engine.get_file_count(device.serial)
            if count > 0:
                self.count_var.set(f"{count:,} files indexed")
                self._do_search()
                
                # Update extension filter
                stats = self.search_engine.get_extension_stats(device.serial)
                exts = ["All"] + [ext.upper().lstrip(".") for ext, _ in stats if ext]
                self.ext_combo.configure(values=exts)
            else:
                self.count_var.set("Not indexed - click Index")
                self.file_list.clear()
    
    def _start_indexing(self):
        """Start indexing the current device."""
        if not self.adb or not self.indexer:
            messagebox.showwarning(
                "ADB Setup Required",
                "Install Android Platform Tools and restart Android Everything.",
                parent=self.root,
            )
            return

        if not self._current_device:
            messagebox.showwarning("No Device", "Please connect and select a device first.")
            return
        
        if self.indexer.is_indexing:
            self.indexer.cancel()
            self.index_btn.configure(text="Stopping...", state="disabled")
            self.status_var.set("Stopping indexing...")
            return

        device_serial = self._current_device
        
        self.index_btn.configure(text="⏹️ Stop")
        self.progress.grid()
        self.progress["value"] = 0
        
        def on_progress(message: str, current: int, total: int):
            self.root.after(0, lambda: self._update_progress(message, current, total))
        
        def on_complete(count: int):
            self.root.after(
                0,
                lambda: self._on_indexing_complete(device_serial, count),
            )

        def on_error(error: Exception):
            self.root.after(0, lambda: self._on_indexing_error(str(error)))

        def on_cancelled():
            self.root.after(0, self._on_indexing_cancelled)
        
        self._begin_device_operation()
        try:
            started = self.indexer.index_device(
                device_serial,
                progress_callback=on_progress,
                complete_callback=on_complete,
                error_callback=on_error,
                cancelled_callback=on_cancelled,
            )
        except Exception:
            self._end_device_operation()
            self.index_btn.configure(text="📥 Index", state="normal")
            self.progress.grid_remove()
            raise

        if not started:
            self._end_device_operation()
            self.index_btn.configure(text="📥 Index", state="normal")
            self.progress.grid_remove()
            self.status_var.set("Indexing is already in progress")
    
    def _update_progress(self, message: str, current: int, total: int):
        """Update progress bar and status."""
        self.status_var.set(message)
        self.progress["value"] = current
    
    def _on_indexing_complete(self, device_serial: str, count: int):
        """Handle indexing completion."""
        self._end_device_operation()
        self.index_btn.configure(text="📥 Index", state="normal")
        self.progress.grid_remove()

        if self._current_device != device_serial:
            self.status_var.set(
                f"Indexed {count:,} files on {device_serial}"
            )
            self.search_engine.clear_cache()
            return

        self.count_var.set(f"{count:,} files indexed")
        
        # Update extension filter
        stats = self.search_engine.get_extension_stats(device_serial)
        exts = ["All"] + [ext.upper().lstrip(".") for ext, _ in stats if ext]
        self.ext_combo.configure(values=exts)
        
        # Clear cache and refresh results
        self.search_engine.clear_cache()
        self._do_search()

    def _on_indexing_error(self, error: str):
        """Restore the UI after a failed indexing attempt."""
        self._end_device_operation()
        self.index_btn.configure(text="📥 Index", state="normal")
        self.progress.grid_remove()
        self.status_var.set(
            f"Indexing failed: {error}. Previous index preserved."
        )

    def _on_indexing_cancelled(self):
        """Restore the UI after a cancelled indexing attempt."""
        self._end_device_operation()
        self.index_btn.configure(text="📥 Index", state="normal")
        self.progress.grid_remove()
        self.status_var.set(
            "Indexing cancelled. Previous index preserved."
        )
    
    def _on_file_double_click(self, file: dict):
        """Handle double-click on a file - download and open."""
        device_serial = self._current_device
        remote_path = file.get("path", "")
        filename = file.get("name", "file")
        if not self.adb or not device_serial or not remote_path:
            return
        
        # Download to temp folder and open
        import tempfile
        temp_dir = os.path.join(tempfile.gettempdir(), "android_everything")
        os.makedirs(temp_dir, exist_ok=True)
        cache_identity = f"{device_serial}\0{remote_path}"
        local_path = cached_download_path(
            temp_dir,
            filename,
            cache_identity,
        )
        
        self.status_var.set(f"Downloading {filename}...")
        
        def do_download_and_open():
            success = self.adb.pull_file(
                remote_path,
                local_path,
                device_serial=device_serial,
            )
            self.root.after(0, lambda: self._on_open_complete(success, filename, local_path))
        
        self._start_device_worker(do_download_and_open)
    
    def _on_open_complete(self, success: bool, filename: str, local_path: str):
        """Handle download complete and open file."""
        self._end_device_operation()
        if success and os.path.exists(local_path):
            self.status_var.set(f"Opening {filename}...")
            try:
                os.startfile(local_path)  # Windows: open with default app
                self.status_var.set(f"Opened: {filename}")
            except Exception as e:
                self.status_var.set(f"Error opening: {e}")
        else:
            self.status_var.set(f"Failed to download: {filename}")
            messagebox.showerror("Download Error", f"Failed to download {filename}")
    
    def _on_file_right_click(self, file: dict, x: int, y: int):
        """Show context menu."""
        self.context_menu.tk_popup(x, y)
    
    def _pull_file(self, file: dict):
        """Pull a single file to PC."""
        device_serial = self._current_device
        remote_path = file.get("path", "")
        if not self.adb or not device_serial or not remote_path:
            return
        
        # Ask for save location
        default_name = file.get("name", "file")
        local_path = filedialog.asksaveasfilename(
            initialfile=sanitize_windows_filename(default_name),
            title="Save file to..."
        )
        
        if not local_path:
            return
        
        self.status_var.set(f"Downloading {default_name}...")
        
        def do_pull():
            success = self.adb.pull_file(
                remote_path,
                local_path,
                device_serial=device_serial,
            )
            self.root.after(0, lambda: self._on_pull_complete(success, default_name))
        
        self._start_device_worker(do_pull)
    
    def _on_pull_complete(self, success: bool, filename: str):
        """Handle pull completion."""
        self._end_device_operation()
        if success:
            self.status_var.set(f"Downloaded: {filename}")
        else:
            self.status_var.set(f"Failed to download: {filename}")
            messagebox.showerror("Download Error", f"Failed to download {filename}")
    
    def _pull_selected(self):
        """Pull selected files to PC."""
        device_serial = self._current_device
        if not self.adb or not device_serial:
            return

        files = self.file_list.get_selected_files()
        if not files:
            return
        
        if len(files) == 1:
            self._pull_file(files[0])
        else:
            # Multiple files - ask for folder
            folder = filedialog.askdirectory(title="Save files to folder...")
            if not folder:
                return
            
            self.status_var.set(f"Downloading {len(files)} files...")
            
            def do_pull_multiple():
                downloaded = 0
                failed = 0
                reserved_paths = set()
                for file in files:
                    remote_path = file.get("path", "")
                    name = file.get("name", "file")
                    if not remote_path:
                        failed += 1
                        continue

                    local_path = available_download_path(
                        folder,
                        name,
                        reserved_paths,
                    )
                    if self.adb.pull_file(
                        remote_path,
                        local_path,
                        device_serial=device_serial,
                    ):
                        downloaded += 1
                    else:
                        failed += 1

                self.root.after(
                    0,
                    lambda: self._on_pull_multiple_complete(downloaded, failed),
                )
            
            self._start_device_worker(do_pull_multiple)

    def _on_pull_multiple_complete(self, downloaded: int, failed: int):
        """Report the real outcome of a multi-file download."""
        self._end_device_operation()
        self.status_var.set(
            f"Downloaded {downloaded} file(s); {failed} failed"
        )
        if failed:
            messagebox.showwarning(
                "Download Warning",
                f"Downloaded {downloaded} file(s). {failed} file(s) failed.",
            )
    
    def _show_in_explorer(self):
        """Download file and show in Windows Explorer."""
        device_serial = self._current_device
        if not self.adb or not device_serial:
            return

        files = self.file_list.get_selected_files()
        if not files:
            return
        
        file = files[0]  # Show first selected file
        remote_path = file.get("path", "")
        filename = file.get("name", "file")
        if not remote_path:
            return
        
        # Download to temp folder
        import tempfile
        temp_dir = os.path.join(tempfile.gettempdir(), "android_everything")
        os.makedirs(temp_dir, exist_ok=True)
        cache_identity = f"{device_serial}\0{remote_path}"
        local_path = cached_download_path(
            temp_dir,
            filename,
            cache_identity,
        )
        
        self.status_var.set(f"Downloading {filename}...")
        
        def do_download_and_show():
            success = self.adb.pull_file(
                remote_path,
                local_path,
                device_serial=device_serial,
            )
            self.root.after(0, lambda: self._on_show_complete(success, filename, local_path))
        
        self._start_device_worker(do_download_and_show)
    
    def _on_show_complete(self, success: bool, filename: str, local_path: str):
        """Handle download complete and show in Explorer."""
        import subprocess
        self._end_device_operation()
        if success and os.path.exists(local_path):
            self.status_var.set(f"Showing {filename} in Explorer...")
            try:
                # Open Explorer and select the file
                subprocess.run(['explorer', '/select,', local_path])
                self.status_var.set(f"Opened folder: {filename}")
            except Exception as e:
                self.status_var.set(f"Error opening Explorer: {e}")
        else:
            self.status_var.set(f"Failed to download: {filename}")
            messagebox.showerror("Download Error", f"Failed to download {filename}")
    
    def _copy_path(self):
        """Copy file path to clipboard."""
        files = self.file_list.get_selected_files()
        if files:
            paths = "\n".join(f.get("path", "") for f in files)
            self.root.clipboard_clear()
            self.root.clipboard_append(paths)
            self.status_var.set("Path copied to clipboard")
    
    def _delete_selected(self):
        """Delete selected files."""
        device_serial = self._current_device
        if not self.adb or not device_serial:
            return

        files = self.file_list.get_selected_files()
        if not files:
            return
        
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Delete {len(files)} file(s) from device?\n\nThis cannot be undone!"
        )
        
        if not confirm:
            return
        
        self.status_var.set(f"Deleting {len(files)} files...")
        
        def do_delete():
            deleted = 0
            deleted_paths = []
            for file in files:
                path = file.get("path", "")
                if self.adb.delete_file(
                    path,
                    device_serial=device_serial,
                ):
                    deleted += 1
                    deleted_paths.append(path)
            
            self.root.after(
                0,
                lambda: self._on_delete_complete(
                    device_serial,
                    deleted,
                    len(files),
                    deleted_paths,
                ),
            )
        
        self._start_device_worker(do_delete)
    
    def _on_delete_complete(
        self,
        device_serial: str,
        deleted: int,
        total: int,
        deleted_paths: list,
    ):
        """Handle delete completion."""
        self._end_device_operation()
        self.status_var.set(f"Deleted {deleted}/{total} files")
        
        # Remove deleted files from database index
        if deleted_paths:
            from database import get_database
            db = get_database()
            db.delete_files(device_serial, deleted_paths)
            self.search_engine.clear_cache()
        
        if deleted < total:
            messagebox.showwarning("Delete Warning", f"Some files could not be deleted ({total - deleted} failed)")
        
        # Refresh search
        if self._current_device == device_serial:
            self._do_search()
    
    def run(self):
        """Run the application."""
        # Center window
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"+{x}+{y}")
        
        self.root.mainloop()
