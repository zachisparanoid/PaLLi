"""Her words on the mobile door, as timeline events (spec §4, §6).

The mirror of bots/telegram/reply.py's cadenced send: each beat is its own
``message`` event, after the pause her cadence gave it. A barge-in cancels
between beats; what was already sent stays sent. Files go out by registered
id, never by path. A voice reply is a ``voice_note`` event: OGG/Opus from
``plan_voice_reply``, the same decision Telegram makes.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from bots.common.voice_note import plan_voice_reply
from bots.mobile import MOBILE_CHANNEL_ID, MOBILE_PLATFORM
from core.turn.inbox import inbox_root
from core.turn.types import Beat

logger = logging.getLogger(__name__)


def inbox_dir() -> Path:
    return Path(inbox_root()) / "mobile"


def outbox_dir() -> Path:
    return Path(inbox_root()).parent / "outbox" / "mobile"


async def _emit(owner_id: int, kind: str, payload: Dict[str, Any], *, store: Any, hub: Any) -> Dict[str, Any]:
    """Append an event to the timeline, then push it to the open phones.

    The append-and-publish pair runs as one shielded unit: a barge-in that
    cancels the caller while the append is in flight still lets the row, once
    committed, reach the open phones — the caller sees the CancelledError, the
    timeline and the hub never disagree about a row that exists. A publish that
    fails after a good append is logged and the event returned: the row is in
    the timeline, and the phone's replay from its cursor carries it. An append
    failure still raises.
    """
    async def append_and_publish() -> Dict[str, Any]:
        try:
            event = await store.append(owner_id=int(owner_id), kind=kind, payload=payload)
        except Exception as exc:
            logger.warning("mobile reply: a %s event was not appended (%s)", kind, exc)
            raise
        try:
            await hub.publish(event, owner_id=int(owner_id))
        except Exception as exc:  # noqa: BLE001 — the row is the delivery; replay covers the push
            logger.warning("mobile reply: event %s is in the timeline but its push failed (%s)",
                           event.get("id"), exc)
        return event

    return await asyncio.shield(append_and_publish())


async def emit_text(owner_id: int, text: str, *, reminder: bool = False, store: Any, hub: Any) -> Dict[str, Any]:
    return await _emit(owner_id, "message", {"text": text, "reminder": bool(reminder)}, store=store, hub=hub)


async def emit_notice(owner_id: int, text: str, *, level: str, store: Any, hub: Any) -> Dict[str, Any]:
    return await _emit(owner_id, "notice", {"level": level, "text": text}, store=store, hub=hub)


def _file_name(path: str) -> str:
    return os.path.basename(str(path)) or "file"


def file_not_delivered(path: str) -> str:
    """The plain trouble line for a file she made that is gone from the
    machine, so the phone cannot have it."""
    return f"I made {_file_name(path)}, but it is not on my machine any more."


def file_send_failed(path: str) -> str:
    """The plain trouble line for a file still on the machine whose send
    failed after her text landed (bots/mobile/adapter.py)."""
    return f"I made {_file_name(path)}, but it didn't come through."


async def emit_file(owner_id: int, path: str, *, store: Any, hub: Any) -> Dict[str, Any]:
    name = os.path.basename(str(path)) or "file"
    if not os.path.isfile(str(path)):
        return await emit_notice(owner_id, file_not_delivered(path),
                                 level="trouble", store=store, hub=hub)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    ref = await store.register_file(owner_id=int(owner_id), path=str(path), filename=name,
                                    content_type=content_type)
    return await _emit(owner_id, "file", {"file": ref}, store=store, hub=hub)


async def emit_voice_note(owner_id: int, audio: bytes, *, text: str, store: Any, hub: Any,
                          directory: Path) -> Dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"voice-{uuid.uuid4().hex}.ogg"
    path.write_bytes(audio)
    ref = await store.register_file(owner_id=int(owner_id), path=str(path), filename="voice.ogg",
                                    content_type="audio/ogg")
    return await _emit(owner_id, "voice_note", {"text": text, "file": ref}, store=store, hub=hub)


def _beats(result: Any) -> List[Beat]:
    cadenced = getattr(result, "cadenced_reply", None)
    if cadenced is not None and len(cadenced.beats) > 1:
        return list(cadenced.beats)
    text = str(getattr(result, "response_text", "") or "")
    return [Beat(text=text)] if text.strip() else []


async def _emit_beats(owner_id: int, beats: List[Beat], *, store: Any, hub: Any, sleep: Any) -> int:
    count = 0
    for index, beat in enumerate(beats):
        delay = float(getattr(beat, "delay_after_previous_seconds", 0.0) or 0.0)
        if index and delay > 0:
            await sleep(delay)
        text = str(getattr(beat, "text", "") or "").strip()
        if text:
            await emit_text(owner_id, text, store=store, hub=hub)
            count += 1
    return count


async def deliver_result(owner_id: int, result: Any, *, store: Any, hub: Any,
                         directory: Optional[Path] = None, sleep: Any = asyncio.sleep,
                         plan_voice: Any = plan_voice_reply, typing: Any = None) -> int:
    """Everything a turn's result says on this door, in order — beats, the
    files she made, a voice note. Returns the number of events written;
    quiet (or nothing to say) writes none.

    ``typing`` is the door's "typing…" (an async-context-manager factory),
    handed to ``plan_voice`` so her typing shows while a voice reply is made,
    as on Telegram. Left out, ``plan_voice`` is called exactly as before."""
    text = str(getattr(result, "response_text", "") or "")
    attachments = tuple(getattr(result, "attachments", ()) or ())
    notes = tuple(getattr(result, "voice_notes", ()) or ())
    if not (getattr(result, "responded", False) and (text.strip() or attachments or notes)):
        return 0
    directory = directory or outbox_dir()
    try:
        voice_kwargs = {"typing": typing} if typing is not None else {}
        voice = await plan_voice(MOBILE_PLATFORM, MOBILE_CHANNEL_ID, result, owner_id=int(owner_id),
                                 **voice_kwargs)
    except Exception as exc:  # noqa: BLE001 — a failed voice plan never costs her words
        # (CancelledError is a BaseException: a barge-in still propagates.)
        logger.warning("mobile reply: the voice plan failed, sending her words as text (%s)", exc)
        voice = None
    count = 0
    if voice is None:
        count += await _emit_beats(owner_id, _beats(result), store=store, hub=hub, sleep=sleep)
    elif voice.text.strip():
        beats = _beats(result) if voice.text == text else [Beat(text=voice.text)]
        count += await _emit_beats(owner_id, beats, store=store, hub=hub, sleep=sleep)
    for path in attachments:
        await emit_file(owner_id, str(path), store=store, hub=hub)
        count += 1
    if voice is not None:
        # The words of a voice note ride with it when nothing else said them.
        spoken = text.strip() if not voice.text.strip() else ""
        for note in voice.notes:
            await emit_voice_note(owner_id, note, text=spoken, store=store, hub=hub, directory=directory)
            count += 1
    return count
