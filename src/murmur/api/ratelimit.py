"""Per-client rate limits for the expensive public routes (range bundles, headline
search). In memory, per API process (the server runs one): a sliding window of
request times per (route, client address). The client address is uvicorn's, which
honours X-Forwarded-For only from ``api.trusted_proxies``."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request

MAX_KEYS = 10_000
"""Above this many tracked clients, idle ones are dropped before adding another."""


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: tuple[str, str], limit: int, window: float) -> float:
        """Record one request; returns 0 if allowed, else the seconds until it would be."""
        now = self.clock()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= MAX_KEYS:
                    self._prune(now, window)
                q = self._hits[key] = deque()
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(q[0] + window - now, 1.0)
            q.append(now)
            return 0.0

    def _prune(self, now: float, window: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - window]:
            del self._hits[k]


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(request: Request, name: str, limit: int, window: float, what: str) -> None:
    """Raise 429 (with Retry-After) when this client is over ``limit`` per ``window``
    seconds on ``name``. ``limit <= 0`` turns the limit off."""
    if limit <= 0:
        return
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    wait = limiter.hit((name, client_address(request)), limit, window)
    if wait:
        raise HTTPException(
            429,
            f"too many {what}: the limit is {limit} per {_span(window)}; try again later",
            headers={"Retry-After": str(math.ceil(wait))},
        )


def _span(seconds: float) -> str:
    return "hour" if seconds == 3600 else "minute" if seconds == 60 else f"{seconds:g} s"
