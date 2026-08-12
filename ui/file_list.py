"""
File list component with virtual scrolling.
"""
import tkinter as tk
from tkinter import ttk
from typing import List, Callable, Optional
import os

from ui.styles import COLORS, FONTS, format_size, format_date

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_file_icon


class FileListView(ttk.Frame):
    """
    File list view with columns for displaying search results.
    Supports sorting and context menu.
    """
    
    def __init__(
        self, 
        parent, 
        on_double_click: Optional[Callable[[dict], None]] = None,
        on_right_click: Optional[Callable[[dict, int, int], None]] = None,
        **kwargs
    ):
        super().__init__(parent, **kwargs)
        
        self.on_double_click = on_double_click
        self.on_right_click = on_right_click
        self._files: List[dict] = []
        self._sort_column = "name"
        self._sort_reverse = False
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the file list UI."""
        # Create treeview with scrollbars
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        
        # Columns
        columns = ("icon", "name", "path", "size", "modified", "type")
        
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="extended"
        )
        
        # Configure columns
        self.tree.heading("icon", text="")
        self.tree.heading("name", text="Name", command=lambda: self._sort_by("name"))
        self.tree.heading("path", text="Path", command=lambda: self._sort_by("path"))
        self.tree.heading("size", text="Size", command=lambda: self._sort_by("size"))
        self.tree.heading("modified", text="Modified", command=lambda: self._sort_by("modified"))
        self.tree.heading("type", text="Type", command=lambda: self._sort_by("extension"))
        
        self.tree.column("icon", width=40, minwidth=40, stretch=False)
        self.tree.column("name", width=300, minwidth=150)
        self.tree.column("path", width=400, minwidth=200)
        self.tree.column("size", width=100, minwidth=80, anchor="e")
        self.tree.column("modified", width=150, minwidth=100)
        self.tree.column("type", width=80, minwidth=60)
        
        # Scrollbars
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        # Grid layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        # Bind events
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Return>", self._on_double_click)
        
        # Alternate row colors
        self.tree.tag_configure("odd", background=COLORS["row_alt"])
    
    def set_files(self, files: List[dict]):
        """
        Set the files to display.
        
        Args:
            files: List of file dictionaries
        """
        self._files = files
        self._refresh_tree()
    
    def _refresh_tree(self):
        """Refresh the treeview with current files."""
        # Clear existing items
        self.tree.delete(*self.tree.get_children())
        
        # Sort files
        sorted_files = sorted(
            self._files,
            key=lambda f: (f.get(self._sort_column) or "").lower() 
                if isinstance(f.get(self._sort_column), str) 
                else f.get(self._sort_column) or 0,
            reverse=self._sort_reverse
        )
        
        # Add files to tree
        for i, file in enumerate(sorted_files):
            icon = get_file_icon(file.get("name", ""), file.get("is_dir", False))
            
            values = (
                icon,
                file.get("name", ""),
                file.get("path", ""),
                format_size(file.get("size", 0)),
                format_date(file.get("modified")),
                file.get("extension", "").upper().lstrip(".") or "-"
            )
            
            tags = ("odd",) if i % 2 else ()
            self.tree.insert("", "end", iid=str(i), values=values, tags=tags)
        
        # Store file data by index for retrieval
        self._file_map = {str(i): f for i, f in enumerate(sorted_files)}
    
    def _sort_by(self, column: str):
        """Sort the file list by column."""
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            # Default to descending for size (show largest first)
            self._sort_reverse = column == "size"
        
        self._refresh_tree()
    
    def _on_double_click(self, event):
        """Handle double-click on a file."""
        selection = self.tree.selection()
        if selection and self.on_double_click:
            file = self._file_map.get(selection[0])
            if file:
                self.on_double_click(file)
    
    def _on_right_click(self, event):
        """Handle right-click for context menu."""
        # Identify item under cursor
        item = self.tree.identify_row(event.y)
        if item:
            # Only change selection if clicked item is NOT already selected
            # This preserves multi-selection when right-clicking on selected items
            current_selection = self.tree.selection()
            if item not in current_selection:
                self.tree.selection_set(item)
            
            if self.on_right_click:
                file = self._file_map.get(item)
                if file:
                    self.on_right_click(file, event.x_root, event.y_root)
    
    def get_selected_files(self) -> List[dict]:
        """Get list of currently selected files."""
        return [
            self._file_map[item] 
            for item in self.tree.selection() 
            if item in self._file_map
        ]
    
    def clear(self):
        """Clear the file list."""
        self._files = []
        self.tree.delete(*self.tree.get_children())
        self._file_map = {}
