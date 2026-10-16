"""Conversation-history persistence helpers shared across pipeline + ReAct.

Phase D Wave 2 (2026-05-13): both ``core/turn/turn.py`` (pipeline mode)
and ``core/agent/dispatch.py`` (react mode) need to write user-side and
assistant-side rows to ``conversation_history`` after a turn settles.
Pre-Wave-2, the writers lived only in ``core/turn/turn.py`` and the
ReAct path skipped them entirely — so ReAct turns vanished from
``conversation_history`` and subsequent ReAct turns couldn't see their
own prior work.

Extracted here so dispatch.py can call the same helpers turn.py does
without depending on turn.py's pipeline-specific imports. The helpers
are best-effort: any database error is logged at WARNING and swallowed
so persistence failures never break the reply path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timezone
from pathlib import Path
from typing import Any

from core.turn.types import Turn

logger = logging.getLogger(__name__)

# Surfaces where he TALKS rather than types. A voice turn is filed under its
# door's platform ("discord" for a Discord voice call) with the voice
# channel's id, so without a mark nothing tells it from a text-chat row, and
# "the private chat he used last" (core/turn/proactive.py::_pick_proactive_surface)
# could pick a voice channel to post into (fix plan 5.8, round 1).
_VOICE_SURFACES = ("discord_voice", "voice_local")

# Surfaces where he types in a Discord or Telegram chat. Each row from one
# says whether that chat is a private chat with him (``is_dm``: a Discord DM,
# a Telegram private chat), from what the door's intake saw. Her own messages
# to him go only to a private chat, and this mark is how "the private chat he
# used last" tells his DM from a server channel or a group (the owner,
# 2026-10-13: on 10-12 a note meant for him went to a server channel).
# The phone app (the mobile door, 2026-10-13) is a private chat with him by
# construction — a device token is always the owner — and its rows say so.
_CHAT_SURFACES = ("discord_text", "telegram_text", "mobile_text")


async def _persist_user_side_with_content(turn: Turn, content: str) -> None:
    """Insert the role=user conversation row with caller-specified content.

    Used for both the simple-content top-of-process_turn write and the
    vision-rich post-recall write.
    """
    try:
        from core.conversation_history_store import ConversationHistoryStore
        from core.database import brain
        from core.turn.recall import _platform_for_surface
    except Exception as exc:
        logger.warning("persist_turn: import failed: %s", exc)
        return

    platform = _platform_for_surface(str(turn.surface)) or str(turn.surface)
    channel_id = int(turn.metadata.channel_id or 0)
    user_id = int(turn.owner_id)
    # Telegram door (2026-09-14, task 2): a Telegram turn has no Discord
    # handle, and a NULL username comes back as an anonymous history row.
    # The second door's handle rides in metadata.extra — no schema churn,
    # task 1's ruling — and this is the one place that reads it.
    username = turn.metadata.discord_username or (
        turn.metadata.extra or {}).get("telegram_username")
    # A voice turn's row says which surface it came from; a chat row says
    # whether its chat is private with him; any other row is written exactly
    # as before (no metadata).
    surface = str(turn.surface)
    if surface in _VOICE_SURFACES:
        row_mark = {"metadata": {"surface": surface}}
    elif surface in _CHAT_SURFACES:
        row_mark = {"metadata": {"is_dm": bool(turn.metadata.is_dm)}}
    else:
        row_mark = {}

    try:
        from core.secrets_redaction import redact  # item 7c: his denylist

        store = ConversationHistoryStore(brain)
        await store.store_conversation(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=None,
            role="user",
            content=redact(content),
            username=username,
            **row_mark,
        )
    except Exception as exc:
        logger.warning("persist_turn: user-side store failed: %s", exc)

    await _mark_reachouts_answered(turn, platform=platform, channel_id=channel_id)


def _arrival_epoch(turn: Turn) -> float:
    """When the owner's message ARRIVED, as epoch seconds: the turn's
    ``received_at`` — naive UTC by the Turn's convention (datetime.utcnow),
    so it is read as UTC, never as the host's local time; an aware one is
    taken as it is. The current time only when the turn carries none."""
    received = getattr(turn, "received_at", None)
    if received is None:
        return time.time()
    try:
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        return float(received.timestamp())
    except Exception:
        return time.time()


async def _mark_reachouts_answered(turn: Turn, *, platform: str, channel_id: int) -> None:
    """The owner's message answers her reach-outs on the same door (Batch 6,
    task 6): every one still unanswered there from the six hours before it
    is marked answered in her book of reach-outs (core/turn/outbound.py).
    Only the owner's own words come through here — guests are routed away
    before it, and a guest's id is not the owner's anyway. A no-op while the
    arbiter is off; the file work runs off the event loop (DEFECT §9); never
    raises into the turn. A spoken answer is filed under the voice channel,
    so it is another door.

    The answer is timed by the message's ARRIVAL, not by this store (fix
    round 1, M2): the attachment paths store the message late — after the
    vision read, or after the whole run — and a reach-out sent in that gap
    came after the message, so it is not answered by it. The same turn
    stored twice carries the same arrival, so it is one answer."""
    try:
        from core.turn import outbound

        if not channel_id or not outbound.reachouts_enabled():
            return
        await asyncio.to_thread(
            outbound.mark_answered,
            owner_id=int(turn.owner_id), platform=str(platform),
            channel_id=int(channel_id), at=_arrival_epoch(turn),
        )
    except Exception as exc:
        logger.warning("persist_turn: marking her reach-outs answered failed: %s", exc)


async def _persist_assistant_side_to_history(*, turn: Turn, response_text: str) -> None:
    """Insert the role=assistant conversation row when respond produced text.

    Tier-2 returns response_text="" because the delegation bridge owns
    delivery; in that case we skip this — the bridge persists its own
    assistant rows separately. Same pattern for unresponded turns.
    """
    response_text = (response_text or "").strip()
    if not response_text:
        return
    try:
        from core.conversation_history_store import ConversationHistoryStore
        from core.database import brain
        from core.turn.recall import _platform_for_surface
    except Exception as exc:
        logger.warning("persist_turn: import failed: %s", exc)
        return

    platform = _platform_for_surface(str(turn.surface)) or str(turn.surface)
    channel_id = int(turn.metadata.channel_id or 0)
    user_id = int(turn.owner_id)

    try:
        from core.secrets_redaction import redact  # item 7c: his denylist

        store = ConversationHistoryStore(brain)
        await store.store_conversation(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=None,
            role="assistant",
            content=redact(response_text),
        )
    except Exception as exc:
        logger.warning("persist_turn: assistant-side store failed: %s", exc)


async def persist_delivery_failure(
    *,
    owner_id: int,
    platform: str,
    channel_id: int,
    reason: str,
    failed_text: str | None = None,
    store: Any = None,
    sessions_root: Path | None = None,
    landed: int = 0,
    total: int = 0,
) -> None:
    """A door's send died: make her record match what he saw (fix plan 5.10).

    Dispatch writes her reply to ``conversations`` and to her session BEFORE
    the door sends it. That keeps the order every door and the sidecar rely
    on, so it stays (ruling R9). A failed send therefore MARKS the reply
    instead of withholding it, in two places:

    * the reply itself, flagged in her runner session ``{platform}:{channel}``
      (the transcript her next payload is built from; a ``system`` row never
      reaches it), so her next turn reads it stamped;
    * a ``system`` row in ``conversations`` after it, so the record matches
      what he saw. Another row, such as a barge-in, can land in between.

    ``landed`` of ``total`` parts reached him (R9d). None landed means "not
    delivered". Some but not all means "only partly delivered": he saw the
    start, and the row and the stamp say so.

    The session is flagged FIRST. The flag only looks at the current
    exchange (after his last message), and flagging before the database
    await leaves the least room for his next message to land in between.

    ``reason`` is short and secret-free: an exception class and code, never a
    message body. This never fires ``turn_failed``, because the send module
    already did, once. Best-effort like every helper here: a failure is logged
    and swallowed, never raised into a door.
    """
    why = str(reason or "").strip() or "send failed"
    partial = 0 < int(landed) < int(total)
    if partial:
        content = f"[only partly delivered to them: {why} after {int(landed)} of {int(total)} parts]"
    else:
        content = f"[not delivered to them: {why}]"

    text = str(failed_text or "").strip()
    if text:
        from core.session_keys import room_key

        session_key = room_key(platform, int(channel_id))   # the room's transcript (Batch 8, R7)
        try:
            from core.agent.session import Session

            root = Path(sessions_root or os.environ.get("PALLI_AGENT_SESSIONS_ROOT")
                        or "data/agent_sessions")
            session = await Session.open_or_create(session_key, root=root)
            flagged = await session.mark_undelivered(text, partial=partial)
            if not flagged:
                # An owner's reply carries the real value where her record
                # keeps the masked token (put-back, Batch 1). It is the same
                # reply, so try the masked form too.
                from core.agent.payload_masking import mask_text

                masked = mask_text(text)
                if masked != text:
                    flagged = await session.mark_undelivered(masked, partial=partial)
            if not flagged:
                # R9b/R9e: only an exact match in the current exchange is
                # flagged. A sidecar's reply is not in this transcript yet,
                # and a guess would stamp a reply that landed.
                logger.info("persist_turn: not-delivered reply has no exact match in "
                            "session %s; left as is", session_key)
        except Exception as exc:
            logger.warning("persist_turn: not-delivered session flag failed: %s", exc)

    try:
        if store is None:
            from core.conversation_history_store import ConversationHistoryStore
            from core.database import brain

            store = ConversationHistoryStore(brain)
        await store.store_conversation(
            user_id=int(owner_id),
            platform=str(platform),
            channel_id=int(channel_id),
            thread_id=None,
            role="system",
            content=content,
            # Her day log must never attribute the note to a person; without a
            # name the store would label it User<owner_id>.
            username="system",
        )
    except Exception as exc:
        logger.warning("persist_turn: not-delivered row failed: %s", exc)


def _expand_content_with_attachments(
    content: str,
    image_descriptions: tuple[str, ...],
    text_attachment_contents: tuple[str, ...],
) -> str:
    """Bake attachment summaries into a single string suitable for
    conversations.content, so future turns remember what an image/file
    showed. Mirrors respond_tier0's expansion so the persisted form matches
    what the LLM actually saw at reply time. Text attachments are truncated
    more aggressively in history to keep the conversations table tidy.

    Shared by both engines: the pipeline (core/turn/turn.py) and the ReAct
    path (core/agent/dispatch.py, Bug 5 fix 2026-07-04). Lives here so the
    two persist paths can't diverge.
    """
    parts: list[str] = []
    stripped = (content or "").strip()
    placeholders = ("[image attached]", "[file attached]")
    if stripped and stripped not in placeholders:
        parts.append(content)

    if image_descriptions:
        descriptions = "; ".join(
            " ".join(p.strip() for p in d.splitlines() if p.strip())
            for d in image_descriptions
        )
        parts.append(f"[image attached — vision analysis: {descriptions}]")

    for content_block in text_attachment_contents:
        # Trim each attachment in the persisted history so the conversations
        # table doesn't bloat with full file dumps. respond saw the full
        # content at reply time. Limit configurable via
        # TRUNCATION_HISTORY_CONTENT_MAX_CHARS; 0 means store full content.
        from core.config import config
        from core.truncation import truncate
        cap = config.TRUNCATION_HISTORY_CONTENT_MAX_CHARS
        first_line = content_block.split("\n", 1)[0] if content_block else ""
        body = truncate(content_block, cap)
        suffix = "" if cap <= 0 or len(content_block) <= cap else " …(truncated)"
        parts.append(
            f"[file attached — {first_line}]{suffix}\n{body[len(first_line):].lstrip()}"
        )

    return "\n\n".join(parts) if parts else (stripped or "")
