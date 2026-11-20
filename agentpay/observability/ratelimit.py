"""Token-bucket rate limiter keyed by caller (agent id / api key hash / client ip).

In-process; for multi-replica deployments swap the dict for Redis with the
same interface (take(key) -> (allowed, retry_after)).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, rate_per_s: float = 10.0, burst: int = 30, clock=time.monotonic) -> None:
        self.rate, self.burst, self.clock = rate_per_s, burst, clock
        self._b: dict[str, Bucket] = {}
        self._lock = threading.Lock()

    def take(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        now = self.clock()
        with self._lock:
            b = self._b.get(key)
            if b is None:
                b = self._b[key] = Bucket(self.burst, now)
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True, 0.0
            return False, (cost - b.tokens) / self.rate
