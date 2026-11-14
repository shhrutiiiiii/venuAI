"""Tiny in-process sliding-window rate limiter for B4 routers.

Router modules must not import rag_server (cycle), so they cannot reuse its
slowapi `limiter`. This keys on a caller-supplied string (user id) and is
per-process, which matches slowapi's default in-memory storage.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

_lock = threading.Lock()
_hits: dict = defaultdict(deque)


def check(bucket: str, key: str, limit: int, window_s: float = 60.0) -> None:
    """Raise HTTP 429 if `key` made `limit` calls in `bucket` within `window_s`."""
    now = time.monotonic()
    with _lock:
        q = _hits[(bucket, key)]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded ({limit}/{int(window_s)}s).")
        q.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
