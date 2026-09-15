import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("pygramx.db")

class LocalDB:
    """
    Lightweight, persistent key-value database backed by SQLite stdlib.
    Maintains an in-memory cache for O(1) read operations and persists
    structured data as JSON.
    """
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            self.db_path = Path(__file__).resolve().parent.parent / "pygramx.db"
        else:
            self.db_path = Path(db_path).resolve()
        self._cache: Dict[str, Any] = {}
        self._init_db()
        self._load_cache()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS local_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _load_cache(self) -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT key, value FROM local_kv;")
                for row in cursor.fetchall():
                    try:
                        self._cache[row[0]] = json.loads(row[1])
                    except (json.JSONDecodeError, TypeError):
                        self._cache[row[0]] = row[1]
        except Exception as e:
            logger.error("Failed to load local database cache: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve value by key, returning default if not found."""
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store value by key, updating both cache and SQLite table."""
        serialized = json.dumps(value)
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO local_kv (key, value) VALUES (?, ?);",
                (key, serialized),
            )
            conn.commit()
        self._cache[key] = value

    def delete(self, key: str) -> bool:
        """Delete key from cache and SQLite table. Returns True if deleted."""
        if key in self._cache:
            del self._cache[key]
            with self._get_connection() as conn:
                conn.execute("DELETE FROM local_kv WHERE key = ?;", (key,))
                conn.commit()
            return True
        return False

    def has(self, key: str) -> bool:
        """Check if key exists in database."""
        return key in self._cache

    def keys(self) -> List[str]:
        """Return list of all keys."""
        return list(self._cache.keys())

    def all(self) -> Dict[str, Any]:
        """Return shallow copy of all key-value pairs."""
        return dict(self._cache)

    def clear(self) -> None:
        """Clear all entries from database."""
        self._cache.clear()
        with self._get_connection() as conn:
            conn.execute("DELETE FROM local_kv;")
            conn.commit()

    def __getitem__(self, key: str) -> Any:
        if key not in self._cache:
            raise KeyError(key)
        return self._cache[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: str) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __len__(self) -> int:
        return len(self._cache)

    def __repr__(self) -> str:
        return f"<LocalDB path={self.db_path.name} keys={len(self._cache)}>"

# Singleton instance for TeleForge userbot
db = LocalDB()
