"""Present Moment tier-2 input assembly (spec §3).

Organ-owned on purpose: it reuses the same underlying services the
monologue's situation assembler reads, but does NOT call
build_situation_context — coupling the two engines is the Shape-2
entanglement the design rejected, and that assembler carries the dead
open-threads call this organ's sense-health exists to make impossible.
Every source is timeout-wrapped; a failed source becomes a named dark
sense in the metadata, never a silent absence.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Tuple

logger = logging.getLogger(__name__)

_SOURCE_TIMEOUT_S = 3.0


@dataclass
class GatherInputs:
    senses_block: str = "(no senses readable)"
    bases_block: str = "(no reference bases readable)"
    monologue_block: str = "(none)"
    reflection_block: str = "(none)"
    # The thread board digest (arbiter spec §3) — "" unless companion.arbiter
    # is on; only the arbiter prompt variant references it.
    threads_block: str = ""
    # The wants board (re-spec §5.2) — "" unless companion.wanting AND
    # companion.arbiter are on; only the wanting prompt variant reads it.
    wants_block: str = ""
    # The expectations block (organ 8, spec §2) — "" unless companion.surprise
    # is on; only the live prompt rungs reference it. Carries its own header
    # AND the surprise output contract; ends "\n\n" when present.
    expectations_block: str = ""
    # The fears block (organ 6, step 24 §3) — "" unless companion.fears is
    # on AND the book holds fears (the empty book is honest — decision 5);
    # only the live rungs reference it. Carries its own law AND the touch
    # contract; ends "\n\n" when present.
    fears_block: str = ""
    # Step 25 · Regret (spec §4): True when the organ is on AND an open
    # regret is on the page — the moment may name it in "regret_id". The
    # live rungs' two slots render zero bytes otherwise.
    regret_voicing: bool = False
    # Organ 4.13 · Opinions (spec §5): True when the organ is on AND an open,
    # UNMUTED view is on the page — the moment may name it in "view_id".
    view_voicing: bool = False
    # Organ 4.9 · Grounded Vulnerability (spec §4): her small-truths board —
    # "" unless the organ is on AND a truth is unsaid. Carries its own header
    # and ends "\n\n" when present (the fears slot-collapse shape). It rides
    # its OWN slot on the two live rungs, deliberately not bases_block: the
    # bases are the material her moment prose is written from, and a truth
    # that reached him through the prose (or a say's framing) without its id
    # would be shared, never stamped, and re-offered by door 1 until it
    # faded — the inverse of "once ever, forever".
    disclosures_block: str = ""
    # True when that board actually rendered — the moment may offer one in
    # its say and name it in "disclosure_id". Derived from the rendered
    # board, never probed separately, so the say line's pointer can never
    # name a board that is not in the prompt.
    disclosure_offer: bool = False
    sense_health: Dict[str, str] = field(default_factory=dict)


async def _safe(
    name: str, factory: Callable[[], Awaitable[str]], health: Dict[str, str]
) -> str:
    """Run one source; record ok/dark in health; never raise (except cancel)."""
    try:
        text = await asyncio.wait_for(factory(), timeout=_SOURCE_TIMEOUT_S)
        health[name] = "ok"
        return (text or "").strip()
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        health[name] = "timeout"
        return ""
    except Exception as exc:
        health[name] = " ".join(str(exc).split())[:120] or exc.__class__.__name__
        return ""


def _companion():
    """Companion settings block, or None when settings are unbooted."""
    try:
        from core.turn.companion_flags import companion_settings

        return companion_settings()
    except Exception:
        return None


def _recent_notes_line() -> str:
    """Her own recent notes, as RECENT NOTES WITH THEIR AGE — never as claims.

    The pen (roadmap step 10). Owner chose yes-with-a-toggle 2026-07-27
    (spec §5). The framing carries the whole mitigation: a note saying
    "I'm someone who X" must reach her present as *a thing she wrote down
    two days ago*, not as a fact about her — otherwise a deliberately
    self-written line can compound into belief with no evidence under it.

    Double-gated (the pen itself AND the moment toggle) and inert while
    the moment is shadow. ⚠️ Re-decide this at Slice 2, when the moment
    starts shaping her replies — see CHECKLIST step 11.
    """
    comp = _companion()
    if comp is None or not bool(getattr(comp, "self_notes", False)):
        return ""
    if not bool(getattr(comp, "self_notes_in_moment", True)):
        return ""
    try:
        from ai import self_notes_store as _notes_store

        entries = _notes_store.read_recent(days_back=3, limit=3)
    except Exception:
        return ""
    if not entries:
        return ""
    now = time.time()
    parts = []
    for e in entries:
        hours = max(0.0, (now - e.written_at) / 3600.0)
        when = "today" if hours < 12 else f"{int(round(hours / 24))}d ago"
        parts.append(f"you wrote down ({when}): {e.text[:160]}")
    return "WHAT I'VE WRITTEN DOWN LATELY:\n" + "\n".join(parts)


async def _notes() -> str:
    """Async wrapper so the notes line rides the standard _safe() source
    pattern (health-tracked, timeout-bounded, never raising)."""
    return _recent_notes_line()


def _own_work_line() -> str:
    """Her workbench, as SHE recorded it — the authorship trace.

    Her own work reaching her own present is the point of roadmap 16-17.
    Until 2026-08-14 this sense head-kept 600 bytes of an append-only
    logbook, so it showed her July-28 hello-world forever while her real
    hands built unseen — the three-doors inversion's quiet accomplice.
    It now renders the RECENT entries with authorship framing ("YOU did
    this"). Gated on companion.own_work; empty when off/quiet/missing
    (never an error).
    """
    comp = _companion()
    if comp is None or not bool(getattr(comp, "own_work", False)):
        return ""
    try:
        from ai.workbench_trace import build_authorship_trace

        return build_authorship_trace()
    except Exception:
        return ""


async def _own_work() -> str:
    """Async wrapper — the _notes() pattern exactly."""
    return _own_work_line()


async def _list_active_threads(*, owner_user_id: int):
    from core.database import brain

    return await brain.list_active_open_threads(owner_user_id=owner_user_id)


_THREADS_BUDGET_CHARS = 1800
_EMPTY_BOARD = "Nothing on your thread board."
# The legacy urgency keys, from before the writer spoke in free text —
# folded into _urgency_bin's three bins so an old row still sorts.
_URGENCY_ORDER = {"high": 0, "urgent": 0, "medium": 1, "normal": 1, "low": 2}


def _urgency_bin(text: object) -> int:
    """Three bins for the board's order (Batch 5, task 8, 2026-09-28,
    ruling R48). The writer records urgency as free text — "time-sensitive
    — same-day prescription pickup", "when-the-moment-fits", "no urgency —
    they're circling, not deciding" — and the old key knew only the legacy
    words, so every live row (227 of them) fell into the middle bin and the
    board was ordered by age alone. Case-insensitive on the stripped text:
    starts with "time-sensitive" → 0; "when-the-moment-fits" → 1; starts
    with "no urgency" → 2; a legacy key keeps its bin (high/urgent 0,
    low 2); anything else → 1. Meaning stays with the writer; this is
    presentation into bins (Law 3) — the digest still prints her words."""
    t = str(text or "").strip().lower()
    if t.startswith("time-sensitive"):
        return 0
    if t.startswith("when-the-moment-fits"):
        return 1
    if t.startswith("no urgency"):
        return 2
    return _URGENCY_ORDER.get(t, 1)


async def _threads(owner_user_id: int) -> str:
    """Her thread board, as a budgeted digest (arbiter spec §3). Ages are
    visible so HER judgment can set things down; ordering is deterministic
    presentation (urgency, then youngest) — the judgment stays hers. Only
    assembled when the arbiter is on: flag-off cycles must not even carry
    the health key (byte-identical-off)."""
    rows = await _list_active_threads(owner_user_id=owner_user_id)
    if not rows:
        return _EMPTY_BOARD
    now = time.time()
    rows = sorted(
        rows,
        key=lambda t: (
            _urgency_bin(t.urgency),
            -(float(t.created_at or 0.0)),
        ),
    )
    lines: list[str] = []
    used = 0
    for t in rows:
        held_d = max(0, int((now - float(t.created_at or now)) // 86400))
        kind = getattr(t.kind, "value", str(t.kind))
        line = (
            f"- [{str(t.id)[:8]}] [{kind} | {t.urgency} | open {held_d}d] "
            f"{str(t.topic_summary or '')[:140]}"
        )
        why = str(t.why_it_matters or "").strip()
        if why:
            line += f" — {why[:100]}"
        if used + len(line) + 1 > _THREADS_BUDGET_CHARS:
            lines.append(f"(+{len(rows) - len(lines)} more not shown)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


async def _tonight_line() -> str:
    """The night's unsaid pick as one board line (ai/tonight.py); "" when
    there is none or it is said. Only called while companion.tonight and
    the arbiter are both on."""
    from ai import tonight

    return tonight.render_board_line(tonight.load_current())


async def _conversation_tail(owner_user_id: int) -> str:
    from core.database import brain
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    uid = refs.get("palli_user_id") or owner_user_id
    rows = await brain.get_conversation_history(
        user_id=int(uid), limit=12, include_metadata=True
    )
    lines = []
    now = time.time()
    for r in rows or []:
        raw = " ".join(str(r.get("content") or "").split())
        if not raw:
            continue
        speaker = str(r.get("role") or "").lower()
        # A ``system`` row is the record's own note — a reply the door could
        # not send (persist_delivery_failure) — never hers, so never "me".
        if speaker in ("user", "human"):
            role = "them"
        elif speaker == "system":
            role = "system"
        else:
            role = "me"
        if len(raw) <= 300:
            content = raw
        elif role == "me":
            # Impulse-freshness spec §2 (the 08-06 first-send incident): her
            # replies are wind-up-first, delivery-last. Head-keep preserved
            # intent and discarded proof — she concluded she hadn't done a
            # task she'd finished 14 minutes earlier. Keep the END, and mark
            # the elision: she must never mistake a fragment of herself for
            # the whole.
            content = "…" + raw[-300:]
        else:
            content = raw[:300]
        lines.append(f"[{role}] {content}")
    if not lines:
        return "No conversation on record."
    try:
        last_ts = float(rows[-1].get("timestamp") or 0.0)
        mins = int((now - last_ts) // 60)
        lines.append(f"(last message ~{mins} minutes ago)")
    except (TypeError, ValueError, IndexError):
        pass
    return "\n".join(lines[-13:])


_MACHINE_LINE_MAX_AGE_S = 7200.0


def _machine_line(snap, *, now: float, max_age: float = _MACHINE_LINE_MAX_AGE_S) -> str:
    """The "Machine: <state>." line, only while somebody knows (2026-09-27).

    Two stamps mean a reading was taken: ``source_health["machine"]["updated_at"]``,
    which the machine poller writes on EVERY poll (core/home_presence.py
    record_source_health) and counts only while that entry's status is
    "ready"; and ``last_machine_active_at``, written when he was last seen at
    the keyboard. The newest of the two must be within ``max_age`` (two
    hours), else "". ``last_event_at`` is NOT a machine reading: any source
    stamps it (Home Assistant, a source coming back), so it would revive a
    six-week-old state through a side door — which is the incident: the
    snapshot's newest real reading was 2026-08-18 and "Machine: idle." printed
    on every request for six weeks after it. A stamp that is not a number
    counts as absent. 2026-08-18: no state (presence disabled, or nothing
    measured yet) is silence, not "Machine: unknown." — a sentence about the
    unknown is a thing to muse on; an absent sense is honest.
    """
    snap = snap or {}
    state = str(snap.get("machine_state") or "").strip().lower()
    if not state or state == "unknown":
        return ""
    health = snap.get("source_health")
    machine_health = health.get("machine") if isinstance(health, dict) else None
    candidates = [snap.get("last_machine_active_at")]
    if isinstance(machine_health, dict) and machine_health.get("status") == "ready":
        candidates.append(machine_health.get("updated_at"))
    stamps = [
        value for value in candidates
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not stamps or float(now) - max(stamps) > float(max_age):
        return ""
    return f"Machine: {state}."


async def _presence() -> str:
    from core.home_presence import home_presence_service
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    snap = await home_presence_service.get_context_snapshot(
        owner_user_id=int(refs.get("app_user_id") or 1)
    )
    lines: list[str] = []
    line = _machine_line(snap, now=time.time())
    if line:
        lines.append(line)
    # Discord presence (2026-08-18): the first REAL signal of him on this
    # host — his status/activity across his own devices; "" when unknown.
    try:
        from core.owner_presence import render_line as _discord_line
        line = _discord_line()
        if line:
            lines.append(line)
    except Exception:
        pass
    # The house (step 23, 2026-08-24): "the house right now" — the LAN
    # presence sense beside his Discord line; "" when the sense is off or
    # the book is empty, so a flag-off moment is byte-identical.
    try:
        from core.lan_presence import render_line as _house_line
        line = _house_line()
        if line:
            lines.append(line)
    except Exception:
        pass
    return "\n".join(lines)


async def _activity() -> str:
    from core.desktop_activity_watcher import desktop_activity_watcher

    summary = await desktop_activity_watcher.get_summary()
    if summary.sample_count == 0:
        return ""
    return summary.to_prose()


async def _deeds() -> str:
    """Her recent ACTIONS (impulse-freshness spec §3) — actions, not words.
    The 08-06 incident: deeds were invisible, so she asked for inputs to a
    task she'd delivered 14 minutes earlier. Absent store ⇒ absent section
    (degrade-to-empty, the existing sense contract)."""
    from ai.recent_deeds import read_recent_deeds

    rows = read_recent_deeds(limit=8, max_age_hours=48.0)
    if not rows:
        return ""
    now = time.time()
    lines = []
    for d in rows:
        mins = max(0, int((now - float(d.get("at") or 0.0)) // 60))
        when = f"~{mins}m ago" if mins < 120 else f"~{mins // 60}h ago"
        lines.append(f"- {when} — {d.get('detail')}")
    return "WHAT YOU'VE DONE RECENTLY (actions, not words):\n" + "\n".join(lines)


async def _stakes() -> str:
    from ai.stake_ledger import ledger_enabled, open_stakes, render_stakes_block

    if not ledger_enabled():
        return ""
    stakes = await open_stakes(limit=8)
    if not stakes:
        return "I don't have any particular hopes or worries open right now."
    return render_stakes_block(stakes, now=time.time())


async def _regret_voicing_open() -> str:
    """"open" when an OPEN regret is on the page; "" otherwise — a string so
    it rides _safe like every other sense (timeout/dark ⇒ "" ⇒ not offered)."""
    from ai.stake_ledger import open_stakes

    rows = await open_stakes(limit=8)
    return "open" if any(getattr(s, "kind", "") == "regret" for s in rows) else ""


async def _view_voicing_open() -> str:
    """"open" when an open, unmuted view is on the page; "" otherwise (a
    muted view never reaches the board — held quietly, spec §0.1)."""
    from ai.stake_ledger import open_stakes

    rows = await open_stakes(limit=8)
    return "open" if any(
        getattr(s, "kind", "") == "view" and getattr(s, "muted_at", None) is None
        for s in rows
    ) else ""


async def _care_spine() -> str:
    from ai.narrative_self import read_document

    return (await asyncio.to_thread(read_document, "WHAT_I_CARE_ABOUT.md")) or ""


async def _interests() -> str:
    from ai.passions import passions_enabled, read_interests_text

    if not passions_enabled():
        return ""
    return (await asyncio.to_thread(read_interests_text)) or ""


async def _monologue() -> str:
    from ai.inner_monologue import InnerMonologueEngine

    entries = await asyncio.to_thread(
        InnerMonologueEngine.get_recent_thoughts, max_entries=3, max_age_hours=12.0
    )
    if not entries:
        return ""
    return InnerMonologueEngine.format_thoughts_for_prompt(entries)


async def _reflection() -> str:
    from ai.night_cycle_store import read_latest_daily_meaning

    entry = await asyncio.to_thread(read_latest_daily_meaning, max_age_days=2)
    return getattr(entry, "text", "") or ""


async def _wants() -> str:
    """Her wishbook as the appraisal's board (re-spec §5.2). Only
    assembled when wanting+arbiter are on — flag-off cycles must not even
    carry the health key (byte-identical-off, the threads pattern)."""
    from ai.quest_store import render_board

    return render_board(budget_chars=800)


async def _daydream_line() -> str:
    """Her latest daydream, age-framed (re-spec §1.3) — the
    monologue_thought pattern for her OWN lane. Empty store ⇒ empty."""
    from ai.daydream_store import read_recent

    entries = read_recent(days_back=2, limit=1)
    if not entries:
        return ""
    e = entries[0]
    hours = max(0.0, (time.time() - e.written_at) / 3600.0)
    when = f"~{int(hours)}h ago" if hours >= 1 else "just now"
    return f"A daydream of yours ({when}): {e.text[:240]}"


async def gather_moment_inputs(*, owner_user_id: int) -> GatherInputs:
    health: Dict[str, str] = {}
    (
        conversation, presence, activity, deeds, notes, own_work,
        stakes, care_spine, interests,
        monologue, reflection,
    ) = await asyncio.gather(
        _safe("conversation", lambda: _conversation_tail(owner_user_id), health),
        _safe("presence", _presence, health),
        _safe("activity", _activity, health),
        # Her deeds (impulse-freshness 2026-08-07): actions, not words —
        # empty until the store's first write (degrade-to-empty).
        _safe("deeds", _deeds, health),
        # The pen (2026-07-27): double-gated and age-framed; returns "" unless
        # both companion.self_notes and companion.self_notes_in_moment are on.
        _safe("notes", _notes, health),
        # Her own work (2026-07-28): gated on companion.own_work; one line
        # per active project, in the words she wrote in WORKBENCH.md.
        _safe("own_work", _own_work, health),
        _safe("stakes", _stakes, health),
        _safe("care_spine", _care_spine, health),
        _safe("interests", _interests, health),
        _safe("monologue", _monologue, health),
        _safe("reflection", _reflection, health),
    )

    # The thread board (arbiter spec §3): flag-CONDITIONAL, deliberately
    # outside the ten-source gather above — a flag-off cycle must not even
    # carry the "threads" health key, or off stops being byte-identical.
    comp = _companion()
    arbiter_on = bool(
        comp is not None
        and getattr(comp, "arbiter", False)
        and getattr(comp, "present_moment", False)
    )
    threads = ""
    if arbiter_on:
        threads = await _safe("threads", lambda: _threads(owner_user_id), health)

    # One thing on her mind (Batch 6, task 1, 2026-09-29): while the night's
    # pick is unsaid it is one line at the top of the board, under the fixed
    # id "tonight" — a say that serves it names that id in "thread_id", the
    # slot the board already has, so no moment prompt changes. It rides the
    # board and never the reference bases: a once-only thing that reached
    # the moment's prose would be said with nothing to record it (the small
    # truths' ruling). Flag-conditional like the board itself: an organ that
    # is off carries neither the line nor the health key.
    try:
        from ai import tonight as _tonight
        tonight_on = arbiter_on and _tonight.tonight_enabled()
    except Exception:
        tonight_on = False
    if tonight_on:
        pick_line = await _safe("tonight", _tonight_line, health)
        if pick_line:
            rest = "" if threads == _EMPTY_BOARD else threads
            threads = pick_line + ("\n" + rest if rest else "")

    # The wants board (re-spec §5.2): same strict conditionality — a
    # wanting-off cycle carries neither the block nor its health key.
    wanting_on = bool(
        arbiter_on
        and comp is not None
        and getattr(comp, "wanting", False)
    )
    wants = ""
    if wanting_on:
        wants = await _safe("wants", _wants, health)

    # The daydream sense (re-spec §1.3): wanting+present_moment only — it
    # does NOT require the arbiter (unlike the wants board above).
    wanting_sense_on = bool(
        comp is not None
        and getattr(comp, "wanting", False)
        and getattr(comp, "present_moment", False)
    )
    daydream = ""
    if wanting_sense_on:
        daydream = await _safe("daydream", _daydream_line, health)

    # The expectations sense (organ 8, spec §2): flag-conditional like the
    # wants board — off carries neither the block nor the health key.
    expectations = ""
    try:
        from ai.expectations import surprise_enabled
        surprise_on = surprise_enabled()
    except Exception:
        surprise_on = False
    if surprise_on:
        async def _expectations() -> str:
            from ai.expectations import render_expectations_block
            return render_expectations_block(include_contract=True)
        expectations = await _safe("expectations", _expectations, health)
        if expectations:
            # _safe strips; restore the block's documented shape — a
            # non-empty block ends "\n\n" so the template slot collapses
            # cleanly and never glues onto the next section.
            expectations += "\n\n"

    # The fears sense (organ 6, step 24 §3): flag-conditional like the
    # expectations block — off carries neither the block nor the health
    # key. On with an EMPTY book the sense runs (health "ok") and renders
    # zero bytes: the empty book is honest (decision 5).
    fears = ""
    try:
        from ai.fear_store import fears_enabled
        fears_on = fears_enabled()
    except Exception:
        fears_on = False
    if fears_on:
        async def _fears() -> str:
            from ai.fear_store import render_fears_block
            return render_fears_block(include_contract=True)
        fears = await _safe("fears", _fears, health)
        if fears:
            # _safe strips; restore the "\n\n" slot-collapse shape.
            fears += "\n\n"

    # Step 25 · Regret (spec §4): the say→regret_id contract is offered only
    # when the organ is on AND an open regret is actually on the page —
    # flag-off AND empty-book gathers are byte-identical (health key only
    # when the sense ran — the fears shape).
    regret_voicing = False
    try:
        from ai.stake_ledger import ledger_enabled, regret_enabled
        regret_on = ledger_enabled() and regret_enabled()
    except Exception:
        regret_on = False
    if regret_on:
        regret_voicing = bool(await _safe("regret_voicing", _regret_voicing_open, health))

    # Organ 4.13 · Opinions (spec §5): the say→view_id contract is offered
    # only when the organ is on AND an open, UNMUTED view is actually on the
    # page — the regret shape (health key only when the sense ran), so a
    # flag-off gather and a no-live-view gather stay byte-identical.
    view_voicing = False
    try:
        from ai.stake_ledger import ledger_enabled, opinions_enabled
        views_on = ledger_enabled() and opinions_enabled()
    except Exception:
        views_on = False
    if views_on:
        view_voicing = bool(await _safe("view_voicing", _view_voicing_open, health))

    # Organ 4.9 · Grounded Vulnerability (spec §4): her small-truths board —
    # flag-conditional like the fears block, so a vulnerability-off cycle
    # carries neither the board nor the health key. On with an EMPTY book the
    # sense runs (health "ok") and renders zero bytes: the empty book is
    # honest, and identical to off.
    #
    # The offer is READ OFF the rendered board rather than probed separately:
    # the say line points her at "YOUR SMALL TRUTHS", so an offer that could
    # be true while the board was dark (a timeout, a broken book) would send
    # her to a board that is not in her prompt. One read, one truth.
    disclosures = ""
    try:
        from ai.disclosure_store import vulnerability_enabled
        vulnerability_on = vulnerability_enabled()
    except Exception:
        vulnerability_on = False
    if vulnerability_on:
        async def _disclosures() -> str:
            from ai.disclosure_store import render_disclosures_block
            return render_disclosures_block()
        disclosures = await _safe("disclosures", _disclosures, health)
        if disclosures:
            # _safe strips; restore the "\n\n" slot-collapse shape so the
            # template slot ({disclosures_block}Then, separately, …) sits
            # flush against the paragraph its say line lives in.
            disclosures += "\n\n"
    disclosure_offer = bool(disclosures)

    senses = [s for s in (conversation, presence, activity, deeds, notes, own_work) if s]
    if daydream:
        senses.append(daydream)
    bases_parts = []
    if stakes:
        bases_parts.append("MY OPEN STAKES:\n" + stakes)
    if care_spine:
        bases_parts.append("MY CARE SPINE:\n" + care_spine)
    if interests:
        bases_parts.append("MY INTERESTS:\n" + interests)
    # Her small truths are deliberately NOT a base here — see disclosures_block.

    return GatherInputs(
        senses_block="\n\n".join(senses) or "(no senses readable)",
        bases_block="\n\n".join(bases_parts) or "(no reference bases readable)",
        monologue_block=monologue or "(none)",
        reflection_block=reflection or "(none)",
        threads_block=threads,
        wants_block=wants,
        expectations_block=expectations,
        fears_block=fears,
        regret_voicing=regret_voicing,
        view_voicing=view_voicing,
        disclosures_block=disclosures,
        disclosure_offer=disclosure_offer,
        sense_health=health,
    )
