"""Tiny TTL cache. Sheet API has 60 reads/min/user; we cache for 30s and
serve hundreds of UI requests per minute from memory between Sheet hits.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """Single-key cache with TTL. Thread-safe (we have one uvicorn worker)."""

    def __init__(self, ttl_seconds: float = 30.0):
        self._ttl = ttl_seconds
        self._lock = Lock()
        self._entry: _Entry[T] | None = None

    def get(self) -> T | None:
        with self._lock:
            if self._entry is None or time.monotonic() >= self._entry.expires_at:
                return None
            return self._entry.value

    def set(self, value: T) -> None:
        with self._lock:
            self._entry = _Entry(value=value, expires_at=time.monotonic() + self._ttl)

    def invalidate(self) -> None:
        with self._lock:
            self._entry = None
