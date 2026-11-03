"""Her own time — one thing at a time (2026-08-18, item 5c).

Owner: her own-time activities may happen whenever, even while talking to
him — "all of the above, just one at a time." Two of them are long and
tool-heavy: a workbench session (up to 20 min) and a curiosity dive. This
is the one lock they hold. His chat never touches it — his turns run on
their own session key and are never queued behind her.

Contract: ``hold(what)`` is an async context manager (waits its turn);
``busy()`` names what is running or None; ``reset_for_tests()``.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Optional

_lock: Optional[asyncio.Lock] = None
_holder: Optional[str] = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def busy() -> Optional[str]:
    """What is using her time right now, or None."""
    lock = _get_lock()
    return _holder if lock.locked() else None


@contextlib.asynccontextmanager
async def hold(what: str) -> AsyncIterator[None]:
    """Take her time for one activity; waits if another has it."""
    global _holder
    lock = _get_lock()
    async with lock:
        _holder = str(what or "something")
        try:
            yield
        finally:
            _holder = None


def reset_for_tests() -> None:
    global _lock, _holder
    _lock = None
    _holder = None
