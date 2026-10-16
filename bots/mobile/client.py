"""The mobile door's owner lane and its start (spec §2, §6).

The phone app uses the owner lane Telegram uses (bots/common/owner_lane.py,
moved there for this door): a burst of messages is one turn; typing on the
phone holds it, as on Discord; a new message while she replies cancels the
reply, or — with companion.converse_while_working on — goes through the
steering fork, so her work goes on. Started like a chat door's client: it
registers the adapter and the owner-notice sender for as long as it runs.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from bots.common.owner_lane import LaneHooks, OwnerLane
from bots.discord.recording import record_user_fragment
from bots.mobile import MOBILE_CHANNEL_ID, MOBILE_PLATFORM
from bots.mobile.adapter import MobileChannelAdapter
from bots.mobile.hub import hub as default_hub
from bots.mobile.reply import deliver_result, emit_notice, emit_text
from core.agent.dispatch import dispatch_turn
from core.channel_runtime import channel_runtime
from core.local_mode import get_local_owner_refs
from core.mobile_events_store import events_store
from core.owner_notice import register_owner_notice_sender, unregister_owner_notice_sender

logger = logging.getLogger(__name__)

_door: Optional["MobileDoor"] = None

# What the phone is told when a turn goes wrong: a plain line in her voice. The
# detail (an exception, a provider's message) stays in PaLLi's log.
TROUBLE_NOTICE = "Something went wrong answering that — the details are in PaLLi's log."


def active_door() -> Optional["MobileDoor"]:
    return _door


class _Typing:
    """Her typing indicator on every open phone, for as long as a turn runs."""

    def __init__(self, hub: Any, owner_id: int) -> None:
        self._hub = hub
        self._owner_id = owner_id

    async def __aenter__(self) -> "_Typing":
        await self._hub.set_typing(True, owner_id=self._owner_id)
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        try:
            await self._hub.set_typing(False, owner_id=self._owner_id)
        except Exception:
            logger.debug("mobile door: typing-off failed", exc_info=True)
        return False


async def _as_is(turn: Any) -> Any:
    """The phone's intake already saved and heard everything (bots/mobile/intake.py)."""
    return turn


class MobileDoor:
    def __init__(self, *, owner_id: int, store: Any, hub: Any = None, dispatch: Any = None,
                 deliver: Any = None, record: Any = None, sessions_root: Optional[Path] = None,
                 settle: float = 3.0, typing_window: float = 10.0, max_hold: float = 45.0) -> None:
        self._owner_id = int(owner_id)
        self._store = store
        self._hub = hub if hub is not None else default_hub
        self._dispatch = dispatch or dispatch_turn
        self._deliver = deliver or self._deliver_with_typing
        self._record = record or record_user_fragment
        self._sessions_root = Path(
            sessions_root or os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions")
        self._lane = OwnerLane(
            LaneHooks(
                door=MOBILE_PLATFORM,
                log_name="mobile door",
                dispatch=lambda turn: self._dispatch(turn),
                record=lambda turn, **kw: self._record(turn, **kw),
                send_text=lambda chat_id, text: emit_text(self._owner_id, text, store=self._store, hub=self._hub),
                deliver=self._deliver_hook,
                typing=lambda chat_id: _Typing(self._hub, self._owner_id),
                owner_ok=self._owner_alone,
                owner_burst=self._owner_burst,
                sessions_root=lambda: self._sessions_root,
            ),
            settle=settle, typing_window=typing_window, max_hold=max_hold,
            # The phone says when he is typing; his message ends it (Discord's rule, 2026-10-12).
            end_typing_on_message=True,
        )

    @property
    def owner_id(self) -> int:
        return self._owner_id

    async def accept(self, turn: Any) -> None:
        md = turn.metadata
        has_attachment = bool(turn.images or md.text_attachments or md.file_attachments or md.is_voice)
        # The author is whose turn it is (the device's owner, from the intake), so the
        # lane's owner gate reads who actually sent what; the routes refuse any other.
        await self._lane.arrive(MOBILE_CHANNEL_ID, turn, author_id=int(turn.owner_id),
                                has_attachment=has_attachment, resolve=_as_is)

    def note_typing(self) -> None:
        self._lane.note_typing(MOBILE_CHANNEL_ID, self._owner_id)

    async def _owner_alone(self, chat_id: int, authors: set) -> bool:
        # The authors the burst actually had (accept records each turn's owner): his alone.
        return authors == {self._owner_id}

    async def _owner_burst(self, turn: Any, **_kw: Any) -> Any:
        # The room pass is for rooms; the phone app is never one.
        return turn

    async def _deliver_with_typing(self, owner_id: int, result: Any, *, store: Any, hub: Any) -> int:
        """The door's own delivery: her typing shows on the phones while a
        voice reply is made, as Telegram's does (bots/telegram/client.py)."""
        return await deliver_result(owner_id, result, store=store, hub=hub,
                                    typing=lambda: _Typing(hub, owner_id))

    async def _deliver_hook(self, chat_id: int, result: Any, **_kw: Any) -> None:
        try:
            await self._deliver(self._owner_id, result, store=self._store, hub=self._hub)
            said = bool(getattr(result, "responded", False)) and bool(
                str(getattr(result, "response_text", "") or "").strip())
            if getattr(result, "error", None) and not said:
                logger.warning("mobile door: a turn failed with nothing to say: %s", result.error)
                await emit_notice(self._owner_id, TROUBLE_NOTICE, level="trouble",
                                  store=self._store, hub=self._hub)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the phone hears that something broke
            logger.exception("mobile door: delivering the turn failed")
            try:
                await emit_notice(self._owner_id, TROUBLE_NOTICE, level="trouble",
                                  store=self._store, hub=self._hub)
            except Exception:
                logger.exception("mobile door: the trouble notice failed too")


async def start_mobile_door() -> None:
    """The door's start, a channel integration like ``start_telegram_client``.
    The routes are always mounted; this is what makes the door a door."""
    global _door
    refs = await get_local_owner_refs()
    owner_id = refs.get("app_user_id")
    if owner_id is None:
        logger.error("mobile door cannot start — local owner unresolved")
        return
    store = events_store()
    adapter = MobileChannelAdapter(owner_id=int(owner_id), store=store)
    door = MobileDoor(owner_id=int(owner_id), store=store)
    _door = door
    try:
        try:
            await channel_runtime.register_adapter(
                MOBILE_PLATFORM, adapter, metadata={"label": "Phone app", "transport": "timeline+websocket"})
        except Exception:
            logger.exception("Failed to register the mobile channel adapter")
        register_owner_notice_sender(MOBILE_PLATFORM, adapter.send_owner_notice)
        logger.info("mobile door: open")
        await asyncio.Event().wait()
    finally:
        unregister_owner_notice_sender(MOBILE_PLATFORM, adapter.send_owner_notice)
        try:
            await channel_runtime.unregister_adapter(MOBILE_PLATFORM, reason="stopped")
        except Exception:
            logger.exception("Failed to unregister the mobile channel adapter")
        if _door is door:
            _door = None
