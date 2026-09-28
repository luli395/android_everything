"""
SQLite database for file indexing with FTS5 full-text search.
"""
import sqlite3
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from datetime import datetime, timedelta
from contextlib import contextmanager

from config import DATABASE_PATH


SCHEMA_VERSION = 3
FTS_SCHEMA_VERSION = 2
TRIGRAM_MIN_LENGTH = 3
TRIGRAM_TOKENIZER = "trigram"
STAGING_RETENTION_DAYS = 1
SEARCH_SORT_EXPRESSIONS = {
    "name": "f.name_normalized",
    "path": "f.path_normalized",
    "size": "COALESCE(f.size, 0)",
    "modified": "COALESCE(f.modified, '')",
    "extension": "COALESCE(f.extension, '') COLLATE NOCASE",
}


class SearchQueryError(RuntimeError):
    """Raised when SQLite cannot complete a file search."""


@dataclass(frozen=True)
class SearchPage:
    """One bounded page from a potentially much larger result set."""

    items: List[dict]
    total: int
    offset: int


def extract_search_terms(query: str) -> List[str]:
    """Return literal Unicode word terms from user-entered search text.

    Punctuation and FTS5 operators are deliberately treated as separators so
    input such as ``(photo)``, ``notes-or``, or a bare quote can never become
    MATCH syntax.  This preserves the application's existing safe-query
    behavior while allowing each returned term to be matched as a substring.
    """
    return re.findall(r"[^\W_]+", query, flags=re.UNICODE)


def normalize_search_text(value: str) -> str:
    """Case-fold text and remove combining marks for consistent searching."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def build_fts_trigram_query(terms: List[str]) -> Optional[str]:
    """Build a safe FTS5 trigram query for searchable-length terms.

    Terms are normalized before being quoted because the FTS table indexes the
    same normalized text.  This keeps case and diacritic behavior consistent
    across SQLite versions, including the SQLite 3.35 bundled with Python 3.8.
    Terms shorter than three normalized Unicode code points cannot produce a
    trigram and are handled separately with ``instr()``.
    """
    normalized_terms = [normalize_search_text(term) for term in terms]
    searchable_terms = [
        term
        for term in normalized_terms
        if len(term) >= TRIGRAM_MIN_LENGTH
    ]
    if not searchable_terms:
        return None

    escaped_terms = [term.replace('"', '""') for term in searchable_terms]
    return " AND ".join(f'"{term}"' for term in escaped_terms)


class Database:
    """SQLite database for storing file index."""
    
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        parent_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent_dir, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Keep schema creation and upgrades atomic. If rebuilding the FTS
            # index fails (for example because the disk is full), reopening
            # the previous application version still sees its original index.
            cursor.execute("BEGIN IMMEDIATE")
            
            # Main files table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_serial TEXT NOT NULL,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        name_normalized TEXT NOT NULL DEFAULT '',
                        path_normalized TEXT NOT NULL DEFAULT '',
                        size INTEGER DEFAULT 0,
                    modified TEXT,
                    is_dir INTEGER DEFAULT 0,
                    extension TEXT,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(device_serial, path)
                )
            ''')
            
            # Create indexes for faster queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_device 
                ON files(device_serial)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_name 
                ON files(name)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_extension 
                ON files(extension)
            ''')
            
            # Devices table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    serial TEXT PRIMARY KEY,
                    model TEXT,
                    last_indexed TEXT,
                    file_count INTEGER DEFAULT 0
                )
            ''')

            # A scan is written here in small committed batches. Search keeps
            # reading the live ``files`` table until the completed scan is
            # promoted in one atomic transaction.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS index_staging_runs (
                    scan_id TEXT PRIMARY KEY,
                    device_serial TEXT NOT NULL,
                    started_at TEXT NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_staging_runs_device
                ON index_staging_runs(device_serial)
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files_staging (
                    scan_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name_normalized TEXT NOT NULL,
                    path_normalized TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    modified TEXT,
                    is_dir INTEGER DEFAULT 0,
                    extension TEXT,
                    PRIMARY KEY(scan_id, path),
                    FOREIGN KEY(scan_id) REFERENCES index_staging_runs(scan_id)
                        ON DELETE CASCADE
                ) WITHOUT ROWID
            ''')

            self._ensure_normalized_columns(cursor)
            self._ensure_trigram_index(cursor)

            conn.commit()

    def _ensure_normalized_columns(self, cursor: sqlite3.Cursor):
        """Add and populate search-normalized columns on legacy databases."""
        cursor.execute("PRAGMA table_info(files)")
        columns = {row[1] for row in cursor.fetchall()}
        added_column = False

        if "name_normalized" not in columns:
            cursor.execute(
                "ALTER TABLE files ADD COLUMN "
                "name_normalized TEXT NOT NULL DEFAULT ''"
            )
            added_column = True
        if "path_normalized" not in columns:
            cursor.execute(
                "ALTER TABLE files ADD COLUMN "
                "path_normalized TEXT NOT NULL DEFAULT ''"
            )
            added_column = True

        if added_column:
            cursor.execute('''
                UPDATE files
                SET name_normalized = unicode_search_normalize(name),
                    path_normalized = unicode_search_normalize(path)
            ''')

    def _ensure_trigram_index(self, cursor: sqlite3.Cursor):
        """Create or migrate the external-content FTS index to trigram.

        Releases through v0.1.3 created an unversioned ``unicode61`` FTS
        table.  Replacing only the virtual table and its triggers preserves the
        regular ``files`` rows, then ``rebuild`` repopulates the new trigram
        index from that content table.
        """
        cursor.execute("PRAGMA user_version")
        schema_version = cursor.fetchone()[0]
        cursor.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'files_fts'"
        )
        row = cursor.fetchone()
        fts_sql = (row[0] or "").lower() if row else ""
        needs_rebuild = (
            schema_version < FTS_SCHEMA_VERSION
            or "trigram" not in fts_sql
            or "name_normalized" not in fts_sql
            or "path_normalized" not in fts_sql
        )

        if needs_rebuild:
            for trigger_name in ("files_ai", "files_ad", "files_au"):
                cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            cursor.execute("DROP TABLE IF EXISTS files_fts")

        cursor.execute(f'''
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name_normalized,
                path_normalized,
                content='files',
                content_rowid='id',
                tokenize='{TRIGRAM_TOKENIZER}'
            )
        ''')

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(
                    rowid, name_normalized, path_normalized
                ) VALUES (
                    new.id, new.name_normalized, new.path_normalized
                );
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(
                    files_fts, rowid, name_normalized, path_normalized
                ) VALUES (
                    'delete', old.id,
                    old.name_normalized, old.path_normalized
                );
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(
                    files_fts, rowid, name_normalized, path_normalized
                ) VALUES (
                    'delete', old.id,
                    old.name_normalized, old.path_normalized
                );
                INSERT INTO files_fts(
                    rowid, name_normalized, path_normalized
                ) VALUES (
                    new.id, new.name_normalized, new.path_normalized
                );
            END
        ''')

        if needs_rebuild:
            cursor.execute("INSERT INTO files_fts(files_fts) VALUES ('rebuild')")

        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function(
            "unicode_search_normalize",
            1,
            lambda value: normalize_search_text(value)
            if isinstance(value, str)
            else "",
            deterministic=True,
        )
        try:
            yield conn
        finally:
            conn.close()
    
    def clear_device_files(self, device_serial: str):
        """Clear all files for a device before re-indexing."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM files WHERE device_serial = ?",
                (device_serial,)
            )
            conn.commit()

    def replace_device_index(
        self,
        device_serial: str,
        files: List[Tuple],
        model: str = "",
        batch_size: int = 5000,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> int:
        """Atomically replace all indexed files for a device.

        The existing files, FTS entries, and device metadata are preserved if
        any part of the replacement raises. The progress callback may raise to
        cancel the operation and trigger the same rollback.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        total = len(files)

        scan_id = self.begin_index_staging(device_serial)
        try:
            if progress_callback:
                progress_callback(0, total)

            for start in range(0, total, batch_size):
                batch = files[start:start + batch_size]
                self.stage_files_batch(scan_id, device_serial, batch)
                if progress_callback:
                    progress_callback(start + len(batch), total)

            def before_commit():
                # This final callback also gives an empty replacement a
                # cancellation point immediately before publication.
                if progress_callback:
                    progress_callback(total, total)

            return self.publish_staged_index(
                scan_id,
                device_serial,
                model=model,
                before_commit=before_commit,
            )
        finally:
            # Publication removes its own rows. This also cleans up batches
            # left behind by validation, cancellation, or database failures.
            self.discard_staged_index(scan_id)

    def begin_index_staging(self, device_serial: str) -> str:
        """Create and return an isolated staging run for one device."""
        if not device_serial or not device_serial.strip():
            raise ValueError("device_serial is required")

        scan_id = uuid.uuid4().hex
        started_at = datetime.now().isoformat()
        stale_before = (
            datetime.now() - timedelta(days=STAGING_RETENTION_DAYS)
        ).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            # A process killed during a scan cannot run its normal cleanup.
            # Remove only old runs so another active application instance is
            # not disturbed.
            cursor.execute(
                "DELETE FROM index_staging_runs WHERE started_at < ?",
                (stale_before,),
            )
            cursor.execute('''
                INSERT INTO index_staging_runs
                (scan_id, device_serial, started_at)
                VALUES (?, ?, ?)
            ''', (scan_id, device_serial, started_at))
            conn.commit()

        return scan_id

    @staticmethod
    def _require_staging_run(
        cursor: sqlite3.Cursor,
        scan_id: str,
        device_serial: str,
    ) -> str:
        """Return the run timestamp or reject a missing/mismatched run."""
        cursor.execute('''
            SELECT started_at
            FROM index_staging_runs
            WHERE scan_id = ? AND device_serial = ?
        ''', (scan_id, device_serial))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("The index staging run does not exist")
        return row[0]

    def stage_files_batch(
        self,
        scan_id: str,
        device_serial: str,
        files: List[Tuple],
    ) -> int:
        """Commit one bounded file batch without changing the live index.

        Returns the number of records accepted from this batch.
        """
        if not files:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                self._require_staging_run(cursor, scan_id, device_serial)
                return 0

        data = [
            (
                scan_id,
                name,
                path,
                normalize_search_text(name),
                normalize_search_text(path),
                size,
                modified,
                is_dir,
                extension,
            )
            for name, path, size, modified, is_dir, extension in files
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                self._require_staging_run(cursor, scan_id, device_serial)
                cursor.executemany('''
                    INSERT INTO files_staging
                    (scan_id, name, path, name_normalized, path_normalized,
                     size, modified, is_dir, extension)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id, path) DO UPDATE SET
                        name = excluded.name,
                        name_normalized = excluded.name_normalized,
                        path_normalized = excluded.path_normalized,
                        size = excluded.size,
                        modified = excluded.modified,
                        is_dir = excluded.is_dir,
                        extension = excluded.extension
                ''', data)
                conn.commit()
                return len(data)
            except BaseException:
                conn.rollback()
                raise

    def publish_staged_index(
        self,
        scan_id: str,
        device_serial: str,
        model: str = "",
        before_commit: Optional[Callable[[], None]] = None,
    ) -> int:
        """Atomically replace a live device index with a completed staging run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                self._require_staging_run(
                    cursor,
                    scan_id,
                    device_serial,
                )
                indexed_at = datetime.now().isoformat()
                cursor.execute(
                    "SELECT COUNT(*) FROM files_staging WHERE scan_id = ?",
                    (scan_id,),
                )
                indexed_count = cursor.fetchone()[0]

                cursor.execute(
                    "DELETE FROM files WHERE device_serial = ?",
                    (device_serial,),
                )
                cursor.execute('''
                    INSERT INTO files
                    (device_serial, name, path, name_normalized,
                     path_normalized, size, modified, is_dir, extension,
                     indexed_at)
                    SELECT ?, name, path, name_normalized, path_normalized,
                           size, modified, is_dir, extension, ?
                    FROM files_staging
                    WHERE scan_id = ?
                ''', (device_serial, indexed_at, scan_id))
                cursor.execute('''
                    INSERT OR REPLACE INTO devices
                    (serial, model, last_indexed, file_count)
                    VALUES (?, ?, ?, ?)
                ''', (device_serial, model, indexed_at, indexed_count))

                if before_commit:
                    before_commit()

                cursor.execute(
                    "DELETE FROM index_staging_runs WHERE scan_id = ?",
                    (scan_id,),
                )
                conn.commit()
                return indexed_count
            except BaseException:
                conn.rollback()
                raise

    def discard_staged_index(self, scan_id: str):
        """Remove one staging run without touching searchable rows."""
        if not scan_id:
            return
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM index_staging_runs WHERE scan_id = ?",
                (scan_id,),
            )
            conn.commit()
    
    def insert_files_batch(self, device_serial: str, files: List[Tuple]) -> int:
        """
        Insert files in batch for better performance.
        
        Args:
            device_serial: Device serial number
            files: List of (name, path, size, modified, is_dir, extension) tuples
            
        Returns:
            Number of files inserted
        """
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Prepare data with device serial and timestamp
            data = [
                (
                    device_serial,
                    name,
                    path,
                    normalize_search_text(name),
                    normalize_search_text(path),
                    size,
                    modified,
                    is_dir,
                    ext,
                    now,
                )
                for name, path, size, modified, is_dir, ext in files
            ]
            
            cursor.executemany('''
                INSERT OR REPLACE INTO files
                (device_serial, name, path, name_normalized, path_normalized,
                 size, modified, is_dir, extension, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', data)
            
            conn.commit()
            return len(data)
    
    def update_device_info(self, serial: str, model: str, file_count: int):
        """Update device information after indexing."""
        now = datetime.now().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO devices (serial, model, last_indexed, file_count)
                VALUES (?, ?, ?, ?)
            ''', (serial, model, now, file_count))
            conn.commit()
    
    def search(
        self,
        device_serial: str,
        query: str,
        limit: int = 1000,
        extension_filter: Optional[str] = None
    ) -> List[dict]:
        """Search indexed files and expose a stable application error."""
        return self.search_page(
            device_serial,
            query,
            limit=limit,
            extension_filter=extension_filter,
            # The legacy list-returning API does not expose a total.
            known_total=0,
        ).items

    def search_page(
        self,
        device_serial: str,
        query: str,
        offset: int = 0,
        limit: int = 200,
        extension_filter: Optional[str] = None,
        sort_column: str = "name",
        sort_reverse: bool = False,
        known_total: Optional[int] = None,
    ) -> SearchPage:
        """Return a count and one sorted page for a virtual result list."""
        if offset < 0:
            raise ValueError("offset must not be negative")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if sort_column not in SEARCH_SORT_EXPRESSIONS:
            raise ValueError(f"Unsupported search sort column: {sort_column}")
        if known_total is not None and known_total < 0:
            raise ValueError("known_total must not be negative")

        try:
            return self._search_page(
                device_serial,
                query,
                offset=offset,
                limit=limit,
                extension_filter=extension_filter,
                sort_column=sort_column,
                sort_reverse=sort_reverse,
                known_total=known_total,
            )
        except sqlite3.OperationalError as error:
            raise SearchQueryError(
                "Search could not be completed. Try rebuilding the index "
                "or using a simpler query."
            ) from error

    def _search_page(
        self,
        device_serial: str,
        query: str,
        offset: int,
        limit: int,
        extension_filter: Optional[str],
        sort_column: str,
        sort_reverse: bool,
        known_total: Optional[int],
    ) -> SearchPage:
        """Execute one paged search using only whitelisted ORDER BY SQL."""
        query = query.strip()
        terms = extract_search_terms(query) if query else []
        normalized_terms = [
            normalize_search_text(term) for term in terms
        ]
        fts_query = build_fts_trigram_query(normalized_terms)
        short_terms = [
            term
            for term in normalized_terms
            if len(term) < TRIGRAM_MIN_LENGTH
        ]

        # Punctuation-only input has no searchable literal terms. Return an
        # empty result rather than passing invalid syntax to MATCH or treating
        # the input as an empty query that displays every indexed file.
        if query and not terms:
            return SearchPage(items=[], total=0, offset=offset)

        from_sql = "FROM files f"
        params = [device_serial]
        if fts_query:
            from_sql += " INNER JOIN files_fts fts ON f.id = fts.rowid"

        conditions = ["f.device_serial = ?"]
        if fts_query:
            conditions.append("files_fts MATCH ?")
            params.append(fts_query)

        for term in short_terms:
            conditions.append('''
                (
                    instr(f.name_normalized, ?) > 0
                    OR instr(f.path_normalized, ?) > 0
                )
            ''')
            params.extend((term, term))

        if extension_filter:
            conditions.append("f.extension = ?")
            params.append(extension_filter.lower())

        where_sql = " WHERE " + " AND ".join(conditions)
        direction = "DESC" if sort_reverse else "ASC"
        sort_expression = SEARCH_SORT_EXPRESSIONS[sort_column]
        order_sql = (
            f" ORDER BY {sort_expression} {direction}, "
            "f.path COLLATE NOCASE ASC, f.id ASC"
        )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Keep count and page on the same snapshot if a completed index is
            # published between the two statements.
            cursor.execute("BEGIN")
            if known_total is None:
                cursor.execute(
                    "SELECT COUNT(*) " + from_sql + where_sql,
                    params,
                )
                total = cursor.fetchone()[0]
            else:
                total = known_total

            sql = (
                "SELECT f.id, f.name, f.path, f.size, f.modified, "
                "f.is_dir, f.extension "
                + from_sql
                + where_sql
                + order_sql
                + " LIMIT ? OFFSET ?"
            )
            cursor.execute(sql, params + [limit, offset])
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "name": row["name"],
                    "path": row["path"],
                    "size": row["size"],
                    "modified": row["modified"],
                    "is_dir": bool(row["is_dir"]),
                    "extension": row["extension"],
                })
            
            conn.commit()
            return SearchPage(items=results, total=total, offset=offset)
    
    def delete_files(self, device_serial: str, paths: list):
        """Delete specific files from the index."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for path in paths:
                cursor.execute(
                    "DELETE FROM files WHERE device_serial = ? AND path = ?",
                    (device_serial, path)
                )
            conn.commit()
    
    def get_file_count(self, device_serial: str) -> int:
        """Get total file count for a device."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM files WHERE device_serial = ?",
                (device_serial,)
            )
            return cursor.fetchone()[0]
    
    def get_extension_stats(self, device_serial: str) -> List[Tuple[str, int]]:
        """Get file count by extension."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT extension, COUNT(*) as count
                FROM files
                WHERE device_serial = ? AND extension IS NOT NULL
                GROUP BY extension
                ORDER BY count DESC
                LIMIT 20
            ''', (device_serial,))
            return [(row[0], row[1]) for row in cursor.fetchall()]


# Singleton instance
_db: Optional[Database] = None


def get_database() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db
