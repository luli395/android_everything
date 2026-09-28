from contextlib import closing
import sqlite3
import threading
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from database import Database, SearchPage, cancellable_search
from search_engine import SearchEngine
from test_search_special_characters import make_async_search_window

SLOW_SQL = '''WITH RECURSIVE n(x) AS (
    SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000000
) SELECT sum(x) FROM n'''


class SearchCancellationTests(unittest.TestCase):
    def test_interrupt_running_sql_and_remove_handler(self):
        started = threading.Event()
        cancel = threading.Event()

        def run():
            with closing(sqlite3.connect(':memory:')) as conn:
                try:
                    with cancellable_search(conn, cancel):
                        conn.set_trace_callback(lambda sql: started.set())
                        conn.execute(SLOW_SQL).fetchone()
                finally:
                    # A cancelled handler must not poison subsequent SQL.
                    self.assertEqual(conn.execute('SELECT 42').fetchone()[0], 42)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            try:
                self.assertTrue(started.wait(2))
                cancel.set()
                with self.assertRaises(CancelledError):
                    future.result(timeout=2)
            finally:
                cancel.set()

    def test_cancelled_database_and_cached_search(self):
        with TemporaryDirectory() as folder:
            db = Database(str(Path(folder) / 'files.db'))
            engine = SearchEngine(db)
            engine.search_page('phone', '', 0, 10)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(CancelledError):
                db.search_page('phone', '', cancel_event=cancel)
            with self.assertRaises(CancelledError):
                engine.search_page('phone', '', 0, 10, cancel_event=cancel)
            self.assertEqual(db.search_page('phone', '').total, 0)

    def test_cancel_during_query_does_not_cache_result(self):
        cancel = threading.Event()
        db = Mock()
        def page(*args, **kwargs):
            cancel.set()
            return SearchPage([], 0, 0)
        db.search_page.side_effect = page
        engine = SearchEngine(db)
        with self.assertRaises(CancelledError):
            engine.search_page('phone', '', 0, 10, cancel_event=cancel)
        self.assertEqual(engine._cache, {})

    def exercise_window_cancellation(self, close):
        window = make_async_search_window()
        window.root.destroy = Mock()
        pool = ThreadPoolExecutor(max_workers=1)
        window._search_executor = pool
        started = threading.Event()
        def page(device, query, *args, cancel_event=None):
            if query == 'photo':
                with closing(sqlite3.connect(':memory:')) as conn:
                    with cancellable_search(conn, cancel_event):
                        conn.set_trace_callback(lambda sql: started.set())
                        conn.execute(SLOW_SQL).fetchone()
            return SearchPage([], 0, 0)
        window.search_engine.search_page.side_effect = page
        try:
            window._do_search()
            old_future = next(iter(window._search_futures))
            self.assertTrue(started.wait(2))
            old_token = window._search_cancel_event
            if close:
                window._on_close()
                window.root.destroy.assert_called_once()
            else:
                window.search_var.set('new')
                window._on_search_changed()
            self.assertTrue(old_token.is_set())
            with self.assertRaises(CancelledError):
                old_future.result(timeout=2)
            if not close:
                window._do_search()
                next(iter(window._search_futures)).result(timeout=2)
                window._poll_search_completions()
                window.file_list.set_virtual_results.assert_called_once()
            self.assertNotIn('unavailable', window.status_var.value.lower())
        finally:
            window._invalidate_search()
            pool.shutdown(wait=True)

    def test_typing_interrupts_running_sql_and_releases_worker(self):
        self.exercise_window_cancellation(close=False)

    def test_close_interrupts_running_sql(self):
        self.exercise_window_cancellation(close=True)
