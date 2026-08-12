"""
Search engine for querying indexed files.
"""
from typing import List, Optional
from database import Database, get_database
from config import MAX_RESULTS_DISPLAY


class SearchEngine:
    """Search engine for finding files in the index."""
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_database()
        self._cache = {}  # Simple query cache
    
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
            query: Search query (supports prefix matching)
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
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Search database
        results = self.db.search(
            device_serial,
            query,
            limit=limit,
            extension_filter=extension_filter
        )
        
        # Cache results (limit cache size)
        if len(self._cache) > 100:
            self._cache.clear()
        self._cache[cache_key] = results
        
        return results
    
    def clear_cache(self):
        """Clear the search cache."""
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
