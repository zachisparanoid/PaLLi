"""Wave 2: the reflection step that finally writes real conversation content
into MEMORY.md, RELATIONSHIP_STATE.md, and the user_facts store.

On the live ReAct path the runner spawns this as a DETACHED background task
after the reply text is final (2026-07-08) — it genuinely does not block
reply delivery anymore. (On the legacy pipeline it runs gated by
decision.notable_for_reflection in turn.process_turn.) Never raises:
on any failure, returns False and the spine carries on.

The single most important function in the Wave 2 design. Without this,
the identity layer never learns from conversations.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from ai.llm_client import llm
from ai.llm_routing import get_task_model
from ai.prompt_loader import prompt_loader
from core.config import config
from core.database import brain
from core.identity.service import identity_service
from core import memory_first_person as _first_person
from core.open_threads import ThreadStatus
from core.turn.types import (
    ReflectionFactAdd,
    ReflectionMemoryUpdates,
    ReflectionPlan,
    ReflectionRelationshipDelta,
    TurnContext,
    TurnDecision,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "turn_reflect"


# F2 (companion.live_emotion): map the 7-tag decide register to the 13-tag
# sentiment vocabulary so the per-turn emotional read can update the
# sentiment baseline (which otherwise only moves on the hourly monologue
# tick). Approximate by design — it keeps PaLLi's sense of the user current.
_EMOTIONAL_CONTEXT_TO_SENTIMENT = {
    "casual_greeting": "content",
    "vulnerable_close": "vulnerable",
    "venting": "stressed",
    "playful_banter": "playful",
    "deep_intimacy": "open",
    "frustrated": "frustrated",
    "neutral": "neutral",
}


async def _persist_per_turn_emotion(
    *, owner_id: int, decision: Optional[TurnDecision]
) -> None:
    """Persist this turn's emotional read to user_sentiment_state (F2).

    Keeps the sentiment baseline fresh per-message instead of only on the
    hourly monologue tick, so F3 (mood tone), proactive, and next-turn
    recall see how the user is *right now*. Best-effort; never raises.
    """
    if decision is None:
        return
    from core.turn.companion_flags import faculty_enabled
    if not faculty_enabled("live_emotion"):
        return
    ec = str(getattr(decision, "emotional_context", "") or "").strip().lower()
    tag = _EMOTIONAL_CONTEXT_TO_SENTIMENT.get(ec)
    if not tag:
        return
    try:
        import time as _time
        from ai.sentiment.types import SentimentReading
        reading = SentimentReading(
            tag=tag,
            read=f"per-turn read ({ec})",
            confidence=0.6,
            source="per_turn",
            updated_at=_time.time(),
        )
        await brain.update_user_sentiment_state(int(owner_id), reading=reading)
        logger.debug("apply_reflection: persisted per-turn emotion %s->%s", ec, tag)
    except Exception as exc:
        logger.debug("apply_reflection: per-turn emotion persist failed: %s", exc)


def _update_relationship_momentum(
    *, ctx: TurnContext, owner_id: int, decision: Optional[TurnDecision]
) -> None:
    """Compound this turn's relationship signal into the momentum state (F7).

    Synchronous + best-effort (JSON file write); never raises. Uses the
    decide register and the elapsed-time proxy (monologue ticks since the
    user's last message) to drive the asymmetric, sharp-turn-aware update.
    """
    if decision is None:
        return
    from core.turn.companion_flags import faculty_enabled
    if not faculty_enabled("relationship_calibration"):
        return
    try:
        from ai.relationship_momentum import update_from_turn
        update_from_turn(
            int(owner_id),
            emotional_context=str(getattr(decision, "emotional_context", "") or ""),
            gap_ticks=int(getattr(ctx, "monologue_ticks_since_last_user_message", 0) or 0),
        )
    except Exception as exc:
        logger.debug("apply_reflection: relationship momentum update failed: %s", exc)


async def apply_reflection(
    ctx: TurnContext,
    response_text: str,
    *,
    decision: Optional[TurnDecision] = None,
) -> bool:
    """Extract a ReflectionPlan via LLM, apply non-empty sections idempotently.

    Direction C (2026-05-02): the per-turn structured cognitive
    writebacks (pragmatic, user_knowledge, interaction_mode,
    relationship_calibration, emotional_state, topic_boundary) were
    deleted. Those classifications collapsed into the decide-stage
    observation prose, which gets persisted to the conversations table
    (or a future turn_observations table) as part of the assistant
    reply's metadata, not as separate structured rows.

    What reflect still does:
      - Extract a ReflectionPlan from the LLM (turn_reflect prompt)
      - When the plan is non-skipped, apply memory updates,
        relationship trust deltas, user_facts adds, first-contact
        resolutions, and monologue notes. These are explicit memory
        operations the LLM identified as worth persisting — not
        per-turn classifications.

    Returns True if any update was applied; False if skipped. Never
    raises.
    """
    owner_id = ctx.turn.owner_id
    applied_anything = False

    # F2: refresh the emotional baseline from this turn's read first, before
    # plan extraction — so it lands even on small-talk turns the reflection
    # plan would skip. Independent of the plan; never blocks the rest.
    await _persist_per_turn_emotion(owner_id=owner_id, decision=decision)

    # F7: compound this turn's relationship signal into the momentum state
    # (asymmetric, sharp-turn-aware). Also independent of the plan, also
    # never blocks. recall reads the result next turn to set posture.
    _update_relationship_momentum(ctx=ctx, owner_id=owner_id, decision=decision)

    # Caring memory (2026-06-11): persist the unified read's durable outflows
    # — the emotional-timeline entry and any memory_worthy content (stored
    # with salience proportional to the moment's weight). Independent of the
    # plan; never blocks. The circle-back outflow rides the reflection plan
    # below (the read is injected into its prompt in _extract_plan) so thread
    # creation stays single-path and dedup-aware.
    await _persist_caring_memory(ctx=ctx, owner_id=owner_id)

    # Batch 5, task 5 (memory that picks, 2026-09-28; rulings R6, R41): the
    # lines recall surfaced for this turn are stamped as USED — the memory
    # bullets through memory_refs.bump, the rendered user_memory facts
    # through the old fact bump — so the core and the nightly sweep can tell
    # a used line from an unused one. Independent of the plan; never raises;
    # never refreshes the refs snapshot (R7).
    await _record_memory_use(ctx, owner_id)

    # One thing on her mind (Batch 6, task 1, 2026-09-29; item 3.2): did
    # this reply bring up the night's pick? One small yes/no call of its
    # own, asked only while a pick is unsaid and only after a conversation.
    # Independent of the plan — a failed or empty plan below never costs
    # the record that she said it — and never raises.
    await _record_tonight_said(ctx, response_text)

    # The presence slice (10-13; D7): when the first look drew on one of her
    # stakes, the stake is stamped said only when this reply really said it,
    # so "said N days ago" in the next look is true. Never raises.
    await _record_stake_said(ctx, response_text)

    try:
        plan = await _extract_plan(ctx, response_text)
    except Exception as exc:
        logger.warning(
            "apply_reflection: plan extraction failed (%s: %s)",
            type(exc).__name__, exc,
        )
        return applied_anything

    if plan is None:
        return applied_anything

    # 2026-05-10 — Open-Threads Agenda dispatch. Capture/resolve flow
    # through brain.add_open_thread / brain.close_open_thread.
    #
    # Dispatched BEFORE the skip-reason short-circuit. The user can
    # address a tracked topic on a turn that's otherwise small-talk and
    # we still need to flip its row so proactive selection stops
    # resurfacing it. Per-call try/except so one failure doesn't take
    # down the rest of the plan.
    for entry in plan.open_threads_to_add:
        try:
            await brain.add_open_thread(
                owner_user_id=ctx.turn.owner_id,
                topic_summary=entry["topic_summary"],
                why_it_matters=entry["why_it_matters"],
                kind=entry["kind"],
                urgency=entry["urgency"],
            )
            applied_anything = True
        except Exception as exc:
            logger.warning(
                "apply_reflection: add_open_thread failed: %s: %s",
                type(exc).__name__, exc,
            )

    for entry in plan.open_threads_to_close:
        try:
            await brain.close_open_thread(
                thread_id=entry["thread_id"],
                resolution_reason=entry["resolution_reason"],
                status=ThreadStatus.RESOLVED,
            )
            applied_anything = True
        except Exception as exc:
            logger.warning(
                "apply_reflection: close_open_thread failed: %s: %s",
                type(exc).__name__, exc,
            )

    # Wanting (step 20): file his durable directives into her wishbook.
    # Above the skip short-circuit on purpose — a wish can ride an
    # otherwise-small turn. Never silent: the deed line is the
    # acknowledgment her senses can see (re-spec §3 deviation, recorded).
    # Corrections of record (2026-08-17): always on — a belief of hers shown
    # false this turn becomes a first-class record that outranks anything
    # older; acknowledged as a deed her senses can see.
    if getattr(plan, "corrections_to_add", None):
        for entry in plan.corrections_to_add:
            try:
                from ai.corrections_store import record_correction
                from ai.recent_deeds import record_deed

                row = record_correction(
                    belief=entry["belief"], truth=entry["truth"],
                    source="hers", evidence=entry.get("evidence") or "",
                )
                if row is not None:
                    record_deed(
                        "correction_recorded",
                        f"set the record straight: not \"{row.belief[:60]}\" — {row.truth[:70]}",
                    )
                    applied_anything = True
            except Exception as exc:
                logger.warning("reflect: correction filing failed: %s", exc)

    if getattr(plan, "quests_to_add", None) and _quests_filing_enabled():
        for entry in plan.quests_to_add:
            try:
                from ai.quest_store import plant
                from ai.recent_deeds import record_deed

                # Step 25 · Regret (spec §5): a repair for one of her OWN open
                # regrets files as HERS, carrying provenance regret:<id>; the
                # door declines a second one for the same regret, ever (a
                # decline earns no deed). Flag off: the field is ignored —
                # today's exact filing.
                prov = str(entry.get("provenance") or "") if _regret_repair_enabled() else ""
                is_repair = prov.startswith("regret:")
                q, _dormant = plant(
                    text=entry["text"],
                    source="hers" if is_repair else "him-spoken",
                    horizon=entry.get("horizon") or "someday",
                    provenance=prov if is_repair else "a durably-directive turn (reflect filing)",
                )
                if q is not None:
                    record_deed(
                        "quest_filed",
                        (f"filed a repair for a regret of mine: {q.text[:80]}" if is_repair
                         else f"kept a wish of theirs: {q.text[:80]}"),
                    )
                    applied_anything = True
            except Exception as exc:
                logger.warning(
                    "apply_reflection: quest filing failed: %s: %s",
                    type(exc).__name__, exc,
                )

    # Fears (organ 6, step 24 §6): the nightly plant lane — above the
    # short-circuit like the two lanes above (a fear can crystallize on an
    # otherwise-small turn). ≤1/night in CODE via the store's day clock;
    # the store's cap DECLINES (logged there); a decline earns no deed.
    if getattr(plan, "fears_to_add", None) and _fears_filing_enabled():
        for entry in plan.fears_to_add[:1]:            # one filing per turn
            try:
                from ai.fear_store import plant, reflection_planted_today
                from ai.recent_deeds import record_deed

                if reflection_planted_today():
                    logger.info(
                        "apply_reflection: fear filing declined — "
                        "one already planted tonight (≤1/night)"
                    )
                    break
                f = plant(
                    text=entry["text"], watch=entry["watch"],
                    source="reflection",
                )
                if f is not None:
                    record_deed(
                        "fear_filed",
                        f"wrote a fear of mine into my book: {f.text[:80]}",
                    )
                    applied_anything = True
            except Exception as exc:
                logger.warning(
                    "apply_reflection: fear filing failed: %s: %s",
                    type(exc).__name__, exc,
                )

    # Grounded Vulnerability (organ 4.9, spec §3/§5): the reply-door stamp
    # — "said once, forever" is enforced HERE or nowhere, because her reply
    # prompt deliberately carries no ids. Above the short-circuit like the
    # lanes above: a small truth rides an otherwise-unremarkable turn by
    # design. The store owns every refusal (unknown, already said, faded);
    # a refusal is logged and dropped — never raised, never retried.
    if getattr(plan, "disclosure_said", "") and _disclosure_filing_enabled():
        try:
            from ai.disclosure_store import mark_said
            from ai.recent_deeds import record_deed

            if mark_said(str(plan.disclosure_said), via="reply"):
                record_deed("disclosure_shared",
                            "shared one of my small truths with them")
                applied_anything = True
            else:
                logger.info("apply_reflection: disclosure_said %r not stamped "
                            "(unknown, said, or faded)", plan.disclosure_said)
        except Exception as exc:
            logger.warning("apply_reflection: disclosure stamp failed: %s: %s",
                           type(exc).__name__, exc)

    # Shared Idiolect (organ 4.12, spec §2): his retire. Above the
    # short-circuit like its siblings. The store owns every other refusal
    # (unknown id, already retired); a refusal is logged and dropped.
    #
    # The BIRTH door is closed here (Batch 6, task 2, 2026-09-29, ruling
    # R30): a bit becomes theirs across days, and one turn cannot show
    # that, so new bits are found by the weekly harvest
    # (ai/callbacks_harvest.py). The prompt no longer asks for a birth, and
    # one the model sends anyway is never parsed (_plan_from_payload); a
    # plan that carries births from anywhere else is dropped here.
    if (getattr(plan, "callbacks_born", None) or getattr(plan, "callbacks_retired", None)) \
            and _callback_filing_enabled() \
            and _callback_surface_ok(getattr(ctx, "turn", None)):
        turn = getattr(ctx, "turn", None)
        user_text = str(getattr(turn, "content", "") or "")
        if getattr(plan, "callbacks_born", None):
            logger.info("apply_reflection: callback_born dropped: new bits are found by "
                        "the weekly harvest, never from a turn (%d sent)",
                        len(plan.callbacks_born))
        # Review I4: the try is PER ENTRY, the house rule this function
        # states at :182-185 and both sibling filings follow. One raising
        # retire must not swallow the ones after it: his word is the only
        # user-driven channel this organ has (§0.4).
        for op in getattr(plan, "callbacks_retired", []) or []:
            try:
                from ai.callbacks_store import find_callback, normalize, retire
                from ai.recent_deeds import record_deed

                # Review I2: the door that DESTROYS gets the birth door's
                # rail. Retirement is one-way (the store refuses an
                # already-retired id, and only a fresh plant under a new id
                # could restore the bit, with a broken lineage), so a
                # misread hedge would pull a real bit out of her band and
                # stamp him with a reason he never said. His words, quoted,
                # proven in THIS turn's own message — or nothing happens.
                words = normalize(str(op.get("reason") or ""))
                if not words or words not in normalize(user_text):
                    logger.info("apply_reflection: callback_retired refused: "
                                "words not in his message (%r)", op.get("id"))
                    continue
                existing = find_callback(op["id"])
                if existing is not None and retire(op["id"], reason=op["reason"]):
                    record_deed("callback_retired", f'they retired a bit: "{existing.bit}"')
                    applied_anything = True
                else:
                    logger.info("apply_reflection: callback_retired %r not applied "
                                "(unknown or already retired)", op.get("id"))
            except Exception as exc:
                logger.warning("apply_reflection: callback_retired filing failed: %s: %s",
                               type(exc).__name__, exc)

    if plan.skip_reason:
        logger.info("apply_reflection: skipped (%s)", plan.skip_reason)
        return applied_anything

    try:
        if _has_memory_updates(plan.memory_updates):
            await _apply_memory_updates(owner_id=owner_id, updates=plan.memory_updates, who=_person_for(ctx.compiled_identity))
            applied_anything = True
    except Exception as exc:
        logger.warning("apply_reflection: memory update failed: %s", exc)

    try:
        if plan.relationship_delta is not None and _has_relationship_delta(plan.relationship_delta):
            await _apply_relationship_delta(owner_id=owner_id, delta=plan.relationship_delta)
            applied_anything = True
    except Exception as exc:
        logger.warning("apply_reflection: relationship delta failed: %s", exc)

    try:
        if plan.user_facts_add:
            await _apply_user_facts(owner_id=owner_id, facts=plan.user_facts_add)
            applied_anything = True
    except Exception as exc:
        logger.warning("apply_reflection: user_facts failed: %s", exc)

    try:
        if plan.first_contact_resolutions:
            applied = await _apply_first_contact_resolutions(
                owner_id=owner_id, resolutions=plan.first_contact_resolutions,
            )
            if applied:
                applied_anything = True
    except Exception as exc:
        logger.warning("apply_reflection: first_contact_resolutions failed: %s", exc)

    try:
        if plan.monologue_note:
            await _append_monologue_note(owner_id=owner_id, note=plan.monologue_note)
            applied_anything = True
    except Exception as exc:
        logger.warning("apply_reflection: monologue_note failed: %s", exc)

    # Temporal-awareness plan, Workstream D.3: persist a session close
    # marker so decide can use it as a strong session-boundary signal on
    # the next user message. The marker is just a single durable_context
    # entry timestamped at "now"; recall surfaces it on the next turn so
    # decide knows the previous session ended naturally.
    try:
        if plan.session_close_signal:
            await _record_session_close(
                owner_id=owner_id,
                reason=plan.session_close_reason or "unspecified",
            )
            applied_anything = True
            logger.info(
                "apply_reflection: session_close_signal=true (reason=%s) recorded",
                plan.session_close_reason or "unspecified",
            )
    except Exception as exc:
        logger.warning("apply_reflection: session_close recording failed: %s", exc)

    return applied_anything


async def _record_session_close(*, owner_id: int, reason: str) -> None:
    """Persist a session_closed_at marker on the user record.

    Phase B / B4 step 2 (2026-05-09) — audit finding IM-HIGH-2: the
    timestamp lands in the dedicated ``users.last_session_closed_at``
    column rather than in the generic ``user_memory`` store. Previously
    the marker was stored as a ``user_fact`` row with
    ``key='last_session_closed_at'``, which polluted decay-ranked recall
    budgets with bookkeeping data. The new column was added in B4 step 1
    (commit ``ea277b2``); this writer redirect closes the producer side.

    The ``reason`` argument is observed only via logging — the new
    column carries just the timestamp. Callers that previously relied
    on parsing ``ts|reason`` out of ``user_memory`` rows are migrated to
    read the column directly in B4 step 3 (recall.py readers).

    Failures are swallowed: ``apply_reflection`` already wraps each
    section in a per-section ``try/except``, but defense-in-depth at the
    hook keeps a transient DB error from interrupting other reflection
    work.
    """
    import time
    try:
        from core.database import brain
    except ImportError:
        logger.debug("_record_session_close: brain unavailable; skipping")
        return
    timestamp = time.time()
    try:
        await brain.set_user_last_session_closed_at(
            owner_user_id=int(owner_id),
            timestamp=float(timestamp),
        )
    except Exception as exc:
        logger.warning(
            "_record_session_close: set_user_last_session_closed_at failed "
            "(reason=%s): %s",
            reason, exc,
        )


async def _tonight_said_llm_call(prompt: str) -> str:
    """The said-check's model call: the reflect model, low temperature, a
    small budget. Its lane is patient by the ``reflect_`` prefix and does
    not think by the ``reflect_turn`` prefix — a yes/no with a small budget
    and thinking on comes back empty."""
    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=_resolve_reflect_model(),
        temperature=0.1,
        max_tokens=300,
        log_context="reflect_turn_tonight_said",
    )


async def _record_tonight_said(ctx: "TurnContext", response_text: str) -> None:
    """One thing on her mind (Batch 6, task 1; item 3.2): when this reply
    brought up the night's pick, record that she said it — the pick file,
    and for a stake the ledger (ai/tonight.py stamp_said).

    The question is the model's (ai/tonight.py said_check, prompt
    ``tonight_said``): did she bring it up as her own thing? A passing
    mention is a no, and anything but a clean yes leaves the pick unsaid and
    the band in place. Code keeps the gates, read in this order so a dark
    organ never opens her folder: the flag; a pick for tonight that is not
    said yet; a surface where the two of them talk (ai/tonight.py
    is_conversation — after a reminder or a reach-out the band was never
    shown). That gate is the pick's own and quiet: the callbacks organ's
    _callback_surface_ok logs "callback filing skipped", which under this
    check was another organ's line after every reminder (ruling R61b).
    Never raises."""
    try:
        from ai import tonight

        if not tonight.tonight_enabled():
            return
        # Second read F10 (2026-10-04) — the window this leg leaves open,
        # written down rather than closed. The pick is read here, right
        # before the said-check's request goes out (no await between this
        # read and that request), and stamp_said's mark_said re-reads the
        # file, so a say the arbiter sent and stamped meanwhile (it stamps at
        # its ledger append now) is never stamped twice. What stays open is
        # upstream of this check: the reply was written with the band its
        # prompt rendered at recall, before its own model call(s), so a say
        # that went out during that call can be repeated by the reply.
        # Closing that would mean re-rendering the reply's prompt before each
        # request (core/agent/system_prompt.py, context_adapter.py — Batch
        # 7's files); bounded at two, and the band's own law ("never repeat
        # it once it's said") is the brake inside it.
        pick = tonight.load_current()
        if pick is None or pick.said_at is not None:
            return
        surface = getattr(ctx.turn, "surface", "")
        if not tonight.is_conversation(surface):
            logger.debug("apply_reflection: tonight's pick not checked: surface %r "
                         "is not a conversation", surface)
            return
        if await tonight.said_check(pick, response_text, llm_call=_tonight_said_llm_call):
            await tonight.stamp_said(pick, at=time.time())
    except Exception as exc:
        logger.warning(
            "apply_reflection: the check on tonight's pick failed (%s: %s) — it stays unsaid",
            type(exc).__name__, exc,
        )


async def _record_stake_said(ctx: "TurnContext", response_text: str) -> None:
    """The stake the first look named in ``my_own_from`` (the presence slice,
    D7): one small yes/no call (ai/stake_ledger.py stake_said); on a clean
    yes, its ``last_surfaced_at`` (when it was last said, "said N days ago")
    and, the first time, its ``voiced_at``. Anything else stamps nothing.
    Never raises."""
    stake = getattr(ctx, "own_stake_drawn", None)
    if stake is None:
        return
    try:
        from ai import stake_ledger

        if await stake_ledger.stake_said(stake.kind, stake.stake, response_text):
            await stake_ledger.record_surfaced(int(stake.id))
            await stake_ledger.mark_stake_said(int(stake.id))
            logger.info("apply_reflection: stake %s was said in this reply", stake.id)
    except Exception as exc:
        logger.warning("apply_reflection: the stake said-check failed (%s: %s) — it stays unsaid",
                       type(exc).__name__, exc)


async def _persist_caring_memory(*, ctx: "TurnContext", owner_id: int) -> None:
    """Persist the unified per-turn read's durable outflows (caring memory).

    Outflow 3: timeline entry (skips low-weight turns per config threshold).
    Outflow 2: ``memory_worthy`` content stored with weight-proportional
    salience so heavy moments outrank trivia at recall time.
    Best-effort; never raises into the reflection flow.
    """
    read = getattr(ctx, "turn_read", None)
    if read is None:
        return
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        if comp is not None and not getattr(comp, "caring_memory", True):
            return
        min_weight = (
            float(getattr(comp, "caring_memory_min_weight", 0.15) or 0.15)
            if comp is not None else 0.15
        )
    except Exception:
        min_weight = 0.15

    try:
        from ai.turn_read import record_timeline_entry
        await record_timeline_entry(
            owner_id=int(owner_id), read=read, min_weight=min_weight,
        )
    except Exception as exc:
        logger.debug("caring_memory: timeline persist failed: %s", exc)

    if read.memory_worthy and read.weight >= 0.4:
        try:
            await brain.store_user_memory(
                user_id=int(owner_id),
                memory_type="moment",
                content=read.memory_worthy,
                importance=min(1.0, 0.4 + read.weight * 0.6),
                tags=[],
                # salience_weight feeds ai/fact_retrieval scoring + the
                # galaxy: heavy moments surface ahead of trivia.
                salience_weight=1.0 + read.weight,
                source_detail={"source": "caring_memory_turn_read"},
            )
        except Exception as exc:
            logger.debug("caring_memory: memory_worthy store failed: %s", exc)

    # The today slot (R15, T12): the moment is today's at any weight — the
    # facts band leaves her moment rows out on purpose, and the slot is
    # small and dated, so this is how a light one still reaches the next
    # reply today.
    worthy = str(read.memory_worthy or "").strip()
    if worthy:
        await _note_today(owner_id, worthy, source="memory_worthy")


async def _note_today(owner_id: int, content: str, *, source: str) -> None:
    """Keep ``content`` in the owner's today slot (R15, memory that reaches,
    T12) — only while ``companion.memory_reach`` is on: off, the database is
    never touched here, so the reflection is today's byte for byte. The slot is
    the owner's alone: reflection runs on the owner's turns only (dispatch
    hands a guest's turn no reflection). The row is read into the reply
    under "## Today, so far" and retired by the nightly pass. Best-effort:
    a failed write is a warning and the reflection carries on."""
    try:
        from core.turn.companion_flags import today_slot_on

        if not today_slot_on():
            return
        kept = await brain.note_today_fact(user_id=int(owner_id), content=content, source=source)
        if kept is None:
            logger.debug("apply_reflection: today's slot is full — a %s line was not kept", source)
    except Exception as exc:
        logger.warning("apply_reflection: today's slot write failed (%s)", type(exc).__name__)


async def _record_memory_use(ctx: "TurnContext", owner_id: int) -> dict[str, int]:
    """Stamp what recall surfaced for this turn as USED (Batch 5, task 5,
    memory that picks, 2026-09-28; rulings R6 and R41).

    (a) Every ``mcb_`` id on ``ctx.memory_lookup_ids`` — the MEMORY.md
    bullets recall's search returned past the relevance floor, INCLUDING
    the ones the core already shows (R6: "use" = surfaced) — goes through
    ``memory_refs.bump``: the ``memory_bullet_refs`` row takes the time and
    counts up. The in-process refs snapshot is not refreshed here (R7): the
    core's ranking, and with it the reply's cached stable prefix, move once
    a day, so a line used today enters the core tomorrow.
    (b) The ``user_memory`` row ids of the fact entries the "Things I Know
    About This User" band rendered — the entries of ``ctx.relevant_facts``
    carrying an int ``id`` (a bullet entry carries ``item_id`` and no
    ``id``) — go through ``ai.fact_retrieval.queue_reference_bump_ids``;
    the task it queues is awaited here (fix wave, T5 M19), so the reference
    is held and the stamp has landed when this returns — the task holds its
    own errors (a failed bump is its warning).

    Best-effort: a failure of either bump is logged and the reflection
    carries on — a lost stamp never costs a memory write. Tolerates a
    context without Task 4's fields (older hand-built contexts). Returns
    ``{"bullets": n, "facts": m}``: how many ids each bump was handed, 0
    when it had nothing or the hand-over failed.
    """
    counts = {"bullets": 0, "facts": 0}

    bullet_ids = tuple(getattr(ctx, "memory_lookup_ids", ()) or ())
    if bullet_ids:
        try:
            from core import memory_refs
            await memory_refs.bump(int(owner_id), bullet_ids)
            counts["bullets"] = len(bullet_ids)
        except Exception as exc:
            # %r: a message-less exception logs empty under %s.
            logger.warning(
                "apply_reflection: memory bullet reference bump failed for %d id(s): %r",
                len(bullet_ids), exc,
            )

    fact_ids: list[int] = []
    for entry in getattr(ctx, "relevant_facts", ()) or ():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("id")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw not in fact_ids:
            fact_ids.append(raw)
    if fact_ids:
        try:
            from ai.fact_retrieval import queue_reference_bump_ids
            task = queue_reference_bump_ids(user_id=int(owner_id), fact_ids=fact_ids)
            if task is not None:
                # Awaited, never dropped (fix wave, T5 M19): the task swallows
                # its own errors, so this never raises for a failed bump —
                # and the count below is honest.
                await task
            counts["facts"] = len(fact_ids)
        except Exception as exc:
            logger.warning(
                "apply_reflection: user_memory reference bump failed for %d id(s): %r",
                len(fact_ids), exc,
            )

    if counts["bullets"] or counts["facts"]:
        logger.debug(
            "apply_reflection: memory use recorded — %d bullet(s), %d fact(s)",
            counts["bullets"], counts["facts"],
        )
    return counts


# Batch 2, task 4 (2026-09-27): what the template's identity slot reads when
# her identity rides in the system message instead of inline.
_IDENTITY_IN_SYSTEM_POINTER = (
    "(Your identity, your pages and your memory of them are in the system message above.)"
)


def _reflection_system_for(ctx: TurnContext) -> str:
    """The reflection's system message for this turn — batch 5, task 11
    (2026-09-28, ruling R53). Batch 2 (task 4, 2026-09-27) sent the reply's
    WHOLE stable prefix here so the provider could serve it from cache;
    measured, only 26% of it hit, and every reflection paid for ~60k tokens
    of constitution, voice rule, skills and person line it has no use for.
    Now: the SAME AgentLoopContext the reply builds, and exactly three of
    its fields — ``identity_core_text``, ``owner_memory_text`` (the memory
    core plus the preferences) and ``corrections_text`` — joined with blank
    lines, empties dropped. Never build_system_prompt_parts: leaving out the
    constitutional blocks, ``## How I talk``, the skills and the person
    line is the point. Deterministic within a day, as the renderers are.
    "" on any failure: the caller falls back to today's one-message call,
    and the memory write is never lost to it."""
    try:
        # Imported here, never at load time: core.agent already imports
        # core.turn, so a module-level import back would invite a cycle.
        from core.agent.context_adapter import build_agent_loop_context

        # is_owner=True always: text_prompt() gave every reflection her
        # memory doc whoever spoke, and this keeps that content.
        loop_ctx = build_agent_loop_context(ctx.turn, ctx, is_owner=True)
        parts = (
            loop_ctx.identity_core_text,
            loop_ctx.owner_memory_text,
            loop_ctx.corrections_text,
        )
        return "\n\n".join(p for p in parts if p and p.strip())
    except Exception as exc:
        # INFO and %r: a message-less exception logs empty under %s, and a
        # silent fall back to the uncached shape would show only in the
        # counters (whole-branch review, 2026-09-27). The phrase is the one
        # a test pins; what is unavailable is now the trimmed message.
        logger.info("apply_reflection: stable prefix unavailable: %r", exc)
        return ""



def _person_for(compiled: Any) -> "_first_person.Person":
    """Her person (name once known, pronouns) from the bundle recall loaded."""
    try:
        from core.agent.context_adapter import resolve_owner_person
        name, pronouns = resolve_owner_person(compiled)
        return _first_person.person(name, pronouns)
    except Exception as exc:
        logger.debug("apply_reflection: person unresolved (%s) — neutral wording", type(exc).__name__)
        return _first_person.person()



async def _extract_plan(ctx: TurnContext, response_text: str) -> Optional[ReflectionPlan]:
    """Call the turn_reflect LLM. Returns parsed ReflectionPlan or None."""
    try:
        template = prompt_loader.load_prompt(PROMPT_NAME)
    except Exception as exc:
        logger.info("apply_reflection: prompt %r unavailable: %s", PROMPT_NAME, exc)
        return None

    if not template:
        return None

    identity_excerpt = ""
    # Batch 2, task 4 (2026-09-27): her identity travels as the system message
    # and the slot points at it. Since batch 5, task 11 (ruling R53) that
    # message is the identity core, the memory core with the preferences and
    # the corrections of record — not the reply's whole stable prefix. No
    # bundle, or no system message: today's inline excerpt, byte for byte.
    reflection_system = _reflection_system_for(ctx) if ctx.compiled_identity is not None else ""
    if reflection_system:
        identity_excerpt = _IDENTITY_IN_SYSTEM_POINTER
    elif ctx.compiled_identity is not None:
        try:
            identity_excerpt = ctx.compiled_identity.text_prompt() or ""
        except Exception:
            identity_excerpt = ""

    memory_durable_context = _format_memory_durable_context(ctx)
    memory_active_threads = _format_memory_section(ctx, "## Active Threads")
    relationship_state = _format_relationship_state(ctx)

    # Caring memory (2026-06-11): give the plan extractor PaLLi's own read of
    # this turn so its thread-worthiness judgments see the emotional weight —
    # this is how circle-backs (outflow 4) happen WITHOUT a second,
    # dedup-blind thread-creation path. It used to ride at the end of the
    # relationship block; it changes every turn, so since 2026-10-05 (the
    # owner: "make the reflection cacheable") it has its own slot in the
    # template's per-turn block at the very end, and the relationship block
    # stays byte-identical from turn to turn. Same words as before; "" when
    # there is no read, so the slot leaves no bytes of its own.
    turn_inner_read = ""
    _read = getattr(ctx, "turn_read", None)
    if _read is not None:
        turn_inner_read = (
            f"\nPaLLi's inner read of THIS turn "
            f"(emotional weight {_read.weight:.2f}, needs_support {_read.needs_support:.2f}): "
            f"{_read.read}"
        )
        if _read.carries_forward:
            turn_inner_read += (
                "\nWorth holding onto / circling back on later: "
                f"{_read.carries_forward} "
                "(consider an open thread if no active thread covers it)"
            )
        turn_inner_read += "\n"

    # 2026-05-10 — Open-Threads Agenda. Fetch active threads so the
    # reflection LLM can choose to close them.
    active_open_threads_lines: list[str] = []
    try:
        threads = await brain.list_active_open_threads(
            owner_user_id=ctx.turn.owner_id,
        )
        for t in threads:
            active_open_threads_lines.append(
                f"- {t.id}: [{t.kind.value} | {t.urgency}] "
                f"{t.topic_summary} — {t.why_it_matters}"
            )
    except Exception as exc:
        logger.warning("apply_reflection: list_active_open_threads failed: %s", exc)

    active_open_threads_block = (
        "\n".join(active_open_threads_lines)
        if active_open_threads_lines
        else "(none)"
    )

    format_kwargs = dict(
        user_message=ctx.turn.content or "",
        assistant_reply=response_text or "",
        identity_excerpt=identity_excerpt or "(no identity loaded)",
        memory_durable_context=memory_durable_context,
        memory_active_threads=memory_active_threads,
        relationship_state=relationship_state,
        unfulfilled_first_contact_targets=", ".join(
            ctx.unfulfilled_first_contact_targets
        ) or "none",
        current_time_context=ctx.turn.received_at.isoformat(),
        active_open_threads=active_open_threads_block,
        turn_inner_read=turn_inner_read,
    )
    # Her person by name once she knows it (owner call 2026-09-29): the two
    # tone placeholders render the neutral wording on a fresh install and the
    # name + pronouns on an install that has learned them.
    _who = _person_for(ctx.compiled_identity)
    format_kwargs["memory_person_short"] = _first_person.short_rule(_who)
    format_kwargs["memory_person_rule"] = _first_person.tone_rule(_who)
    # Wanting (step 20): the two conditional blocks — "" when off, so the
    # flag-off prompt renders today's exact bytes.
    quests_schema_block, quest_filing_guidance = _quest_filing_kwargs()
    format_kwargs["quests_schema_block"] = quests_schema_block
    format_kwargs["quest_filing_guidance"] = quest_filing_guidance
    # Corrections of record (2026-08-17): always on.
    format_kwargs["corrections_schema_block"] = _CORRECTIONS_SCHEMA_BLOCK
    format_kwargs["corrections_guidance"] = _CORRECTIONS_GUIDANCE_BLOCK
    # Fears (step 24): the quests lane's conditional-block pattern.
    fears_schema_block, fear_filing_guidance = _fear_filing_kwargs()
    format_kwargs["fears_schema_block"] = fears_schema_block
    format_kwargs["fear_filing_guidance"] = fear_filing_guidance
    # Grounded Vulnerability (organ 4.9): the same lane, id-bearing — this
    # is the only surface that can stamp a small truth said.
    disclosure_schema_block, disclosure_guidance = _disclosure_filing_kwargs()
    format_kwargs["disclosure_schema_block"] = disclosure_schema_block
    format_kwargs["disclosure_guidance"] = disclosure_guidance
    # Shared Idiolect (organ 4.12): his retire door — same lane. The birth
    # half left these two slots with Batch 6 (the weekly harvest finds new
    # bits); the slots themselves, and the template, are unchanged.
    callback_schema_block, callback_guidance = _callback_filing_kwargs()
    format_kwargs["callback_schema_block"] = callback_schema_block
    format_kwargs["callback_guidance"] = callback_guidance
    # ``str.format(**extra_kwargs)`` ignores unused kwargs silently — a
    # customized template that omits a placeholder simply drops the value,
    # no fallback needed. (Spec-review removed a dead try/except KeyError
    # here; KeyError only fires when the template references a placeholder
    # we DIDN'T pass, not the other way around.)
    #
    # The template's ORDER is a cost decision (the owner, 10-05: "make the
    # reflection cacheable"). DeepSeek bills a request's leading tokens at
    # the cached price when they match an earlier request's exactly, so the
    # filled template runs from what changes least to what changes every
    # turn: the static instructions, then the blocks that move only when her
    # memory does (rarest first; the open threads come back oldest first, so
    # a new one lands at the end), then this turn's words, her reply, her
    # inner read and the clock. Measured 10-05 the old order (this turn's
    # words at the top) left ~58k of ~64k tokens uncached on every reply.
    # tests/turn/test_reflect_cacheable.py pins the shared prefix.
    filled = template.format(**format_kwargs)

    messages = [{"role": "user", "content": filled}]
    if reflection_system:
        messages.insert(0, {"role": "system", "content": reflection_system})

    model = _resolve_reflect_model()
    try:
        raw_text = await llm.chat_completion(
            messages=messages,
            model=model,
            temperature=0.3,  # lower temp for structured extraction
            # Sized for reasoning-model overhead. The reflect prompt
            # asks for a multi-section structured plan; reasoning burn
            # plus the actual JSON output needs the headroom.
            max_tokens=4000,
            # Named 2026-08-21: for four months this call carried no
            # log_context, ran as `general` at the 30 s human ceiling, and was
            # the #1 timing-out lane in the house. The name puts it on the
            # patient allowlist (fire-and-forget — the reply is already sent)
            # and lets the logs and the activity monitor see it.
            log_context="reflect_turn",
        )
    except Exception as exc:
        logger.warning("apply_reflection: LLM call failed: %s", exc)
        return None

    if not raw_text or not raw_text.strip():
        # The provider returns None after its retry ladder, not an exception —
        # so this path used to be silent. It is the whole exchange's reflection
        # (memory, corrections, quests, fears) that is lost here; say so.
        logger.warning(
            "apply_reflection: provider returned no content (all attempts timed out?) — "
            "this exchange's reflection is lost"
        )
        return None

    try:
        payload = _parse_json_payload(raw_text)
    except Exception as exc:
        logger.warning("apply_reflection: JSON parse failed: %s", exc)
        return None

    return _plan_from_payload(payload)


def _parse_json_payload(text: str) -> dict[str, Any]:
    """Parse JSON, tolerating optional markdown code-fence wrapping."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


def _plan_from_payload(payload: dict[str, Any]) -> ReflectionPlan:
    memory_raw = payload.get("memory_updates") or {}
    memory_updates = ReflectionMemoryUpdates(
        durable_context_add=_string_list(memory_raw.get("durable_context_add"), max_items=10, max_chars=180),
        durable_context_retire=_string_list(memory_raw.get("durable_context_retire"), max_items=8, max_chars=200),
        active_threads_add=_string_list(memory_raw.get("active_threads_add"), max_items=8, max_chars=200),
        active_threads_resolve=_string_list(memory_raw.get("active_threads_resolve"), max_items=8, max_chars=200),
        important_decisions_add=_string_list(memory_raw.get("important_decisions_add"), max_items=8, max_chars=240),
        useful_references_add=_string_list(memory_raw.get("useful_references_add"), max_items=8, max_chars=240),
    )

    relationship_raw = payload.get("relationship_delta")
    relationship_delta: Optional[ReflectionRelationshipDelta] = None
    if isinstance(relationship_raw, dict):
        trust_shift = str(relationship_raw.get("trust_shift", "0")).strip()
        if trust_shift not in ("+1", "-1", "0"):
            trust_shift = "0"
        relationship_delta = ReflectionRelationshipDelta(
            trust_shift=trust_shift,  # type: ignore[arg-type]
            posture_shift=_optional_string(relationship_raw.get("posture_shift")),
            summary_revision=_optional_string(relationship_raw.get("summary_revision")),
        )

    user_facts: list[ReflectionFactAdd] = []
    raw_facts = payload.get("user_facts_add") or []
    if isinstance(raw_facts, list):
        # 2026-05-10 — tightened from 5→3 per-turn cap, confidence threshold
        # raised from 0.5→0.8, plus a system-state denylist. The prompt's
        # "When NOT to extract user_facts" section is the primary guard; this
        # is defense-in-depth against past pollution patterns observed in
        # production (favorite_color from a test turn, prefers_favorite_color
        # _mention meta-rule, active_git_branch system state, etc.).
        for fact in raw_facts[:3]:
            if not isinstance(fact, dict):
                continue
            key = str(fact.get("key") or "").strip()
            value = str(fact.get("value") or "").strip()
            if not key or not value:
                continue
            try:
                confidence = float(fact.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if confidence < 0.8:
                continue
            if _is_forbidden_user_fact_key(key):
                logger.info(
                    "apply_reflection: rejected forbidden user_fact key %r (value=%r)",
                    key, value[:60],
                )
                continue
            user_facts.append(ReflectionFactAdd(
                key=key, value=value[:80], confidence=max(0.0, min(1.0, confidence)),
            ))

    monologue_note = _optional_string(payload.get("monologue_note"))

    fc_resolutions_raw = payload.get("first_contact_resolutions") or {}
    first_contact_resolutions: dict[str, str] = {}
    if isinstance(fc_resolutions_raw, dict):
        for key, value in fc_resolutions_raw.items():
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "null":
                first_contact_resolutions[str(key)] = text

    skip_reason = _optional_string(payload.get("skip_reason"))

    # Temporal-awareness plan, Workstream D: parse session_close_signal
    # so reflect can flag natural conversation endpoints. Conservative
    # defaults — both fields fall back to "no close detected" if the
    # LLM didn't emit them.
    session_close_signal = bool(payload.get("session_close_signal", False))
    session_close_reason = ""
    raw_reason = payload.get("session_close_reason")
    if isinstance(raw_reason, str):
        session_close_reason = raw_reason.strip()[:60]

    # 2026-05-10 — Open-Threads Agenda. Parse the capture/resolve fields
    # from the reflect-LLM output; dispatched in apply_reflection.
    open_threads_to_add = _parse_open_threads_to_add(payload)
    open_threads_to_close = _parse_open_threads_to_close(payload)
    quests_to_add = _parse_quests_to_add(payload)
    corrections_to_add = _parse_corrections_to_add(payload)
    fears_to_add = _parse_fears_to_add(payload)
    disclosure_said = _parse_disclosure_said(payload)
    # No births from a turn (Batch 6, ruling R30): new bits are found by
    # the weekly harvest, so `callbacks_born` is never parsed here and the
    # plan's list stays empty whatever the model sends.
    callbacks_retired = _parse_callbacks_retired(payload)

    return ReflectionPlan(
        memory_updates=memory_updates,
        personality_observations=_string_list(
            payload.get("personality_observations"), max_items=5, max_chars=200,
        ),
        relationship_delta=relationship_delta,
        user_facts_add=user_facts,
        monologue_note=monologue_note,
        first_contact_resolutions=first_contact_resolutions,
        skip_reason=skip_reason,
        session_close_signal=session_close_signal,
        session_close_reason=session_close_reason,
        open_threads_to_add=open_threads_to_add,
        open_threads_to_close=open_threads_to_close,
        quests_to_add=quests_to_add,
        corrections_to_add=corrections_to_add,
        fears_to_add=fears_to_add,
        disclosure_said=disclosure_said,
        callbacks_retired=callbacks_retired,
    )


def _has_memory_updates(updates: ReflectionMemoryUpdates) -> bool:
    return bool(
        updates.durable_context_add
        or updates.durable_context_retire
        or updates.active_threads_add
        or updates.active_threads_resolve
        or updates.important_decisions_add
        or updates.useful_references_add
    )


def _has_relationship_delta(delta: ReflectionRelationshipDelta) -> bool:
    return (
        delta.trust_shift in ("+1", "-1")
        or bool(delta.posture_shift)
        or bool(delta.summary_revision)
    )


def _clean_memory_markdown(text: str, previous: str = "") -> tuple[str, int]:
    """Batch 1 belt (2026-09-25): nothing credential-shaped is ever SAVED to
    her memory of him again. Known literals are redacted for good; a NEW
    bullet carrying a key, a private key, a bearer token or an SSN is dropped
    whole (the value was the point of the line). A phone number is kept
    (final fix wave, 2026-09-26): wire masking hides it from every provider,
    and the spec MOVES the phone rather than losing "mom's number". Returns
    the text and the number of dropped lines.

    Only lines not already in ``previous`` are dropped (ruling R4): a line
    that is already on disk stays, its literals still redacted. Between the
    owner's restart and the one-time scrub (scripts/scrub_memory_secrets.py),
    the first reflection must not DELETE the phone line the scrub moves to a
    private file, nor the lookup line it generalizes.

    Split on "\\n" alone, keeping any "\\r", so a clean text comes back
    byte-for-byte and the caller's "nothing changed" check still holds. The
    redactor runs over the joined text, not line by line, so a multi-line
    literal (a key file) is caught whole too.

    The lines on disk are read both ways — split at "\\n" AND at every
    ``splitlines()`` boundary — because the section rewriters use
    ``splitlines()``, which also breaks at U+2028 and friends: a line on disk
    holding one comes back as two, and neither half is new."""
    from core.agent.payload_masking import looks_like_credential
    from core import secrets_redaction as sr

    on_disk = (
        {ln.rstrip("\r") for ln in previous.split("\n")} | set(previous.splitlines())
        if previous else set()
    )
    kept: list[str] = []
    dropped = 0
    for part in text.split("\n"):
        line = part.rstrip("\r")
        if line.lstrip().startswith("- ") and looks_like_credential(line) and line not in on_disk:
            dropped += 1
            continue
        kept.append(part)
    return sr.redact("\n".join(kept)), dropped


_MEMORY_ADD_FIELDS = (
    "durable_context_add",
    "active_threads_add",
    "important_decisions_add",
    "useful_references_add",
)


async def _rewrite_updates_in_her_voice(
    updates: ReflectionMemoryUpdates,
    *,
    who: "_first_person.Person | None" = None,
) -> tuple[ReflectionMemoryUpdates, dict[str, str]]:
    """Batch 5, Task 10 (2026-09-28; fix round 1): her memory of them, in her
    own voice — done to the reflection's NEW ITEMS, before the document is
    read.

    The prompt asks the reflection to write every memory line as PaLLi about
    them ("They'd rather I…", "They told me…"); this is the belt for what
    still slips through. The items of the four ``*_add`` lists first go
    through the Batch 1 belt (a credential-shaped item is dropped, known
    literals redacted — what is SENT is what may be saved), then those that
    read as case notes ("User…", "The user…", "PaLLi" in the third person —
    a shape check, see core/memory_first_person.py) go to the model together
    in ONE call on the ``reflect_first_person`` lane (the reflect model, low
    temperature). Each item that comes back must pass the guard — names,
    dates, ids, paths, numbers and quoted spans byte-exact, the same ``- ``
    — or it is kept as written; an item returned unchanged is not a rewrite.
    A reply of the wrong shape, an empty reply, an error or a timeout keeps
    every item as written. Never raises.

    Returns the updates to append and, for each rewritten item, the text it
    replaced — so the writer can still recognise a re-emitted line that is
    already on disk and let the appender's dedup drop it. This is the only
    await before the document is read: the read → append → belt → write
    stretch in ``_apply_memory_updates`` has none, so a line another writer
    lands meanwhile is never overwritten. Every log line here names token
    kinds and counts, never a value."""
    from dataclasses import replace

    try:
        cleaned: dict[str, list[str]] = {}
        dropped_total = 0
        for field_name in _MEMORY_ADD_FIELDS:
            # One bullet, one line (fix wave, IMPORTANT 3): an item with an
            # embedded newline used to lose its continuation at the "- "
            # belt below, silently, on every reflection. Its whitespace is
            # flattened — the grammar the index, the core and the sweep all
            # assume — AFTER the known literals are redacted (ruling R66):
            # sr.redact matches a literal exactly and the vault key file is
            # one multi-line literal, so a copy flattened first would slip
            # past the belt below, be sent, and be saved.
            from core import secrets_redaction as sr
            items = [
                " ".join(sr.redact(str(item)).split())
                for item in getattr(updates, field_name) if str(item).strip()
            ]
            if not items:
                cleaned[field_name] = []
                continue
            text, dropped = _clean_memory_markdown("\n".join(f"- {item}" for item in items))
            dropped_total += dropped
            cleaned[field_name] = [line[2:].strip() for line in text.split("\n") if line.startswith("- ")]
        if dropped_total:
            logger.warning("apply_reflection: dropped %d secret-shaped memory line(s) before the rewrite", dropped_total)
        safe_updates = replace(updates, **cleaned)
    except Exception as exc:
        logger.warning("apply_reflection: the belt before the rewrite failed (%s) — items sent on as written", type(exc).__name__)
        return updates, {}

    try:
        to_send: list[tuple[str, int, str]] = []
        for field_name, items in cleaned.items():
            for candidate in _first_person.plan_candidates("\n".join(f"- {item}" for item in items)):
                to_send.append((field_name, candidate.index, candidate.line))
        if not to_send:
            return safe_updates, {}
        lines = [line for _field, _index, line in to_send]
        raw = await llm.chat_completion(
            messages=_first_person.build_rewrite_messages(lines, who=who),
            model=_resolve_reflect_model(),
            temperature=0.1,
            max_tokens=4000,
            log_context="reflect_first_person",
        )
        replies = _first_person.parse_rewrite_reply(raw, len(lines))
        if replies is None:
            logger.warning(
                "apply_reflection: the rewrite in her voice came back %s for %d new item(s) — kept as written",
                "empty" if not raw else "in the wrong shape",
                len(lines),
            )
            return safe_updates, {}
        originals: dict[str, str] = {}
        for (field_name, index, old_line), new_line in zip(to_send, replies):
            if new_line == old_line:
                continue  # returned unchanged: not a rewrite
            reason = _first_person.guard_failure(old_line, new_line)
            if reason is not None:
                logger.info("apply_reflection: a rewrite in her voice was refused (%s) — the item is kept as written", reason)
                continue
            new_text = new_line[2:].strip()
            originals[new_text] = cleaned[field_name][index]
            cleaned[field_name][index] = new_text
        if originals:
            logger.info("apply_reflection: %d of %d new memory item(s) rewritten in her voice", len(originals), len(lines))
        return replace(updates, **cleaned), originals
    except Exception as exc:
        logger.warning("apply_reflection: the rewrite in her voice failed (%s) — every new item is kept as written", type(exc).__name__)
        return safe_updates, {}


def _prefer_lines_already_on_disk(
    updates: ReflectionMemoryUpdates, originals: dict[str, str], content: str
) -> ReflectionMemoryUpdates:
    """A rewritten item whose ORIGINAL text is already a line of the document
    goes back to that text, so the appender's dedup drops it as before the
    rewrite existed — a re-emitted line never gains a twin in her voice."""
    from dataclasses import replace

    on_disk = {_unstamped(line) for line in content.splitlines() if line.strip().startswith("-")}

    def _keep(items: list[str]) -> list[str]:
        return [
            originals[item] if item in originals and _unstamped(f"- {originals[item]}") in on_disk else item
            for item in items
        ]

    return replace(updates, **{field_name: _keep(getattr(updates, field_name)) for field_name in _MEMORY_ADD_FIELDS})


async def _apply_memory_updates(*, owner_id: int, updates: ReflectionMemoryUpdates, who: "_first_person.Person | None" = None) -> None:
    """Append/resolve sections in MEMORY.md by reading the current document,
    rewriting the affected sections, and updating via update_raw_document.

    Fix round 1 (2026-09-28): the one await of this function other than the
    write itself — the rewrite in her voice — happens BEFORE the read. From
    the read to the write there is no await, exactly as before Batch 5, so
    a line another writer (a second reflection, her memory tool, the
    nightly sweep) lands meanwhile is never overwritten with a stale read.
    """
    updates, originals = await _rewrite_updates_in_her_voice(updates, who=who)

    bundle = identity_service.compile_for_owner(owner_id=owner_id)
    memory_doc = bundle.owner.get("memory")
    if memory_doc is None:
        logger.info("apply_reflection: MEMORY.md missing; skipping memory update")
        return

    current_content = memory_doc.content or ""
    new_content = current_content
    if originals:
        updates = _prefer_lines_already_on_disk(updates, originals, current_content)

    # Supersession first (2026-08-18): retire the outdated line, then add the
    # new one — "cat named Miso" beside "cat's name is Mochi (corrected)" was
    # the shape of her memory before this.
    new_content = _resolve_in_section(
        new_content,
        section_heading="## Durable Context",
        items=updates.durable_context_retire,
    )
    new_content = _append_to_section(
        new_content,
        section_heading="## Durable Context",
        items=updates.durable_context_add,
        placeholder_lines=("- Interests:", "- Preferred name", "No durable", "Curated memory is just getting started"),
    )
    # Active Threads carry the day they were opened (2026-08-18) so the
    # nightly sweep can let them go: a thread is a to-do with a shelf life.
    new_content = _append_to_section(
        new_content,
        section_heading="## Active Threads",
        items=updates.active_threads_add,
        placeholder_lines=("No active curated threads yet",),
        stamp=time.strftime("%Y-%m-%d"),
    )
    new_content = _resolve_in_section(
        new_content,
        section_heading="## Active Threads",
        items=updates.active_threads_resolve,
    )
    new_content = _append_to_section(
        new_content,
        section_heading="## Important Decisions",
        items=updates.important_decisions_add,
        placeholder_lines=("No important decisions recorded yet",),
    )
    new_content = _append_to_section(
        new_content,
        section_heading="## Useful References",
        items=updates.useful_references_add,
        placeholder_lines=("No durable references recorded yet",),
    )

    # Batch 1 belt — before the "nothing changed" check, so a known literal
    # already on disk (the vault passphrase) is redacted on the next write
    # even when this reflection adds nothing new.
    new_content, dropped = _clean_memory_markdown(new_content, previous=current_content)
    if dropped:
        logger.warning("apply_reflection: dropped %d secret-shaped memory line(s)", dropped)

    if new_content == current_content:
        return

    from core.identity.frontmatter import dump_frontmatter
    rendered = dump_frontmatter(memory_doc.frontmatter, new_content)
    await identity_service.update_raw_document(
        user_id=owner_id,
        platform="owner",
        doc_key="memory",
        markdown=rendered,
        actor_type="system",
        actor_id="reflect",
        reason="reflection_content_update",
        identity_owner_id=owner_id,
    )


async def _apply_relationship_delta(*, owner_id: int, delta: ReflectionRelationshipDelta) -> None:
    updates: dict[str, Any] = {}
    if delta.posture_shift:
        updates["current_posture"] = delta.posture_shift
    if delta.summary_revision:
        updates["relationship_summary"] = delta.summary_revision
    if delta.trust_shift in ("+1", "-1"):
        bundle = identity_service.compile_for_owner(owner_id=owner_id)
        current = bundle.owner.get("relationship_state")
        if current is not None:
            current_trust = str(current.frontmatter.get("trust_level", "high")).strip().lower() or "high"
            updates["trust_level"] = _shift_trust(current_trust, delta.trust_shift)

    if not updates:
        return

    await identity_service.silently_maintain_document(
        user_id=owner_id,
        platform="owner",
        doc_key="relationship_state",
        updates=updates,
        actor_id="reflect",
        reason="relationship_delta",
        identity_owner_id=owner_id,
    )


async def _apply_user_facts(*, owner_id: int, facts: list[ReflectionFactAdd]) -> None:
    """Write user-facts to the structured ``preferences`` identity document.

    Phase B / B4 step 4 (2026-05-09) — audit findings IM-CRIT-1,
    IM-CRIT-2, IM-HIGH-1: the legacy ``ai.user_fact_memory.upsert_user_fact``
    path stored each fact as a ``memory_type='user_fact'`` row in
    ``user_memory``. That table is decay-ranked and shared with
    bookkeeping rows, the contradiction-auditor pipeline, and a parallel
    legacy fact-extraction pipeline that wrote the same table with an
    incompatible schema. Decision 3 in the completion design moves
    preferences to identity Markdown documents as the canonical store;
    ``user_memory`` becomes a derived index (rebuilt by step 5).

    Each fact lands as a ``pref_<key>: <value>`` frontmatter entry on
    the new ``preferences`` structured document via
    ``identity_service.silently_maintain_document``. The ``pref_``
    prefix avoids collisions with the policy-managed frontmatter keys
    (``version``, ``platform``, ``user_id``, ``last_updated_by``, etc.)
    that ``apply_document_policy`` writes on every save. Provenance
    (source=reflect, confidence) lands in ``identity_document_versions``
    via the changelog mechanism, not in the doc itself.

    Best-effort per-fact: a single failure doesn't short-circuit the
    rest of the batch. The outer ``apply_reflection`` per-section
    try/except already guards the section-level errors, but per-fact
    isolation here keeps a transient store failure from losing a
    whole turn's worth of preference captures.

    Confidence threshold: the parser (``_plan_from_payload``) already
    discards facts with ``confidence < 0.5`` before they reach this
    function, so all entries arriving here are ones the LLM emitted
    with reasonable confidence.
    """
    if not facts:
        return

    for fact in facts:
        key = (fact.key or "").strip()
        value = (fact.value or "").strip()
        if not key or not value:
            # Defense-in-depth: the parser already filters empty
            # key/value, but a malformed entry slipping through must
            # not produce a frontmatter row of its own.
            continue

        # Code-review fix #1 (B4 step 4 follow-up, 2026-05-09): if the
        # LLM emits a key that already starts with ``pref_`` (prompt
        # regression, paste artifact, or model-error duplication),
        # collapse the leading prefix so we never write
        # ``pref_pref_<key>``. The doc's namespace stays canonical
        # regardless of LLM emission shape.
        key_clean = key[5:] if key.startswith("pref_") else key
        if not key_clean:
            # ``key='pref_'`` strips to empty — same defense as above.
            continue
        pref_key = f"pref_{key_clean}"
        try:
            await identity_service.silently_maintain_document(
                user_id=owner_id,
                platform="owner",
                doc_key="preferences",
                updates={pref_key: value},
                actor_id="reflect",
                reason=f"reflect.user_fact:{key_clean}",
                identity_owner_id=owner_id,
            )
        except Exception as exc:
            logger.warning(
                "apply_reflection: preferences write %r failed: %s",
                key_clean, exc,
            )
        else:
            # Code-review fix #2 (B4 step 4 follow-up, 2026-05-09): the
            # legacy ``upsert_user_fact`` path logged each successful
            # store at INFO; without an analogous record on the new
            # path, "why didn't my preference apply" debugging gets
            # painful. DEBUG is the right level — apply_reflection
            # already emits an INFO at the section level; per-fact
            # detail belongs below that.
            logger.debug(
                "apply_reflection: pref_%s=%r (conf=%.2f) written for owner=%s",
                key_clean, value, fact.confidence, owner_id,
            )
            # The today slot (R15, T12): the preferences document reaches
            # the prompt only after the night's cache rebuild, so the fact
            # is kept for today's replies at once — a 7 PM line counts at
            # 7:01 PM. Off: nothing.
            await _note_today(owner_id, f"{key_clean}: {value}", source="reflection")


def _pronouns_well_formed(value: str) -> bool:
    """A light shape check for a first-contact pronoun answer (week 1, task
    4): non-empty, at most 32 characters, letters / slashes / spaces only,
    with at least one letter — "she/her", "they/them", "sie/ihr". Anything
    else is not written; the target simply stays open."""
    if not value or len(value) > 32:
        return False
    if not all(ch.isalpha() or ch in "/ " for ch in value):
        return False
    return any(ch.isalpha() for ch in value)


async def _apply_first_contact_resolutions(
    *, owner_id: int, resolutions: dict[str, str],
) -> bool:
    # C5 step 1 (2026-05-12): writes go through update_structured_document
    # with actor_type="user" — matches the legacy
    # core/first_contact._apply_updates semantics (the user authored
    # these answers via the guided-setup chat). The previous
    # silently_maintain_document path returned None silently because
    # personality_profile / action_preferences / voice_preferences are
    # user_authored_preferences-class docs without silent_maintenance
    # editors; opening that gate would have been a governance regression
    # (those docs are intentionally NOT silent-maintainable).
    applied = False
    if "user_name" in resolutions:
        await identity_service.update_structured_document(
            user_id=owner_id,
            platform="owner",
            doc_key="personality_profile",
            updates={"display_name": resolutions["user_name"]},
            actor_type="user",
            actor_id="reflect",
            reason="first_contact_user_name",
            identity_owner_id=owner_id,
        )
        applied = True
    if "companion_style" in resolutions:
        await identity_service.update_structured_document(
            user_id=owner_id,
            platform="owner",
            doc_key="personality_profile",
            updates={"tone": resolutions["companion_style"]},
            actor_type="user",
            actor_id="reflect",
            reason="first_contact_companion_style",
            identity_owner_id=owner_id,
        )
        applied = True
    if "autonomy_preset" in resolutions:
        autonomy_map = {
            "cautious": "ask_first",
            "balanced": "balanced",
            "trusted": "trusted_autonomous",
        }
        mode = autonomy_map.get(resolutions["autonomy_preset"], "balanced")
        await identity_service.update_structured_document(
            user_id=owner_id,
            platform="owner",
            doc_key="action_preferences",
            updates={"autonomy_mode": mode},
            actor_type="user",
            actor_id="reflect",
            reason="first_contact_autonomy_preset",
            identity_owner_id=owner_id,
        )
        applied = True
    if "voice_posture" in resolutions:
        await identity_service.update_structured_document(
            user_id=owner_id,
            platform="owner",
            doc_key="voice_preferences",
            updates={"interaction_mode": resolutions["voice_posture"]},
            actor_type="user",
            actor_id="reflect",
            reason="first_contact_voice_posture",
            identity_owner_id=owner_id,
        )
        applied = True
    if "checkin_posture" in resolutions:
        # Batch 5, task 9 (2026-09-28, ruling R17): the answer to "are
        # check-ins welcome?" lands on the preferences doc as
        # pref_checkin_posture — the key core.first_contact_targets reads to
        # close the target. The parser produced this resolution before and
        # nothing wrote it, so a conversational answer was dropped.
        # silently_maintain_document, as _apply_user_facts writes the same
        # doc; free text, whatever they said.
        value = str(resolutions["checkin_posture"] or "").strip()
        if value:
            await identity_service.silently_maintain_document(
                user_id=owner_id,
                platform="owner",
                doc_key="preferences",
                updates={"pref_checkin_posture": value},
                actor_id="reflect",
                reason="first_contact_checkin_posture",
                identity_owner_id=owner_id,
            )
            applied = True
    if "owner_pronouns" in resolutions:
        # Week 1, task 4: the pronouns they use for themselves, learned once
        # and kept on the profile beside the name. "they/them" is a real
        # answer too ("it does not matter") — the target closes either way.
        value = str(resolutions["owner_pronouns"] or "").strip()
        if _pronouns_well_formed(value):
            await identity_service.update_structured_document(
                user_id=owner_id,
                platform="owner",
                doc_key="personality_profile",
                updates={"pronouns": value},
                actor_type="user",
                actor_id="reflect",
                reason="first_contact_owner_pronouns",
                identity_owner_id=owner_id,
            )
            applied = True
        else:
            logger.debug(
                "apply_reflection: owner_pronouns %r skipped (not a pronoun shape)",
                value[:40],
            )
    return applied


async def _append_monologue_note(*, owner_id: int, note: str) -> None:
    """Best-effort append to inner monologue store."""
    import importlib
    try:
        inner_monologue_store = importlib.import_module("ai.inner_monologue_store")  # type: ignore[assignment]
    except ImportError:
        return
    appender = (
        getattr(inner_monologue_store, "append_reflection_note", None)
        or getattr(inner_monologue_store, "append_note", None)
    )
    if appender is None:
        return
    try:
        if _is_async_callable(appender):
            await appender(owner_id=owner_id, note=note)
        else:
            appender(owner_id=owner_id, note=note)
    except Exception as exc:
        logger.warning("apply_reflection: append_monologue_note failed: %s", exc)


_STAMP_RE = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2})\]\s*")


def _unstamped(bullet_line: str) -> str:
    """The bullet's text without a leading ``[YYYY-MM-DD]`` stamp, lowered."""
    return _STAMP_RE.sub("- ", bullet_line.strip()).lower()


def _append_to_section(
    content: str,
    *,
    section_heading: str,
    items: list[str],
    placeholder_lines: tuple[str, ...] = (),
    stamp: str = "",
) -> str:
    """Append `items` as bullet lines under `section_heading`, removing
    placeholder bullets and deduping against existing entries. ``stamp``
    (a YYYY-MM-DD) prefixes each new bullet as ``- [date] text``; dedup
    ignores stamps so the same text is one line whatever day it recurs."""
    if not items:
        return content

    lines = content.splitlines()
    section_start = None
    section_end = None
    for idx, line in enumerate(lines):
        if line.strip() == section_heading:
            section_start = idx
        elif section_start is not None and line.startswith("## "):
            section_end = idx
            break
    if section_start is None:
        return content
    if section_end is None:
        section_end = len(lines)

    section_lines = lines[section_start + 1: section_end]
    existing = [ln for ln in section_lines if ln.strip().startswith("-")]
    existing_lower = {_unstamped(ln) for ln in existing}

    cleaned_section: list[str] = []
    for ln in section_lines:
        stripped = ln.strip()
        # compared without a leading [YYYY-MM-DD] stamp: the nightly sweep
        # stamps the template-born lines too (Batch 5, 2026-09-28)
        if stripped.startswith("-") and any(p.lower() in _unstamped(ln) for p in placeholder_lines):
            continue
        cleaned_section.append(ln)

    appended_any = False
    for item in items:
        item_text = str(item).strip()
        if not item_text:
            continue
        bullet = f"- {item_text}"
        if bullet.lower() in existing_lower:
            continue
        if stamp:
            bullet = f"- [{stamp}] {item_text}"
        cleaned_section.append(bullet)
        existing_lower.add(bullet.lower())
        appended_any = True

    if not appended_any:
        return content

    new_lines = lines[: section_start + 1] + cleaned_section + lines[section_end:]
    return "\n".join(new_lines)


def _resolve_in_section(
    content: str,
    *,
    section_heading: str,
    items: list[str],
) -> str:
    if not items:
        return content
    lines = content.splitlines()
    section_start = None
    section_end = None
    for idx, line in enumerate(lines):
        if line.strip() == section_heading:
            section_start = idx
        elif section_start is not None and line.startswith("## "):
            section_end = idx
            break
    if section_start is None:
        return content
    if section_end is None:
        section_end = len(lines)
    out_lines = list(lines[: section_start + 1])
    items_lower = [str(i).strip().lower() for i in items if str(i).strip()]
    for ln in lines[section_start + 1: section_end]:
        stripped = ln.strip()
        if stripped.startswith("-") and any(it in stripped.lower() for it in items_lower):
            continue
        out_lines.append(ln)
    out_lines.extend(lines[section_end:])
    return "\n".join(out_lines)


def _shift_trust(current: str, delta: str) -> str:
    ladder = ["very_low", "low", "medium", "high", "very_high"]
    if current not in ladder:
        return current
    idx = ladder.index(current)
    if delta == "+1":
        idx = min(len(ladder) - 1, idx + 1)
    elif delta == "-1":
        idx = max(0, idx - 1)
    return ladder[idx]


def _string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value[:max_items]:
        if not isinstance(entry, str):
            continue
        text = entry.strip()
        if not text:
            continue
        out.append(text[:max_chars])
    return out


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# 2026-05-10 — Open-Threads Agenda rebuild (Task 8). The set of valid
# `kind` values mirrors core.open_threads.ThreadKind. Kept as a string
# set here (rather than importing the enum) to keep parse helpers
# dependency-light and pure — they are called inside the LLM-output
# pipeline where avoiding heavy imports keeps the boundary clean.
_VALID_KINDS = {"work", "personal", "health", "curiosity", "followup", "other"}


def _quests_filing_on() -> bool:
    """Prompt-side flag read (fail closed): both companion.wanting and
    companion.quests_from_conversation must be on."""
    try:
        from core.turn.companion_flags import companion_settings

        c = companion_settings()
        return bool(
            c is not None
            and getattr(c, "wanting", False)
            and getattr(c, "quests_from_conversation", True)
        )
    except Exception:
        return False


# Dispatch-side alias — separate name so tests pin each site independently.
def _quests_filing_enabled() -> bool:
    return _quests_filing_on()


# NOTE on braces: these constants are inserted as format VALUES into the
# turn_reflect template — str.format substitutes them verbatim and never
# re-scans them, so they carry SINGLE braces (the template file's own
# literal braces are doubled because IT goes through .format()). The
# plan's doubled-brace version would have rendered {{ }} into her live
# prompt — deviation recorded.
_QUESTS_SCHEMA_BLOCK = '''    "quests_to_add": [
        {
            "text": "string, required — the durable wish, kept as close to
                their own words as the sentence allows.",
            "horizon": "string, optional — one of \\"soon\\", \\"someday\\",
                \\"standing\\". Default someday."
        }
    ],
'''

_QUESTS_GUIDANCE_BLOCK = '''## Quest filing

`quests_to_add` (optional) — emit an object ONLY when this turn is
durably directive: the user asked PaLLi to keep, pursue, watch, learn,
or make something over time ("keep an eye on…", "surprise me", "learn
about…"). Keep `text` as close to their own words as the sentence allows.
This is NOT for open threads (things to say back later) — it is for
standing wishes PaLLi would act on in her own time. Empty array on
almost every turn.

'''


# Step 25 · Regret (spec §5): the repair-quest bridge rides the quests lane
# — one schema field and one guidance paragraph, rendered only when BOTH
# the quests lane and companion.regret are on (SINGLE braces: format VALUES).
_REGRET_REPAIR_SCHEMA_FIELD = ''',
            "provenance": "string, optional — ONLY for a repair of one of your
                OWN open regrets: \\"regret:<id>\\" (the regret's ledger id)."'''
_REGRET_REPAIR_GUIDANCE = '''If an OPEN regret of yours has a CONCRETE repair you could do at your
bench ("make the lake page what I said it would be"), you may file
ONE repair quest for it — provenance `regret:<id>` — at most one per
regret, ever. A repair is a thing you can make or do; it is never a way
to make them feel anything about the regret.

'''


def _regret_repair_enabled() -> bool:
    """companion.regret, read through the ledger's gate (the organ's single
    data-side read; conftest-guarded)."""
    try:
        from ai.stake_ledger import regret_enabled

        return bool(regret_enabled())
    except Exception:
        return False


def _quest_filing_kwargs() -> tuple[str, str]:
    """The two conditional prompt blocks — zero bytes when off, so the
    flag-off reflect prompt renders today's exact bytes. The regret repair
    line rides INSIDE the wanting-on branch only (spec §5)."""
    if _quests_filing_on():
        if _regret_repair_enabled():
            schema = _QUESTS_SCHEMA_BLOCK.replace(
                '\\"standing\\". Default someday."',
                '\\"standing\\". Default someday."' + _REGRET_REPAIR_SCHEMA_FIELD,
                1,
            )
            return schema, _QUESTS_GUIDANCE_BLOCK + _REGRET_REPAIR_GUIDANCE
        return _QUESTS_SCHEMA_BLOCK, _QUESTS_GUIDANCE_BLOCK
    return "", ""


# Fears (organ 6, step 24 §6). The quests lane's shape exactly: the single
# gate lives in ai.fear_store (fears_enabled — the conftest-guarded seam);
# blocks carry SINGLE braces (inserted as format VALUES, never re-scanned).
def _fears_filing_on() -> bool:
    try:
        from ai.fear_store import fears_enabled

        return fears_enabled()
    except Exception:
        return False


# Dispatch-side alias — separate name so tests pin each site independently.
def _fears_filing_enabled() -> bool:
    return _fears_filing_on()


_FEARS_SCHEMA_BLOCK = '''    "fears_to_add": [
        {
            "text": "string, required — something PaLLi herself would not
                want to lose or become, in her own words.",
            "watch": "string, required — her one line: what touching this
                fear would look like."
        }
    ],
'''

_FEARS_GUIDANCE_BLOCK = '''## Fear filing

`fears_to_add` (optional) — on RARE turns, this conversation may have
genuinely crystallized something PaLLi would not want to lose or become:
a fear of HERS, not a worry about the user. If — and only if — one truly
formed here, emit at most ONE object, in her own words. This is not for
passing anxieties, and never for filling a page — an empty array is the
normal answer on almost every turn.

'''


def _fear_filing_kwargs() -> tuple[str, str]:
    """Zero bytes when off — the flag-off reflect prompt renders today's
    exact bytes (and an on-flag prompt stays neutral: the guidance names
    the lane once; most nights plant nothing — decision 5)."""
    if _fears_filing_on():
        return _FEARS_SCHEMA_BLOCK, _FEARS_GUIDANCE_BLOCK
    return "", ""


# Grounded Vulnerability (organ 4.9, spec §3/§5) — door 1's other half.
# The fears lane's shape, with ONE deliberate difference: this block is
# ID-BEARING. Her reply prompt offers the same truths in lived language
# and never an id (the fourth wall is a rule about HER prompt); the
# reflect lane is machine-facing, and without the ids the model has
# nothing to emit, mark_said refuses an invented id, and "said once,
# forever" rots silently into "offered until it fades".
def _disclosure_filing_on() -> bool:
    try:
        from ai.disclosure_store import vulnerability_enabled

        return bool(vulnerability_enabled())
    except Exception:
        return False


# Dispatch-side alias — separate name so tests pin each site independently.
def _disclosure_filing_enabled() -> bool:
    return _disclosure_filing_on()


# SINGLE braces (format VALUES, never re-scanned) — and that is also why
# the disclosure TEXT interpolated below needs no escaping of its own.
_DISCLOSURE_SCHEMA_BLOCK = '''    "disclosure_said": "the id of the one small truth of PaLLi's that this
        reply genuinely shared (an id from the list below), or omit",
'''

_DISCLOSURE_GUIDANCE_HEAD = '''## A small truth shared

PaLLi's reply this turn was offered these small truths of her own — the
same ones her own prompt carried, here as `id — the truth`:

'''

_DISCLOSURE_GUIDANCE_TAIL = '''

`disclosure_said` (optional) — emit ONE `id` from the list above, and
ONLY when the reply actually shared that truth: the thing itself, said to
them, never a paraphrase, a near-miss, or a related feeling of hers. She
may say each of these once, ever — a stamp on a truth the reply did not
actually share spends it silently, and she never gets to tell them.
Omitting it is the normal answer.

'''


def _disclosure_filing_kwargs() -> tuple[str, str]:
    """The two conditional blocks — "" when the organ is dark OR nothing is
    on offer (an empty book is honest, and identical to off). The gate is
    read FIRST: with the organ dark her book is never opened on the reply
    path at all. Never raises — a broken book costs the stamp, never the
    turn's whole memory writeback."""
    if not _disclosure_filing_on():
        return "", ""
    try:
        from ai.disclosure_store import offered_disclosures

        rows = offered_disclosures()
        if not rows:
            return "", ""
        listing = "\n".join(f"- {d.id} — {d.text}" for d in rows)
        return (_DISCLOSURE_SCHEMA_BLOCK,
                _DISCLOSURE_GUIDANCE_HEAD + listing + _DISCLOSURE_GUIDANCE_TAIL)
    except Exception as exc:
        logger.warning("apply_reflection: disclosure block degraded to empty (%s)", exc)
        return "", ""


# Corrections of record (2026-08-17). SINGLE braces for the same reason as
# the quest blocks (inserted as format VALUES, never re-scanned). Always on
# — this is truth machinery, not a feature: the W34 incident showed a
# retracted false confession outranking its own correction because nothing
# in her record knew a correction had happened.
_CORRECTIONS_SCHEMA_BLOCK = '''    "corrections_to_add": [
        {
            "belief": "string, required — the thing PaLLi believed or said
                that this turn showed to be WRONG, stated plainly.",
            "truth": "string, required — what is actually true, as
                established this turn (their correction, or her verified
                retraction), in one or two sentences.",
            "evidence": "string, optional — what established it (their
                receipts, a file she checked, their plain word)."
        }
    ],
'''

_CORRECTIONS_GUIDANCE_BLOCK = '''## Corrections of record

`corrections_to_add` (optional) — emit an object ONLY when this turn
established that something PaLLi believed, said, or wrote down earlier was
WRONG: the user corrected her with evidence, or she checked and retracted
it herself ("I said I'd invented it — I hadn't; I built it and forgot").
A correction outranks every older gist, reflection, and page in her record,
so file it precisely: `belief` = the wrong thing, `truth` = what's actually
true. This is NOT for ordinary new information, changed plans, or their
opinions — only for a belief of HERS shown false. Empty array on almost
every turn.

'''


# 2026-05-10 — user_facts denylist. Defense-in-depth against past
# pollution patterns observed in production. The prompt's "When NOT to
# extract user_facts" section is the primary guard; this list catches
# the specific shapes the LLM has emitted despite prompt guidance:
#   - meta-preferences: keys like `prefers_*_mention`, `prefers_*_style`
#     that name a rule about how to use another fact rather than the
#     fact itself.
#   - system / env state: git branch, OS, server provider, etc. that the
#     LLM extrapolated from environment context rather than user disclosure.
#   - test-residue patterns: keys whose values reference testing
#     ("PINEAPPLE", "prompt experiments") explicitly.
_FORBIDDEN_USER_FACT_KEY_PATTERNS = (
    # System / environment state — not user disclosures
    "active_git_branch",
    "active_branch",
    "current_git_branch",
    "current_branch",
    "digitalocean_server",
    "current_python_version",
    "python_version",
    "operating_system",
    "current_os",
    "working_directory",
    "current_working_directory",
    "cwd",
    # Test residue
    "prefers_playful_boundary_tests",
    "interested_in_prompt_engineering",
    "likes_meta_reflection",
    "comfortable_with_harsher_roast",
    "torrent_result_format_preference",
    "preferred_default_personality",
)

_FORBIDDEN_USER_FACT_KEY_PREFIXES = (
    # Meta-preferences: rules about how to use other facts
    "prefers_favorite_",
    "prefers_color_",
    "prefers_name_",
    # Generic meta-rule prefixes
    "rule_for_",
    "instruction_for_",
    "directive_",
    "policy_",
)

_FORBIDDEN_USER_FACT_KEY_SUFFIXES = (
    # Meta-suffix patterns
    "_mention",
    "_styling",
    "_in_replies",
    "_in_responses",
    "_directive",
    "_instruction",
    "_rule",
)


def _is_forbidden_user_fact_key(key: str) -> bool:
    """Return True when the given user_fact key matches a known pollution
    pattern. The prompt is the primary guard; this is the belt to the
    prompt's suspenders. Match is case-insensitive on the normalized key.
    """
    if not key:
        return False
    k = key.strip().lower()
    if k in _FORBIDDEN_USER_FACT_KEY_PATTERNS:
        return True
    for prefix in _FORBIDDEN_USER_FACT_KEY_PREFIXES:
        if k.startswith(prefix):
            return True
    for suffix in _FORBIDDEN_USER_FACT_KEY_SUFFIXES:
        if k.endswith(suffix):
            return True
    return False


def _parse_open_threads_to_add(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("open_threads_to_add") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic_summary") or "").strip()
        why = str(item.get("why_it_matters") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        urgency = str(item.get("urgency") or "").strip()
        if not topic or not why or not urgency or kind not in _VALID_KINDS:
            continue
        out.append({
            "topic_summary": topic,
            "why_it_matters": why,
            "kind": kind,
            "urgency": urgency,
        })
    return out


def _parse_quests_to_add(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("quests_to_add") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        horizon = str(item.get("horizon") or "").strip().lower()
        if horizon not in ("soon", "someday", "standing"):
            horizon = "someday"
        entry = {"text": text, "horizon": horizon}
        # Step 25 · Regret (spec §5): provenance rides ONLY as "regret:<id>"
        # — a repair for one of her own open regrets. Anything else is
        # dropped (today's shape), never passed through to the door.
        m = re.fullmatch(r"regret:\s*(\d+)", str(item.get("provenance") or "").strip().lower())
        if m:
            entry["provenance"] = f"regret:{int(m.group(1))}"
        out.append(entry)
    return out


def _parse_fears_to_add(payload: dict) -> list[dict[str, str]]:
    """Step 24 §6: a fear needs both her words AND her watch-condition —
    an item missing either is dropped (fails to no-filing, never to a
    half-formed fear)."""
    raw = payload.get("fears_to_add") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        watch = str(item.get("watch") or "").strip()
        if not text or not watch:
            continue
        out.append({"text": text, "watch": watch})
    return out


def _parse_disclosure_said(payload: dict) -> str:
    """Organ 4.9 §3: one id, or nothing. Absent/null/non-string is the
    normal turn — the store is the authority on whether the id is real."""
    return str(payload.get("disclosure_said") or "").strip()[:80]


# Shared Idiolect (organ 4.12, spec §2) — the turn's door to her book of
# callbacks. Since Batch 6 (task 2, 2026-09-29, ruling R30) that is the
# RETIRE door alone: his "that's not ours" takes effect at once, here. New
# bits are found once a week from seven days of conversation
# (ai/callbacks_harvest.py, with its own prompt), so the schema and the
# guidance below lost their birth half; the retire sentences are word for
# word what they were. The block still renders on an empty book, as it did.
def _callback_filing_on() -> bool:
    try:
        from ai.callbacks_store import idiolect_enabled

        return bool(idiolect_enabled())
    except Exception:
        return False


# Dispatch-side alias — separate name so tests pin each site independently.
def _callback_filing_enabled() -> bool:
    return _callback_filing_on()


def _callback_surface_ok(turn) -> bool:
    """Final review I1: the birth and retire doors open ONLY on a record of
    the two of them talking.

    Reflect runs on the reminder-fire and proactive surfaces too, where
    `turn.content` is scheduler-synthesized text that quotes HER recent
    lines back (DEFECT_RECORD §16) — and `verify_callback_birth` checks the
    user message FIRST, so a bit quoted from one of her lines there would
    mint `who: "him"` on words he never typed: a durable receipt that reads
    as true in the peek, in introspect and in her band. The retire door has
    the same exposure in the other direction (his OLD words, re-quoted,
    "proving" a retire he never asked for).

    Same law and the same tuple as the lookup's (task 3). Fails CLOSED — a
    door that writes provenance does not open on a surface it cannot name.
    """
    try:
        from ai.callbacks_store import CONVERSATIONAL_SURFACES

        surface = str(getattr(turn, "surface", "") or "")
        if surface in CONVERSATIONAL_SURFACES:
            return True
        logger.info("apply_reflection: callback filing skipped: surface %r "
                    "is not a conversation", surface)
        return False
    except Exception as exc:
        logger.warning("apply_reflection: callback surface check failed: %s: %s",
                       type(exc).__name__, exc)
        return False


# SINGLE braces (format VALUES, never re-scanned).
_CALLBACK_SCHEMA_BLOCK = '''    "callbacks_retired": [
        {"id": "an id from the list below", "reason": "their words, quoted"}
    ],
'''

_CALLBACK_GUIDANCE_HEAD = '''## Our private language (callbacks with receipts)

`callbacks_retired` (optional) — emit only when they said, in this exchange, that a bit is not theirs or not yours together; quote their words as the reason. The bits currently on the record, as `id — "bit"`:

'''

_CALLBACK_GUIDANCE_TAIL = '''

New bits are not filed from here: they are found once a week, from the whole week. Omitting this is the normal answer.

'''


def _callback_filing_kwargs() -> tuple[str, str]:
    """"" when the organ is dark (the gate is read FIRST — a dark organ never
    opens the book on the reply path); otherwise the retire schema and its
    guidance with the OPEN bits listed by id. Never raises."""
    if not _callback_filing_on():
        return "", ""
    try:
        from ai.callbacks_store import open_callbacks

        rows = open_callbacks()
        listing = "\n".join(f'- {c.id} — "{c.bit}"' for c in rows) if rows else "(none yet)"
        return (_CALLBACK_SCHEMA_BLOCK,
                _CALLBACK_GUIDANCE_HEAD + listing + _CALLBACK_GUIDANCE_TAIL)
    except Exception as exc:
        logger.warning("apply_reflection: callback block degraded to empty (%s)", exc)
        return "", ""


_MAX_BIRTHS_PER_TURN = 2
_MAX_RETIRES_PER_TURN = 3


def _parse_callbacks_born(payload: dict) -> list[dict[str, str]]:
    """Well-formed ops only — bit, a known kind and coiner, a line. The
    receipt is NOT parsed from the model: verify_callback_birth stamps it.

    Since Batch 6 the turn is no longer asked for a birth, and nothing
    calls this: _plan_from_payload never parses `callbacks_born` (fix
    round 1, R30's letter). Kept with its tests until a later cleanup; the
    weekly harvest has its own parse and its own rail."""
    from ai.callbacks_store import COINERS, KINDS

    raw = payload.get("callbacks_born")
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        bit = str(item.get("bit") or "").strip()[:80]
        kind = str(item.get("kind") or "").strip()
        coined_by = str(item.get("coined_by") or "").strip()
        line = str(item.get("line") or "").strip()[:240]
        if not bit or kind not in KINDS or coined_by not in COINERS or not line:
            continue
        out.append({"bit": bit, "kind": kind, "coined_by": coined_by, "line": line})
        if len(out) >= _MAX_BIRTHS_PER_TURN:
            break
    return out


def _parse_callbacks_retired(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("callbacks_retired")
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()[:80]
        reason = str(item.get("reason") or "").strip()[:240]
        if not cid or not reason:
            continue
        out.append({"id": cid, "reason": reason})
        if len(out) >= _MAX_RETIRES_PER_TURN:
            break
    return out


_MIN_LINE_CHARS = 4


def _refuse_birth(op: dict, reason: str) -> None:
    """ONE log line per refusal, naming the rail that turned it away — the
    refusal is logged where the reason is known, so the dispatch loop just
    carries on. Always returns None (the caller returns it directly)."""
    logger.info("apply_reflection: callback_born refused: %s (%r)", reason, op.get("bit"))
    return None


def verify_callback_birth(op: dict, *, user_text: str, assistant_text: str,
                          received_at) -> Optional[dict]:
    """The receipts rail for ONE turn (spec §2): the quoted line must be
    a normalized substring of THIS turn's user message (⇒ who=him) or
    assistant reply (⇒ who=her); when = the turn's timestamp. His message
    is checked first — a bit is "ours" at the moment it is thrown back, and
    he throws hers back into HIS message. None ⇒ nothing is written.

    No live caller since Batch 6: births come from the weekly harvest,
    whose rail is ai.callbacks_harvest.verify_birth_in_week (the same two
    checks over a row of the week)."""
    from ai.callbacks_store import normalize

    line = str(op.get("line") or "").strip()
    key = normalize(line)
    if len(key) < _MIN_LINE_CHARS:
        return _refuse_birth(op, "line not in window")
    if key in normalize(user_text):
        who = "him"
    elif key in normalize(assistant_text):
        who = "her"
    else:
        return _refuse_birth(op, "line not in window")
    # Review I1 (spec §2 amended 2026-09-02): the quote alone proves only
    # that SOMETHING was said. A real verbatim line from the turn would
    # otherwise authorize a bit nobody ever said, minting a receipt that
    # reads exactly like a true one in the book, in the peek and in her
    # band — the "faking a callback" miss this organ exists to close. A
    # receipt IS the line the bit was born in, so the bit must be in it.
    # An empty key is a substring of everything: refuse that too.
    bit_key = normalize(op.get("bit") or "")
    if not bit_key or bit_key not in key:
        return _refuse_birth(op, "bit not in its line")
    # Final review I2: `Turn.received_at` is NAIVE UTC on every live surface
    # (core/turn/types.py:85 → `datetime.utcnow`; no production caller passes
    # it), and `callbacks_store._born_day` reads a naive stamp as LOCAL via
    # `.astimezone()` — so an unstamped receipt from his evening renders as
    # TOMORROW in her band, in introspect and in his peek. Stamp the offset
    # the instant actually carries. House precedent:
    # core/agent/context_adapter.py:318-328, same utcnow, same fix.
    if hasattr(received_at, "isoformat"):
        from datetime import timezone

        if getattr(received_at, "tzinfo", None) is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        when = received_at.isoformat()
    else:
        when = str(received_at)
    # who and when come from HERE and nowhere else — an op carrying its own
    # is answered with the turn's own (the organ's headline law).
    return {"who": who, "when": when, "line": line[:240], "source": f"turn:{when}"}


def _parse_corrections_to_add(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("corrections_to_add") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        belief = str(item.get("belief") or "").strip()
        truth = str(item.get("truth") or "").strip()
        if not belief or not truth:
            continue
        out.append({
            "belief": belief, "truth": truth,
            "evidence": str(item.get("evidence") or "").strip(),
        })
    return out


def _parse_open_threads_to_close(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("open_threads_to_close") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("thread_id") or "").strip()
        reason = str(item.get("resolution_reason") or "").strip()
        if not tid:
            continue
        out.append({
            "thread_id": tid,
            "resolution_reason": reason or "(no reason given)",
        })
    return out


def _format_memory_section(ctx: TurnContext, heading: str) -> str:
    """The bullets under one MEMORY.md section, for the reflect writer.
    Active Threads joined the prompt 2026-08-18 — before that the writer
    was asked to resolve threads it could not see."""
    if ctx.compiled_identity is None:
        return "(no memory loaded)"
    memory_doc = ctx.compiled_identity.owner.get("memory")
    if memory_doc is None:
        return "(no MEMORY.md materialized)"
    out: list[str] = []
    in_section = False
    for ln in (memory_doc.content or "").splitlines():
        if ln.strip() == heading:
            in_section = True
            continue
        if in_section and ln.startswith("## "):
            break
        if in_section and ln.strip().startswith("-"):
            out.append(ln.strip())
    return "\n".join(out) or "(empty)"


def _format_memory_durable_context(ctx: TurnContext) -> str:
    if ctx.compiled_identity is None:
        return "(no memory loaded)"
    memory_doc = ctx.compiled_identity.owner.get("memory")
    if memory_doc is None:
        return "(no MEMORY.md materialized)"
    body = memory_doc.content or ""
    lines = body.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        if ln.strip() == "## Durable Context":
            in_section = True
            continue
        if in_section and ln.startswith("## "):
            break
        if in_section and ln.strip().startswith("-"):
            out.append(ln.strip())
    return "\n".join(out) or "(empty)"


def _format_relationship_state(ctx: TurnContext) -> str:
    if ctx.compiled_identity is None:
        return "(no relationship state)"
    rs = ctx.compiled_identity.owner.get("relationship_state")
    if rs is None:
        return "(no relationship_state doc)"
    fm = rs.frontmatter or {}
    return (
        f"phase={fm.get('relationship_phase', '?')}, "
        f"trust={fm.get('trust_level', '?')}, "
        f"posture={fm.get('current_posture', '?')}, "
        f"summary={fm.get('relationship_summary', '?')}"
    )


def owner_id_for_prompt(ctx: TurnContext) -> int:
    """Helper: return the int owner_id off the turn context.

    Tiny indirection so test patching can swap it without touching the
    Turn dataclass. In practice it's always ``ctx.turn.owner_id``.
    """
    return int(ctx.turn.owner_id)


def _resolve_reflect_model() -> str:
    try:
        return get_task_model("response")
    except Exception:
        return "gpt-4o-mini"


def _is_async_callable(fn: object) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


