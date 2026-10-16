from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

# How long a door may take to say whether a chat is the owner's private chat
# with her (``ChannelRuntimeRegistry.shows_owner_private_chat``). Discord may
# have to open the owner's DM to answer; a gateway that has gone away must not
# hold her reach-out for long, and no answer is "not shown".
OWNER_PRIVATE_CHAT_TIMEOUT_S = 10.0


class DoorNotReady(RuntimeError):
    """The door is not up yet: no adapter is registered for the platform, or
    the one registered says it cannot send yet (Discord registers its adapter
    before ``client.start()`` and is not ready until its gateway connects).

    Nothing was sent and nothing was refused, so a caller that counts failed
    deliveries — the reminder loop's dead-letter cap — does not count this
    while a door can still be expected to come up (final review, Important
    3; bounded to the loop's boot grace window by R17a). A RuntimeError, so
    every caller that already catches one keeps working."""


@dataclass(slots=True)
class ChannelAttachment:
    kind: str
    filename: str = ""
    content_type: str = ""
    data: Optional[bytes] = None
    path: str = ""
    url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChannelDeliveryEnvelope:
    text: str = ""
    attachments: List[ChannelAttachment] = field(default_factory=list)
    reply_to: str = ""
    typing_hint: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(Protocol):
    async def send_envelope(self, *, surface_id: str, envelope: ChannelDeliveryEnvelope) -> Dict[str, Any]:
        ...


class ChannelRuntimeRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[str, ChannelAdapter] = {}
        self._health: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def register_adapter(self, platform: str, adapter: ChannelAdapter, *, metadata: Optional[Dict[str, Any]] = None) -> None:
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            self._adapters[clean_platform] = adapter
            self._health[clean_platform] = {
                "platform": clean_platform,
                "status": "connected",
                "updated_at": time.time(),
                "metadata": dict(metadata or {}),
            }

    async def unregister_adapter(self, platform: str, *, reason: str = "") -> None:
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            self._adapters.pop(clean_platform, None)
            self._health[clean_platform] = {
                "platform": clean_platform,
                "status": "disconnected",
                "updated_at": time.time(),
                "metadata": {"reason": str(reason or "").strip()},
            }

    async def update_health(self, platform: str, *, status: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            existing = dict(self._health.get(clean_platform) or {})
            existing.update(
                {
                    "platform": clean_platform,
                    "status": str(status or "unknown").strip().lower(),
                    "updated_at": time.time(),
                }
            )
            if metadata:
                merged = dict(existing.get("metadata") or {})
                merged.update(metadata)
                existing["metadata"] = merged
            self._health[clean_platform] = existing

    async def door_ready(self, platform: str) -> bool:
        """True when an adapter is registered for ``platform`` and, if it can
        say so (an ``is_ready()`` method), is ready to send. Read-only and
        never raises: a probe that breaks leaves the verdict to the send."""
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            adapter = self._adapters.get(clean_platform)
        if adapter is None:
            return False
        probe = getattr(adapter, "is_ready", None)
        if not callable(probe) or inspect.iscoroutinefunction(probe):
            return True
        try:
            return probe() is not False
        except Exception:  # noqa: BLE001 — not a verdict; the send will say
            return True

    async def shows_owner_private_chat(self, platform: str, surface_id: str) -> bool:
        """True only when the door for ``platform`` shows that ``surface_id``
        is the owner's private chat with her (the owner, 2026-10-13: her own
        messages to him go to a private chat, never a group).

        core/doors.py asks this about a chat whose rows carry no private mark
        (rows written before 10-13). Only the door can tell, through an
        ``is_owner_private_chat(surface_id)`` method of its adapter (Discord's
        opens his DM and compares). No adapter, an adapter without that method,
        an answer that is not ``True``, an error, or no answer within
        ``OWNER_PRIVATE_CHAT_TIMEOUT_S``: all False, so a chat that cannot be
        shown private is never one. Never raises."""
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            adapter = self._adapters.get(clean_platform)
        check = getattr(adapter, "is_owner_private_chat", None)
        if not callable(check):
            return False
        try:
            answer = await asyncio.wait_for(
                check(str(surface_id or "").strip()), OWNER_PRIVATE_CHAT_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("channel_runtime: the %s door did not say within %.0f s whether "
                           "chat %s is the owner's private chat", clean_platform,
                           OWNER_PRIVATE_CHAT_TIMEOUT_S, surface_id)
            return False
        except Exception as exc:  # noqa: BLE001 — not shown is not private
            logger.warning("channel_runtime: the %s door could not say whether chat %s "
                           "is the owner's private chat (%s)", clean_platform, surface_id, exc)
            return False
        return answer is True

    async def send_envelope(self, *, platform: str, surface_id: str, envelope: ChannelDeliveryEnvelope) -> Dict[str, Any]:
        clean_platform = str(platform or "").strip().lower()
        async with self._lock:
            adapter = self._adapters.get(clean_platform)
        if adapter is None:
            raise DoorNotReady(f"No channel adapter registered for {clean_platform}")
        result = await adapter.send_envelope(surface_id=str(surface_id or "").strip(), envelope=envelope)
        # Present Moment sense D (2026-07-24): she feels her own initiative
        # sends go out (proactive / reminders / ambient — replies don't pass
        # here; they ride sense A). Structural only: platform + size.
        try:
            from ai.present_moment import notice as _pm_notice
            _pm_notice(
                "initiative_sent",
                platform=clean_platform,
                chars=len(getattr(envelope, "text", "") or ""),
            )
        except Exception:
            pass
        return result

    def snapshot(self) -> Dict[str, Any]:
        return {
            "platforms": [dict(item) for item in self._health.values()],
            "connected_platforms": sorted([platform for platform, item in self._health.items() if item.get("status") == "connected"]),
        }


channel_runtime = ChannelRuntimeRegistry()
