"""
Search engine for querying indexed files.
"""
import threading
from typing import List, Optional
from database import Database, SearchPage, get_database, check_search_cancelled
from config import MAX_RESULTS_DISPLAY


class SearchEngine:
    """Search engine for finding files in the index."""
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()
        self._cache = {}  # Simple query cache
        self._cache_lock = threading.RLock()
        self._cache_epoch = 0

    def _get_cached(self, cache_key):
        with self._cache_lock:
            return self._cache.get(cache_key), self._cache_epoch

    def _store_cached(self, cache_key, value, epoch: int):
        with self._cache_lock:
            # An index refresh may clear the cache while this database query is
            # running. Never reinsert a result from the previous index epoch.
            if epoch != self._cache_epoch:
                return
            if len(self._cache) >= 100:
                self._cache.clear()
            self._cache[cache_key] = value
    
    def search(
        self,
        device_serial: str,
        query: str,
        extension_filter: Optional[str] = None,
        limit: int = MAX_RESULTS_DISPLAY
    ) -> List[dict]:
        """
        Search for files matching the query.
        
        Args:
            device_serial: Device to search
            query: Search query (supports case-insensitive substring matching)
            extension_filter: Optional extension filter (e.g., ".jpg")
            limit: Maximum results to return
            
        Returns:
            List of file dictionaries with keys:
            - id, name, path, size, modified, is_dir, extension
        """
        # Normalize query
        query = query.strip().lower()
        
        # Check cache
        cache_key = (device_serial, query, extension_filter, limit)
        cached, epoch = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        # Search database
        results = self.db.search(
            device_serial,
            query,
            limit=limit,
            extension_filter=extension_filter
        )
        
        self._store_cached(cache_key, results, epoch)
        
        return results

    def search_page(
        self,
        device_serial: str,
        query: str,
        offset: int,
        limit: int,
        extension_filter: Optional[str] = None,
        sort_column: str = "name",
        sort_reverse: bool = False,
        known_total: Optional[int] = None,
        cancel_event=None,
    ) -> SearchPage:
        """Return one cached database page for the virtual file list."""
        check_search_cancelled(cancel_event)
        query = query.strip().lower()
        cache_key = (
            "page",
            device_serial,
            query,
            extension_filter,
            offset,
            limit,
            sort_column,
            sort_reverse,
            known_total,
        )
        cached, epoch = self._get_cached(cache_key)
        if cached is not None:
            return cached

        page = self.db.search_page(
            device_serial,
            query,
            offset=offset,
            limit=limit,
            extension_filter=extension_filter,
            sort_column=sort_column,
            sort_reverse=sort_reverse,
            known_total=known_total,
            cancel_event=cancel_event,
        )
        check_search_cancelled(cancel_event)
        self._store_cached(cache_key, page, epoch)
        return page
    
    def clear_cache(self):
        """Clear the search cache."""
        with self._cache_lock:
            self._cache_epoch += 1
            self._cache.clear()
    
    def get_file_count(self, device_serial: str) -> int:
        """Get total indexed file count."""
        return self.db.get_file_count(device_serial)
    
    def get_extension_stats(self, device_serial: str) -> List[tuple]:
        """Get file count by extension."""
        return self.db.get_extension_stats(device_serial)


# Singleton instance
_search_engine: Optional[SearchEngine] = None


def get_search_engine() -> SearchEngine:
    """Get the global search engine instance."""
    global _search_engine
    if _search_engine is None:
        _search_engine = SearchEngine()
    return _search_engine
