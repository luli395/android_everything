"""Virtualized file-list component for large search result sets."""

from collections import OrderedDict
import os
import sys
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from ui.styles import COLORS, format_date, format_size

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SEARCH_PAGE_SIZE,
    VIRTUAL_LIST_DEFAULT_ROWS,
    VIRTUAL_LIST_HEADER_HEIGHT,
    VIRTUAL_LIST_MAX_CACHED_PAGES,
    VIRTUAL_LIST_ROW_HEIGHT,
    get_file_icon,
)


class VirtualListModel:
    """Bounded page cache backing a virtual list without Tk dependencies."""

    def __init__(
        self,
        page_size: int = SEARCH_PAGE_SIZE,
        max_cached_pages: int = VIRTUAL_LIST_MAX_CACHED_PAGES,
    ):
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        if max_cached_pages <= 0:
            raise ValueError("max_cached_pages must be greater than zero")
        self.page_size = page_size
        self.max_cached_pages = max_cached_pages
        self.total_count = 0
        self._pages = OrderedDict()
        self._pending_pages = set()

    def reset(self, total_count: int = 0):
        """Discard cached pages and configure the logical result length."""
        if total_count < 0:
            raise ValueError("total_count must not be negative")
        self.total_count = total_count
        self._pages.clear()
        self._pending_pages.clear()

    def put_page(self, offset: int, items: List[dict]):
        """Cache a page returned for an aligned logical offset."""
        if offset < 0 or offset % self.page_size:
            raise ValueError("page offset must be non-negative and aligned")
        page_items = tuple(items[:self.page_size])
        self._pending_pages.discard(offset)
        self._pages[offset] = page_items
        self._pages.move_to_end(offset)
        while len(self._pages) > self.max_cached_pages:
            self._pages.popitem(last=False)

    def get(self, index: int) -> Optional[dict]:
        """Return a cached logical row, or ``None`` while its page loads."""
        if index < 0 or index >= self.total_count:
            return None
        page_offset = (index // self.page_size) * self.page_size
        page = self._pages.get(page_offset)
        if page is None:
            return None
        self._pages.move_to_end(page_offset)
        relative_index = index - page_offset
        if relative_index >= len(page):
            return None
        return page[relative_index]

    def request_missing(self, start: int, count: int) -> List[Tuple[int, int]]:
        """Mark and return uncached pages intersecting a visible range."""
        if count <= 0 or self.total_count <= 0:
            return []
        start = max(0, min(start, self.total_count - 1))
        end = min(self.total_count, start + count)
        first_page = (start // self.page_size) * self.page_size
        requests = []
        for page_offset in range(first_page, end, self.page_size):
            if (
                page_offset in self._pages
                or page_offset in self._pending_pages
            ):
                continue
            self._pending_pages.add(page_offset)
            requests.append((
                page_offset,
                min(self.page_size, self.total_count - page_offset),
            ))
        return requests

    def page_failed(self, offset: int):
        """Allow a failed page to be requested again on a later render."""
        self._pending_pages.discard(offset)

    @property
    def cached_page_count(self) -> int:
        """Expose cache size for diagnostics and focused unit tests."""
        return len(self._pages)


class FileListView(ttk.Frame):
    """Treeview facade that creates rows only for the visible viewport.

    Search results stay in a bounded :class:`VirtualListModel` page cache. The
    vertical scrollbar addresses logical result positions while a small pool
    of Treeview items is reused as the viewport moves.
    """

    _COLUMN_LABELS = {
        "icon": "",
        "name": "Name",
        "path": "Path",
        "size": "Size",
        "modified": "Modified",
        "extension": "Type",
    }

    def __init__(
        self,
        parent,
        on_double_click: Optional[Callable[[dict], None]] = None,
        on_right_click: Optional[Callable[[dict, int, int], None]] = None,
        on_sort_changed: Optional[Callable[[str, bool], None]] = None,
        **kwargs
    ):
        super().__init__(parent, **kwargs)
        self.on_double_click = on_double_click
        self.on_right_click = on_right_click
        self.on_sort_changed = on_sort_changed
        self._sort_column = "name"
        self._sort_reverse = False
        self._model = VirtualListModel()
        self._static_files: Optional[List[dict]] = None
        self._page_request: Optional[Callable[[int, int], None]] = None
        self._window_start = 0
        self._visible_rows = VIRTUAL_LIST_DEFAULT_ROWS
        self._file_map: Dict[str, dict] = {}
        self._absolute_index_map: Dict[str, int] = {}
        self._setup_ui()

    @property
    def sort_column(self) -> str:
        return self._sort_column

    @property
    def sort_reverse(self) -> bool:
        return self._sort_reverse

    @property
    def rendered_row_count(self) -> int:
        """Return the physical rows, not the logical result count."""
        return len(self.tree.get_children())

    def _setup_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        columns = ("icon", "name", "path", "size", "modified", "extension")
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="extended",
        )

        for column in columns:
            heading_options = {"text": self._COLUMN_LABELS[column]}
            if column != "icon":
                heading_options["command"] = (
                    lambda value=column: self._sort_by(value)
                )
            self.tree.heading(column, **heading_options)

        self.tree.column("icon", width=40, minwidth=40, stretch=False)
        self.tree.column("name", width=300, minwidth=150)
        self.tree.column("path", width=400, minwidth=200)
        self.tree.column("size", width=100, minwidth=80, anchor="e")
        self.tree.column("modified", width=150, minwidth=100)
        self.tree.column("extension", width=80, minwidth=60)

        self.vsb = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self._on_vertical_scroll,
        )
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Return>", self._on_double_click)
        self.tree.bind("<Configure>", self._on_configure)
        self.tree.bind("<MouseWheel>", self._on_mouse_wheel)
        self.tree.bind("<Button-4>", lambda event: self._scroll_units(-3))
        self.tree.bind("<Button-5>", lambda event: self._scroll_units(3))
        self.tree.bind("<Up>", lambda event: self._navigate(-1))
        self.tree.bind("<Down>", lambda event: self._navigate(1))
        self.tree.bind("<Prior>", lambda event: self._navigate_page(-1))
        self.tree.bind("<Next>", lambda event: self._navigate_page(1))
        self.tree.bind("<Home>", lambda event: self._navigate_to(0))
        self.tree.bind(
            "<End>",
            lambda event: self._navigate_to(self._model.total_count - 1),
        )
        self.tree.tag_configure("odd", background=COLORS["row_alt"])
        self.tree.tag_configure("loading", foreground=COLORS["text_secondary"])
        self._update_heading_labels()
        self._update_scrollbar()

    def begin_search(self):
        """Clear old rows immediately while an asynchronous query runs."""
        self._static_files = None
        self._page_request = None
        self._model.reset()
        self._window_start = 0
        self._render_window()

    def set_virtual_results(
        self,
        total_count: int,
        offset: int,
        files: List[dict],
        page_request: Callable[[int, int], None],
    ):
        """Start a virtual result set with its already-fetched first page."""
        self._static_files = None
        self._page_request = page_request
        self._model.reset(total_count)
        if files or total_count:
            self._model.put_page(offset, files)
        self._window_start = min(
            self._window_start,
            max(0, total_count - self._visible_rows),
        )
        self._render_window()

    def set_page(self, offset: int, files: List[dict]):
        """Apply an asynchronously loaded page and repaint visible rows."""
        if self._static_files is not None:
            return
        self._model.put_page(offset, files)
        self._render_window()

    def page_failed(self, offset: int):
        self._model.page_failed(offset)

    def set_files(self, files: List[dict]):
        """Compatibility path for callers with a complete in-memory list."""
        self._static_files = list(files)
        self._page_request = None
        self._sort_static_files()
        self._model.reset(len(self._static_files))
        self._window_start = 0
        self._render_window()

    def _get_file(self, absolute_index: int) -> Optional[dict]:
        if self._static_files is not None:
            if 0 <= absolute_index < len(self._static_files):
                return self._static_files[absolute_index]
            return None
        return self._model.get(absolute_index)

    def _on_configure(self, event):
        body_height = max(1, event.height - VIRTUAL_LIST_HEADER_HEIGHT)
        visible_rows = max(1, body_height // VIRTUAL_LIST_ROW_HEIGHT)
        if visible_rows != self._visible_rows:
            self._visible_rows = visible_rows
            self._window_start = min(
                self._window_start,
                max(0, self._model.total_count - visible_rows),
            )
            self._render_window()

    def _render_window(self):
        selected_indices = {
            self._absolute_index_map[item]
            for item in self.tree.selection()
            if item in self._absolute_index_map
        }
        total = self._model.total_count
        row_count = min(self._visible_rows, max(0, total - self._window_start))
        children = list(self.tree.get_children())

        while len(children) < row_count:
            iid = f"virtual-row-{len(children)}"
            self.tree.insert("", "end", iid=iid)
            children.append(iid)
        if len(children) > row_count:
            self.tree.delete(*children[row_count:])
            children = children[:row_count]

        self._file_map = {}
        self._absolute_index_map = {}
        selected_items = []
        for slot, iid in enumerate(children):
            absolute_index = self._window_start + slot
            file_info = self._get_file(absolute_index)
            self._absolute_index_map[iid] = absolute_index
            if file_info is None:
                values = ("", "Loading...", "", "", "", "")
                tags = ("loading",)
            else:
                self._file_map[iid] = file_info
                values = self._format_values(file_info)
                tags = ("odd",) if absolute_index % 2 else ()
            if absolute_index in selected_indices:
                selected_items.append(iid)
            self.tree.item(iid, values=values, tags=tags)

        self.tree.selection_set(selected_items)
        if children:
            self.tree.yview_moveto(0.0)
        self._update_scrollbar()
        self._request_visible_pages(row_count)

    @staticmethod
    def _format_values(file_info: dict) -> tuple:
        return (
            get_file_icon(
                file_info.get("name", ""),
                file_info.get("is_dir", False),
            ),
            file_info.get("name", ""),
            file_info.get("path", ""),
            format_size(file_info.get("size", 0)),
            format_date(file_info.get("modified")),
            (file_info.get("extension") or "").upper().lstrip(".") or "-",
        )

    def _request_visible_pages(self, row_count: int):
        if self._static_files is not None or self._page_request is None:
            return
        for offset, count in self._model.request_missing(
            self._window_start,
            row_count,
        ):
            try:
                self._page_request(offset, count)
            except Exception:
                self._model.page_failed(offset)
                raise

    def _update_scrollbar(self):
        total = self._model.total_count
        if total <= 0 or total <= self._visible_rows:
            self.vsb.set(0.0, 1.0)
            return
        first = self._window_start / total
        last = min(1.0, (self._window_start + self._visible_rows) / total)
        self.vsb.set(first, last)

    def _on_vertical_scroll(self, *args):
        if not args:
            return
        if args[0] == "moveto":
            fraction = max(0.0, min(1.0, float(args[1])))
            self._set_window_start(round(fraction * self._model.total_count))
        elif args[0] == "scroll":
            amount = int(args[1])
            if args[2] == "pages":
                amount *= max(1, self._visible_rows - 1)
            self._scroll_units(amount)

    def _on_mouse_wheel(self, event):
        delta = getattr(event, "delta", 0)
        units = -int(delta / 120) if delta else 0
        if units == 0 and delta:
            units = -1 if delta > 0 else 1
        self._scroll_units(units * 3)
        return "break"

    def _scroll_units(self, units: int):
        self._set_window_start(self._window_start + units)
        return "break"

    def _set_window_start(self, start: int):
        max_start = max(0, self._model.total_count - self._visible_rows)
        start = max(0, min(int(start), max_start))
        if start != self._window_start:
            self._window_start = start
            self._render_window()

    def _selected_absolute_index(self) -> int:
        selection = self.tree.selection()
        if selection:
            return self._absolute_index_map.get(selection[0], self._window_start)
        return self._window_start

    def _navigate(self, delta: int):
        return self._navigate_to(self._selected_absolute_index() + delta)

    def _navigate_page(self, direction: int):
        return self._navigate_to(
            self._selected_absolute_index()
            + direction * max(1, self._visible_rows - 1)
        )

    def _navigate_to(self, absolute_index: int):
        if self._model.total_count <= 0:
            return "break"
        absolute_index = max(
            0,
            min(absolute_index, self._model.total_count - 1),
        )
        if absolute_index < self._window_start:
            self._set_window_start(absolute_index)
        elif absolute_index >= self._window_start + self._visible_rows:
            self._set_window_start(absolute_index - self._visible_rows + 1)

        for iid, index in self._absolute_index_map.items():
            if index == absolute_index:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                break
        return "break"

    def _sort_by(self, column: str):
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column == "size"
        self._update_heading_labels()

        if self._static_files is not None:
            self._sort_static_files()
            self._window_start = 0
            self._render_window()
        elif self.on_sort_changed:
            self.on_sort_changed(self._sort_column, self._sort_reverse)

    def _sort_static_files(self):
        if self._static_files is None:
            return

        def sort_key(file_info):
            value = file_info.get(self._sort_column)
            if self._sort_column != "size":
                return str(value or "").casefold()
            return value or 0

        self._static_files.sort(key=sort_key, reverse=self._sort_reverse)

    def _update_heading_labels(self):
        for column, label in self._COLUMN_LABELS.items():
            if column == self._sort_column:
                label += " ▼" if self._sort_reverse else " ▲"
            self.tree.heading(column, text=label)

    def _on_double_click(self, event):
        selection = self.tree.selection()
        if selection and self.on_double_click:
            file_info = self._file_map.get(selection[0])
            if file_info:
                self.on_double_click(file_info)

    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        file_info = self._file_map.get(item)
        if not file_info:
            return
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        if self.on_right_click:
            self.on_right_click(file_info, event.x_root, event.y_root)

    def get_selected_files(self) -> List[dict]:
        return [
            self._file_map[item]
            for item in self.tree.selection()
            if item in self._file_map
        ]

    def clear(self):
        self._static_files = None
        self._page_request = None
        self._model.reset()
        self._window_start = 0
        self._render_window()
