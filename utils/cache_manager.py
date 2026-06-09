"""
utils/cache_manager.py

Disk-based JSON cache for all external API calls.
Prevents hammering rate-limited APIs and makes runs reproducible.

Cache layout:
    cache/
        openalex/   <sha256_of_query>.json
        semantic/   <sha256_of_query>.json
        nih/        <sha256_of_query>.json
        scrape/     <sha256_of_url>.json
        llm/        <sha256_of_prompt>.json
"""

import json
import hashlib
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

log = get_logger(__name__)

_CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache"


def _cache_path(namespace: str, key: str) -> Path:
    """Return the full path for a cache entry."""
    ns_dir = _CACHE_ROOT / namespace
    ns_dir.mkdir(parents=True, exist_ok=True)
    hashed = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return ns_dir / f"{hashed}.json"


def cache_get(namespace: str, key: str) -> Optional[Any]:
    """
    Retrieve a cached value.
    Returns None if not found.
    """
    path = _cache_path(namespace, key)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            log.debug("Cache HIT  [%s] key=%s...", namespace, key[:60])
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Cache read error [%s]: %s", namespace, e)
            return None
    log.debug("Cache MISS [%s] key=%s...", namespace, key[:60])
    return None


def cache_set(namespace: str, key: str, value: Any) -> None:
    """
    Store a value in the cache.
    Silently skips on write errors (non-critical path).
    """
    path = _cache_path(namespace, key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        log.debug("Cache SET  [%s] key=%s...", namespace, key[:60])
    except OSError as e:
        log.warning("Cache write error [%s]: %s", namespace, e)


def cache_clear(namespace: Optional[str] = None) -> None:
    """
    Clear a specific namespace or the entire cache.
    """
    if namespace:
        ns_dir = _CACHE_ROOT / namespace
        if ns_dir.exists():
            for f in ns_dir.glob("*.json"):
                f.unlink()
            log.info("Cleared cache namespace: %s", namespace)
    else:
        for ns_dir in _CACHE_ROOT.iterdir():
            if ns_dir.is_dir():
                for f in ns_dir.glob("*.json"):
                    f.unlink()
        log.info("Cleared entire cache")
