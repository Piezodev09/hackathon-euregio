"""Einfache Token-Bucket-Ratenbegrenzung im Speicher (reicht für eine Demo-VM)."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: int, max_keys: int = 10_000):
        self.rate = rate_per_s
        self.burst = burst
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            if ok:
                tokens -= cost
            if len(self._buckets) >= self.max_keys and key not in self._buckets:
                self._buckets.clear()  # Schutz vor Speicherwachstum durch viele Absender
            self._buckets[key] = (tokens, now)
            return ok
