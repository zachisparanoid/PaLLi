"""Wave 3: Discord events -> spine Turn.

Replaces the intake work that bots/discord/bot.py and
bots/discord/message_intake.py do inline. Pure conversion: take a
discord.Message-compatible object, emit a canonical Turn for the
spine to process. No batching here (decide.py handles dedup at the
turn level), no access gating (local mode is single-owner), no
inline voice processing (voice_attachment.py + Wave 4 voice surface
own that path).

The legacy intake files stay on disk through Wave 4; Wave 5 deletes
them once Wave 4 has soaked.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from bots.common.attachments import is_text_like as _is_text_like
from bots.discord.voice_attachment import VOICE_PLACEHOLDER_CONTENT
from core.agent import cadence as _cadence
from core.agent import quiet_choice as _qc
from core.agent.speakers import clean_name, make_speaker
from core.turn.types import ImageRef, TextAttachmentRef, Turn, TurnMetadata

logger = logging.getLogger(__name__)

_IMAGE_MIME_PREFIX = "image/"
_VOICE_MIME_PREFIXES = ("audio/", "video/ogg")
# Which files she can READ (the legacy bot.py read them into the prompt so
# PaLLi could see code/log/config files dropped into chat): the lists and
# the question moved to bots/common/attachments.py for the telegram door
# (2026-09-14, task 2) — a .py file is text at every door, and two copies
# would drift invisibly.


def _is_image(att) -> bool:
    ct = (getattr(att, "content_type", "") or "").lower()
    return ct.startswith(_IMAGE_MIME_PREFIX)


def _is_voice(att) -> bool:
    ct = (getattr(att, "content_type", "") or "").lower()
    return any(ct.startswith(p) for p in _VOICE_MIME_PREFIXES)


def _is_text_attachment(att) -> bool:
    return _is_text_like(
        mime=getattr(att, "content_type", "") or "",
        filename=getattr(att, "filename", "") or "",
    )


def _extract_text_attachments(attachments) -> tuple[TextAttachmentRef, ...]:
    """Pull text/code attachments off a Discord message. URLs only here;
    recall._safe_load_text_attachments downloads + decodes asynchronously."""
    out: list[TextAttachmentRef] = []
    for att in attachments:
        if not _is_text_attachment(att):
            continue
        out.append(
            TextAttachmentRef(
                url=getattr(att, "url", None),
                filename=getattr(att, "filename", None),
                mime=getattr(att, "content_type", None),
                size_bytes=getattr(att, "size", None),
            )
        )
    return tuple(out)


def _strip_self_mention(content: str, bot_user_id: int) -> str:
    pattern = re.compile(rf"<@!?{bot_user_id}>")
    return pattern.sub("", content).strip()


# Step 2, part 7 (2026-10-08): an @mention of a person reads as their name.
# 10-06: the owner @mentioned a friend; she saw a bare <@1467…>, took it for
# her own ping and answered. Channels (<#…>) and roles (<@&…>) stay codes;
# a mention the app didn't resolve stays as it came.
_USER_MENTION = re.compile(r"<@!?(\d+)>")
# A forward's words are someone else's, and Discord doesn't say whose.
FORWARD_LINE = "(forwarded message; the app doesn't show who first wrote it)"


def _account_id(user) -> int | None:
    """A real account id (an int), or None: a test fake's MagicMock id, or
    a missing one, names nobody."""
    uid = getattr(user, "id", None)
    return uid if isinstance(uid, int) and not isinstance(uid, bool) else None


def _people(message) -> list:
    try:
        return [m for m in list(getattr(message, "mentions", None) or [])
                if _account_id(m) is not None]
    except TypeError:
        return []


def _shown_name(user) -> str:
    return clean_name(getattr(user, "display_name", None) or getattr(user, "name", None) or "")


def _names_for_mentions(content: str, people: list, bot_user_id: int) -> str:
    names = {str(m.id): _shown_name(m) for m in people if m.id != bot_user_id and _shown_name(m)}
    if not names:
        return content
    return _USER_MENTION.sub(
        lambda mt: f"@{names[mt.group(1)]}" if mt.group(1) in names else mt.group(0), content)


def _is_forward(message) -> bool:
    ref = getattr(message, "reference", None)
    kind = getattr(ref, "type", None) if ref is not None else None
    return str(getattr(kind, "name", kind) or "").lower() == "forward"


def _forwarded_words(message) -> str:
    """A forward's own words, and a placeholder for what it brought along."""
    try:
        snapshots = list(getattr(message, "message_snapshots", None) or [])
    except TypeError:
        return ""
    lines: list[str] = []
    for snap in snapshots:
        words = str(getattr(snap, "content", "") or "").strip()
        try:
            atts = list(getattr(snap, "attachments", None) or [])
        except TypeError:
            atts = []
        extras = (["[forwarded image]"] if any(_is_image(a) for a in atts) else []) + (
            ["[forwarded file]"] if any(not _is_image(a) for a in atts) else [])
        line = " ".join(x for x in (words, *extras) if x)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _addressed_to(message, people: list, bot_user_id: int) -> list[dict]:
    """Who the message is addressed to, as the app says: the author of the
    message it replies to, then each person it @mentions."""
    out: list[dict] = []
    ref = getattr(message, "reference", None)
    if ref is not None and not _is_forward(message):
        target = getattr(ref, "resolved", None)
        if target is None or getattr(target, "author", None) is None:
            target = getattr(ref, "cached_message", None)
        author = getattr(target, "author", None) if target is not None else None
        if author is not None and _account_id(author) is not None:
            out.append({"id": str(author.id), "name": _shown_name(author), "how": "reply"})
    for m in people:
        out.append({"id": str(m.id), "name": _shown_name(m), "how": "mention"})
    return out


def is_system_message(message) -> bool:
    """Discord's own notices (someone joined, a pin, a thread started, a
    boost) are nobody's words: not a turn. Compared by identity with
    discord's enum, so a test fake's type (a MagicMock) is an ordinary
    message, never a notice."""
    kind = getattr(message, "type", None)
    try:
        import discord
    except Exception:
        return False
    if not isinstance(kind, discord.MessageType):
        return False
    return kind not in (discord.MessageType.default, discord.MessageType.reply)


def _thread_parent(channel) -> str | None:
    """The channel a thread is in ("" when the app doesn't say); None for
    anything that isn't a thread."""
    try:
        import discord
        if isinstance(channel, discord.Thread):
            parent = getattr(channel, "parent", None)
            return str(getattr(parent, "name", "") or "") if parent is not None else ""
    except Exception:
        pass
    return None


# She decides (Batch 8, task 2b — ruling T2b-3): how much of the message a
# reply answers rides along as a signal; a quote, not the whole thing.
_REFERENCE_CHARS = 300


def _reply_signal(message, bot_user_id: int) -> dict | None:
    """A reply's signal: whether it answers one of HER messages and, when it
    does, that message's words and time. None when this is not a reply (a
    forward is not one). Reads ``reference.resolved``, then
    ``reference.cached_message``; with neither at hand (or the message
    deleted) the reply is named without its text -- no fetch here: intake is
    pure."""
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    kind = getattr(ref, "type", None)
    if str(getattr(kind, "name", kind) or "").lower() == "forward":
        return None
    target = getattr(ref, "resolved", None)
    if target is None or getattr(target, "author", None) is None:
        target = getattr(ref, "cached_message", None)
    author = getattr(target, "author", None) if target is not None else None
    if author is None:
        return {"to_her": None}
    if getattr(author, "id", None) != bot_user_id:
        return {"to_her": False}
    at = getattr(target, "created_at", None)
    return {
        "to_her": True,
        "text": _strip_self_mention(str(getattr(target, "content", "") or ""),
                                    bot_user_id)[:_REFERENCE_CHARS],
        "at": at.isoformat() if hasattr(at, "isoformat") else "",
    }


def _extract_images(attachments: Iterable) -> list[ImageRef]:
    images: list[ImageRef] = []
    for att in attachments:
        if not _is_image(att):
            continue
        images.append(
            ImageRef(
                url=getattr(att, "url", None),
                local_path=None,
                mime=getattr(att, "content_type", None),
                description=getattr(att, "filename", None),
            )
        )
    return images


def _is_dm_channel(message) -> bool:
    try:
        from discord import DMChannel
        return isinstance(message.channel, DMChannel)
    except Exception:
        return False


def _extract_file_attachments(attachments: Iterable) -> tuple[TextAttachmentRef, ...]:
    """DEFECT_RECORD §19 (2026-09-02): the catch-all bucket — anything that is
    not an image, a voice note, or a text file. Before this, an .xlsx or a
    .pdf matched no bucket, the content stayed empty, normalize_for_llm
    dropped the empty row, and she never knew a file had come. Nothing
    falls through any more; recall receives these into the inbox."""
    out: list[TextAttachmentRef] = []
    for att in attachments:
        if _is_image(att) or _is_voice(att) or _is_text_attachment(att):
            continue
        out.append(
            TextAttachmentRef(
                url=getattr(att, "url", None),
                filename=getattr(att, "filename", None),
                mime=getattr(att, "content_type", None),
                size_bytes=getattr(att, "size", None),
            )
        )
    return tuple(out)


def _bucket_of(att) -> str:
    if _is_image(att):
        return "image"
    if _is_voice(att):
        return "voice"
    if _is_text_attachment(att):
        return "text"
    return "file"


_IMAGE_ONLY_PLACEHOLDER = "[image attached]"
_FILE_ONLY_PLACEHOLDER = "[file attached]"


def build_turn_from_message(
    message,
    *,
    bot_user_id: int,
    owner_id: int,
    speaker_kind: str | None = None,
) -> Turn:
    """Produce a spine Turn from a discord.Message (or compatible mock).

    Owner resolution is the caller's responsibility (the launcher decides
    whether single-owner local mode applies). bot_user_id is needed both
    to detect self-mentions and to strip the mention tokens from content
    so the LLM sees clean text.
    """
    raw_content = getattr(message, "content", "") or ""
    people = _people(message)
    content = _names_for_mentions(_strip_self_mention(raw_content, bot_user_id), people,
                                  bot_user_id)
    own_words = content
    if _is_forward(message):
        forwarded = _forwarded_words(message)
        if forwarded:
            content = "\n".join(x for x in (content, FORWARD_LINE, forwarded) if x)

    attachments = list(getattr(message, "attachments", []) or [])
    images = _extract_images(attachments)
    text_attachments = _extract_text_attachments(attachments)
    file_attachments = _extract_file_attachments(attachments)
    has_voice = any(_is_voice(a) for a in attachments)
    # §19: every arrival is on the record — name, type, size, and where it went.
    for att in attachments:
        logger.info(
            "intake: attachment %s (%s, %s bytes) → %s bucket",
            getattr(att, "filename", None) or "?", getattr(att, "content_type", None) or "?",
            getattr(att, "size", None) if isinstance(getattr(att, "size", None), int) else "?",
            _bucket_of(att),
        )

    # When the user sends an attachment with no caption, content is empty.
    # Downstream the LLM sees an empty user message + the recent history,
    # which lets it latch onto whatever the prior thread was about
    # instead of the attachment actually present. Synthesize a marker so
    # decide/respond unambiguously see "this turn is an attachment arrival"
    # — recall.py's image_descriptions / text_attachment_contents in
    # TurnContext plus this marker together signal what's attached.
    if not content.strip():
        if images:
            content = _IMAGE_ONLY_PLACEHOLDER
        elif text_attachments or file_attachments:
            content = _FILE_ONLY_PLACEHOLDER
        elif has_voice:
            # Never let a voice-only message through with empty content — an
            # empty user turn persisted into the agent session is what 400'd
            # every later call in the 2026-06-11 outage. The client replaces
            # this placeholder with the actual transcript (or an honest
            # unavailable-marker) before dispatch.
            content = VOICE_PLACEHOLDER_CONTENT

    is_dm = _is_dm_channel(message)
    mentions = getattr(message, "mentions", []) or []
    is_mention = any(getattr(m, "id", None) == bot_user_id for m in mentions)

    # She decides (Batch 8, task 2b -- ruling T2b-3): with
    # companion.she_decides on, the signals she weighs (never requirements):
    # a reply to one of her messages, with what it answers, and her name.
    # Off, nothing here changes: today's metadata, field for field.
    is_reply_to_bot = False
    extra: dict = {}
    if _qc.she_decides_enabled():
        reply = _reply_signal(message, bot_user_id)
        if reply is not None:
            extra["reply_to"] = reply
            is_reply_to_bot = reply.get("to_her") is True
        extra["names_her"] = _cadence.names_her(own_words)
        # A6 (step 2, part 7): it @mentions someone, and not only her.
        others = [n for n in dict.fromkeys(
            _shown_name(m) for m in people
            if m.id not in (bot_user_id, getattr(getattr(message, "author", None), "id", None)))
            if n]
        if others:
            extra["mentions_others"] = others

    channel = getattr(message, "channel", None)
    guild = getattr(channel, "guild", None) if channel is not None else None
    if _qc.she_decides_enabled():
        thread_in = _thread_parent(channel)
        if thread_in is not None:
            extra["thread_in"] = thread_in
    # Step 2 (2026-10-08): who sent it, from the account — only when the
    # caller has decided (the client classifies owner or guest first). No
    # default: a caller that doesn't say records nobody, never the owner.
    author = getattr(message, "author", None)
    speaker = None
    if speaker_kind in ("owner", "guest"):
        speaker = make_speaker(
            speaker_kind, platform="discord", account_id=getattr(author, "id", ""),
            name=getattr(author, "display_name", None) or getattr(author, "name", None) or "",
            handle=getattr(author, "name", None) or "",
            to=_addressed_to(message, people, bot_user_id),
        )
    metadata = TurnMetadata(
        speaker=speaker,
        channel_id=getattr(channel, "id", None) if channel is not None else None,
        channel_name=getattr(channel, "name", None) if channel is not None else None,
        guild_id=getattr(guild, "id", None) if guild is not None else None,
        guild_name=getattr(guild, "name", None) if guild is not None else None,
        is_dm=is_dm,
        is_mention=is_mention,
        is_reply_to_bot=is_reply_to_bot,
        is_voice=has_voice,
        discord_username=getattr(getattr(message, "author", None), "name", None),
        origin="user",
        text_attachments=text_attachments,
        file_attachments=file_attachments,
        extra=extra,
    )

    return Turn(
        surface="discord_text",
        owner_id=owner_id,
        content=content,
        images=images,
        metadata=metadata,
    )
