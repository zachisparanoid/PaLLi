"""Dashboard channel adapter — buffers delegation envelopes for HTTP drain.

The dashboard surface (api/chat/message) is request/response, not a
push transport like Discord. So the channel runtime can't just call
adapter.send_envelope and have it land somewhere; the adapter has to
buffer the envelope until the in-flight HTTP request can drain it on
its way out.

This adapter:

- Registers itself for `platform="dashboard"` at boot.
- Stores incoming envelopes in a per-(owner_id, surface_id) deque
  (most-recent first).
- Exposes a `drain(owner_id, surface_id)` method for the HTTP handler
  in api/chat.py to call right after `process_turn` returns. Drained
  envelopes are removed from the buffer.
- Falls back to keeping envelopes in the buffer if no caller drains
  them within a TTL (default 5 minutes), then evicts.

Why this matters: testing on 2026-05-04 surfaced
`_deliver_final_result: send_envelope failed: No channel adapter
registered for dashboard`. Tier-2 delegations would complete, the
voicer would generate a substantive reply with attachment paths,
the bridge would call channel_runtime.send_envelope("dashboard", ...),
and the registry would raise RuntimeError. That meant the dashboard
chat endpoint received only the Tier-0 holding-line text, never
the Tier-2 result. Discord worked because Discord registered an
adapter that pushes via the gateway; dashboard had no equivalent
buffering surface. This module fills that gap.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.channel_runtime import (
    ChannelDeliveryEnvelope,
    channel_runtime,
)
from core.local_mode import get_local_owner_refs

logger = logging.getLogger(__name__)


_BUFFER_TTL_SECONDS = 300  # 5 minutes
_MAX_BUFFER_PER_KEY = 16


@dataclass(slots=True)
class _BufferedEnvelope:
    surface_id: str
    envelope: ChannelDeliveryEnvelope
    received_at: float = field(default_factory=time.time)


class DashboardChannelAdapter:
    """Buffers Tier-2 / reminder envelopes for the HTTP request handler to
    drain on its outbound path.

    Thread-safety: all access goes through an asyncio.Lock so concurrent
    process_turn invocations can't interleave drain/append corruption.
    """

    def __init__(self, *, ttl_seconds: int = _BUFFER_TTL_SECONDS, max_per_key: int = _MAX_BUFFER_PER_KEY) -> None:
        self._buffer: Dict[Tuple[int, str], deque[_BufferedEnvelope]] = defaultdict(
            lambda: deque(maxlen=max_per_key),
        )
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._max_per_key = max_per_key

    async def send_envelope(self, *, surface_id: str, envelope: ChannelDeliveryEnvelope) -> Dict[str, Any]:
        """Channel runtime entry point. Buffers the envelope and returns a
        delivery descriptor (for telemetry parity with Discord adapter)."""
        owner_id = await self._extract_owner_id(envelope)
        clean_surface = (surface_id or "").strip() or "default"
        key = (owner_id, clean_surface)
        async with self._lock:
            self._evict_expired_locked()
            self._buffer[key].append(_BufferedEnvelope(
                surface_id=clean_surface,
                envelope=envelope,
            ))
        logger.info(
            "dashboard_adapter: buffered envelope owner=%s surface=%s "
            "text_len=%d attachments=%d (queue=%d)",
            owner_id, clean_surface, len(envelope.text or ""),
            len(envelope.attachments or []),
            len(self._buffer[key]),
        )
        return {
            "delivered": True,
            "transport": "dashboard_buffer",
            "queue_depth": len(self._buffer[key]),
        }

    async def drain(self, *, owner_id: int, surface_id: str) -> List[ChannelDeliveryEnvelope]:
        """Return and remove all buffered envelopes for (owner_id, surface_id).

        Called by api/chat.py after process_turn so the HTTP response can
        carry any Tier-2 deliveries that landed during the turn.
        """
        clean_surface = (surface_id or "").strip() or "default"
        key = (int(owner_id), clean_surface)
        async with self._lock:
            self._evict_expired_locked()
            buf = self._buffer.get(key)
            if not buf:
                return []
            envelopes = [b.envelope for b in buf]
            buf.clear()
        if envelopes:
            logger.info(
                "dashboard_adapter: drained %d envelopes for owner=%s surface=%s",
                len(envelopes), owner_id, clean_surface,
            )
        return envelopes

    def _evict_expired_locked(self) -> None:
        """Remove envelopes older than TTL. Caller must hold the lock."""
        now = time.time()
        for key, buf in list(self._buffer.items()):
            while buf and (now - buf[0].received_at) > self._ttl:
                evicted = buf.popleft()
                logger.warning(
                    "dashboard_adapter: evicting expired envelope owner=%s "
                    "surface=%s (age=%.1fs, text_len=%d)",
                    key[0], key[1], now - evicted.received_at,
                    len(evicted.envelope.text or ""),
                )
            if not buf:
                self._buffer.pop(key, None)

    async def _extract_owner_id(self, envelope: ChannelDeliveryEnvelope) -> int:
        """Resolve owner-id from envelope metadata, falling back to local_mode.

        Decision 2 of the 2026-05-08 completion refactor: single-owner
        local mode resolution goes through `local_mode.get_local_owner_refs()`
        when metadata is sparse. Raises if no resolution succeeds — the
        registry should not silently buffer envelopes under user_id=1.
        """
        md = envelope.metadata or {}
        for key in ("owner_id", "owner_user_id", "user_id", "palli_user_id"):
            value = md.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        refs = await get_local_owner_refs()
        app_user_id = refs.get("app_user_id")
        if app_user_id is None:
            raise RuntimeError(
                "dashboard adapter: cannot extract owner_id — metadata "
                "lacks owner keys and local_mode resolution returned None"
            )
        return int(app_user_id)


# Module-level singleton — registered once at boot.
dashboard_channel_adapter = DashboardChannelAdapter()


async def register_dashboard_adapter(*, force: bool = False) -> None:
    """Register the dashboard adapter with the channel runtime.

    Idempotent: re-calling this when the adapter is already registered is
    a no-op (the registry's register_adapter swaps in the same instance).
    Use `force=True` only if a test needs to swap in a fresh adapter.
    """
    if force:
        await channel_runtime.unregister_adapter("dashboard", reason="re-register")
    await channel_runtime.register_adapter(
        "dashboard",
        dashboard_channel_adapter,
        metadata={"transport": "http_request_drain", "buffered": True},
    )
    logger.info("dashboard_adapter: registered with channel_runtime")
