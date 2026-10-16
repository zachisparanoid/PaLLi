"""Tests for the CredentialPool (Phase 8c).

Covers rotation, cooldown, recovery, the env-var parsing helper, and the
both-locks-work property (sync and async APIs share the same state).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from ai.credential_pool import (
    DEFAULT_COOLDOWN_SECONDS,
    CredentialEntry,
    CredentialPool,
)


# =============================================================================
# Construction
# =============================================================================


def test_from_keys_filters_empty_and_whitespace():
    pool = CredentialPool.from_keys(["abc", "", " ", "def", "  ghi  "])
    assert len(pool) == 3
    assert [e.key for e in pool.entries] == ["abc", "def", "  ghi  "]


def test_from_env_value_handles_single_key():
    """Backwards compat — `OPENAI_API_KEY=sk-x` still produces a pool of one."""
    pool = CredentialPool.from_env_value("sk-onlyone")
    assert len(pool) == 1
    assert pool.first_key() == "sk-onlyone"
    assert not pool.is_multi_key


def test_from_env_value_splits_comma_separated_list():
    pool = CredentialPool.from_env_value("sk-aaa, sk-bbb,sk-ccc")
    assert len(pool) == 3
    assert pool.is_multi_key
    assert [e.key for e in pool.entries] == ["sk-aaa", "sk-bbb", "sk-ccc"]


def test_from_env_value_handles_empty_string():
    """Empty env var → empty pool. is_empty is True."""
    pool = CredentialPool.from_env_value("")
    assert len(pool) == 0
    assert pool.is_empty
    assert pool.first_key() == ""


# =============================================================================
# Rotation (round-robin)
# =============================================================================


def test_acquire_rotates_through_keys():
    """Sequential acquires walk through every key, then wrap around."""
    pool = CredentialPool.from_keys(["a", "b", "c"])
    seen = [pool.acquire_sync() for _ in range(6)]
    assert seen == ["a", "b", "c", "a", "b", "c"]


def test_acquire_returns_none_when_pool_empty():
    pool = CredentialPool.from_keys([])
    assert pool.acquire_sync() is None


def test_acquire_returns_same_key_when_pool_has_one():
    pool = CredentialPool.from_keys(["only"])
    for _ in range(5):
        assert pool.acquire_sync() == "only"


# =============================================================================
# Cooldown / failure handling
# =============================================================================


def test_mark_failure_skips_cooled_down_key_in_next_acquire():
    """The failed key is skipped on the next acquire and returned to the
    rotation only after its cooldown expires.
    """
    pool = CredentialPool.from_keys(["a", "b", "c"], cooldown_seconds=0.05)
    # Pretend we acquired "a", then it failed with a 429.
    pool.acquire_sync()  # advances cursor past "a", now points at "b"
    pool.mark_failure_sync("a", status_code=429)

    # Next two acquires should be "b" and "c", skipping "a".
    seen = [pool.acquire_sync(), pool.acquire_sync()]
    assert seen == ["b", "c"]

    # Wait long enough for cooldown to expire — "a" should be available again.
    time.sleep(0.06)
    next_key = pool.acquire_sync()
    assert next_key == "a"


def test_mark_failure_records_failure_count_and_status():
    pool = CredentialPool.from_keys(["a"], cooldown_seconds=10.0)
    pool.mark_failure_sync("a", status_code=429)
    pool.mark_failure_sync("a", status_code=401)
    entry = pool.entries[0]
    assert entry.failure_count == 2
    assert entry.last_status_code == 401  # most recent


def test_mark_success_clears_cooldown():
    """After a key recovers from cooldown via a successful call, it's
    immediately available again."""
    pool = CredentialPool.from_keys(["a", "b"], cooldown_seconds=600.0)
    pool.mark_failure_sync("a", status_code=429)
    # "a" is now cooling for 10 minutes — wouldn't recover naturally during this test.
    pool.mark_success_sync("a")
    # Should be available again immediately.
    assert pool.entries[0].is_available(now=time.time())


def test_acquire_returns_none_when_all_keys_cooling():
    pool = CredentialPool.from_keys(["a", "b"], cooldown_seconds=10.0)
    pool.mark_failure_sync("a", status_code=429)
    pool.mark_failure_sync("b", status_code=429)
    assert pool.acquire_sync() is None


def test_mark_failure_with_explicit_cooldown_override():
    """Caller can override the default cooldown — useful for very
    short-lived failures (e.g. transient 503) vs long-lived (revoked key).
    """
    pool = CredentialPool.from_keys(["a"], cooldown_seconds=600.0)
    pool.mark_failure_sync("a", status_code=503, cooldown_seconds=0.05)
    # Explicit override of 0.05s, not the default 600s.
    time.sleep(0.06)
    assert pool.entries[0].is_available(now=time.time())


# =============================================================================
# Diagnostics — never leak full keys
# =============================================================================


def test_fingerprint_truncates_key():
    entry = CredentialEntry(key="sk-supersecret-12345")
    assert entry.fingerprint() == "sk-s…"


def test_fingerprint_handles_empty_key():
    entry = CredentialEntry(key="")
    assert entry.fingerprint() == "<empty>"


def test_diagnostics_omits_full_keys():
    pool = CredentialPool.from_keys(["sk-alpha-1234", "sk-beta-5678"])
    diag = pool.diagnostics()
    serialized = repr(diag)
    # The full keys must not appear in diagnostics output.
    assert "sk-alpha-1234" not in serialized
    assert "sk-beta-5678" not in serialized
    # Fingerprints (first 4 chars) are fine.
    assert any("sk-a" in entry["fingerprint"] for entry in diag)
    assert any("sk-b" in entry["fingerprint"] for entry in diag)


def test_diagnostics_reports_cooldown_remaining():
    pool = CredentialPool.from_keys(["a"], cooldown_seconds=30.0)
    pool.mark_failure_sync("a", status_code=429)
    diag = pool.diagnostics()
    assert diag[0]["available"] is False
    assert diag[0]["failure_count"] == 1
    assert diag[0]["last_status_code"] == 429
    # Remaining cooldown should be close to 30s, give or take a few ms.
    assert 25.0 < diag[0]["cooldown_remaining_s"] <= 30.0


# =============================================================================
# Async API parity
# =============================================================================


def test_async_api_shares_state_with_sync_api():
    """The async and sync paths operate on the same entries; this test
    proves a sync call sees the effect of an async call (modulo locks).
    """

    async def _run() -> None:
        pool = CredentialPool.from_keys(["a", "b"], cooldown_seconds=60.0)
        first = await pool.acquire()
        assert first == "a"
        await pool.mark_failure(first, status_code=429)

        # Now check from the sync side: "a" is cooling, only "b" available.
        keys = {pool.acquire_sync() for _ in range(3)}
        assert keys == {"b"}

    asyncio.run(_run())


# =============================================================================
# Constants
# =============================================================================


def test_default_cooldown_is_60_seconds():
    """OpenAI rate-limit windows are per-minute. 60s is the right default."""
    assert DEFAULT_COOLDOWN_SECONDS == 60.0
