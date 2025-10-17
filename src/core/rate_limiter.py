#!/usr/bin/env python3
"""Utilities for throttling upstream traffic using request-per-minute limits."""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional


class RequestRateLimiter:
    """Simple asynchronous rate limiter that enforces a minimum gap between requests."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._next_available: Dict[str, float] = {}

    async def acquire(self, key: str, requests_per_minute: Optional[float]) -> None:
        """Wait until the next request is permitted for the provided key."""
        if not key:
            key = "__default__"

        if requests_per_minute is None:
            return

        try:
            rpm = float(requests_per_minute)
        except (TypeError, ValueError):
            return

        if rpm <= 0:
            return

        interval = 60.0 / rpm
        now = time.monotonic()

        async with self._lock:
            scheduled = self._next_available.get(key, now)
            wait_until = scheduled if scheduled > now else now
            self._next_available[key] = wait_until + interval

        wait_time = wait_until - now
        if wait_time > 0:
            await asyncio.sleep(wait_time)
