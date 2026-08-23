"""
SQLite database for file indexing with FTS5 full-text search.
"""
import sqlite3
import os
import re
import unicodedata
from typing import Callable, List, Optional, Tuple
from datetime import datetime
from contextlib import contextmanager

from config import DATABASE_PATH


SCHEMA_VERSION = 1
TRIGRAM_MIN_LENGTH = 3
TRIGRAM_TOKENIZER = "trigram case_sensitive 0 remove_diacritics 1"


class SearchQueryError(RuntimeError):
    """Raised when SQLite cannot complete a file search."""


def extract_search_terms(query: str) -> List[str]:
    """Return literal Unicode word terms from user-entered search text.

    Punctuation and FTS5 operators are deliberately treated as separators so
    input such as ``(photo)``, ``notes-or``, or a bare quote can never become
    MATCH syntax.  This preserves the application's existing safe-query
    behavior while allowing each returned term to be matched as a substring.
    """
    return re.findall(r"[^\W_]+", query, flags=re.UNICODE)


def normalize_short_search_text(value: str) -> str:
    """Case-fold text and remove combining marks for short-term searches."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def build_fts_trigram_query(terms: List[str]) -> Optional[str]:
    """Build a safe FTS5 trigram query for searchable-length terms.

    The trigram tokenizer matches quoted terms anywhere inside a token, so
    ``hot`` matches ``photo.jpg``.  Terms shorter than three Unicode code
    points cannot produce a trigram and are handled separately with a
    pre-normalized ``instr()`` predicate in :meth:`Database._search`.
    """
    searchable_terms = [
        term for term in terms if len(term) >= TRIGRAM_MIN_LENGTH
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
            schema_version < SCHEMA_VERSION
            or "trigram" not in fts_sql
            or "remove_diacritics 1" not in fts_sql
        )

        if needs_rebuild:
            for trigger_name in ("files_ai", "files_ad", "files_au"):
                cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            cursor.execute("DROP TABLE IF EXISTS files_fts")

        cursor.execute(f'''
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name,
                path,
                content='files',
                content_rowid='id',
                tokenize='{TRIGRAM_TOKENIZER}'
            )
        ''')

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, name, path)
                VALUES (new.id, new.name, new.path);
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name, path)
                VALUES ('delete', old.id, old.name, old.path);
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name, path)
                VALUES ('delete', old.id, old.name, old.path);
                INSERT INTO files_fts(rowid, name, path)
                VALUES (new.id, new.name, new.path);
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
        conn.create_function(
            "unicode_search_normalize",
            1,
            lambda value: normalize_short_search_text(value)
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

        now = datetime.now().isoformat()
        total = len(files)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")

                if progress_callback:
                    progress_callback(0, total)

                cursor.execute(
                    "DELETE FROM files WHERE device_serial = ?",
                    (device_serial,)
                )

                for start in range(0, total, batch_size):
                    batch = files[start:start + batch_size]
                    data = [
                        (
                            device_serial,
                            name,
                            path,
                            normalize_short_search_text(name),
                            normalize_short_search_text(path),
                            size,
                            modified,
                            is_dir,
                            extension,
                            now,
                        )
                        for name, path, size, modified, is_dir, extension in batch
                    ]
                    cursor.executemany('''
                        INSERT INTO files
                        (device_serial, name, path, name_normalized,
                         path_normalized, size, modified, is_dir, extension,
                         indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(device_serial, path) DO UPDATE SET
                            name = excluded.name,
                            name_normalized = excluded.name_normalized,
                            path_normalized = excluded.path_normalized,
                            size = excluded.size,
                            modified = excluded.modified,
                            is_dir = excluded.is_dir,
                            extension = excluded.extension,
                            indexed_at = excluded.indexed_at
                    ''', data)

                    if progress_callback:
                        progress_callback(start + len(batch), total)

                cursor.execute(
                    "SELECT COUNT(*) FROM files WHERE device_serial = ?",
                    (device_serial,)
                )
                indexed_count = cursor.fetchone()[0]

                cursor.execute('''
                    INSERT OR REPLACE INTO devices
                    (serial, model, last_indexed, file_count)
                    VALUES (?, ?, ?, ?)
                ''', (device_serial, model, now, indexed_count))

                # This final callback also gives an empty replacement a
                # cancellation point immediately before commit.
                if progress_callback:
                    progress_callback(total, total)

                conn.commit()
                return indexed_count
            except BaseException:
                conn.rollback()
                raise
    
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
                    normalize_short_search_text(name),
                    normalize_short_search_text(path),
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
        try:
            return self._search(
                device_serial,
                query,
                limit=limit,
                extension_filter=extension_filter,
            )
        except sqlite3.OperationalError as error:
            raise SearchQueryError(
                "Search could not be completed. Try rebuilding the index "
                "or using a simpler query."
            ) from error

    def _search(
        self, 
        device_serial: str, 
        query: str, 
        limit: int = 1000,
        extension_filter: Optional[str] = None
    ) -> List[dict]:
        """
        Search files using FTS5.
        
        Args:
            device_serial: Device serial number
            query: Search query
            limit: Maximum results
            extension_filter: Optional extension filter (e.g., ".jpg")
            
        Returns:
            List of file dictionaries
        """
        query = query.strip()
        terms = extract_search_terms(query) if query else []
        fts_query = build_fts_trigram_query(terms)
        short_terms = [
            term for term in terms if len(term) < TRIGRAM_MIN_LENGTH
        ]

        # Punctuation-only input has no searchable literal terms. Return an
        # empty result rather than passing invalid syntax to MATCH or treating
        # the input as an empty query that displays every indexed file.
        if query and not terms:
            return []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if not query.strip():
                # No query, return all files
                sql = '''
                    SELECT id, name, path, size, modified, is_dir, extension
                    FROM files
                    WHERE device_serial = ?
                '''
                params = [device_serial]
                
                if extension_filter:
                    sql += " AND extension = ?"
                    params.append(extension_filter.lower())
                
                sql += " ORDER BY name LIMIT ?"
                params.append(limit)
                
            else:
                # Trigram FTS provides arbitrary substring matching for terms
                # of at least three code points. Shorter terms use the regular
                # content table with pre-normalized Unicode instr() checks.
                sql = '''
                    SELECT f.id, f.name, f.path, f.size, f.modified,
                           f.is_dir, f.extension
                    FROM files f
                '''
                params = [device_serial]

                if fts_query:
                    sql += " INNER JOIN files_fts fts ON f.id = fts.rowid"

                sql += " WHERE f.device_serial = ?"

                if fts_query:
                    sql += " AND files_fts MATCH ?"
                    params.append(fts_query)

                for term in short_terms:
                    sql += '''
                        AND (
                            instr(f.name_normalized, ?) > 0
                            OR instr(f.path_normalized, ?) > 0
                        )
                    '''
                    folded_term = normalize_short_search_text(term)
                    params.extend((folded_term, folded_term))
                
                if extension_filter:
                    sql += " AND f.extension = ?"
                    params.append(extension_filter.lower())
                
                if fts_query:
                    sql += " ORDER BY fts.rank, f.name COLLATE NOCASE LIMIT ?"
                else:
                    sql += " ORDER BY f.name COLLATE NOCASE LIMIT ?"
                params.append(limit)

            cursor.execute(sql, params)
            
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
            
            return results
    
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
