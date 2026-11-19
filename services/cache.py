"""Unified thread-safe LRU + TTL cache with hit/miss metrics."""
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Bounded in-memory LRU cache with per-entry TTL and observability metrics."""

    def __init__(self, name: str, max_size: int = 256, default_ttl: float = 300.0):
        self.name = name
        self.max_size = max(1, int(max_size))
        self.default_ttl = float(default_ttl)
        self._store: "OrderedDict[str, Tuple[float, T]]" = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> Optional[T]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if now >= expires_at:
                self._store.pop(key, None)
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: T, ttl: Optional[float] = None) -> None:
        expires_at = time.monotonic() + (self.default_ttl if ttl is None else float(ttl))
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (expires_at, value)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)
                self.evictions += 1

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            # Prune expired entries lazily on stats read
            expired = [k for k, (exp, _) in self._store.items() if now >= exp]
            for k in expired:
                self._store.pop(k, None)
            total = self.hits + self.misses
            hit_rate = round((self.hits / total) * 100, 1) if total > 0 else 0.0
            return {
                "size": len(self._store),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate_pct": hit_rate,
            }
