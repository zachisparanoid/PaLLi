"""Telegram updates -> spine Turn (telegram door, 2026-09-14, task 2).

The mirror of bots/discord/intake.py: pure conversion, no network, no
library import. Take an ``Update``-compatible object, emit a canonical
``Turn`` the spine already knows how to process.

Three things are Telegram's own and live here:

  * **The owner gate runs at intake, not after it.** Discord's client
    classifies the author and then builds the turn twice over; here the
    verdict shapes the turn once, and ``IntakeResult.is_owner`` tells the
    client which lane to take — one classification, one answer.
  * **A group message is not automatically hers to answer.** Privacy mode
    (set with BotFather) is the first lock: Telegram only delivers group
    messages that mention her or reply to her. This module is the second
    lock on the same door, because a privacy setting is a checkbox on a
    website and the promise it makes is hers to keep either way. With
    ``companion.she_decides`` on (Batch 8, task 2b — ruling T2b-4) the
    second lock is gone: every group message she is given reaches her own
    decision (the room's first read), and a mention, a reply to her or her
    name are signals she weighs, never requirements. Privacy mode still
    filters what Telegram delivers until the owner turns it off (SETUP.md).
  * **No attachment ever carries a URL.** Telegram's file URL is
    ``https://api.telegram.org/file/bot<TOKEN>/<path>`` — her bot token, in
    a string that would be persisted into history, transcripts and logs.
    Intake records the file IDS; ``materialize_attachments`` downloads them
    through the authenticated ``Bot`` afterwards and points the refs at
    local files.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path
from typing import Any

from bots.common.attachments import is_text_like
from bots.common.voice_note import (
    TRANSCRIPTION_TOO_LARGE_CONTENT,
    TRANSCRIPTION_UNAVAILABLE_CONTENT,
    VOICE_PLACEHOLDER_CONTENT,
    resolve_voice_content,
    transcribe_note,
)
from core.turn.inbox import INBOX_MAX_BYTES
from core.turn.types import ImageRef, TextAttachmentRef, Turn, TurnMetadata

logger = logging.getLogger(__name__)

_IMAGE_ONLY_PLACEHOLDER = "[image attached]"
_FILE_ONLY_PLACEHOLDER = "[file attached]"

# Every OTHER kind of thing Telegram can deliver (review round 1, important
# 2). Before this, a video, a sticker or a pinned location matched no
# handler at all: no turn, no line, no log — the exact shape DEFECT §19
# exists to prevent, where a thing he sent simply never happened. She cannot
# read any of these yet, so she says so in his words rather than being
# mysteriously silent. The ORDER matters only where two attributes can both
# be set; each line names one kind.
_UNREADABLE_KINDS: tuple[tuple[str, str], ...] = (
    ("video_note", "[a video note arrived — I can't watch those yet]"),
    ("video", "[a video arrived — I can't watch it yet]"),
    ("animation", "[an animated GIF arrived — I can't watch it yet]"),
    ("sticker", "[a sticker arrived — I can't see it yet]"),
    ("location", "[a location arrived — I can't read it yet]"),
    ("venue", "[a place arrived — I can't read it yet]"),
    ("contact", "[a contact card arrived — I can't read it yet]"),
    ("poll", "[a poll arrived — I can't read it yet]"),
    ("dice", "[a dice roll arrived — I can't read it yet]"),
)
# The last word, so a Turn with EMPTY content is unreachable by construction
# (review round 1, minor 10): an empty user row is what poisoned a whole
# session in the 2026-06-11 outage, and "I don't know what this was" is
# still more honest — and safer — than nothing at all.
_UNKNOWN_ARRIVAL = "[something arrived that I can't read yet]"

# What a file must fit under to be worth fetching at all, and it is the
# SMALLER of two walls (review round 1, minor 4):
#
#   * Telegram's own — getFile refuses above 50 MB, and a local Bot API
#     server raises that, which is what the override is for;
#   * the inbox's — core/turn/inbox.py refuses anything over INBOX_MAX_BYTES
#     when recall receives the file.
#
# Unclamped, a 30 MB file passed here, landed on disk, and was then refused
# by the inbox with "the file never arrived here" — she would have told him
# a file was lost while it sat in data/inbox/telegram/. One cap, named in
# the line she says, so the refusal happens once and honestly.
TELEGRAM_MAX_FILE_BYTES = min(
    int(os.environ.get("PALLI_TELEGRAM_MAX_FILE_BYTES", str(50 * 1024 * 1024))),
    INBOX_MAX_BYTES,
)

# Audio is capped LOWER, and by Telegram rather than by us: getFile hands a
# bot a download path only for files up to 20 MB, and asking for more is an
# error, not a file. A voice note over it is refused before the request, so
# what she says about it is "that was too big to fetch" rather than a fetch
# that was always going to fail.
#
# Clamped to the inbox's wall for the same reason the file cap is (review
# round 1, minor 5): the voice bytes land in data/inbox/telegram/ like every
# other attachment, and a cap that lets through what the inbox would refuse
# is a promise made in one place and broken in another.
TELEGRAM_VOICE_MAX_BYTES = min(20 * 1024 * 1024, INBOX_MAX_BYTES)

_PRIVATE = "private"
_GROUP_TYPES = ("group", "supergroup")
# What a Telegram username may contain — the boundary test for her own handle.
_HANDLE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True)
class IntakeResult:
    """The turn, and WHO built it.

    The client needs the verdict to pick a lane, and re-deriving it there
    would be two answers to one question — the exact shape of bug the owner
    gate exists to prevent.
    """

    turn: Turn
    is_owner: bool


# ── the update ────────────────────────────────────────────────────────────
def _message_of(update: Any) -> Any:
    """The one message this update is about, or None.

    Edited messages and channel posts are deliberately not turns: an edit
    is not a new thing said, and a channel post is a broadcast nobody is
    speaking to her through.
    """
    if getattr(update, "edited_message", None) is not None:
        return None
    if getattr(update, "channel_post", None) is not None:
        return None
    if getattr(update, "edited_channel_post", None) is not None:
        return None
    message = getattr(update, "message", None)
    if message is None:
        message = getattr(update, "effective_message", None)
    return message


def _mentions_bot(message: Any, bot_username: str) -> bool:
    """True when an ``@her`` mention entity in this message names HER.

    A mention of somebody else in a group is not a message to her — that is
    the whole reason this reads the entity's own text rather than searching
    for an '@' anywhere in the line.
    """
    handle = str(bot_username or "").strip().lstrip("@").lower()
    if not handle:
        return False
    target = "@" + handle

    # python-telegram-bot's own accessors, when the object has them. BOTH
    # are needed (review round 1, important 1): parse_entities reads
    # message.text/message.entities ONLY (_message.py:4613 →
    # _utils/entities.py:71), so on a photo or a document — where the words
    # are the CAPTION — it returns an empty dict, and reading that as "she
    # was not mentioned" dropped "@palli look at this [photo]" in a group
    # without a trace. parse_caption_entities is the other half
    # (_message.py:4640). Union them; either may be absent on a fake.
    parsed: dict = {}
    answered = False
    for accessor in ("parse_entities", "parse_caption_entities"):
        method = getattr(message, accessor, None)
        if not callable(method):
            continue
        try:
            found = method(["mention"])
        except Exception:  # noqa: BLE001 — an object that cannot answer
            continue
        if isinstance(found, dict):
            answered = True
            parsed.update(found)
    if answered:
        return any(str(value or "").strip().lower() == target
                   for value in parsed.values())

    text = str(getattr(message, "text", None)
               or getattr(message, "caption", None) or "")
    entities = list(getattr(message, "entities", None) or ())
    entities += list(getattr(message, "caption_entities", None) or ())
    # Telegram's offsets count UTF-16 code units (an emoji is two): slice the
    # UTF-16 encoding, as _mentioned does (the friend-test rehearsal, 10-09).
    units = text.encode("utf-16-le")
    for entity in entities:
        if str(getattr(entity, "type", "") or "").lower() != "mention":
            continue
        offset = getattr(entity, "offset", None)
        length = getattr(entity, "length", None)
        if isinstance(offset, int) and isinstance(length, int):
            word = units[2 * offset:2 * (offset + length)].decode("utf-16-le", "ignore")
            if word.strip().lower() == target:
                return True
    return False


def _hidden_sender(message: Any) -> str | None:
    """Who wrote it, when Telegram hides the person (step 2, part 8): an
    anonymous admin, or a channel posting into its discussion group. Both
    arrive "from" Telegram's own placeholder bot with ``sender_chat`` set,
    and used to be dropped as a bot. None for an ordinary message."""
    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat is None or getattr(sender_chat, "id", None) is None:
        return None
    chat = getattr(message, "chat", None)
    if getattr(chat, "id", object()) == sender_chat.id:
        return "an anonymous admin"
    title = _title(getattr(sender_chat, "title", None))
    kind = "channel" if str(getattr(sender_chat, "type", "") or "") == "channel" else "group"
    return f'the {kind} "{title}"' if title else f"a {kind}"


def _title(value: Any) -> str:
    return " ".join(str(value or "").replace('"', "'").split())[:80]


def _forward_line(message: Any) -> str | None:
    """Where a forward came from, all four of Telegram's kinds (step 2, part
    8); None when it isn't one. Unlike Discord, Telegram names the original
    sender unless that person hides their account."""
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None
    from core.agent.speakers import clean_name

    kind = str(getattr(origin, "type", "") or "").lower()
    if kind == "user":
        user = getattr(origin, "sender_user", None)
        name = clean_name(getattr(user, "full_name", None) or "") or (
            "@" + clean_name(getattr(user, "username", None) or "") if getattr(user, "username", None) else "")
        return f"(forwarded from {name})" if name else "(forwarded message)"
    if kind == "hidden_user":
        name = _title(getattr(origin, "sender_user_name", None))
        return (f'(forwarded from "{name}"; Telegram doesn\'t show their account)' if name
                else "(forwarded message; Telegram doesn't show who first wrote it)")
    if kind == "chat":
        title = _title(getattr(getattr(origin, "sender_chat", None), "title", None))
        return f'(forwarded from the group "{title}")' if title else "(forwarded from a group)"
    if kind == "channel":
        title = _title(getattr(getattr(origin, "chat", None), "title", None))
        return f'(forwarded from the channel "{title}")' if title else "(forwarded from a channel)"
    return "(forwarded message)"


def _mentioned(message: Any, bot_username: str, author_id: int) -> list[tuple[str, str]]:
    """The people a message @mentions, as (account id or "", name), her own
    handle and the author left out: ``mention`` entities are the @username
    as typed; ``text_mention`` entities carry the person's account."""
    from core.agent.speakers import clean_name

    handle = "@" + str(bot_username or "").strip().lstrip("@").lower()
    text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
    # Telegram's offsets count UTF-16 code units (an emoji is two), so the
    # library's own parse comes first, as in _mentions_bot; the fallback
    # slices the UTF-16 encoding (Fable's final review, 2).
    pairs: list[tuple[Any, str]] = []
    answered = False
    for accessor in ("parse_entities", "parse_caption_entities"):
        method = getattr(message, accessor, None)
        if not callable(method):
            continue
        try:
            found = method(["mention", "text_mention"])
        except Exception:  # noqa: BLE001 - an object that cannot answer
            continue
        if isinstance(found, dict):
            answered = True
            pairs += [(entity, str(word or "")) for entity, word in found.items()]
    if not answered:
        try:
            entities = list(getattr(message, "entities", None) or ())
            entities += list(getattr(message, "caption_entities", None) or ())
        except TypeError:
            return []
        units = text.encode("utf-16-le")
        for entity in entities:
            offset, length = getattr(entity, "offset", None), getattr(entity, "length", None)
            word = ""
            if isinstance(offset, int) and isinstance(length, int):
                word = units[2 * offset:2 * (offset + length)].decode("utf-16-le", "ignore")
            pairs.append((entity, word))
    out: list[tuple[str, str]] = []
    for entity, word in pairs:
        kind = str(getattr(entity, "type", "") or "").lower()
        word = word.strip()
        if kind == "mention":
            if word and word.lower() != handle:
                out.append(("", word))
        elif kind == "text_mention":
            user = getattr(entity, "user", None)
            uid = getattr(user, "id", None)
            if isinstance(uid, int) and uid != author_id:
                name = clean_name(getattr(user, "full_name", None) or getattr(user, "username", None) or "")
                if name:
                    out.append((str(uid), name))
    return list(dict.fromkeys(out))


def _strip_leading_handle(content: str, bot_username: str) -> str:
    """Drop her own ``@handle`` off the front, as Discord strips its mention
    token (bots/discord/intake.py): the model should see what was SAID."""
    handle = str(bot_username or "").strip().lstrip("@")
    if not handle:
        return content.strip()
    body = content.strip()
    prefix = "@" + handle
    if body[:len(prefix)].lower() == prefix.lower():
        # On a BOUNDARY, not a prefix (review round 1, minor 5): a Telegram
        # username is [A-Za-z0-9_], so "@palli_bot2 hi" is somebody ELSE and
        # stripping her own handle off the front of it left "2 hi".
        rest = body[len(prefix):]
        if not rest[:1] or rest[0] not in _HANDLE_CHARS:
            body = rest
    return body.strip()


# She decides (Batch 8, task 2b — ruling T2b-3): how much of the message a
# reply answers rides along as a signal; a quote, not the whole thing.
_REFERENCE_CHARS = 300


def _reply_signal(reply_to: Any, to_her: bool) -> dict:
    """A reply's signal: whether it answers one of HER messages and, when it
    does, that message's words and time (Telegram always delivers the
    replied-to message, one level deep)."""
    if not to_her:
        return {"to_her": False}
    at = getattr(reply_to, "date", None)
    return {
        "to_her": True,
        "text": str(getattr(reply_to, "text", None)
                    or getattr(reply_to, "caption", None) or "")[:_REFERENCE_CHARS],
        "at": at.isoformat() if hasattr(at, "isoformat") else "",
    }


# ── the attachments ───────────────────────────────────────────────────────
def _unreadable_line(message: Any) -> str | None:
    """The honest line for a kind she cannot read, or None."""
    for attribute, line in _UNREADABLE_KINDS:
        if getattr(message, attribute, None) is not None:
            return line
    return None


def _largest_photo(sizes: Any) -> Any:
    """Telegram sends one photo as several sizes. Take the biggest — the
    thumbnail is not what he wanted her to look at."""
    candidates = [s for s in list(sizes or []) if s is not None]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda s: (
            int(getattr(s, "file_size", 0) or 0),
            int(getattr(s, "width", 0) or 0) * int(getattr(s, "height", 0) or 0),
        ),
    )


def _file_entry(obj: Any, *, bucket: str, filename: str, mime: str | None) -> dict:
    return {
        "bucket": bucket,
        "file_id": str(getattr(obj, "file_id", "") or ""),
        "file_unique_id": str(getattr(obj, "file_unique_id", "") or ""),
        "filename": filename,
        "mime": mime,
        "size": getattr(obj, "file_size", None),
    }


def _collect_attachments(message: Any) -> tuple[list[dict], list[ImageRef],
                                                tuple, tuple, bool]:
    """Every file on this message, sorted into §19's buckets.

    Returns the file entries (what ``materialize_attachments`` downloads),
    the image refs, the text bucket, the file bucket, and whether a voice
    note came with it. Every ref is built WITHOUT a url — see the module
    docstring; the client fills them in afterwards.
    """
    entries: list[dict] = []
    images: list[ImageRef] = []
    text_refs: list[TextAttachmentRef] = []
    file_refs: list[TextAttachmentRef] = []
    has_voice = False

    photo = _largest_photo(getattr(message, "photo", None))
    if photo is not None:
        name = f"photo-{getattr(photo, 'file_unique_id', 'telegram')}.jpg"
        entries.append(_file_entry(photo, bucket="image", filename=name,
                                   mime="image/jpeg"))
        images.append(ImageRef(url=None, local_path=None, mime="image/jpeg",
                               description=name))

    document = getattr(message, "document", None)
    if document is not None:
        name = str(getattr(document, "file_name", None) or "file")
        mime = getattr(document, "mime_type", None)
        bucket = "text" if is_text_like(mime=mime, filename=name) else "file"
        entries.append(_file_entry(document, bucket=bucket, filename=name,
                                   mime=mime))
        ref = TextAttachmentRef(url=None, filename=name, mime=mime,
                                size_bytes=getattr(document, "file_size", None))
        (text_refs if bucket == "text" else file_refs).append(ref)

    for attr, default_mime, default_name in (
        ("voice", "audio/ogg", "voice.ogg"),
        ("audio", "audio/mpeg", "audio"),
    ):
        note = getattr(message, attr, None)
        if note is None:
            continue
        has_voice = True
        name = str(getattr(note, "file_name", None) or default_name)
        entries.append(_file_entry(
            note, bucket="voice", filename=name,
            mime=getattr(note, "mime_type", None) or default_mime))

    for entry in entries:
        logger.info(
            "telegram intake: attachment %s (%s, %s bytes) → %s bucket",
            entry["filename"] or "?", entry["mime"] or "?",
            entry["size"] if isinstance(entry["size"], int) else "?",
            entry["bucket"],
        )
    return entries, images, tuple(text_refs), tuple(file_refs), has_voice


# ── the turn ──────────────────────────────────────────────────────────────
def build_turn(
    update: Any,
    *,
    bot_username: str,
    bot_user_id: int,
    owner_ref: dict,
) -> IntakeResult | None:
    """Produce a spine Turn from a Telegram Update, or None.

    None means "not hers to answer": no message, an edit, a channel post, a
    bot talking, or a group message that neither mentions her nor replies to
    her.
    """
    message = _message_of(update)
    if message is None:
        return None
    author = getattr(message, "from_user", None)
    hidden = _hidden_sender(message)
    if author is None or (bool(getattr(author, "is_bot", False)) and hidden is None):
        return None
    chat = getattr(message, "chat", None)
    if chat is None or getattr(chat, "id", None) is None:
        return None

    chat_id = int(chat.id)
    chat_type = str(getattr(chat, "type", "") or "")
    is_dm = chat_type == _PRIVATE

    reply_to = getattr(message, "reply_to_message", None)
    is_reply_to_bot = bool(
        reply_to is not None
        and int(getattr(getattr(reply_to, "from_user", None), "id", 0) or 0)
        == int(bot_user_id or 0)
    )
    # She decides (Batch 8, task 2b — rulings T2b-3, T2b-4): with the switch
    # on, a reply to her is its own signal, no longer folded into the
    # mention, and the second lock is gone; off, both are today's exactly.
    from core.agent import quiet_choice as _qc

    she_decides = _qc.she_decides_enabled()
    mentioned = _mentions_bot(message, bot_username)
    is_mention = mentioned if she_decides else (mentioned or is_reply_to_bot)

    # The second lock (see the module docstring). A DM is always hers; in a
    # group she answers only what is addressed to her; anywhere else (a
    # channel, a type we do not know) is not a conversation at all. With
    # she_decides on, a group message reaches her decision either way.
    if not is_dm and chat_type not in _GROUP_TYPES:
        return None
    if not is_dm and not she_decides and not is_mention:
        return None

    entries, images, text_refs, file_refs, has_voice = _collect_attachments(message)

    content = _strip_leading_handle(
        str(getattr(message, "text", None)
            or getattr(message, "caption", None) or ""),
        bot_username,
    )
    # The signals she weighs (ruling T2b-3), noted before any line of ours
    # joins the content: her name in what was said, and what a reply answers.
    forward = _forward_line(message)
    author_id = int(getattr(author, "id", 0) or 0)
    mentioned = _mentioned(message, bot_username, author_id)
    signals: dict = {}
    if she_decides:
        from core.agent.cadence import names_her

        # Her name inside a forward is someone else's words, not said to her.
        signals["names_her"] = names_her(content) if forward is None else False
        if reply_to is not None:
            signals["reply_to"] = _reply_signal(reply_to, is_reply_to_bot)
        others = [name for _, name in mentioned]
        if others:
            signals["mentions_others"] = others          # A6 (step 2, part 8)
    if forward is not None:
        content = f"{forward}\n{content}" if content.strip() else forward
    # A kind she cannot read says so, AFTER his caption — the words he chose
    # come first, and the line tells her what they were about.
    unreadable = _unreadable_line(message)
    if unreadable:
        content = f"{content}\n{unreadable}" if content.strip() else unreadable

    if not content.strip():
        # An attachment with no caption arrives as an empty message, and an
        # empty user row lets the model latch onto whatever the last thread
        # was about instead of the thing actually in front of it. Worse, an
        # empty voice turn is what poisoned a whole session in the
        # 2026-06-11 outage. Say what came, always — the last branch makes
        # "always" true by construction rather than by hoping.
        if images:
            content = _IMAGE_ONLY_PLACEHOLDER
        elif text_refs or file_refs:
            content = _FILE_ONLY_PLACEHOLDER
        elif has_voice:
            content = VOICE_PLACEHOLDER_CONTENT
        else:
            content = _UNKNOWN_ARRIVAL

    from core.owner_gate import (
        GUEST_ORIGIN, classify_author, guest_content, is_owner_verdict,
    )

    verdict = classify_author(author_id, owner_ref.get("telegram_user_id"))
    # A hidden sender is never the owner: the account Telegram shows is its
    # placeholder bot's (step 2, part 8).
    is_owner = is_owner_verdict(verdict) and hidden is None
    username = str(getattr(author, "username", None) or "").strip()
    full_name = str(getattr(author, "full_name", None) or "").strip()
    name = hidden or username or full_name or "guest"     # the account's handle
    # One name for a guest (Fable's checkpoint review, 8; 2026-10-13): their
    # tag and their record say full_name, the name the group shows; rows
    # tagged with the username before still read under it (core.agent.speakers).
    shown = hidden or full_name or username or "guest"

    if is_owner:
        owner_id = int(owner_ref.get("app_user_id") or 0)
        origin = "user"
    else:
        # The guest shape, exactly as bots/discord/client._handle_guest_message
        # builds it: THEIR id (so the identity store compiles their own empty
        # template docs, never his), origin 'guest' (dispatch gives no tools
        # and renders none of his private bands), and their name on the line
        # so the shared transcript stays legible about who spoke.
        owner_id = author_id
        origin = GUEST_ORIGIN
        # A guest's voice note keeps the placeholder here: since 10-12 (the
        # owner: guests' messages are "normal messages, minus permissions")
        # it is HEARD, by ``hear_guest_voice`` in the guest's own task, which
        # puts the words — or the honest marker — under this same tag.
        content = guest_content(shown, content)

    from core.agent.speakers import make_speaker
    to = []
    if reply_to is not None:
        replied = getattr(reply_to, "from_user", None)
        if isinstance(getattr(replied, "id", None), int):
            to.append({"id": str(replied.id), "how": "reply",
                       "name": str(getattr(replied, "full_name", None)
                                   or getattr(replied, "username", None) or "")})
    to += [{"id": uid, "name": who, "how": "mention"} for uid, who in mentioned]
    if hidden is not None:
        speaker = make_speaker("hidden", platform="telegram", name=hidden, to=to)
    else:
        speaker = make_speaker(
            "owner" if is_owner else "guest", platform="telegram", account_id=author_id,
            name=shown, handle=username, to=to,
        )

    metadata = TurnMetadata(
        speaker=speaker,
        channel_id=chat_id,
        channel_name=(str(getattr(chat, "title", None) or "") or None),
        is_dm=is_dm,
        is_mention=is_mention,
        is_reply_to_bot=is_reply_to_bot,
        is_voice=has_voice,
        origin=origin,
        text_attachments=text_refs,
        file_attachments=file_refs,
        extra={
            "telegram_username": name,
            "telegram_name": shown,          # the name on a guest's tag
            "telegram_chat_type": chat_type,
            "telegram_message_id": getattr(message, "message_id", None),
            **({"telegram_files": entries} if entries else {}),
            **signals,
        },
    )
    turn = Turn(
        surface="telegram_text",
        owner_id=owner_id,
        content=content,
        images=images,
        metadata=metadata,
    )
    return IntakeResult(turn=turn, is_owner=is_owner)


# ── the download, after the fact ──────────────────────────────────────────
def inbox_dir() -> Path:
    """Where Telegram's files land — under §19's inbox root, in their own
    subfolder so a file's door is visible from its path."""
    from core.turn.inbox import inbox_root

    return Path(inbox_root()) / "telegram"


def _note(content: str, line: str) -> str:
    body = str(content or "").strip()
    return (body + "\n" + line).strip() if body else line


async def materialize_attachments(turn: Any, bot: Any, *,
                                  root: Path | None = None) -> Any:
    """Download this turn's Telegram files and re-point its refs at them.

    Runs in the CLIENT, after intake, because it needs the authenticated
    Bot — and because that is what keeps the token out of the Turn. Each
    file is written once under the inbox and the ref carries ``file://`` +
    its absolute path, which ``core.turn.attachments.fetch_attachment_bytes``
    reads off disk.

    Nothing is ever lost silently (§19): a file over Telegram's own cap, or
    one whose download failed, drops its ref and gains an honest line on the
    turn's content instead. A GUEST's file-bucket attachment is refused the
    same way and for the same reason — see the loop.
    """
    from core.owner_gate import GUEST_ORIGIN

    metadata = getattr(turn, "metadata", None)
    extra = dict(getattr(metadata, "extra", None) or {})
    entries = list(extra.get("telegram_files") or [])
    if not entries:
        return turn

    directory = Path(root) if root is not None else inbox_dir()
    is_guest = str(getattr(metadata, "origin", "") or "") == GUEST_ORIGIN
    images: list[ImageRef] = []
    text_refs: list[TextAttachmentRef] = []
    file_refs: list[TextAttachmentRef] = []
    content = str(getattr(turn, "content", "") or "")

    for entry in entries:
        name = str(entry.get("filename") or "file")
        mime = entry.get("mime")
        size = entry.get("size")
        bucket = str(entry.get("bucket") or "file")

        if bucket == "voice":
            # The voice note has its own owner: resolve_voice_turn, which
            # runs next and has its own cap, its own download and its own
            # honest line (the marker). Fetching it here would only mean two
            # sentences about one failure — and a ref nothing ever reads.
            continue

        if is_guest and bucket == "file":
            # A guest turn is dispatched with NO tool registry at all
            # (core/agent/dispatch.py:174-182 — `registry = None if _guest`),
            # so a file in the inbox can never be read for them: the
            # download bought nothing and cost 25 MB of his disk per
            # stranger, five times per ten minutes, with no eviction — a fill
            # vector with a friendly face (final review, I4). The image and
            # text buckets stay: recall reads those on a guest turn as it
            # does on his, so refusing them would cost her the thing in front
            # of her. §19 still holds — it is SAID, not silently not-there.
            logger.info(
                "telegram intake: %s not downloaded — a guest's file bucket "
                "is never taken in", name)
            content = _note(content, (
                f"[attachment {name} was not taken in — I don't hold files "
                f"for guests]"))
            continue

        if isinstance(size, int) and size > TELEGRAM_MAX_FILE_BYTES:
            # The cap named here is the EFFECTIVE one (the smaller of
            # Telegram's and the inbox's), and it is not called Telegram's:
            # when the inbox is the shorter wall, saying "Telegram's limit"
            # would be a small lie in a sentence whose whole job is honesty.
            cap_mb = TELEGRAM_MAX_FILE_BYTES / (1024 * 1024)
            logger.warning(
                "telegram intake: %s not downloaded — %d bytes is over the "
                "%.0f MB limit", name, size, cap_mb)
            content = _note(content, (
                f"[attachment {name} was not fetched — "
                f"{size / (1024 * 1024):.1f} MB is over the "
                f"{cap_mb:.0f} MB limit on what I can take in]"))
            continue

        path = await _download(bot, entry, directory)
        if path is None:
            content = _note(content, f"[attachment {name} could not be fetched]")
            continue

        url = f"file://{path}"
        if bucket == "image":
            images.append(ImageRef(url=None, local_path=url, mime=mime,
                                   description=name))
        elif bucket == "text":
            text_refs.append(TextAttachmentRef(url=url, filename=name,
                                               mime=mime, size_bytes=size))
        elif bucket == "file":
            file_refs.append(TextAttachmentRef(url=url, filename=name,
                                               mime=mime, size_bytes=size))
        entry["local_path"] = str(path)

    return _dc_replace(
        turn,
        content=content,
        images=images,
        metadata=_dc_replace(
            metadata,
            text_attachments=tuple(text_refs),
            file_attachments=tuple(file_refs),
            extra={**extra, "telegram_files": entries},
        ),
    )


async def resolve_voice_turn(turn: Any, bot: Any, *, root: Path | None = None,
                             service: Any = None) -> Any:
    """Make a voice note HEARD: bytes → transcript → the turn's content.

    On his lane it runs in the client immediately after
    ``materialize_attachments`` (a guest's runs through ``hear_guest_voice``,
    before the room read; it downloads on its own) and BEFORE the fragment
    is recorded — exactly where bots/discord/client.py
    awaits ``resolve_voice_turn_content`` — because the row written to his
    transcript on arrival has to be what he said, not "[VOICE_MESSAGE]".

    The transcript comes from the configured speech stack (``transcribe_note``
    → ``VoiceSpeechService.transcribe_attachment_bytes``), so this door hears
    through whichever STT boot selected rather than through one vendor.

    A turn that carried no voice note is handed straight back. Everything
    else ends with non-empty content: the transcript, his caption with the
    transcript after it, or the honest unavailable-marker (the 2026-06-11
    law — an empty user row poisoned a whole session).
    """
    metadata = getattr(turn, "metadata", None)
    if not bool(getattr(metadata, "is_voice", False)):
        return turn

    extra = dict(getattr(metadata, "extra", None) or {})
    entries = [e for e in (extra.get("telegram_files") or [])
               if str(e.get("bucket") or "") == "voice"]
    directory = Path(root) if root is not None else inbox_dir()

    transcript = None
    marker = TRANSCRIPTION_UNAVAILABLE_CONTENT
    for entry in entries:
        transcript, marker = await _voice_transcript(bot, entry, directory,
                                                     service=service)
        if transcript:
            break

    content = str(getattr(turn, "content", "") or "")
    resolved = resolve_voice_content(transcript, content, marker=marker)
    if resolved == content:
        return turn
    return _dc_replace(turn, content=resolved)


async def hear_guest_voice(turn: Any, bot: Any, *, root: Path | None = None,
                           service: Any = None) -> Any:
    """A guest's voice note HEARD, inside their name tag (10-12 — the owner:
    guests' messages are "normal messages, minus permissions").

    Intake tags a guest's line as it builds the turn (``guest_content``), so
    the words under the tag are resolved exactly as ``resolve_voice_turn``
    resolves his — the transcript, their caption with the transcript after
    it, or the honest marker — and the same tag goes back around them. The
    client runs this in the guest's own background task, before the room
    read: a slow download never holds the door, and the read judges their
    words. Their audio is not kept once heard (final review, I4: "I don't
    hold files for guests") — the words are on the turn, and nothing reads a
    guest's audio after.
    """
    from core.owner_gate import guest_content

    metadata = getattr(turn, "metadata", None)
    if not bool(getattr(metadata, "is_voice", False)):
        return turn
    from bots.common.voice_note import guest_hearing_slot

    extra = dict(getattr(metadata, "extra", None) or {})
    name = extra.get("telegram_name") or extra.get("telegram_username")
    tag = guest_content(name, "")
    content = str(getattr(turn, "content", "") or "")
    # A few guests' notes are heard at once, across both doors (the download
    # and the STT both inside); the rest wait their turn (Fable's review).
    async with guest_hearing_slot():
        if content.startswith(tag):
            heard = await resolve_voice_turn(
                _dc_replace(turn, content=content[len(tag):]),
                bot, root=root, service=service)
            heard = _dc_replace(heard, content=guest_content(name, heard.content))
        else:                   # not intake's guest shape: resolve it whole
            heard = await resolve_voice_turn(turn, bot, root=root, service=service)
    _discard_voice_copies(heard)
    return heard


def _discard_voice_copies(turn: Any) -> None:
    """Remove the inbox copy of each voice note on ``turn`` (a guest's —
    see ``hear_guest_voice``). Never raises: a copy that will not go is
    logged, and costs the turn nothing."""
    extra = getattr(getattr(turn, "metadata", None), "extra", None) or {}
    for entry in extra.get("telegram_files") or []:
        if str(entry.get("bucket") or "") != "voice":
            continue
        path = entry.pop("local_path", None)
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("telegram intake: could not discard a guest's voice "
                           "note from the inbox: %s", exc)


async def _voice_transcript(bot: Any, entry: dict, directory: Path, *,
                            service: Any = None) -> tuple[str | None, str]:
    """One voice entry: its transcript, and the marker its failure deserves.

    Never raises: audio she cannot make out is a thing she says, not a
    message he loses. The second half of the pair is the honest line to use
    when the first is None — "too large" when the cap refused it, and
    "transcription unavailable" for every other reason.
    """
    name = str(entry.get("filename") or "voice.ogg")
    size = entry.get("size")
    cap_mb = TELEGRAM_VOICE_MAX_BYTES / (1024 * 1024)
    if isinstance(size, int) and size > TELEGRAM_VOICE_MAX_BYTES:
        logger.warning(
            "telegram intake: %s not transcribed — %d bytes is over the "
            "%.0f MB cap on audio she can fetch", name, size, cap_mb)
        return None, TRANSCRIPTION_TOO_LARGE_CONTENT

    path = await _download(bot, entry, directory)   # the one download path
    if path is None:
        return None, TRANSCRIPTION_UNAVAILABLE_CONTENT   # _download said why
    try:
        data = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram intake: could not read %s back for "
                       "transcription: %s", name, exc)
        return None, TRANSCRIPTION_UNAVAILABLE_CONTENT

    if len(data) > TELEGRAM_VOICE_MAX_BYTES:
        # file_size is Telegram's WORD for it, not a measurement — a file
        # that lied about its size gets refused on arrival, and the copy on
        # disk goes with it (review round 1, minor 6): the inbox must not
        # keep what she just told him she could not take.
        logger.warning(
            "telegram intake: %s not transcribed — %d bytes arrived over the "
            "%.0f MB cap", name, len(data), cap_mb)
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("telegram intake: could not discard the over-cap "
                           "%s from the inbox: %s", name, exc)
        return None, TRANSCRIPTION_TOO_LARGE_CONTENT

    entry["local_path"] = str(path)
    transcript = await transcribe_note(
        data, mime=str(entry.get("mime") or "audio/ogg"), filename=name,
        service=service,
    )
    return transcript, TRANSCRIPTION_UNAVAILABLE_CONTENT


async def _download(bot: Any, entry: dict, directory: Path) -> Path | None:
    """One file onto disk, or None. Never raises: one attachment that will
    not come must not cost him the message it arrived with."""
    from core.turn.inbox import safe_name

    file_id = str(entry.get("file_id") or "")
    if not file_id:
        return None
    try:
        handle = await bot.get_file(file_id)
        data = bytes(await handle.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram intake: download failed for %s: %s",
                       entry.get("filename") or file_id, exc)
        return None
    if not data:
        logger.warning("telegram intake: %s arrived empty",
                       entry.get("filename") or file_id)
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        unique = safe_name(entry.get("file_unique_id") or file_id)
        path = (directory / f"{unique}-{safe_name(entry.get('filename'))}").resolve()
        path.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram intake: could not write %s to the inbox: %s",
                       entry.get("filename") or file_id, exc)
        return None
    logger.info("telegram intake: saved %s → %s (%d bytes)",
                entry.get("filename") or file_id, path, len(data))
    return path
