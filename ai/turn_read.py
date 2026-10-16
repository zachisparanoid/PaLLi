"""The unified per-turn read — the heart of caring memory (2026-06-11).

Once per user turn, PaLLi takes ONE fresh LLM read of how the owner is doing
and what matters right now, in its own words. That single read flows four ways
(the owner's "all together, like how the human mind works" design,
docs/superpowers/specs/2026-06-11-caring-memory-design.md):

  1. the reply — rendered as private awareness in the system prompt;
  2. memory weight — heavy moments store facts with higher salience;
  3. the emotional timeline — a durable arc ("you've been carrying this all
     week"), persisted here in the ``emotional_timeline`` table;
  4. circle-backs — ``carries_forward`` becomes an open thread the proactive
     rail can return to.

Owner hard rules honored here: NO keyword matching, NO canned phrases, NO
fixed mood vocabularies — the LLM writes its understanding fresh each turn.
Structured numbers (weight, needs_support) are its judgment, not lookups.

The read is strictly best-effort: any timeout/parse failure returns None and
the turn proceeds exactly as it would have without this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Hard wall-clock cap for the read. The turn already makes multiple LLM calls;
# the read must never become the reason a reply feels stuck.
READ_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class TurnRead:
    read: str
    weight: float
    needs_support: float
    carries_forward: str
    memory_worthy: str
    created_at: float


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse(raw: str) -> Optional[TurnRead]:
    """Parse the LLM's JSON. Tolerates fencing/prose around the object."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    read_text = str(data.get("read") or "").strip()
    if not read_text:
        return None
    return TurnRead(
        read=read_text,
        weight=_clamp01(data.get("weight")),
        needs_support=_clamp01(data.get("needs_support")),
        carries_forward=str(data.get("carries_forward") or "").strip(),
        memory_worthy=str(data.get("memory_worthy") or "").strip(),
        created_at=time.time(),
    )


# One first look (R14, T5; plan §2.3): while the runner takes her first look
# on a turn, the look is that turn's first read and the runner hands it to the
# reflection and the reply itself, so recall takes none. A switch here, not an
# import in recall (core/turn/recall.py is at its coupling cap): set by the
# runner around recall only; False everywhere else, so every other read is
# today's.
first_look_in_charge: ContextVar[bool] = ContextVar("palli_first_look_in_charge", default=False)


def read_applies(turn: Any) -> bool:
    """The read is of how the OWNER is doing, so a guest's turn takes none
    (step 2, part 4, 2026-10-08). On a guest's turn the prompt dropped it
    anyway (context_adapter's P()) and reflection never runs, so the call was
    paid for and thrown away. Nor does a turn whose first look is in charge
    (R14): one look, not a look and a read."""
    from core.owner_gate import is_guest_turn

    return not is_guest_turn(turn) and not first_look_in_charge.get()


def room_conversation() -> Optional[str]:
    """In a shared room with group chat on, the room the runner read for this
    turn, guests' lines included (core.agent.room_tail); None elsewhere, where
    the read keeps today's recent history."""
    from core.agent.room_tail import current_room_tail

    return current_room_tail.get()


# The room-reading fix (2026-10-12, D; the owner's approval): in a group a
# message may be meant for someone else, so her first read there assumes
# nothing. Group rooms only: a DM's read is today's, byte for byte.
_DM_OPENING = "Before I answer, I take a quiet read of them."
_ROOM_OPENING = "Before I decide anything, I take a quiet read of them."
_DM_LABEL = "WHAT THEY JUST SAID TO ME:"
_ROOM_LABEL = "WHAT WAS JUST SAID (in a group, it may be meant for someone else):"


def room_template(template: str) -> str:
    """The read's template as a group room reads it; the template unchanged
    (and a warning) when its two lines have drifted."""
    if template.count(_DM_OPENING) != 1 or template.count(_DM_LABEL) != 1:
        logger.warning("turn_read: the template's opening or label drifted; the "
                       "group-room wording is not applied")
        return template
    return template.replace(_DM_OPENING, _ROOM_OPENING).replace(_DM_LABEL, _ROOM_LABEL)


def room_names() -> str:
    """The names line for the message being read (core.room_people, her own
    words), in a shared room with group chat on; "" everywhere else."""
    from core.agent.room_tail import current_room_names

    return current_room_names.get()


async def take_turn_read(
    *,
    message: str,
    recent_conversation: str,
    relationship_posture: str,
    emotional_arc: str,
    held_stakes: str = "",
    in_room: bool = False,
) -> Optional[TurnRead]:
    """Take the per-turn read. Returns None on any failure (turn proceeds).

    held_stakes (2026-07-06, companion.stake_ledger): compact first-person
    render of PaLLi's own open stakes — INPUT only; the output contract
    (read/weight/needs_support/carries_forward/memory_worthy) is unchanged.
    The WHOLE section (header + instruction) is built here and substituted
    into a bare {held_stakes} slot, so with the faculty off (empty value) the
    rendered prompt is byte-identical to the pre-feature prompt — flag-off
    purity is a design pillar. The template variable and this kwarg must stay
    in lockstep: str.format raises KeyError on a missing kwarg, which the
    broad except below would swallow into a silently-dead read.
    """
    try:
        from ai.llm_client import get_task_model, llm
        from ai.prompt_loader import prompt_loader

        stakes_section = ""
        _stakes = str(held_stakes or "").strip()
        if _stakes:
            stakes_section = (
                "\nWHAT'S ON MY OWN MIND (my open hopes, worries, "
                "tastes and wonders):\n"
                f"{_stakes[:1500]}\n"
                "If what they just said bears on one of these — a hope "
                "resolving, a worry easing or confirming, a horizon arriving "
                "— I say so in my read too; that feeling is mine, and it "
                "belongs here.\n"
            )

        template = prompt_loader.load_prompt("turn_read")
        names = ""
        if in_room:
            template = room_template(template)
            names = room_names()
        rendered = template.format(
            message=str(message or "")[:2000] + (f"\n{names}" if names else ""),
            recent_conversation=str(recent_conversation or "(none)")[:4000],
            relationship_posture=str(relationship_posture or "(still forming)")[:800],
            emotional_arc=str(emotional_arc or "(no recent arc)")[:1500],
            held_stakes=stakes_section,
        )
        model = get_task_model("analysis")
        raw = await asyncio.wait_for(
            llm.chat_completion(
                messages=[{"role": "user", "content": rendered}],
                model=model,
                # 1500, not 400 (2026-07-24): the analysis lane now resolves to
                # deepseek-v4-flash (a thinking model) after the Anthropic
                # credit-exhaustion reroute; 400 was Haiku-sized and a thinking
                # model can spend it all on chain-of-thought and return empty
                # (the consolidation comment's measured ~1-in-8 failure).
                # Output stays a small JSON read; the headroom is reasoning.
                max_tokens=1500,
                log_context="turn_read",
            ),
            timeout=READ_TIMEOUT_SECONDS,
        )
        result = _parse(raw or "")
        if result is None:
            logger.debug("turn_read: unparseable LLM output; skipping read")
        return result
    except asyncio.TimeoutError:
        logger.debug("turn_read: timed out after %.0fs; skipping", READ_TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        logger.debug("turn_read: failed (%s); skipping", exc)
        return None


# ---------------------------------------------------------------------------
# Emotional timeline persistence (outflow 3)
# ---------------------------------------------------------------------------


async def record_timeline_entry(*, owner_id: int, read: TurnRead, min_weight: float) -> None:
    """Persist a read to the emotional_timeline (skips low-signal turns)."""
    if read.weight < float(min_weight):
        return
    try:
        from core.database import brain
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "INSERT INTO emotional_timeline "
                "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                int(owner_id),
                float(read.created_at),
                read.read,
                float(read.weight),
                float(read.needs_support),
                read.carries_forward,
            )
        finally:
            try:
                await conn.close()
            except Exception:
                pass
    except Exception as exc:
        logger.debug("turn_read: timeline write failed: %s", exc)


async def render_recent_arc(
    *, owner_id: int, max_entries: int = 5, max_age_days: float = 10.0
) -> str:
    """Render the recent emotional arc as compact prose for the prompt.

    Returns "" when there's no meaningful history — callers render nothing.
    """
    try:
        from core.database import brain
        cutoff = time.time() - max_age_days * 86400.0
        conn = await brain.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT created_at, read_text, weight FROM emotional_timeline "
                "WHERE owner_user_id = $1 AND created_at > $2 "
                "ORDER BY created_at DESC LIMIT $3",
                int(owner_id),
                float(cutoff),
                int(max_entries),
            )
        finally:
            try:
                await conn.close()
            except Exception:
                pass
        if not rows:
            return ""
        lines = []
        now = time.time()
        for row in reversed(rows):  # oldest first, newest last
            d = dict(row)
            age_days = max(0.0, (now - float(d.get("created_at") or now)) / 86400.0)
            when = (
                "earlier today" if age_days < 1
                else ("yesterday" if age_days < 2 else f"{int(age_days)} days ago")
            )
            lines.append(f"- {when}: {str(d.get('read_text') or '').strip()}")
        return "\n".join(line for line in lines if line.strip("- :"))
    except Exception as exc:
        logger.debug("turn_read: arc render failed: %s", exc)
        return ""


async def search_timeline(
    *,
    owner_id: int,
    query_tokens: list[str],
    days_back: int = 30,
    limit: int = 5,
    brain_client: Any = None,
) -> list[dict]:
    """Search past reads of the owner by topic — the introspect tool's
    timeline read path (2026-07-13).

    ``render_recent_arc`` shows only the last ~10 days ambiently; this lets
    an explicit question ("when did you last think I was struggling?")
    reach the whole window. OR-token substring match over ``read_text``,
    newest first, capped. Best-effort: [] on any failure (the sibling
    readers' quiet-degrade contract). Scans at most the newest 400 rows
    inside the window."""
    tokens = [t.strip().lower() for t in (query_tokens or []) if t and t.strip()]
    if not tokens:
        return []
    try:
        if brain_client is None:
            from core.database import brain as _default_brain

            db: Any = _default_brain
        else:
            db = brain_client
        cutoff = time.time() - max(1, int(days_back)) * 86400.0
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT created_at, read_text, weight FROM emotional_timeline "
                "WHERE owner_user_id = $1 AND created_at > $2 "
                "ORDER BY created_at DESC LIMIT 400",
                int(owner_id),
                float(cutoff),
            )
        finally:
            try:
                await conn.close()
            except Exception:
                pass
        out: list[dict] = []
        for row in rows:
            d = dict(row)
            text = str(d.get("read_text") or "")
            low = text.lower()
            if any(t in low for t in tokens):
                out.append(
                    {
                        "created_at": float(d.get("created_at") or 0.0),
                        "read_text": text,
                        "weight": float(d.get("weight") or 0.0),
                    }
                )
                if len(out) >= max(1, int(limit)):
                    break
        return out
    except Exception as exc:
        logger.debug("turn_read: search_timeline failed: %s", exc)
        return []


async def recent_reads(
    *,
    owner_id: int,
    days_back: int = 30,
    limit: int = 50,
    brain_client: Any = None,
) -> list[dict]:
    """Recent reads of the owner, unfiltered — the interiority page's arc
    feed (Wave 0b, 2026-07-14).

    Same SELECT shape as ``search_timeline`` (``created_at DESC``, window
    cutoff) minus the token filter, plus ``needs_support`` in the
    projection. Additive: nothing on the turn path calls this. Best-effort:
    [] on any failure (the sibling readers' quiet-degrade contract).
    """
    try:
        if brain_client is None:
            from core.database import brain as _default_brain

            db: Any = _default_brain
        else:
            db = brain_client
        cutoff = time.time() - max(1, int(days_back)) * 86400.0
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT created_at, read_text, weight, needs_support "
                "FROM emotional_timeline "
                "WHERE owner_user_id = $1 AND created_at > $2 "
                "ORDER BY created_at DESC LIMIT $3",
                int(owner_id),
                float(cutoff),
                max(1, int(limit)),
            )
        finally:
            try:
                await conn.close()
            except Exception:
                pass
        return [
            {
                "created_at": float(d.get("created_at") or 0.0),
                "read_text": str(d.get("read_text") or ""),
                "weight": float(d.get("weight") or 0.0),
                "needs_support": float(d.get("needs_support") or 0.0),
            }
            for d in (dict(row) for row in rows)
        ]
    except Exception as exc:
        logger.debug("turn_read: recent_reads failed: %s", exc)
        return []
