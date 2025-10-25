#!/usr/bin/env python3
"""Utilities for throttling upstream traffic using request-per-minute limits."""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class _LimiterState:
    """Internal per-key rate limiter state."""

    next_slot: float
    history: Deque[float] = field(default_factory=deque)


class RequestRateLimiter:
    """Asynchronous RPM limiter that smooths bursts and honours the first-request window."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._state: Dict[str, _LimiterState] = {}

    async def acquire(self, key: str, requests_per_minute: Optional[float]) -> float:
        """Wait until the next request is permitted for the provided key.

        Returns:
            float: The number of seconds the caller waited before the request may proceed.
        """
        if not key:
            key = "__default__"

        if requests_per_minute is None:
            return 0.0

        try:
            rpm = float(requests_per_minute)
        except (TypeError, ValueError):
            return 0.0

        if rpm <= 0:
            return 0.0

        interval = 60.0 / rpm
        safety_margin = max(0.5, interval * 0.1)
        window = 60.0
        now = time.monotonic()

        async with self._lock:
            state = self._state.get(key)
            if state is None:
                state = _LimiterState(next_slot=now)
                self._state[key] = state

            history = state.history

            # Drop timestamps that have left the rolling one-minute window
            cutoff = now - window
            while history and history[0] < cutoff:
                history.popleft()

            # Honour previously scheduled dispatch time to keep requests spaced apart
            scheduled_time = max(state.next_slot, now)

            # Guard against bursts that could still exceed provider quotas
            max_within_window = max(1, math.ceil(rpm))
            if len(history) >= max_within_window:
                earliest_allowed = history[0] + window
                if earliest_allowed > scheduled_time:
                    scheduled_time = earliest_allowed

            wait_time = scheduled_time - now

            # Reserve the slot for this request and record it in history
            state.next_slot = scheduled_time + interval + safety_margin
            history.append(scheduled_time)

        if wait_time > 0:
            await asyncio.sleep(wait_time)

        return float(wait_time) if wait_time > 0 else 0.0

    def get_last_request_time(self, key: str) -> Optional[float]:
        """Get the timestamp of the last request for the given key.

        Args:
            key: The key to query

        Returns:
            Optional[float]: The time.time() timestamp of the last request, or None if no requests yet.
        """
        if not key:
            key = "__default__"

        state = self._state.get(key)
        if state is None or not state.history:
            return None

        # Convert from monotonic time to wall clock time
        # We need to store wall clock time for the burst delay calculation
        # For now, return the last scheduled time (which is in monotonic time)
        # This is an approximation, but sufficient for burst detection
        return time.time() - (time.monotonic() - state.history[-1])
