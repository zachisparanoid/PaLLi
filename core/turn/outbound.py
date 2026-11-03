"""One custody primitive for her organic sends (arbiter spec §5, 2026-08-06).

Every autonomous path that reaches the owner used to re-implement custody
(or skip it): the proactive lane transcribed to proactive_1.jsonl — a file
her Discord replies never read — and the ambient greeting wrote no
transcript at all and bypassed sense D. This is the 2026-07-30 pattern
(core/turn/proactive.py:678-736) generalized, with the session key
corrected to the delivering channel:

    1. send via channel_runtime.send_envelope  → sense D fires (she FEELS it)
    2. DB assistant row (the delivering door's platform, real channel_id,
       on SEND)
    3. assistant-only line in the {platform}:{channel_id} transcript —
       the payload her next reply actually reads

The door is a parameter, defaulted to Discord (telegram door, 2026-09-14,
task 1): every caller that names none behaves exactly as it did.

A send failure RAISES before anything is recorded (record on send, never
on intent). Bookkeeping failures after the send are logged and swallowed —
they must never un-send a message or block a reminder.

Also home (Batch 6, task 6) to her book of reach-outs,
``data/reachouts/reachouts.jsonl``: an append-only log folded on read, like
the fears book. A message she started herself (origin ``arbiter`` or
``workbench_share``) is written there by the custody bookkeeping; the owner's
next message on the same door, inside six hours, marks it answered
(core/turn/persistence.py); the freshness judge reads the last few
(ai/arbiter.py). Events:

    {"op": "sent", "id", "at", "owner_id", "platform", "channel_id",
     "origin", "medium": "text"|"voice", "head": <first 80 chars>}
    {"op": "answered", "id", "at"}

The book is the arbiter's and is shut while the arbiter is off — the CALLERS
check that (reachouts_enabled); the functions that read and write the file
are plain. The folder is resolved at call time, so tests redirect it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The surface each door's rows are filed under (the mobile door, 2026-10-13).
_SURFACE_BY_PLATFORM = {
    "discord": "discord_text",
    "telegram": "telegram_text",
    "mobile": "mobile_text",
}

REACHOUTS_DIR = Path("data/reachouts")
# Only a message she started herself is a reach-out. Her hands' sends (to the
# owner at their ask, or to a friend), forge follow-ups and mid-task speech
# answer something the owner asked for; reminders never pass this module.
REACHOUT_ORIGINS = frozenset({"arbiter", "workbench_share"})
# The owner's message answers a reach-out on the same door sent at most this
# long before it (six hours).
ANSWER_WINDOW_S = 21600.0
_HEAD_CAP = 80


async def record_outbound(
    *,
    owner_id: int,
    channel_id: int,
    text: str,
    origin: str = "arbiter",
    extra: dict | None = None,
    platform: str = "discord",
    surface_id: str | None = None,
) -> bool:
    """``platform`` / ``surface_id`` (telegram door, 2026-09-14, task 1): the
    door this send goes out of. Defaulted so every existing caller — the
    arbiter, her hands, the duplex acts — is byte-identical; a caller that
    names another door gets all four custody legs under THAT door, because a
    line filed in the wrong transcript is a line she never reads again.
    ``surface_id`` defaults to the channel id, which is the address on both
    doors today."""
    text = str(text or "").strip()
    if not text or not channel_id:
        return False

    platform = str(platform or "discord").strip().lower() or "discord"
    surface_id = str(surface_id) if surface_id is not None else str(int(channel_id))

    import core.channel_runtime as _cr

    # Leg 1 — the send. Raises to the caller: no record without a send.
    await _cr.channel_runtime.send_envelope(
        platform=platform,
        surface_id=surface_id,
        envelope=_cr.ChannelDeliveryEnvelope(text=text),
    )

    await _custody_bookkeeping(
        owner_id=int(owner_id),
        channel_id=int(channel_id),
        text=text,
        origin=origin,
        extra=dict(extra or {}),
        transcript_text=text,
        deed_detail=f'sent a message ({origin}): "{str(text)[:60]}…"',
        platform=platform,
    )
    return True


async def _custody_bookkeeping(
    *,
    owner_id: int,
    channel_id: int,
    text: str,
    origin: str,
    extra: dict,
    transcript_text: str,
    deed_detail: str,
    platform: str = "discord",
    medium: str = "text",
) -> None:
    """Legs 2–4, shared by the text and spoken primitives so the record
    can never drift between mediums. Every leg best-effort: bookkeeping
    failures are logged and swallowed — they must never un-send."""
    # Leg 2 — DB assistant row, the 07-30 shape (the surface swap makes the
    # row land on the delivering door's platform with a real channel_id —
    # see core/turn/recall.py:_platform_for_surface).
    # One surface per door: the ternary this replaced filed every door but
    # Telegram's rows as Discord's (found 2026-10-13, the mobile door).
    surface = _SURFACE_BY_PLATFORM.get(platform, "discord_text")
    try:
        import core.turn.persistence as _persistence
        from core.turn.types import Turn, TurnMetadata

        delivered = Turn(
            surface=surface,
            owner_id=int(owner_id),
            content=text,
            metadata=TurnMetadata(
                origin=origin,
                channel_id=int(channel_id),
                extra={**extra, "self_initiated": True},
            ),
        )
        await _persistence._persist_assistant_side_to_history(
            turn=delivered, response_text=text
        )
    except Exception as exc:
        logger.warning("record_outbound: DB persist failed: %s", exc)

    # Her book of reach-outs (Batch 6, task 6): a message she started is
    # written down, so the owner's next message on this door can mark it
    # answered and the freshness judge can see which ones were. Any other
    # origin writes nothing; shut while the arbiter is off; off the loop.
    if origin in REACHOUT_ORIGINS:
        try:
            if reachouts_enabled():
                await asyncio.to_thread(
                    record_reachout,
                    owner_id=int(owner_id), platform=platform,
                    channel_id=int(channel_id), text=text, origin=origin,
                    medium=medium,
                )
        except Exception as exc:
            logger.warning("record_outbound: reach-out book append failed: %s", exc)

    # Leg 3 — her own transcript, assistant-only (a synthetic user turn
    # would rebuild the inversion one layer down — the 07-30 rule).
    if extra.get("mid_task"):
        # Step 27 (2026-08-28): a chime_in / ask_him rides this leg from
        # INSIDE a run. The run holds the session and its tool_use + result
        # already are the record; a second writer here landed her words
        # between an open tool_use and its result (12:59 live, validity §6).
        # DB row and deed above stand; no transcript line.
        return
    try:
        from core.agent.runner import _open_session_with_exchange

        root = Path(
            os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions"
        )
        await _open_session_with_exchange(
            f"{platform}:{int(channel_id)}", root, "", transcript_text
        )
    except Exception as exc:
        logger.warning("record_outbound: transcript append failed: %s", exc)

    # Deeds sense (impulse-freshness spec §3): an organic send is a deed —
    # her present must see it, or a held impulse can re-offer what she
    # already sent. Best-effort, like the bookkeeping legs above.
    try:
        from ai.recent_deeds import record_deed

        record_deed("sent", deed_detail)
    except Exception:
        pass


class SpokenSendFailed(RuntimeError):
    """say_text declined or failed — record on send means NOTHING was
    recorded; the caller decides the fallback (the arbiter falls back to
    the text primitive, spec §0 decision 5)."""


async def record_outbound_spoken(
    *,
    owner_id: int,
    channel_id: int,
    runtime_session_key: str,
    text: str,
    origin: str = "arbiter",
    extra: dict | None = None,
) -> bool:
    """The spoken sibling (step 32, spec §3): leg 1 SPEAKS the line through
    the live step-31 session instead of sending a Discord message; the
    written record then rides the SAME bookkeeping legs — DB row on the
    picked text channel (extra carries spoken_aloud), an assistant-only
    "[said aloud] …" transcript line so the freshness tail and her later
    replies know it happened, the deed, and the sense-D mirror
    (send_envelope fires it for text sends; the spoken leg bypasses
    send_envelope, so it is mirrored here — structural only)."""
    text = str(text or "").strip()
    key = str(runtime_session_key or "").strip()
    if not text or not key or not channel_id:
        raise SpokenSendFailed("no text, no channel, or no live voice session")

    # Leg 1 — the SPEAK. Failure raises before anything is recorded.
    from voice import control as _voice_control

    ok = await _voice_control.voice_session_control_service.say_text(
        runtime_session_key=key, text=text
    )
    if not ok:
        raise SpokenSendFailed("the voice session declined the line")

    # Sense D mirror (channel_runtime.send_envelope's shape).
    try:
        from ai.present_moment import notice as _pm_notice

        _pm_notice("initiative_sent", platform="voice", chars=len(text))
    except Exception:
        pass

    await _custody_bookkeeping(
        owner_id=int(owner_id),
        channel_id=int(channel_id),
        text=text,
        origin=origin,
        extra={**(extra or {}), "spoken_aloud": True},
        transcript_text=f"[said aloud] {text}",
        deed_detail=f'said aloud ({origin}): "{str(text)[:60]}…"',
        medium="voice",
    )
    return True


# ── her book of reach-outs (Batch 6, task 6) ─────────────────────────────

def reachouts_enabled() -> bool:
    """The book is the arbiter's: open only while the arbiter is on. A flag
    that cannot be read counts as off."""
    try:
        from ai.arbiter import arbiter_enabled

        return bool(arbiter_enabled())
    except Exception:
        return False


def _reachouts_path() -> Path:
    """Call-time resolution, so a redirect of REACHOUTS_DIR really takes
    effect."""
    return REACHOUTS_DIR / "reachouts.jsonl"


def _append_reachout(event: Dict[str, Any]) -> None:
    path = _reachouts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def record_reachout(
    *,
    owner_id: int,
    platform: str,
    channel_id: int,
    text: str,
    origin: str,
    medium: str = "text",
    at: Optional[float] = None,
) -> bool:
    """Write one ``sent`` event for a message she started. Any other origin
    writes nothing and returns False. Raises on a failed write; the custody
    leg that calls it logs and swallows."""
    if origin not in REACHOUT_ORIGINS:
        return False
    _append_reachout({
        "op": "sent",
        "id": uuid.uuid4().hex[:16],
        "at": float(time.time() if at is None else at),
        "owner_id": int(owner_id),
        "platform": str(platform or "discord").strip().lower() or "discord",
        "channel_id": int(channel_id),
        "origin": str(origin),
        "medium": "voice" if medium == "voice" else "text",
        "head": " ".join(str(text or "").split())[:_HEAD_CAP],
    })
    return True


def read_reachouts() -> List[Dict[str, Any]]:
    """Fold the log: every reach-out in the order sent, each with its
    ``answered_at`` (None until answered; the first answer is the record).
    A missing book is empty; one bad line is a skipped line, never a lost
    book — a byte that is not UTF-8 (a torn write) spoils only its own line,
    which then fails the JSON read like any other bad line (fix round 1,
    M1). A book that cannot be read at all raises."""
    try:
        text = _reachouts_path().read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    rows: Dict[str, Dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        rid = str(ev.get("id") or "")
        try:
            if ev.get("op") == "sent" and rid and rid not in rows:
                rows[rid] = {
                    "id": rid,
                    "at": float(ev["at"]),
                    "owner_id": int(ev["owner_id"]),
                    "platform": str(ev.get("platform") or ""),
                    "channel_id": int(ev["channel_id"]),
                    "origin": str(ev.get("origin") or ""),
                    "medium": "voice" if ev.get("medium") == "voice" else "text",
                    "head": str(ev.get("head") or ""),
                    "answered_at": None,
                }
            elif (ev.get("op") == "answered" and rid in rows
                  and rows[rid]["answered_at"] is None):
                rows[rid]["answered_at"] = float(ev["at"])
        except (KeyError, TypeError, ValueError):
            continue
    return list(rows.values())


def mark_answered(*, owner_id: int, platform: str, channel_id: int, at: float) -> int:
    """The owner's message that ARRIVED at ``at`` answers every reach-out
    still unanswered on the SAME door (platform and channel both) sent
    within the six hours before it. A message on any other door marks
    nothing, and neither does a reach-out sent after the message arrived.
    Idempotent — an answered reach-out is never marked again, so the same
    message stored twice is one answer. Returns how many were marked. Never
    raises."""
    try:
        platform = str(platform or "").strip().lower()
        channel_id, owner_id, at = int(channel_id), int(owner_id), float(at)
        marked = 0
        for row in read_reachouts():
            if row["answered_at"] is not None:
                continue
            if (row["owner_id"], row["platform"], row["channel_id"]) != (
                    owner_id, platform, channel_id):
                continue
            if not at - ANSWER_WINDOW_S <= row["at"] <= at:
                continue
            _append_reachout({"op": "answered", "id": row["id"], "at": at})
            marked += 1
        return marked
    except Exception as exc:
        logger.warning("reach-outs: marking answered failed: %s", exc)
        return 0


def answer_state(row: Dict[str, Any], *, now: float) -> str:
    """``answered``, or ``no answer yet`` while its six hours are still
    running (calling that one unanswered would be false), or
    ``unanswered``."""
    if row.get("answered_at") is not None:
        return "answered"
    if float(now) - float(row.get("at") or 0.0) < ANSWER_WINDOW_S:
        return "no answer yet"
    return "unanswered"


def recent_reachouts(*, limit: int = 5, medium: Optional[str] = None) -> List[Dict[str, Any]]:
    """The last ``limit`` reach-outs, newest first; ``medium`` keeps only
    that medium. Raises when the book cannot be read (the judge's caller
    shows none instead)."""
    rows = [r for r in read_reachouts() if medium is None or r["medium"] == medium]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:max(0, int(limit))]
