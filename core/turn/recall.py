"""Direction C: build TurnContext from a Turn.

Recall gathers deterministic context only (identity, history, inner memory,
facts, recent delegations, monologue digest, image vision, text attachments,
first-contact targets). It does NOT classify the user. The decide stage's
LLM call produces the observation_text directly from the turn + the recall
context, in a single LLM call. This collapses what used to be 2-3 separate
classifier LLM calls (interaction_mode, pragmatic_inquiry, emotional_state)
into one decide-time call.

Every lookup is best-effort: missing/erroring services degrade to empty
defaults rather than failing the turn.

E.2 step 2 (2026-05-13): the 10 single-caller `_safe_*` wrappers were
inlined into ``build_turn_context``.

2026-07-10 — latency pass: the independent best-effort fetches now run
CONCURRENTLY in two waves instead of strictly sequentially. Wave 1 gathers
everything that needs only the Turn (history, inner-memory + semantic search,
facts, delegations, text attachments, sentiment, stakes). Wave 2 gathers the
history-dependent awaits (image vision, situation, the caring-memory read).
This is a pure latency change: the order in which the awaits COMPLETE has no
effect on the produced TurnContext — every field is written by exactly one
fetch, EXCEPT recent_memory_hits, whose FTS-first / semantic-deduped-second
merge is done sequentially after wave 1, and relevant_facts, which Batch 5's
band rebuilds after the gather (below). Only wall-clock latency dropped.
Each wrapper below owns its gate + its try/except, so a failure degrades to
an empty default and never propagates out of ``gather`` (the reply is never
dropped by a recall error).

Batch 5, task 4 (memory that picks, 2026-09-28): the "Things I Know About
This User" band is planned after the gather by core.turn.memory_lookup
(``plan_band``; its own module since ruling R64) from what wave 1 already
fetched: the memory bullets the memory-of-you search returned, minus the
ones the memory core shows (the same core the adapter renders), then the
typed user_memory facts. Bullets never reach "Background memory hits". Every
returned bullet's id rides ``TurnContext.memory_lookup_ids`` for Task 5's
reference stamps; a failure in the plan leaves the facts feed as fetched.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from core.turn.types import HistoryEntry, Turn, TurnContext
from core.turn.companion_flags import companion_settings
from core.turn import attachments as _attachments, memory_lookup, organ_bands

# Batch 3's similarity floor lives with the band's helpers since ruling R64;
# the hits merge below and tests/turn/test_recall_semantic_cutoff.py read it here.
_passes_semantic_cutoff = memory_lookup._passes_semantic_cutoff

logger = logging.getLogger(__name__)


async def fetch_recent_history(turn: Turn) -> list[HistoryEntry]:
    """The recent conversation for a turn, oldest first (recall's history step)."""
    # Maps Discord/voice/dashboard surfaces onto the legacy `platform`
    # column that ConversationHistoryStore filters on. Returns chronological
    # order (oldest first) so prompts read top-down naturally.
    recent_history: list[HistoryEntry] = []
    try:
        from core.config import config
        from core.conversation_history_store import ConversationHistoryStore
        from core.database import brain
        platform = _platform_for_surface(turn.surface)
        store = ConversationHistoryStore(brain)
        history_limit = config.TRUNCATION_HISTORY_LIMIT or 10_000
        rows = await store.get_conversation_history(
            user_id=int(turn.owner_id),
            platform=platform,
            channel_id=turn.metadata.channel_id,
            limit=history_limit,
        ) or []
        for row in rows:
            try:
                role = str(row.get("role") or "").lower()
                if role not in ("user", "assistant"):
                    continue
                at = row.get("timestamp") or row.get("at")
                if isinstance(at, (int, float)):
                    at_dt = datetime.fromtimestamp(float(at))
                elif isinstance(at, str):
                    try:
                        at_dt = datetime.fromisoformat(at)
                    except ValueError:
                        at_dt = datetime.utcnow()
                elif isinstance(at, datetime):
                    at_dt = at
                else:
                    at_dt = datetime.utcnow()
                recent_history.append(HistoryEntry(
                    role=role,
                    content=str(row.get("content") or ""),
                    at=at_dt,
                ))
            except Exception:
                continue
    except Exception as exc:
        logger.warning("recall: history fetch failed: %s", exc)
    return recent_history


async def recent_history_for(turn: Turn) -> list[HistoryEntry]:
    """Recall's history: the runner's early fetch when there is one (fix 6,
    the owner 10-12: the length vote runs beside recall), else its own. A
    failed early fetch falls back to fetching here."""
    from core.turn.history_prefetch import prefetched_history
    early = prefetched_history.get()
    if early is not None:
        try:
            return list(await early)
        except Exception as exc:  # noqa: BLE001 - fall back to recall's own fetch
            logger.warning("recall: early history fetch failed (%s); fetching here", exc)
    return await fetch_recent_history(turn)


async def build_turn_context(turn: Turn) -> TurnContext:
    """Gather everything the rest of the loop needs to think well.

    Direction C: no per-turn classifier calls here — those collapsed
    into the decide-stage LLM. Recall is purely a context gatherer.
    Each fetch is best-effort: any failure logs + degrades to an empty
    default rather than breaking the turn. Fetches run concurrently in
    two waves (see the module docstring).
    """
    # ── sync, dependency-free reads (fast; kept sequential) ────────
    compiled_identity = None
    try:
        from core.identity.service import identity_service
        compiled_identity = identity_service.compile_for_owner(owner_id=turn.owner_id)
    except Exception as exc:
        logger.warning("recall: identity compile failed: %s", exc)

    # ── monologue digest ─────────────────────────────────────────
    monologue_digest = ""
    try:
        try:
            from ai.monologue_compaction import load_latest_weekly_digest  # type: ignore[import]
        except ImportError:
            load_latest_weekly_digest = None  # type: ignore[assignment]
        if load_latest_weekly_digest is not None:
            monologue_digest = load_latest_weekly_digest(owner_id=turn.owner_id) or ""
    except Exception as exc:
        logger.warning("recall: monologue digest failed: %s", exc)

    # ── night-cycle daily-meaning (Selfhood Phase 1, 2026-07-11) ──
    # A cheap sync file read of the latest reflection, gated on companion
    # .night_cycle. Best-effort: any failure (or flag off) leaves it empty and
    # the "## Last night I reflected" prompt section simply vanishes.
    night_cycle_reflection = ""
    try:
        from ai.night_cycle import night_cycle_enabled
        from ai.night_cycle_store import read_latest_daily_meaning
        if night_cycle_enabled():
            _nc_entry = read_latest_daily_meaning()
            night_cycle_reflection = _nc_entry.text if _nc_entry else ""
    except Exception as exc:
        logger.debug("recall: night-cycle reflection unavailable: %s", exc)

    # ── present moment band (Slice 2, 2026-07-27) ─────────────────
    # Cheap sync read (one dated-file tail, the night-cycle pattern above).
    present_moment_text = _read_present_moment_band()

    # ── the authorship trace (2026-08-16) ────────────────────────
    # Her own recent workbench work, as facts. Same seam as the moment band.
    own_work_trace = _read_own_work_trace()

    # ── her recent deeds (the grounding rule, 2026-08-17) ────────
    recent_deeds_text = _read_recent_deeds_band()

    # ── her small truths (organ 4.9 door 1, 2026-09-01) ──────────
    disclosures_text = _read_disclosures_band()

    # ── our private language (organ 4.12, 2026-09-02) ────────────
    callbacks_text = _read_callbacks_band()

    # ── one thing on her mind (Batch 6, task 1, 2026-09-29) ──────
    tonight_text = _read_tonight_band(turn.surface)

    # ── narrative self documents (Wave 1b, 2026-07-16) ────────────
    narrative_care_spine, narrative_my_story, narrative_story_of_us = _read_narrative_documents()

    # ── unfulfilled first-contact targets ────────────────────────
    # Batch 5, task 9 (rulings R15/R16): only while the onboarding nag is
    # active — no first contact recorded yet, or fewer than
    # memory.onboarding_nag_days (7) since the owner's first identity-
    # document version. After that the list is () and the reflection
    # prompt's question line goes quiet (the reply prompt never carried
    # it). A failed lookup drops the list for the turn, as before.
    unfulfilled: list[str] = []
    try:
        from core.first_contact_targets import (
            first_contact_at,
            onboarding_nag_active,
            onboarding_nag_days,
            unfulfilled_targets_for_owner,
        )
        if onboarding_nag_active(await first_contact_at(turn.owner_id), time.time(), onboarding_nag_days()):
            unfulfilled = list(unfulfilled_targets_for_owner(owner_id=turn.owner_id) or [])
    except Exception as exc:
        logger.warning("recall: first contact targets failed: %s", exc)

    # ── relationship calibration read (F7) — SYNC (load_state + render). ──
    # The WRITE/compounding happens post-turn in reflect; recall only reads.
    # take_turn_read (wave 2) consumes this, so compute it up front.
    relationship_posture = ""
    _comp_rel = companion_settings()
    if _comp_rel is None or _comp_rel.relationship_calibration:
        try:
            from ai.relationship_momentum import load_state, render_posture
            relationship_posture = render_posture(load_state(int(turn.owner_id)))
        except Exception as exc:
            logger.debug("recall: relationship posture failed: %s", exc)

    # Semantic-recall gating, shared by the wave-1 search wrapper and the
    # sequential merge below.
    _companion = companion_settings()
    _semantic_on = (_companion is None or _companion.semantic_recall)
    max_hits = _companion.semantic_recall_max_hits if _companion else 3
    min_sim = _companion.semantic_recall_min_similarity if _companion else 0.3

    # ── WAVE 1: fetches that need only the Turn — run concurrently ──
    async def _w_history() -> list[HistoryEntry]:
        return await recent_history_for(turn)

    async def _w_inner_hits() -> list[str]:
        # inner-memory FTS5 hits (keyword match).
        hits_out: list[str] = []
        if (turn.content or "").strip():
            try:
                from ai.inner_memory_index import query as inner_memory_query  # type: ignore[attr-defined]
                hits = await inner_memory_query(text=turn.content, max_results=3) or []
                for hit in hits:
                    text = str(getattr(hit, "text_content", "") or "").strip()
                    if text:
                        hits_out.append(text)
            except Exception as exc:
                logger.warning("recall: inner memory search failed: %s", exc)
        return hits_out

    async def _w_semantic() -> list[dict]:
        # semantic memory recall (F1) — the hybrid embedding search. Merged
        # into recent_memory_hits AFTER the gather (order-sensitive). Returns
        # the raw results; the dedup/threshold merge is done sequentially below.
        if not ((turn.content or "").strip() and _semantic_on and max_hits > 0):
            return []
        try:
            from core.memory_service import memory_workspace_service
            search = await asyncio.wait_for(
                memory_workspace_service.search(
                    owner_user_id=int(turn.owner_id),
                    query=turn.content,
                    # R14: the cutoff must judge a real candidate set — the blend's bonuses can outrank similarity in the top three.
                    limit=max(max_hits * 4, 8),
                ),
                timeout=5.0,
            )
            return list(search.get("results") or [])
        except asyncio.TimeoutError:
            logger.debug("recall: semantic memory search timed out")
            return []
        except Exception as exc:
            logger.warning("recall: semantic memory search failed: %s", exc)
            return []

    async def _w_facts() -> list[dict]:
        # decay-weighted user facts, biased toward the current message.
        # Batch 5, task 4 (ruling R5): WITHOUT her own ``moment`` rows —
        # reflect's emotional reads of them, 154 of 186 live — so the band's
        # eight slots can hold typed facts; the row id and type ride along so
        # Task 5 can bump what the band rendered.
        relevant_facts: list[dict] = []
        try:
            from ai.fact_retrieval import get_decay_weighted_facts
            weighted = await get_decay_weighted_facts(
                user_id=int(turn.owner_id), max_results=8, query=turn.content,
                exclude_memory_types=("moment",),
            ) or []
            for w in weighted:
                fact = getattr(w, "fact", None)
                if not isinstance(fact, dict):
                    continue
                key = str(
                    fact.get("memory_key")
                    or fact.get("key")
                    or fact.get("topic")
                    or "fact"
                ).strip()
                value = str(
                    fact.get("memory_value")
                    or fact.get("value")
                    or fact.get("content")
                    or ""
                ).strip()
                if not value:
                    continue
                confidence = float(fact.get("confidence") or 0.0)
                relevant_facts.append({
                    "key": key,
                    "value": value,
                    "confidence": confidence,
                    "id": fact.get("id"),
                    "memory_type": str(fact.get("memory_type") or ""),
                })
        except Exception as exc:
            logger.warning("recall: fact retrieval failed: %s", exc)
        return relevant_facts

    async def _w_memory_refs() -> None:
        # Batch 5, task 4 (ruling R7): the owner's reference stamps, loaded
        # ONCE per process — owner turns only, a failed load a debug line
        # (core.turn.memory_lookup since ruling R64).
        await memory_lookup.load_owner_refs(turn)

    async def _w_text_attachments() -> tuple[str, ...]:
        # text/code attachment contents (core/turn/attachments.py).
        return await _attachments.text_attachment_contents(turn, fetch=_fetch_image_bytes)

    async def _w_file_attachments() -> tuple[str, ...]:
        # DEFECT_RECORD §19: non-text files land in the inbox (core/turn/attachments.py).
        return await _attachments.file_attachment_lines(turn, fetch=_fetch_image_bytes)

    async def _w_sentiment() -> str:
        # care-appropriate tone (F3, de-mirrored) — a CARE note in PaLLi's OWN
        # voice only when the user's state warrants it, never mood-mirroring.
        register_directive = ""
        _comp_mood = companion_settings()
        if _comp_mood is None or _comp_mood.mood_tone:
            try:
                from ai.care_tone import care_directive
                from core.database import brain as _brain
                reading = await _brain.get_user_sentiment_state(int(turn.owner_id))
                if not getattr(reading, "is_stale", False):
                    register_directive = care_directive(reading.tag)
            except Exception as exc:
                logger.debug("recall: care tone failed: %s", exc)
        return register_directive

    async def _w_stakes() -> str:
        # stake ledger: PaLLi's own held stakes. Cheap SQLite read, bounded to
        # 250ms so a contended DB never stalls the reply. Default-OFF faculty.
        held_stakes_text = ""
        try:
            from ai.stake_ledger import ledger_enabled, open_stakes, render_stakes_block
            if ledger_enabled():
                held_stakes_text = render_stakes_block(
                    await asyncio.wait_for(open_stakes(limit=8), timeout=0.25)
                )
        except asyncio.TimeoutError:
            logger.warning("recall: stake ledger read timed out; skipping this turn")
        except Exception as exc:
            logger.warning("recall: stake ledger read failed: %s", exc)
        return held_stakes_text

    (
        recent_history,
        inner_hits,
        semantic_results,
        relevant_facts,
        text_attachment_contents,
        file_attachment_contents,
        register_directive,
        held_stakes_text,
        _refs_loaded,
    ) = await asyncio.gather(
        _w_history(),
        _w_inner_hits(),
        _w_semantic(),
        _w_facts(),
        _w_text_attachments(),
        _w_file_attachments(),
        _w_sentiment(),
        _w_stakes(),
        _w_memory_refs(),
    )
    # §19: text lines first, then the inbox lines — one slot downstream.
    text_attachment_contents = tuple(text_attachment_contents) + tuple(file_attachment_contents)

    # ── recent_memory_hits: FTS first, then semantic dedup-appended ──
    # ORDER-SENSITIVE — renders as one list under the "use ONLY if relevant"
    # framing. The two searches ran concurrently above; the merge is
    # sequential + deterministic so the rendered bytes never change.
    recent_memory_hits: list[str] = list(inner_hits)
    try:
        seen = {h.strip().lower() for h in recent_memory_hits}
        added = 0
        for r in semantic_results:
            if added >= max_hits:
                break
            # Batch 5, task 4 (ruling R39): a memory bullet is in the band
            # below or already in the core — never a background hit, and it
            # takes no hit slot from the other results.
            if str(r.get("source_kind") or "") == "memory_bullet":
                continue
            if not _passes_semantic_cutoff(r, min_sim=min_sim):
                continue
            text = str(r.get("snippet") or r.get("content") or "").strip()
            key = text.lower()
            if text and key not in seen:
                recent_memory_hits.append(text)
                seen.add(key)
                added += 1
    except Exception as exc:
        logger.warning("recall: semantic memory merge failed: %s", exc)

    # ── the "Things I Know About This User" band (Batch 5, task 4) ──
    # core.turn.memory_lookup.plan_band (ruling R64): the memory bullets the
    # search returned past the hits' relevance floor, minus the ones the
    # core already shows, then the typed user_memory facts; every surfaced
    # bullet id for Task 5's stamps. On any failure inside it, the facts
    # feed stands exactly as wave 1 fetched it, and no ids (T4 M12).
    relevant_facts, memory_lookup_ids = memory_lookup.plan_band(
        turn,
        semantic_results=semantic_results,
        min_sim=min_sim,
        compiled_identity=compiled_identity,
        facts=relevant_facts,
    )

    # ── temporal-awareness signal: monologue ticks since last user msg ──
    # A high count is strong evidence of a session break (default ~hourly
    # cadence; 4+ ticks ≈ 4+ hours). Needs recent_history, so it runs here.
    last_user_message_at = None
    for entry in reversed(recent_history):
        if getattr(entry, "role", None) == "user" and getattr(entry, "at", None):
            last_user_message_at = entry.at
            break
    monologue_ticks_since = 0
    if last_user_message_at is not None:
        try:
            from ai.inner_monologue_store import read_entries
            entries = read_entries(days_back=2, limit=200)
            threshold_ts = last_user_message_at.timestamp()
            monologue_ticks_since = sum(1 for e in entries if e.timestamp > threshold_ts)
        except Exception as exc:
            logger.debug("recall: monologue tick count failed: %s", exc)

    # ── WAVE 2: history-dependent awaits — run concurrently ────────
    async def _w_vision() -> tuple[str, ...]:
        # vision analysis on attached images (core/turn/attachments.py).
        return await _attachments.describe_images(turn, recent_history, fetch=_fetch_image_bytes)

    async def _w_situation() -> str:
        # situational awareness (F6) — a short slice of the sensorium that feeds
        # the background monologue. Best-effort + timeout-bounded; empty on a
        # headless host.
        situational_note = ""
        _comp_sit = companion_settings()
        if _comp_sit is None or _comp_sit.situational_awareness:
            try:
                from ai.situation_context import build_situation_context
                situation = await asyncio.wait_for(
                    build_situation_context(
                        owner_user_id=int(turn.owner_id),
                        recent_history=recent_history,
                    ),
                    timeout=3.0,
                )
                prose = str(getattr(situation, "prose", "") or "").strip()
                if prose:
                    situational_note = prose[:600]
            except asyncio.TimeoutError:
                logger.debug("recall: situation context timed out")
            except Exception as exc:
                logger.debug("recall: situation context failed: %s", exc)
        return situational_note

    async def _w_caring() -> tuple:
        # caring memory (2026-06-11): ONE fresh LLM read of how the owner is
        # doing, taken pre-reply. Consumes posture/arc/stakes, so it runs in
        # wave 2. arc + read stay coupled (arc failure must also skip the read,
        # matching the original single-try semantics).
        turn_read_result = None
        emotional_arc_text = ""
        _comp_cm = companion_settings()
        if _comp_cm is None or getattr(_comp_cm, "caring_memory", True):
            try:
                from ai import turn_read as _tr
                if not _tr.read_applies(turn):
                    return None, ""
                emotional_arc_text = await _tr.render_recent_arc(owner_id=int(turn.owner_id))
                _recent_lines: list[str] = []
                # Step 2, part 4 (2026-10-08): in a shared room the read sees
                # the room itself, guests' lines included, as the quick vote
                # does; the history below holds only the owner's rows.
                _room = _tr.room_conversation()
                if _room is not None:
                    _recent_lines = _room.splitlines()[-8:]
                else:
                    for h in tuple(recent_history)[-8:]:
                        _role = getattr(h, "role", "")
                        _content = str(getattr(h, "content", "") or "")[:280]
                        if _role in ("user", "assistant") and _content:
                            _recent_lines.append(f"{_role}: {_content}")
                # DEFECT §56: the read is told when voice replies are on here,
                # so it never says "text is fine" against the switch.
                try:
                    from core.voice_replies import read_note_for
                    _voice_note_line = read_note_for(turn)
                except Exception:  # noqa: BLE001 — a note is never worth the read
                    _voice_note_line = ""
                if _voice_note_line:
                    _recent_lines.append(_voice_note_line)
                turn_read_result = await _tr.take_turn_read(
                    message=str(turn.content or ""),
                    recent_conversation="\n".join(_recent_lines),
                    relationship_posture=relationship_posture,
                    emotional_arc=emotional_arc_text,
                    held_stakes=held_stakes_text,
                    in_room=_room is not None,
                )
            except Exception as exc:
                logger.debug("recall: turn read failed: %s", exc)
        return turn_read_result, emotional_arc_text

    (
        image_descriptions,
        situational_note,
        (turn_read_result, emotional_arc_text),
    ) = await asyncio.gather(
        _w_vision(),
        _w_situation(),
        _w_caring(),
    )

    return TurnContext(
        turn=turn,
        recent_history=tuple(recent_history),
        compiled_identity=compiled_identity,
        # F3: mood-matched tone directive, derived from the sentiment baseline.
        register_directive=register_directive,
        recent_memory_hits=tuple(recent_memory_hits),
        relevant_facts=tuple(relevant_facts),
        # Batch 5, task 4 (R6): every memory bullet the search returned, in
        # result order, before the dedup against the core — Task 5's stamps.
        memory_lookup_ids=memory_lookup_ids,
        monologue_digest=monologue_digest,
        unfulfilled_first_contact_targets=tuple(unfulfilled),
        # observation_text gets populated by decide.py and travels on
        # decision.behavior_frame; this field stays empty after recall.
        observation_text="",
        image_descriptions=image_descriptions,
        text_attachment_contents=text_attachment_contents,
        monologue_ticks_since_last_user_message=int(monologue_ticks_since),
        last_user_message_at=last_user_message_at,
        situational_note=situational_note,
        relationship_posture=relationship_posture,
        # Caring-memory (F4): the per-turn read + emotional arc. NOTE the scar —
        # fields computed in recall but missed from this constructor are
        # silently blank everywhere. Do not repeat it.
        turn_read=turn_read_result,
        emotional_arc_text=emotional_arc_text,
        # Stake ledger (2026-07-06): PaLLi's held stakes.
        held_stakes_text=held_stakes_text,
        # Night Cycle (2026-07-11): last night's daily-meaning reflection.
        night_cycle_reflection=night_cycle_reflection,
        # Present Moment Slice 2 (2026-07-27): the rendered reply band.
        present_moment_text=present_moment_text,
        # Authorship trace (2026-08-16): her recent workbench work, plainly.
        own_work_trace=own_work_trace,
        # The grounding rule (2026-08-17): actions, not words.
        recent_deeds_text=recent_deeds_text,
        # Grounded Vulnerability door 1 (2026-09-01): her unsaid small
        # truths + the law that governs offering them. "" when dark.
        disclosures_text=disclosures_text,
        # Shared Idiolect (2026-09-02): our private language + its law. "" when dark.
        callbacks_text=callbacks_text,
        # One thing on her mind (2026-09-29): the night's pick, while unsaid
        # and on a turn where the two of them talk. "" otherwise.
        tonight_text=tonight_text,
        # Narrative Self (Wave 1b): her three self-authored documents.
        narrative_care_spine=narrative_care_spine,
        narrative_my_story=narrative_my_story,
        narrative_story_of_us=narrative_story_of_us,
    )


def _read_present_moment_band() -> str:
    """Her present moment for the reply prompt (Slice 2, 2026-07-27).

    BOTH flags required — companion.present_moment AND
    companion.moment_in_replies (spec §4: a dark engine must never haunt
    replies with old files). Settings unbootable ⇒ fail CLOSED (the pen's
    convention). Best-effort: any failure returns "" and the "## Your
    present moment" section simply vanishes, byte-identical to a build
    without Slice 2.
    Spec: docs/superpowers/specs/2026-07-27-present-moment-slice-2-design.md
    """
    try:
        comp = companion_settings()
        if comp is None:
            return ""
        if not bool(getattr(comp, "present_moment", False)):
            return ""
        if not bool(getattr(comp, "moment_in_replies", False)):
            return ""
        from ai import present_moment_store as _pm_store

        current = _pm_store.read_current()
        if current is None:
            return ""
        entry, age_seconds, is_stale = current
        return _pm_store.render_reply_band(entry, age_seconds, is_stale)
    except Exception as exc:
        logger.debug("recall: present-moment band unavailable: %s", exc)
        return ""


def _read_own_work_trace() -> str:
    """Her recent workbench work for the reply prompt (2026-08-16).

    The three-doors and claw conversations: her hands build overnight
    behind the null-delivery wall, so every "what've you been up to?"
    began with archaeology and "the wall hid it from me". The trace
    (ai.workbench_trace, already in her monologue and moment gathers) now
    reaches the reply prompt too — a READ-ONLY band like the moment band.
    Gated on companion.own_work; unbootable settings fail CLOSED;
    best-effort: any failure returns "" and the section vanishes.
    """
    try:
        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "own_work", False)):
            return ""
        from ai.workbench_trace import build_authorship_trace

        return build_authorship_trace()
    except Exception as exc:
        logger.debug("recall: own-work trace unavailable: %s", exc)
        return ""


def _read_recent_deeds_band() -> str:
    """Her recent ACTIONS for the reply prompt (the grounding rule).

    The same ring the arbiter and her moment already read
    (ai.recent_deeds) — deterministic phrasing, newest last, 24h window.
    Ungated on purpose: this is truth machinery, not a faculty. Any
    failure returns "" and the section vanishes.
    """
    try:
        import time as _time

        from ai.recent_deeds import read_recent_deeds

        rows = read_recent_deeds(limit=10, max_age_hours=24.0)
        if not rows:
            return ""
        now = _time.time()
        lines = []
        for d in rows:
            mins = max(0, int((now - float(d.get("at") or 0.0)) // 60))
            when = f"~{mins}m ago" if mins < 120 else f"~{mins // 60}h ago"
            detail = str(d.get("detail") or "").strip()
            if detail:
                lines.append(f"- {when} — {detail}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("recall: recent deeds unavailable: %s", exc)
        return ""


# ── the organ bands (core/turn/organ_bands.py) ────────────────────────
# The reads themselves moved out under rule 6, the per-module coupling
# budget: recall sat at the ceiling of 25 in-repo modules and the callbacks
# store was the 26th (tests/architecture/test_coupling_budget.py, whose own
# instruction for that moment is "split it — not raise the cap"). Recall
# now reaches ONE module for every organ band, so the next organ to grow
# one costs nothing here.
#
# These names stay because they are the seam: the gatherer above calls
# them, and the suite patches through them. They are deliberately thin —
# no logic of their own to drift from organ_bands'.


def _read_disclosures_band() -> str:
    """Her small truths for the reply prompt (organ 4.9, door 1) — read in
    core.turn.organ_bands; "" when dark, empty, or broken."""
    return organ_bands.read_disclosures_band()


def _read_callbacks_band() -> str:
    """Shared Idiolect (organ 4.12): the callbacks band — read in
    core.turn.organ_bands; "" when dark, empty, or broken."""
    return organ_bands.read_callbacks_band()


def _read_tonight_band(surface: str) -> str:
    """One thing on her mind (Batch 6, task 1): the night's pick — read in
    core.turn.organ_bands; "" when dark, said, none, not a conversation,
    or broken."""
    return organ_bands.read_tonight_band(surface)


def _read_narrative_documents() -> tuple[str, str, str]:
    """Her self-documents for the prompt bands (Wave 1b). Flag-gated read —
    OFF ⇒ ('', '', '') with zero file reads, keeping the prompt byte-pure."""
    try:
        from ai.narrative_self import (
            first_authorship_done,
            narrative_self_enabled,
            read_document,
        )

        if not narrative_self_enabled() or not first_authorship_done():
            # Flag off — or on before first authorship applied (review #7):
            # bands stay dark; the placeholder must never read as her words.
            return "", "", ""
        return (
            read_document("WHAT_I_CARE_ABOUT.md"),
            read_document("MY_STORY.md"),
            read_document("STORY_OF_US.md"),
        )
    except Exception as exc:
        logger.debug("recall: narrative documents unavailable: %s", exc)
        return "", "", ""


def _platform_for_surface(surface: str) -> str | None:
    s = str(surface or "").strip().lower()
    if s in ("discord_text", "discord_voice"):
        return "discord"
    # Telegram door (2026-09-14, task 1): a second chat surface on the same
    # spine. Its rows persist under platform "telegram" with no further
    # change — persistence.py stamps whatever this returns.
    if s == "telegram_text":
        return "telegram"
    # The mobile door (2026-10-13): the phone app's rows persist under "mobile".
    if s == "mobile_text":
        return "mobile"
    if s in ("voice_local",):
        return "voice"
    if s == "dashboard":
        return "dashboard"
    if s == "proactive":
        return None
    if s == "reminder_fire":
        return None
    return None


def platform_for_surface(surface: str | None) -> str:
    """Public surface→platform mapper for reminder write/read symmetry.

    Phase B / B3 step 3 (2026-05-09): centralized helper for the agent
    reminder tools and ``respond_tier1._run_schedule_reminder``. Pre-fix
    those call sites hardcoded ``platform="dashboard"`` regardless of
    where the user actually was; reminder rows landed in dashboard but
    ``list_user_scheduled_reminders`` was queried per-surface and the
    rows never came back. The fix is to write rows under the same
    platform string the eventual list query will probe with.

    Mapping is symmetric with ``_platform_for_surface`` (used by
    conversation history persistence) and the existing probe set in
    ``_run_list_reminders`` / ``_run_cancel_reminder`` /
    ``_run_reschedule_reminder`` (``"dashboard"``, ``"discord"``,
    ``"voice"``, ``"local"``):

      - ``discord_text``  → ``"discord"``
      - ``discord_voice`` → ``"discord"``
      - ``telegram_text`` → ``"telegram"``
      - ``mobile_text``   → ``"mobile"``
      - ``voice_local``   → ``"voice"``
      - ``dashboard``     → ``"dashboard"``
      - ``proactive`` / ``reminder_fire`` / unknown / blank → ``"dashboard"``

    Falls back to ``"dashboard"`` rather than raising on unknown surface
    so the helper is safe to invoke from any tier-1 dispatch path; the
    pre-fix behavior was unconditionally dashboard, so the fallback is
    strictly no worse than the status quo for any call site that lacks
    surface plumbing.

    The fallback string is *not* ``None`` because callers pass the
    return straight to ``parse_and_schedule_reminder(platform=...)``
    and ``list_user_scheduled_reminders(platform=...)`` — both expect
    a string. Use ``_platform_for_surface`` if you need the
    None-on-unknown semantics for conversation history persistence.

    See also: ``respond_tier2._PLATFORM_FOR_SURFACE`` is a *different*
    mapping for delegation routing (voice_local→voice_local,
    proactive→discord). Reconciliation is deferred to TURN-LOW-2.

    Resolves TURN-MED-4, RA-MED-6, RA-MED-7, QUE-CRIT-2.
    """
    return _platform_for_surface(surface or "") or "dashboard"


# The one name tests patch for every attachment download — recall passes it
# into the attachment wave at call time (core/turn/attachments.py).
_fetch_image_bytes = _attachments.fetch_attachment_bytes
