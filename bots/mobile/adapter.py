"""The mobile door's channel adapter (spec §4).

Delivery on this door is an append to its timeline — the same meaning
Telegram's adapter gives "Telegram's server took it". So a reach-out to a
phone that is off is delivered, and lands when the phone reconnects. The
rules every adapter keeps (bots/telegram/adapter.py): nothing to send is
``{"sent": False}``; nothing delivered RAISES, because outbound custody
reads a clean return as delivered.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from bots.mobile import MOBILE_CHANNEL_ID
from bots.mobile.hub import hub as default_hub
from bots.mobile.reply import emit_file, emit_notice, emit_text, file_send_failed
from core.channel_runtime import ChannelDeliveryEnvelope
from core.doors import phone_reachable
from core.mobile_events_store import events_store

logger = logging.getLogger(__name__)


def _door_enabled() -> bool:
    from core.config import config

    return bool(getattr(config, "effective_mobile_enabled", False))


class MobileChannelAdapter:
    def __init__(self, *, owner_id: int, store: Any = None, hub: Any = None,
                 enabled: Optional[Callable[[], bool]] = None) -> None:
        self._owner_id = int(owner_id)
        self._store = store
        self._hub = hub if hub is not None else default_hub
        self._enabled = enabled or _door_enabled

    def _events(self) -> Any:
        return self._store if self._store is not None else events_store()

    async def send_envelope(self, *, surface_id: str, envelope: ChannelDeliveryEnvelope) -> Dict[str, Any]:
        if str(surface_id) != str(MOBILE_CHANNEL_ID):
            raise RuntimeError(
                f"MobileChannelAdapter: no chat {surface_id!r} on the phone app (it has one: {MOBILE_CHANNEL_ID})")
        text = str(envelope.text or "").strip()
        paths = [str(a.path) for a in (envelope.attachments or []) if str(getattr(a, "path", "") or "")]
        if not text and not paths:
            return {"sent": False, "chat_id": MOBILE_CHANNEL_ID}
        reminder = bool((envelope.metadata or {}).get("reminder"))
        store = self._events()
        written = 0
        failed = ""
        try:
            if text:
                await emit_text(self._owner_id, text, reminder=reminder, store=store, hub=self._hub)
                written += 1
            for path in paths:
                failed = path
                await emit_file(self._owner_id, path, store=store, hub=self._hub)
                written += 1
        except Exception as exc:
            if written == 0:
                raise RuntimeError(f"MobileChannelAdapter: nothing was delivered: {exc}") from exc
            logger.warning("mobile adapter: %d part(s) delivered, then: %s", written, exc)
            # Part of it landed, so the send still answers delivered (a raise would
            # read as nothing sent); the owner still hears the file did not come
            # (review M9).
            try:
                await emit_notice(self._owner_id, file_send_failed(failed), level="trouble",
                                  store=store, hub=self._hub)
            except Exception:  # noqa: BLE001 — best effort; the warning above stands
                logger.warning("mobile adapter: the trouble notice for an undelivered file failed too",
                               exc_info=True)
        return {"sent": True, "chat_id": MOBILE_CHANNEL_ID, "events": written}

    def is_ready(self) -> bool:
        return bool(self._enabled())

    async def is_owner_private_chat(self, surface_id: str) -> bool:
        return str(surface_id) == str(MOBILE_CHANNEL_ID)

    async def send_owner_notice(self, text: str) -> bool:
        """core/owner_notice's sender: plain words that are not hers, as a
        ``notice`` line — never a message in her voice.

        False, with nothing written, when no phone can hear it (the door is
        shut or no phone is paired; ``core.doors.phone_reachable``), so the
        next door is asked rather than a timeline nobody reads (review I1)."""
        clean = str(text or "").strip()
        if not clean:
            return False
        if not await phone_reachable(self._owner_id):
            return False
        try:
            await emit_notice(self._owner_id, clean, level="info", store=self._events(), hub=self._hub)
            return True
        except Exception as exc:  # noqa: BLE001 — the next door is asked
            logger.warning("mobile adapter: the owner notice was not delivered (%s)", exc)
            return False
