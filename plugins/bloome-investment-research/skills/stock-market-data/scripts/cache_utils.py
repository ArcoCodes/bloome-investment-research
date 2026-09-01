"""Unified file cache for market data scripts.

Cache directory: $SKILL_CACHE_DIR or <system tmp>/stock-market-data-cache/{category}/{KEY}.json
Each entry stores {"_cached_at": epoch, "data": ...}.
TTL is checked on read; stale entries are treated as cache miss.

Usage:
    from cache_utils import load_cache, save_cache

    data = load_cache("price", "AAPL", ttl=300)
    if data is not None:
        return data
    # ... fetch fresh data ...
    save_cache("price", "AAPL", result)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import tempfile
CACHE_ROOT = Path(os.environ.get("SKILL_CACHE_DIR") or Path(tempfile.gettempdir()) / "stock-market-data-cache")

# Default TTL per category (seconds)
DEFAULT_TTL = {
    "price": 300,           # 5 min
    "macro": 600,           # 10 min
    "events": 3600,         # 1 hour
    "fundamentals": 86400,  # 24 hours
    "short_interest": 3600, # 1 hour
    "technicals": 600,      # 10 min
    "zones": 86400,         # 24 hours
}


def _cache_path(category: str, key: str) -> Path:
    return CACHE_ROOT / category / f"{key}.json"


def load_cache(category: str, key: str, ttl: int | None = None) -> dict | None:
    """Load cached data if fresh. Returns None on miss or stale."""
    if ttl is None:
        ttl = DEFAULT_TTL.get(category, 600)
    path = _cache_path(category, key)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - raw.get("_cached_at", 0) > ttl:
            return None
        return raw.get("data")
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(category: str, key: str, data) -> None:
    """Write data to cache."""
    path = _cache_path(category, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_cached_at": time.time(), "data": data}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
