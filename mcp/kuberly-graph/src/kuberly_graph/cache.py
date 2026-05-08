"""Process-local cache state shared across tools and dashboard endpoints.

Two pieces:

- A monotonically-increasing ``cache_epoch`` integer. Every successful
  ``regenerate_graph`` / ``regenerate_layer`` call bumps it. Caches that
  embed the epoch in their key auto-invalidate after a refresh.
- A tiny TTL cache backed by ``time.monotonic`` — used by the dashboard
  endpoints (5s) and shared with the RxGraph builder so consecutive
  ``shortest_path`` / ``blast_radius`` calls don't rebuild the graph.

Pure stdlib, single process. No threading lock — Python's GIL serialises
dict mutations enough for our read-mostly workload.
"""

from __future__ import annotations

import time
from typing import Any, Callable


_cache_epoch: int = 0


def cache_epoch() -> int:
    return _cache_epoch


def bump_cache_epoch() -> int:
    """Advance the cache epoch; called from regenerate_* after success."""
    global _cache_epoch
    _cache_epoch += 1
    return _cache_epoch


# ---------------------------------------------------------------------------
# Tiny TTL cache (key -> (deadline, value))
# ---------------------------------------------------------------------------


_ttl_cache: dict[Any, tuple[float, Any]] = {}


def ttl_get(key: Any) -> Any | None:
    entry = _ttl_cache.get(key)
    if entry is None:
        return None
    deadline, value = entry
    if time.monotonic() >= deadline:
        _ttl_cache.pop(key, None)
        return None
    return value


def ttl_set(key: Any, value: Any, ttl_seconds: float) -> Any:
    _ttl_cache[key] = (time.monotonic() + max(0.0, float(ttl_seconds)), value)
    return value


def ttl_clear() -> None:
    _ttl_cache.clear()


def ttl_get_or_compute(
    key: Any,
    ttl_seconds: float,
    compute: Callable[[], Any],
) -> Any:
    """Return cached value for ``key`` or compute+store it."""
    hit = ttl_get(key)
    if hit is not None:
        return hit
    value = compute()
    ttl_set(key, value, ttl_seconds)
    return value


__all__ = [
    "cache_epoch",
    "bump_cache_epoch",
    "ttl_get",
    "ttl_set",
    "ttl_clear",
    "ttl_get_or_compute",
]
