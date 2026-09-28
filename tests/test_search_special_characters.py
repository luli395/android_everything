import sqlite3
import threading
import unittest
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from queue import Queue
from unittest.mock import Mock, patch

from database import (
    SCHEMA_VERSION,
    Database,
    SearchPage,
    SearchQueryError,
    build_fts_trigram_query,
    extract_search_terms,
)
from ui.main_window import MainWindow


FILES = [
    ("budget (final).xlsx", "/docs/budget (final).xlsx", 10, None, 0, ".xlsx"),
    ('report "draft".txt', '/docs/report "draft".txt', 20, None, 0, ".txt"),
    ("notes-or-tasks.md", "/docs/notes-or-tasks.md", 30, None, 0, ".md"),
    ("OR roadmap.txt", "/docs/OR roadmap.txt", 40, None, 0, ".txt"),
    ("report AND review.txt", "/docs/report AND review.txt", 50, None, 0, ".txt"),
    ("literal [brackets].txt", "/docs/literal [brackets].txt", 60, None, 0, ".txt"),
    ("photo album.jpg", "/photos/photo album.jpg", 70, None, 0, ".jpg"),
    ("Résumé.txt", "/docs/Résumé.txt", 80, None, 0, ".txt"),
    ("旅行照片.jpg", "/photos/旅行照片.jpg", 90, None, 0, ".jpg"),
]


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class QueuedRoot:
    def __init__(self):
        self.callbacks = []
        self.owner_thread = threading.get_ident()
        self.after_threads = []

    def after(self, delay, callback):
        self.after_threads.append(threading.get_ident())
        self.callbacks.append(callback)
        return str(len(self.callbacks))

    def after_cancel(self, callback_id):
        return None

    def run_callbacks(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args, **kwargs):
        from functools import partial
        function = partial(function, **kwargs)
        future = Future()
        self.jobs.append((future, function, args))
        return future

    def start(self, index=0):
        future, _, _ = self.jobs[index]
        return future.set_running_or_notify_cancel()

    def finish(self, index=0):
        future, function, args = self.jobs[index]
        if not future.running() and not self.start(index):
            return
        try:
            future.set_result(function(*args))
        except Exception as error:
            future.set_exception(error)


def make_async_search_window():
    window = MainWindow.__new__(MainWindow)
    window.root = QueuedRoot()
    window._current_device = "device-1"
    window._search_timer = None
    window._search_generation = 0
    window._active_search = None
    window._active_search_total = None
    window._search_pages_in_flight = set()
    window._search_futures = set()
    window._search_future_requests = {}
    window._search_completions = Queue()
    window._search_poll_timer = None
    window._search_executor = ManualExecutor()
    window._closing = False
    window.search_var = FakeVar("photo")
    window.ext_var = FakeVar("All")
    window.count_var = FakeVar()
    window.status_var = FakeVar()
    window.file_list = Mock()
    window.file_list.sort_column = "name"
    window.file_list.sort_reverse = False
    window.search_engine = Mock()
    return window


class SpecialCharacterSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "files.db")
        self.db = Database(db_path)
        self.db.replace_device_index("device-1", FILES, model="Test Phone")

    def tearDown(self):
        self.temp_dir.cleanup()

    def search_names(self, query):
        return {row["name"] for row in self.db.search("device-1", query)}

    def test_fts_operators_and_punctuation_are_treated_as_text(self):
        cases = [
            ("budget (", "budget (final).xlsx"),
            ('"draft"', 'report "draft".txt'),
            ("notes-or", "notes-or-tasks.md"),
            ("OR", "OR roadmap.txt"),
            ("report AND", "report AND review.txt"),
            ("[brackets]", "literal [brackets].txt"),
            ("(photo)", "photo album.jpg"),
        ]

        for query, expected_name in cases:
            with self.subTest(query=query):
                self.assertIn(expected_name, self.search_names(query))

    def test_punctuation_only_queries_return_no_results(self):
        for query in ['"', "-", "*", "()", '"""', "___", "[{}]"]:
            with self.subTest(query=query):
                self.assertEqual(self.db.search("device-1", query), [])

    def test_trigram_query_builder_omits_short_terms(self):
        terms = extract_search_terms('a report OR "draft"')
        self.assertEqual(
            build_fts_trigram_query(terms),
            '"report" AND "draft"',
        )

    def test_trigram_query_builder_normalizes_diacritics(self):
        self.assertEqual(
            build_fts_trigram_query(["Résumé"]),
            '"resume"',
        )

    def test_middle_substrings_match_names_and_paths(self):
        cases = [
            ("hoto", "photo album.jpg"),
            ("HOTO", "photo album.jpg"),
            ("oto", "photo album.jpg"),
            ("lbum", "photo album.jpg"),
            ("hotos", "photo album.jpg"),
        ]

        for query, expected_name in cases:
            with self.subTest(query=query):
                self.assertIn(expected_name, self.search_names(query))

    def test_one_and_two_character_terms_use_literal_substring_fallback(self):
        self.assertIn("photo album.jpg", self.search_names("h"))
        self.assertIn("photo album.jpg", self.search_names("HO"))
        self.assertIn("Résumé.txt", self.search_names("RÉ"))
        self.assertIn("Résumé.txt", self.search_names("re"))
        self.assertIn("旅行照片.jpg", self.search_names("照片"))

    def test_trigram_search_remains_case_and_diacritic_insensitive(self):
        self.assertIn("Résumé.txt", self.search_names("RESUME"))

    def test_long_and_short_terms_are_combined_with_and_semantics(self):
        self.assertIn("photo album.jpg", self.search_names("oto AL"))
        self.assertNotIn("photo album.jpg", self.search_names("oto zz"))

    def test_sqlite_operational_error_is_wrapped_for_the_ui(self):
        with patch(
            "database.sqlite3.connect",
            side_effect=sqlite3.OperationalError("malformed MATCH expression"),
        ):
            with self.assertRaisesRegex(
                SearchQueryError, r"Search could not be completed"
            ):
                self.db.search("device-1", "photo")

    def test_main_window_displays_search_error_in_status_bar(self):
        window = make_async_search_window()
        window.search_engine.search_page.side_effect = SearchQueryError(
            "Search could not be completed. Try rebuilding the index or "
            "using a simpler query."
        )

        with self.assertLogs("ui.main_window", level="ERROR"):
            window._do_search()
            window._search_executor.finish()
            window.root.run_callbacks()

        window.file_list.clear.assert_called_once_with()
        self.assertEqual(window.count_var.value, "Search unavailable")
        self.assertIn("Search could not be completed", window.status_var.value)

    def test_main_window_ignores_a_stale_async_search_result(self):
        window = make_async_search_window()

        def page_for_query(device, query, *args, **kwargs):
            return SearchPage(
                items=[{
                    "id": 1,
                    "name": f"{query}.txt",
                    "path": f"/{query}.txt",
                    "size": 1,
                    "modified": None,
                    "is_dir": False,
                    "extension": ".txt",
                }],
                total=1,
                offset=0,
            )

        window.search_engine.search_page.side_effect = page_for_query
        window._do_search()
        self.assertTrue(window._search_executor.start(0))

        window.search_var.set("report")
        window._do_search()
        window._search_executor.finish(0)
        window.root.run_callbacks()
        window.file_list.set_virtual_results.assert_not_called()

        window._search_executor.finish(1)
        window.root.run_callbacks()
        applied_items = window.file_list.set_virtual_results.call_args.args[2]
        self.assertEqual(applied_items[0]["name"], "report.txt")

    def test_typing_invalidates_the_running_query_before_debounce(self):
        window = make_async_search_window()
        window.search_engine.search_page.return_value = SearchPage(
            items=[],
            total=0,
            offset=0,
        )
        window._do_search()
        self.assertTrue(window._search_executor.start(0))
        running_generation = window._search_generation

        window.search_var.set("report")
        window._on_search_changed()
        self.assertGreater(window._search_generation, running_generation)
        self.assertIsNone(window._active_search)

        window._search_executor.finish(0)
        window.root.run_callbacks()
        window.file_list.set_virtual_results.assert_not_called()

    def test_worker_completion_is_marshaled_through_the_ui_poller(self):
        window = make_async_search_window()
        window.search_engine.search_page.return_value = SearchPage(
            items=[],
            total=0,
            offset=0,
        )

        window._do_search()
        worker = threading.Thread(target=window._search_executor.finish)
        worker.start()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        window.file_list.set_virtual_results.assert_not_called()
        self.assertEqual(
            window.root.after_threads,
            [window.root.owner_thread],
        )

        window.root.run_callbacks()
        window.file_list.set_virtual_results.assert_called_once()

    def test_rapid_scrolling_discards_old_queued_pages(self):
        window = make_async_search_window()
        generation = 1
        search_spec = ("device-1", "photo", None, "name", False)
        window._search_generation = generation
        window._active_search = search_spec
        window._active_search_total = 1000

        for offset in (256, 512, 768):
            window._request_search_page(
                generation,
                search_spec,
                offset,
                256,
            )

        first_future = window._search_executor.jobs[0][0]
        self.assertTrue(first_future.cancelled())
        window.file_list.page_failed.assert_called_once_with(256)
        self.assertEqual(
            window._search_pages_in_flight,
            {(generation, 512), (generation, 768)},
        )


class TrigramMigrationTests(unittest.TestCase):
    def test_legacy_unicode61_index_is_rebuilt_without_losing_files(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.executescript('''
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT NOT NULL,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        size INTEGER DEFAULT 0,
                        modified TEXT,
                        is_dir INTEGER DEFAULT 0,
                        extension TEXT,
                        indexed_at TEXT NOT NULL,
                        UNIQUE(device_serial, path)
                    );
                    CREATE VIRTUAL TABLE files_fts USING fts5(
                        name,
                        path,
                        content='files',
                        content_rowid='id',
                        tokenize='unicode61'
                    );
                    CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN
                        INSERT INTO files_fts(rowid, name, path)
                        VALUES (new.id, new.name, new.path);
                    END;
                ''')
                conn.execute('''
                    INSERT INTO files (
                        device_serial, name, path, size, modified, is_dir,
                        extension, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    "legacy-device",
                    "photo.jpg",
                    "/DCIM/Camera/photo.jpg",
                    100,
                    None,
                    0,
                    ".jpg",
                    "2026-08-23T00:00:00",
                ))

            db = Database(str(db_path))

            with closing(sqlite3.connect(db_path)) as conn:
                fts_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'files_fts'"
                ).fetchone()[0]
                schema_version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                file_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(files)")
                }

            self.assertIn("trigram", fts_sql.lower())
            self.assertIn("name_normalized", fts_sql.lower())
            self.assertIn("path_normalized", fts_sql.lower())
            self.assertEqual(schema_version, SCHEMA_VERSION)
            self.assertIn("name_normalized", file_columns)
            self.assertIn("path_normalized", file_columns)
            self.assertEqual(
                [row["name"] for row in db.search("legacy-device", "hoto")],
                ["photo.jpg"],
            )
            self.assertEqual(
                [row["name"] for row in db.search("legacy-device", "HO")],
                ["photo.jpg"],
            )


if __name__ == "__main__":
    unittest.main()
