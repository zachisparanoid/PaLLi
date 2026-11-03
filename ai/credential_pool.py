"""Phase 8c — credential pool with cooldown-aware rotation.

A small, dependency-free utility that lets an LLM provider hold N API
keys instead of one. When a request fails with an auth- or
rate-limit-shaped error, the pool puts the offending key on a cooldown
and rotates to the next available key for the retry. If all keys are
cooling down simultaneously, the pool returns None and the caller can
choose to fail the request or back off.

Why this exists:
  Free-tier OpenAI / DeepSeek / Cartesia keys hit per-minute rate limits.
  A second key from a different account or org lets PaLLi keep
  responding through the limit instead of stalling. The pool also
  protects against an individual key being revoked or rate-limited at
  account level — the next key takes over without restart.

This module is intentionally provider-agnostic. Each provider decides
when to call `mark_failure()` (e.g. on HTTP 429) and when to call
`mark_success()` (e.g. on HTTP 200, to reset the failure counter).

Thread/asyncio safety:
  Operations are protected by an `asyncio.Lock` because the provider's
  request loop is async. The lock is held only for the constant-time
  state mutations, so contention is negligible. Sync callers can use
  `acquire_sync` / `mark_failure_sync`; they grab a `threading.Lock`
  for the same protection.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


# Default cooldown: 60s after a rate-limit failure. OpenAI's rate limits
# are per-minute buckets, so 60s usually clears the limit. For auth
# failures (401), a longer cooldown could be ideal, but in practice
# rotating immediately to the next key is the right call — and if the
# 401-d key is still 401-ing 60s later we'll cool it down again.
DEFAULT_COOLDOWN_SECONDS: float = 60.0


@dataclass
class CredentialEntry:
    """One credential in the pool plus its cooldown bookkeeping."""

    key: str
    cooldown_until: Optional[float] = None  # epoch seconds; None == ready
    failure_count: int = 0  # cumulative; for diagnostics + future eviction
    last_status_code: Optional[int] = None  # last HTTP status that failed it

    def is_available(self, *, now: float) -> bool:
        return self.cooldown_until is None or self.cooldown_until <= now

    def fingerprint(self) -> str:
        """Short, log-safe identifier — first 4 chars of the key.

        Never log the full key. The fingerprint is enough to disambiguate
        keys when looking at telemetry without leaking secrets.
        """
        if not self.key:
            return "<empty>"
        prefix = self.key[:4]
        return f"{prefix}…"


@dataclass
class CredentialPool:
    """An ordered list of API keys with cooldown-aware rotation.

    Construct from a list of strings (or a comma-separated env var via
    `from_env_value`). Pool of one preserves single-key behavior — no
    rotation happens because there's nothing to rotate to.
    """

    entries: List[CredentialEntry] = field(default_factory=list)
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    _index: int = 0  # round-robin cursor
    _async_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _sync_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_keys(
        cls,
        keys: Sequence[str],
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> "CredentialPool":
        """Build a pool from a list of key strings. Empty strings filtered."""
        entries = [CredentialEntry(key=k) for k in keys if k and k.strip()]
        return cls(entries=entries, cooldown_seconds=cooldown_seconds)

    @classmethod
    def from_env_value(
        cls,
        raw_value: str,
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> "CredentialPool":
        """Build a pool from one env-var value.

        The value may be a single key (returns a pool of one) or a
        comma-separated list (returns a pool of N). Whitespace around
        each entry is trimmed; empty entries are skipped. This format
        is backwards-compatible with the existing single-key env vars
        (e.g. `OPENAI_API_KEY=sk-…` continues to work as a pool of one).
        """
        if not raw_value:
            return cls(entries=[], cooldown_seconds=cooldown_seconds)
        keys = [piece.strip() for piece in raw_value.split(",") if piece.strip()]
        return cls.from_keys(keys, cooldown_seconds=cooldown_seconds)

    # =========================================================================
    # Inspection helpers
    # =========================================================================

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @property
    def is_multi_key(self) -> bool:
        return len(self.entries) > 1

    def first_key(self) -> str:
        """Return the first key or empty string if pool is empty.

        Used by providers for backwards compatibility — single-key
        callers (e.g. provider construction in tests) treat
        `pool.first_key()` like the old `self.api_key`.
        """
        return self.entries[0].key if self.entries else ""

    def diagnostics(self, *, now: Optional[float] = None) -> List[dict]:
        """Snapshot of pool state for telemetry/logging. Never includes
        the full key — only fingerprint and counters.
        """
        ts = now if now is not None else time.time()
        return [
            {
                "fingerprint": e.fingerprint(),
                "available": e.is_available(now=ts),
                "cooldown_remaining_s": (
                    max(0.0, e.cooldown_until - ts) if e.cooldown_until else 0.0
                ),
                "failure_count": e.failure_count,
                "last_status_code": e.last_status_code,
            }
            for e in self.entries
        ]

    # =========================================================================
    # Sync API (for use outside an asyncio event loop, e.g. tests, CLI)
    # =========================================================================

    def acquire_sync(self) -> Optional[str]:
        """Return the next available key, advancing the round-robin cursor.

        Returns None if every key is cooling down. Caller should treat
        None as "all credentials temporarily unavailable" — typically by
        failing the request or backing off.
        """
        with self._sync_lock:
            return self._acquire_locked()

    def mark_failure_sync(
        self,
        key: str,
        *,
        status_code: Optional[int] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> None:
        with self._sync_lock:
            self._mark_failure_locked(key, status_code, cooldown_seconds)

    def mark_success_sync(self, key: str) -> None:
        with self._sync_lock:
            self._mark_success_locked(key)

    # =========================================================================
    # Async API (used by the provider request loop)
    # =========================================================================

    async def acquire(self) -> Optional[str]:
        async with self._async_lock:
            return self._acquire_locked()

    async def mark_failure(
        self,
        key: str,
        *,
        status_code: Optional[int] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> None:
        async with self._async_lock:
            self._mark_failure_locked(key, status_code, cooldown_seconds)

    async def mark_success(self, key: str) -> None:
        async with self._async_lock:
            self._mark_success_locked(key)

    # =========================================================================
    # Locked-section primitives. Callers must hold either lock.
    # =========================================================================

    def _acquire_locked(self) -> Optional[str]:
        if not self.entries:
            return None
        now = time.time()
        # One full pass over all entries — first available wins.
        n = len(self.entries)
        for _ in range(n):
            entry = self.entries[self._index % n]
            self._index = (self._index + 1) % n
            if entry.is_available(now=now):
                return entry.key
        # All entries are cooling down.
        return None

    def _mark_failure_locked(
        self,
        key: str,
        status_code: Optional[int],
        cooldown_override: Optional[float],
    ) -> None:
        for entry in self.entries:
            if entry.key == key:
                cooldown = (
                    cooldown_override
                    if cooldown_override is not None
                    else self.cooldown_seconds
                )
                entry.cooldown_until = time.time() + cooldown
                entry.failure_count += 1
                entry.last_status_code = status_code
                logger.info(
                    "credential pool: %s on cooldown for %.1fs (status=%s, "
                    "failures=%d)",
                    entry.fingerprint(),
                    cooldown,
                    status_code,
                    entry.failure_count,
                )
                return

    def _mark_success_locked(self, key: str) -> None:
        for entry in self.entries:
            if entry.key == key:
                # Reset cooldown immediately. failure_count stays as a
                # cumulative counter for diagnostics.
                entry.cooldown_until = None
                return
