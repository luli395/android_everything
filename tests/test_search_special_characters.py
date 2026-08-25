import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from database import (
    SCHEMA_VERSION,
    Database,
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
