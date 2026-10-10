"""In-process limits that protect the API key's budget (SPEC 7).

Single-instance only: state is in memory. With more than one instance, move this to a
shared store (README, Known gaps).
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

MAX_TRACKED_CLIENTS = 10_000


class RateLimiter:
    """Sliding-window limit of `per_minute` requests per client key."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 60}
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


class DailyBudget:
    """At most `limit` model calls per UTC day."""

    def __init__(
        self, limit: int, today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat()
    ) -> None:
        self._limit = limit
        self._today = today
        self._day = today()
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            day = self._today()
            if day != self._day:
                self._day, self._used = day, 0
            if self._used >= self._limit:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        return self._used
