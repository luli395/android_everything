import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from database import Database, SearchQueryError, build_fts_prefix_query
from ui.main_window import MainWindow


FILES = [
    ("budget (final).xlsx", "/docs/budget (final).xlsx", 10, None, 0, ".xlsx"),
    ('report "draft".txt', '/docs/report "draft".txt', 20, None, 0, ".txt"),
    ("notes-or-tasks.md", "/docs/notes-or-tasks.md", 30, None, 0, ".md"),
    ("OR roadmap.txt", "/docs/OR roadmap.txt", 40, None, 0, ".txt"),
    ("report AND review.txt", "/docs/report AND review.txt", 50, None, 0, ".txt"),
    ("literal [brackets].txt", "/docs/literal [brackets].txt", 60, None, 0, ".txt"),
    ("photo album.jpg", "/photos/photo album.jpg", 70, None, 0, ".jpg"),
]


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


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

    def test_query_builder_quotes_every_prefix_token(self):
        self.assertEqual(
            build_fts_prefix_query('report AND "draft"'),
            '"report"* AND "AND"* AND "draft"*',
        )

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
        window = MainWindow.__new__(MainWindow)
        window._current_device = "device-1"
        window.search_var = FakeVar("photo")
        window.ext_var = FakeVar("All")
        window.count_var = FakeVar()
        window.status_var = FakeVar()
        window.file_list = Mock()
        window.search_engine = Mock()
        window.search_engine.search.side_effect = SearchQueryError(
            "Search could not be completed. Try rebuilding the index or "
            "using a simpler query."
        )

        with self.assertLogs("ui.main_window", level="ERROR"):
            window._do_search()

        window.file_list.clear.assert_called_once_with()
        self.assertEqual(window.count_var.value, "Search unavailable")
        self.assertIn("Search could not be completed", window.status_var.value)


if __name__ == "__main__":
    unittest.main()
