"""
SQLite database for file indexing with FTS5 full-text search.
"""
import sqlite3
import os
from typing import Callable, List, Optional, Tuple
from datetime import datetime
from contextlib import contextmanager

from config import DATABASE_PATH


class Database:
    """SQLite database for storing file index."""
    
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Main files table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
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
            
            # FTS5 virtual table for full-text search
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    name,
                    path,
                    content='files',
                    content_rowid='id',
                    tokenize='unicode61'
                )
            ''')
            
            # Triggers to keep FTS in sync
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
            
            # Devices table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    serial TEXT PRIMARY KEY,
                    model TEXT,
                    last_indexed TEXT,
                    file_count INTEGER DEFAULT 0
                )
            ''')
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
                            device_serial, name, path, size, modified,
                            is_dir, extension, now
                        )
                        for name, path, size, modified, is_dir, extension in batch
                    ]
                    cursor.executemany('''
                        INSERT INTO files
                        (device_serial, name, path, size, modified, is_dir,
                         extension, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(device_serial, path) DO UPDATE SET
                            name = excluded.name,
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
                (device_serial, name, path, size, modified, is_dir, ext, now)
                for name, path, size, modified, is_dir, ext in files
            ]
            
            cursor.executemany('''
                INSERT OR REPLACE INTO files 
                (device_serial, name, path, size, modified, is_dir, extension, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                
                cursor.execute(sql, params)
            else:
                # Use FTS5 for search
                # Add wildcards for prefix matching
                fts_query = ' '.join(f'{word}*' for word in query.split())
                
                sql = '''
                    SELECT f.id, f.name, f.path, f.size, f.modified, f.is_dir, f.extension
                    FROM files f
                    INNER JOIN files_fts fts ON f.id = fts.rowid
                    WHERE f.device_serial = ?
                    AND files_fts MATCH ?
                '''
                params = [device_serial, fts_query]
                
                if extension_filter:
                    sql += " AND f.extension = ?"
                    params.append(extension_filter.lower())
                
                sql += " ORDER BY rank LIMIT ?"
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
