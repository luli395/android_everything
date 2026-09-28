import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from database import Database
from ui.file_list import FileListView, VirtualListModel


class FakeTree:
    def __init__(self):
        self.children = []
        self.items = {}
        self.selected = ()

    def selection(self):
        return self.selected

    def get_children(self):
        return tuple(self.children)

    def insert(self, parent, position, iid):
        self.children.append(iid)
        self.items[iid] = {}

    def delete(self, *items):
        for item in items:
            self.children.remove(item)
            self.items.pop(item, None)

    def item(self, iid, **values):
        self.items[iid].update(values)

    def selection_set(self, items):
        self.selected = tuple(items)

    def yview_moveto(self, fraction):
        pass


class FakeScrollbar:
    def set(self, first, last):
        self.position = (first, last)


class PagedSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "files.db"))
        files = [
            (
                f"file-{index:03d}.txt",
                f"/storage/emulated/0/file-{index:03d}.txt",
                index,
                f"2026-01-{(index % 28) + 1:02d}T00:00:00",
                0,
                ".txt",
            )
            for index in range(600)
        ]
        self.db.replace_device_index("device-1", files, model="Test Phone")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_page_returns_total_and_requested_slice(self):
        page = self.db.search_page(
            "device-1",
            "file",
            offset=256,
            limit=40,
            sort_column="name",
        )

        self.assertEqual(page.total, 600)
        self.assertEqual(page.offset, 256)
        self.assertEqual(len(page.items), 40)
        self.assertEqual(page.items[0]["name"], "file-256.txt")
        self.assertEqual(page.items[-1]["name"], "file-295.txt")

    def test_search_page_sorts_the_complete_result_set(self):
        page = self.db.search_page(
            "device-1",
            "file",
            offset=0,
            limit=3,
            sort_column="size",
            sort_reverse=True,
        )

        self.assertEqual(
            [item["size"] for item in page.items],
            [599, 598, 597],
        )

    def test_search_page_rejects_untrusted_sort_expressions(self):
        with self.assertRaisesRegex(ValueError, "Unsupported search sort"):
            self.db.search_page(
                "device-1",
                "file",
                sort_column="name; DROP TABLE files",
            )

        self.assertEqual(self.db.get_file_count("device-1"), 600)

    def test_followup_page_can_reuse_known_total(self):
        page = self.db.search_page(
            "device-1",
            "file",
            offset=512,
            limit=256,
            known_total=600,
        )

        self.assertEqual(page.total, 600)
        self.assertEqual(len(page.items), 88)


class VirtualListModelTests(unittest.TestCase):
    @staticmethod
    def rows(start, count):
        return [
            {"name": f"file-{index}", "path": f"/file-{index}"}
            for index in range(start, start + count)
        ]

    def test_requests_only_missing_aligned_pages(self):
        model = VirtualListModel(page_size=100, max_cached_pages=3)
        model.reset(1000)

        self.assertEqual(model.request_missing(95, 20), [(0, 100), (100, 100)])
        self.assertEqual(model.request_missing(95, 20), [])

        model.put_page(0, self.rows(0, 100))
        model.page_failed(100)
        self.assertEqual(model.request_missing(95, 20), [(100, 100)])
        self.assertEqual(model.get(99)["path"], "/file-99")
        self.assertIsNone(model.get(100))

    def test_page_cache_stays_bounded(self):
        model = VirtualListModel(page_size=10, max_cached_pages=2)
        model.reset(100)
        model.put_page(0, self.rows(0, 10))
        model.put_page(10, self.rows(10, 10))
        # Touch page zero, making page ten the least recently used page.
        self.assertEqual(model.get(0)["name"], "file-0")
        model.put_page(20, self.rows(20, 10))

        self.assertEqual(model.cached_page_count, 2)
        self.assertIsNotNone(model.get(0))
        self.assertIsNone(model.get(10))
        self.assertIsNotNone(model.get(20))

    def test_last_page_request_is_clamped_to_total(self):
        model = VirtualListModel(page_size=256, max_cached_pages=2)
        model.reset(600)

        self.assertEqual(model.request_missing(590, 10), [(512, 88)])

    def test_treeview_reuses_only_visible_physical_rows(self):
        view = FileListView.__new__(FileListView)
        view.tree = FakeTree()
        view.vsb = FakeScrollbar()
        view._model = VirtualListModel(page_size=256, max_cached_pages=2)
        view._model.reset(1_000_000)
        view._model.put_page(0, self.rows(0, 256))
        view._static_files = None
        requested = []
        view._page_request = (
            lambda offset, count: requested.append((offset, count))
        )
        view._window_start = 0
        view._visible_rows = 20
        view._file_map = {}
        view._absolute_index_map = {}

        view._render_window()
        self.assertEqual(len(view.tree.get_children()), 20)

        view._set_window_start(999_980)
        self.assertEqual(len(view.tree.get_children()), 20)
        self.assertEqual(requested[-1], (999_936, 64))

    def test_directory_with_null_extension_formats_as_unknown_type(self):
        values = FileListView._format_values({
            "name": "Documents",
            "path": "/storage/emulated/0/Documents",
            "size": 0,
            "modified": None,
            "is_dir": True,
            "extension": None,
        })

        self.assertEqual(values[-1], "-")


if __name__ == "__main__":
    unittest.main()
