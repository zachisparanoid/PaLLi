"""Consolidation & gist-aging — Selfhood Wave 1a (2026-07-15).

Three jobs, one module, two callers (the Night Cycle engine nightly, the
backfill driver over the archive):

- consolidate_day: ONE compact first-person day-gist per lived day, written
  to ai/day_gist_store (keyed by ITS day — honest date attribution) and
  indexed into monologue_memory_fts (source_kind="day_gist").
- age_facts: old episodic facts (memory_type='moment', >AGE_DAYS) get an LLM
  gist rewrite AS A NEW FACT; the original is marked superseded + salience-
  halved and its exact text is NEVER altered (owner decision #1, 2026-07-15).
- reconcile_day: NIGHTLY-ONLY — a day's material contradicting an active fact
  carries the correction through the same supersede mechanics, evidence in
  source_detail. Never runs in backfill (time-travel hazard, spec §4.4).

llm_call and the writers are injected so dry-run and tests stub them by
construction (the run_night_cycle pattern). Everything here is never-raises
on the job level: failures return result objects, CancelledError propagates.

Spec: docs/superpowers/specs/2026-07-15-consolidation-gist-aging-v1-design.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.turn.companion_flags import companion_settings

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# Budgets live in code, not prompt hopes (spec §4.1 defaults).
AGE_DAYS = 45
AGEABLE_TYPES: Tuple[str, ...] = ("moment",)  # episodic only; durable kinds NEVER age in v1
NIGHTLY_MAX_REWRITES = 10
NIGHTLY_MAX_UPDATES = 3
GIST_MAX_CHARS = 600
FACT_GIST_MAX_CHARS = 300

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WORD_RE = re.compile(r"[A-Za-z0-9_']+")


def consolidation_enabled() -> bool:
    """THE gate for this default-OFF faculty (the night_cycle_enabled /
    ledger_enabled pattern): unbooted settings or a missing flag => OFF."""
    comp = companion_settings()
    return comp is not None and bool(getattr(comp, "consolidation", False))


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches consolidation_enabled off (passions pattern).
consolidation_enabled_impl = consolidation_enabled


@dataclass
class DayResult:
    day: str
    wrote: bool = False
    skipped: bool = False
    reason: str = ""
    chars: int = 0
    sources: Dict[str, int] = field(default_factory=dict)
    gist: str = ""
    error: str = ""


@dataclass
class AgingResult:
    candidates: int = 0
    rewritten: int = 0
    skipped: int = 0
    pairs: List[Tuple[int, int]] = field(default_factory=list)  # (old_id, new_id)
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class ReconcileResult:
    checked: int = 0
    corrections: List[Tuple[int, int]] = field(default_factory=list)
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    skipped: bool = False
    error: str = ""


# ── day-gist ─────────────────────────────────────────────────────────────

_CONVO_CAP = 6000
_DAYLOG_CAP = 3000
_THOUGHTS_CAP = 2500
_READS_CAP = 1200
_REFLECTION_CAP = 500


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: max(0, limit - 1)].rstrip() + "…"


def _clip_middle(text: str, limit: int) -> str:
    """Keep the start AND the end — days are remembered by peak and ending."""
    if len(text) <= limit:
        return text
    half = max(200, (limit - 40) // 2)
    return text[:half].rstrip() + "\n[…midday trimmed…]\n" + text[-half:].lstrip()


def _day_bounds(day: str) -> Tuple[float, float]:
    start = datetime.strptime(day, "%Y-%m-%d")
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


async def _gather_day_sections(
    *, owner_id: int, day: str, brain_client: Any = None
) -> Dict[str, str]:
    """Best-effort assembly of everything on file for one lived day. A failed
    source is an omitted section, never an error (recall's degrade pattern).
    Conversations are the PRIMARY material — for the first ~7 archive weeks
    they are the ONLY material (monologue starts 2026-05-05, timeline
    2026-06-15, day-logs with caring-memory in June)."""
    sections: Dict[str, str] = {}
    start_ts, end_ts = _day_bounds(day)

    # Corrections of record (2026-08-17) — FIRST: the gist for a day on which
    # a correction was made must state the corrected truth (the 08-14 gist
    # said only "the doors were real" and lost to the 08-12 false line).
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=800, day=day)
        if block:
            sections["corrections"] = block
    except Exception as exc:
        logger.debug("consolidation: corrections unavailable: %s", exc)

    try:
        from core.database import brain as _default_brain

        db = brain_client or _default_brain
        rows = await db.get_conversations_for_day(day_start_ts=start_ts, day_end_ts=end_ts)
        lines: List[str] = []
        for r in rows:
            when = datetime.fromtimestamp(float(r.get("timestamp") or start_ts)).strftime("%H:%M")
            who = str(r.get("username") or ("Me" if r.get("role") == "assistant" else "them"))
            lines.append(f"[{when}] {who}: {_clip(r.get('content'), 280)}")
        if lines:
            sections["conversations"] = _clip_middle("\n".join(lines), _CONVO_CAP)
    except Exception as exc:
        logger.debug("consolidation: conversations unavailable for %s: %s", day, exc)

    try:
        from core.memory_service import memory_workspace_service

        day_log = await memory_workspace_service.get_day(owner_user_id=int(owner_id), day=day)
        # A shell-only file has non-empty markdown; entries are the real signal.
        if list((day_log or {}).get("entries") or []):
            sections["day_log"] = _clip_middle(str(day_log.get("markdown") or "").strip(), _DAYLOG_CAP)
    except Exception as exc:
        logger.debug("consolidation: day-log unavailable for %s: %s", day, exc)

    try:
        from ai import inner_monologue_store

        entries = inner_monologue_store.read_entries_for_day(day=day, substantive_only=True)
        if entries:
            sections["thoughts"] = _clip_middle(
                "\n".join(e.to_readable() for e in entries), _THOUGHTS_CAP
            )
    except Exception as exc:
        logger.debug("consolidation: monologue unavailable for %s: %s", day, exc)

    try:
        from core.database import brain as _default_brain

        db = brain_client or _default_brain
        conn = await db.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT created_at, read_text, weight FROM emotional_timeline "
                "WHERE owner_user_id = $1 AND created_at >= $2 AND created_at < $3 "
                "ORDER BY created_at ASC LIMIT 60",
                int(owner_id), float(start_ts), float(end_ts),
            )
        finally:
            await conn.close()
        lines = [f"- {_clip(dict(r).get('read_text'), 200)}" for r in rows]
        if lines:
            sections["reads"] = _clip_middle("\n".join(lines), _READS_CAP)
    except Exception as exc:
        logger.debug("consolidation: timeline unavailable for %s: %s", day, exc)

    try:
        from ai import night_cycle_store

        reflection = night_cycle_store.read_daily_meaning_for(date=day)
        if reflection is not None and reflection.text.strip():
            sections["reflection"] = _clip(reflection.text, _REFLECTION_CAP)
    except Exception as exc:
        logger.debug("consolidation: reflection unavailable for %s: %s", day, exc)

    return sections


_SECTION_HEADERS = (
    ("corrections", ""),
    ("conversations", "## The conversations that day"),
    ("day_log", "## The day-log kept with them"),
    ("thoughts", "## Your own quiet thoughts that day"),
    ("reads", "## Your reads of how they were"),
    ("reflection", "## What you wrote that night (context, not content to re-gist)"),
)


def _render_day_material(sections: Dict[str, str]) -> str:
    parts = [
        (f"{header}\n{sections[key]}" if header else sections[key])
        for key, header in _SECTION_HEADERS if sections.get(key)
    ]
    return "\n\n".join(parts)


async def consolidate_day(
    *,
    owner_id: int,
    day: str,
    llm_call,
    now: float,
    brain_client: Any = None,
    store_write=None,
    index_write=None,
) -> DayResult:
    """ONE compact first-person gist for ``day``, written to the day-gist
    store keyed by ITS OWN day and indexed for introspection. Empty day =>
    writes NOTHING (a quiet day is absence, not a fabricated note).
    Never raises except CancelledError."""
    clean_day = str(day or "").strip()
    if not _DAY_RE.fullmatch(clean_day):
        return DayResult(day=clean_day, skipped=True, reason="bad_day")
    if clean_day >= datetime.fromtimestamp(float(now)).strftime("%Y-%m-%d"):
        return DayResult(day=clean_day, skipped=True, reason="day_not_finished")
    try:
        from ai.prompt_loader import prompt_loader

        sections = await _gather_day_sections(
            owner_id=int(owner_id), day=clean_day, brain_client=brain_client
        )
        if not sections:
            return DayResult(day=clean_day, skipped=True, reason="no_material")
        # Flag-picked template (2026-07-19 curiosity continuity): the original
        # file is NEVER edited, so flag-off is byte-identical by construction.
        from ai.curiosity_research import curiosity_continuity_enabled
        _gist_prompt_name = (
            "consolidation_day_gist_continuity"
            if curiosity_continuity_enabled()
            else "consolidation_day_gist"
        )
        template = prompt_loader.load_prompt(_gist_prompt_name)
        if not template:
            return DayResult(day=clean_day, error="prompt_missing")
        prompt = template.replace("<<DAY_DATE>>", clean_day).replace(
            "<<DAY_MATERIAL>>", _render_day_material(sections)
        )
        text = _clip((await llm_call(prompt)) or "", GIST_MAX_CHARS)
        if not text:
            return DayResult(day=clean_day, error="empty_gist")

        if store_write is None:
            from ai.day_gist_store import write_day_gist as store_write  # type: ignore[assignment]
        if index_write is None:
            # Bind brain_client (review fix): the default index writer must
            # honor an injected brain — a sandboxed call writing FTS rows to
            # the live global brain is the test-to-live leak class this repo
            # has been bitten by before.
            from ai.inner_memory_index import index_day_gist as _index_day_gist

            _index_db = brain_client

            async def index_write(*, day, text, source_path=""):  # type: ignore[misc]
                await _index_day_gist(
                    day=day, text=text, source_path=source_path, brain_client=_index_db
                )
        meta = {
            "owner_id": int(owner_id),
            "created_at": float(now),
            "sources": {k: len(v) for k, v in sections.items()},
        }
        path = store_write(day=clean_day, text=text, meta=meta)
        await index_write(day=clean_day, text=text, source_path=str(path or ""))
        essential_logger.info("consolidation: day-gist %s (%d chars)", clean_day, len(text))
        return DayResult(
            day=clean_day, wrote=True, chars=len(text), gist=text, sources=meta["sources"]
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("consolidation: day-gist failed for %s: %s", clean_day, exc)
        return DayResult(day=clean_day, error=str(exc))


# ── fact gist-aging ──────────────────────────────────────────────────────


def _source_detail(fact: Dict[str, Any]) -> Dict[str, Any]:
    """source_detail as a dict regardless of which row-record path built it."""
    raw = fact.get("source_detail")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _fact_day(created_at: float) -> str:
    try:
        ts = float(created_at)
        if ts <= 0:
            return "unknown"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "unknown"


async def age_facts(
    *,
    owner_id: int,
    now: float,
    llm_call,
    max_rewrites: int,
    brain_client: Any = None,
    supersede=None,
) -> AgingResult:
    """Gist-rewrite old episodic facts as NEW facts; originals are marked
    superseded (content untouched) with salience halved. v1 ages by the
    memory_type allowlist ONLY — last_referenced_at has no production writer
    (verified 2026-07-15), so a reference-based criterion is impossible, and
    gisting durable kinds ("Timezone: ET") would be regression, not
    consolidation. Never raises except CancelledError."""
    result = AgingResult()
    try:
        from ai.fact_retrieval import get_user_facts_raw
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("consolidation_fact_gist")
        if not template:
            result.error = "prompt_missing"
            return result
        facts = await get_user_facts_raw(user_id=int(owner_id), brain_client=brain_client)
        cutoff = float(now) - AGE_DAYS * 86400.0
        candidates = [
            f for f in facts
            if str(f.get("status") or "active") == "active"
            and not f.get("superseded_by_fact_id")
            and str(f.get("memory_type") or "") in AGEABLE_TYPES
            and float(f.get("created_at") or 0.0) < cutoff
            # A gist NEVER re-ages (review fix 2026-07-15): successors inherit
            # the original's created_at, so without this they'd re-enter the
            # pool every pass — gist-of-gist blur, forever. One aging per
            # lived memory; deeper fading is a later increment (owner
            # decision #1).
            and not _source_detail(f).get("consolidated_from")
        ]
        candidates.sort(key=lambda f: float(f.get("created_at") or 0.0))
        candidates = candidates[: max(0, int(max_rewrites))]
        result.candidates = len(candidates)

        if supersede is None:
            from core.database import brain as _default_brain

            db = brain_client or _default_brain

            async def supersede(**kwargs):  # type: ignore[misc]
                return await db.supersede_user_memory_with_gist(**kwargs)

        pass_day = datetime.fromtimestamp(float(now)).strftime("%Y-%m-%d")
        for fact in candidates:
            old_id = int(fact["id"])
            try:
                prompt = (
                    template.replace("<<FACT_CONTENT>>", str(fact.get("content") or ""))
                    .replace("<<FACT_DATE>>", _fact_day(fact.get("created_at") or 0.0))
                    .replace("<<MEMORY_TYPE>>", str(fact.get("memory_type") or "moment"))
                )
                gist = _clip((await llm_call(prompt)) or "", FACT_GIST_MAX_CHARS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("consolidation: aging rewrite failed for %s: %s", old_id, exc)
                result.skipped += 1
                continue
            # Proposal recorded BEFORE the write decision — the dry-run report
            # shows every would-be aging even when the pens are stubbed out.
            result.proposals.append(
                {"old_id": old_id, "old_content": str(fact.get("content") or ""),
                 "gist": gist, "from_day": _fact_day(fact.get("created_at") or 0.0)}
            )
            if not gist:
                result.skipped += 1
                continue
            new_id = await supersede(
                user_id=int(owner_id),
                memory_id=old_id,
                gist_content=gist,
                source_detail={"consolidated_from": old_id, "pass_day": pass_day},
            )
            if new_id:
                result.pairs.append((old_id, int(new_id)))
                result.rewritten += 1
            else:
                result.skipped += 1
        if result.rewritten:
            essential_logger.info(
                "consolidation: aged %d fact(s) into gists", result.rewritten
            )
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("consolidation: age_facts failed: %s", exc)
        result.error = str(exc)
        return result


# ── reconciliation (nightly-only; NEVER runs in backfill — spec §4.4) ────

_RECONCILE_MATERIAL_CAP = 6000
_RECONCILE_MAX_FACTS = 15

def _tokens(text: str) -> frozenset:
    """Relevance tokens — SHARED with the retrieval surface (review fix:
    a third private tokenizer left 17/84 live facts invisible to the
    reconcile prefilter while retrieval kept surfacing them; possessives
    normalize and short entities like 'AC'/'ET' survive here exactly as
    they do in ai/fact_retrieval)."""
    from ai.fact_retrieval import _query_tokens

    return _query_tokens(str(text or ""))


def _parse_reconcile_json(raw: str, offered_ids: frozenset) -> Optional[List[Dict[str, Any]]]:
    """Strict, conservative parse: first {...} block, valid JSON, ids must be
    ones the model was shown. Anything else => None (no writes)."""
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    items = data.get("contradictions")
    if not isinstance(items, list):
        return None
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            fact_id = int(item.get("fact_id"))
        except (TypeError, ValueError):
            continue
        corrected = " ".join(str(item.get("corrected_content") or "").split())[:400]
        evidence = " ".join(str(item.get("evidence") or "").split())[:200]
        if fact_id in offered_ids and corrected:
            out.append({"fact_id": fact_id, "corrected_content": corrected, "evidence": evidence})
    return out


async def reconcile_day(
    *,
    owner_id: int,
    day: str,
    llm_call,
    max_updates: int,
    brain_client: Any = None,
    supersede=None,
) -> ReconcileResult:
    """Compare a day's material against the active facts it plausibly touches
    (token-overlap prefilter — low recall for purely semantic contradictions,
    acceptable; misses are safer than false hits). Contradiction => supersede
    with the correction, evidence recorded. Uncertain => no writes.

    The owner's ``today`` rows are never offered (R15, T12; fix round 1):
    they come from the very day under review, so they would win the overlap
    ranking and take the places of the durable facts this exists to check —
    and the night retires them right after, correction and all."""
    result = ReconcileResult()
    try:
        from ai.fact_retrieval import get_user_facts_raw
        from ai.prompt_loader import prompt_loader
        from core.memory_store import TODAY_TYPE

        sections = await _gather_day_sections(
            owner_id=int(owner_id), day=str(day), brain_client=brain_client
        )
        material = _clip_middle(
            "\n\n".join(sections[k] for k in ("conversations", "day_log") if sections.get(k)),
            _RECONCILE_MATERIAL_CAP,
        )
        if not material.strip():
            result.skipped = True
            return result
        material_tokens = _tokens(material)
        facts = [
            f for f in await get_user_facts_raw(user_id=int(owner_id), brain_client=brain_client)
            if str(f.get("status") or "active") == "active"
            and str(f.get("memory_type") or "") != TODAY_TYPE
        ]
        overlapped = sorted(
            ((len(material_tokens & _tokens(f.get("content"))), f) for f in facts),
            key=lambda pair: pair[0],
            reverse=True,
        )
        touched = [f for score, f in overlapped if score > 0][:_RECONCILE_MAX_FACTS]
        result.checked = len(touched)
        if not touched:
            result.skipped = True
            return result
        template = prompt_loader.load_prompt("consolidation_reconcile")
        if not template:
            result.error = "prompt_missing"
            return result
        fact_lines = "\n".join(
            f"{int(f['id'])}: {_clip(f.get('content'), 240)}" for f in touched
        )
        prompt = (
            template.replace("<<DAY_DATE>>", str(day))
            .replace("<<DAY_MATERIAL>>", material)
            .replace("<<ACTIVE_FACTS>>", fact_lines)
        )
        raw = await llm_call(prompt)
        parsed = _parse_reconcile_json(raw, frozenset(int(f["id"]) for f in touched))
        if parsed is None:
            result.error = "unparseable_verdict"
            return result

        if supersede is None:
            from core.database import brain as _default_brain

            db = brain_client or _default_brain

            async def supersede(**kwargs):  # type: ignore[misc]
                return await db.supersede_user_memory_with_gist(**kwargs)

        for item in parsed[: max(0, int(max_updates))]:
            result.proposals.append(dict(item))
            new_id = await supersede(
                user_id=int(owner_id),
                memory_id=int(item["fact_id"]),
                gist_content=item["corrected_content"],
                source_detail={
                    "correction_evidence": f"{day}: {item['evidence'] or 'contradicted by the day'}",
                    "corrected_from": int(item["fact_id"]),
                },
                new_salience_weight=1.0,
                # A correction is NEW information — fresh timestamps on
                # purpose (unlike aging, which inherits for decay continuity).
                inherit_timestamps=False,
            )
            if new_id:
                result.corrections.append((int(item["fact_id"]), int(new_id)))
        if result.corrections:
            essential_logger.info(
                "consolidation: reconciled %d fact(s) for %s", len(result.corrections), day
            )
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("consolidation: reconcile failed for %s: %s", day, exc)
        result.error = str(exc)
        return result


# ── the real LLM deps the nightly composer (and backfill driver) inject ──
#
# Imported lazily so unit tests never touch the LLM/DB import graph.
# Day-gists speak in her reflective register (the night-cycle model pick);
# aging rewrites and reconciliation verdicts run on the analysis lane.


def _make_llm_call(*, task: str, fallback_task: str, temperature: float, max_tokens: int, context: str):
    async def _call(prompt: str) -> str:
        from ai.llm_client import llm
        from ai.llm_routing import get_task_model

        model = get_task_model(task, fallback=get_task_model(fallback_task))
        response = await asyncio.wait_for(
            llm.chat_completion(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                log_context=context,
            ),
            timeout=180,  # patient-lane envelope: 2x60s ceiling + headroom (2026-08-09)
        )
        return response or ""

    return _call


# max_tokens is a REASONING budget here, not an output budget: the reflection
# lane resolves to deepseek-v4-flash (supports_reasoning), which spends tokens
# thinking before it emits. At 400 it intermittently spent the whole ceiling and
# returned empty (finish_reason=length) — measured ~1-in-8 on real day material,
# and an empty gist means that day silently leaves no trace in her record. The
# 87cc2d6 precedent (authorship ceiling 4000 after reasoning ate the spine).
# Output length is bounded by GIST_MAX_CHARS=600 regardless, so headroom is free.
day_gist_llm_call = _make_llm_call(
    # 1500 -> 4000 (2026-08-04): the raise below was not enough. The 08-03
    # verification run caught this lane burning its FULL 1500 on reasoning on
    # both 08-01 and 08-02, losing the day-gists for 07-31 and 08-01 outright.
    # Same call as the night lane, same remedy, same free headroom.
    task="reflection", fallback_task="memory", temperature=0.6, max_tokens=4000,
    context="consolidation_day_gist",
)
# 1500/2000, not 220/500 (2026-07-24): the analysis lane now ALSO resolves to
# deepseek-v4-flash (Anthropic credit-exhaustion reroute), so the reasoning-
# budget law in the comment above applies to these two as well — 220 was sized
# for non-thinking Haiku and leaves a thinking model no room to surface output.
# Output size is bounded by the prompts/parsers regardless; headroom is free.
fact_gist_llm_call = _make_llm_call(
    task="analysis", fallback_task="memory", temperature=0.3, max_tokens=1500,
    context="consolidation_fact_gist",
)
reconcile_llm_call = _make_llm_call(
    task="analysis", fallback_task="memory", temperature=0.0, max_tokens=2000,
    context="consolidation_reconcile",
)


async def run_nightly_consolidation(*, owner_id: int, day: str) -> Dict[str, Any]:
    """The Night Cycle's consolidation step (spec §4.3): day-gist → capped
    aging → capped reconciliation, each never-raises, failures logged and
    skipped (the day stays re-reachable by backfill). Flag OFF => nothing
    runs and nothing is touched — byte-identical nightly behavior.

    Calls the sibling jobs through module attributes on purpose: tests (and
    any hot-patch) swap them at module level."""
    if not consolidation_enabled():
        return {"skipped": "flag_off"}
    from dataclasses import asdict

    import ai.consolidation as _self

    out: Dict[str, Any] = {}
    now = time.time()
    try:
        out["day_gist"] = asdict(
            await _self.consolidate_day(
                owner_id=owner_id, day=day, llm_call=day_gist_llm_call, now=now
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        out["day_gist"] = {"error": str(exc)}
    try:
        out["aging"] = asdict(
            await _self.age_facts(
                owner_id=owner_id, now=now, llm_call=fact_gist_llm_call,
                max_rewrites=NIGHTLY_MAX_REWRITES,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        out["aging"] = {"error": str(exc)}
    try:
        out["reconcile"] = asdict(
            await _self.reconcile_day(
                owner_id=owner_id, day=day, llm_call=reconcile_llm_call,
                max_updates=NIGHTLY_MAX_UPDATES,
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        out["reconcile"] = {"error": str(exc)}
    # The today slot (R15, memory that reaches, T12): the day's facts are
    # filed — the day-gist above, the preferences rebuild later this night —
    # so the slot's rows are retired, under the slot's own switch (off: no
    # key, nothing touched). The cutoff is the pass's start: a line kept
    # while the pass runs belongs to the new day. The reconcile never sees
    # a today row (fix round 1), so nothing it wrote is lost here.
    from core.turn.companion_flags import today_slot_on

    if today_slot_on():
        try:
            from core.database import brain as _db

            out["today_retired"] = await _db.retire_today_facts(
                user_id=int(owner_id), before_ts=now
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("consolidation: today's slot retirement failed: %s", exc)
            out["today_retired"] = {"error": str(exc)}
    # Review fix: superseding facts left the workspace index (recall's
    # semantic lane + introspect's memory notes) stale until a TTL-debounced
    # rebuild — a just-corrected fact kept answering with its pre-correction
    # content. Rebuild NOW when this pass changed facts.
    changed = bool((out.get("aging") or {}).get("rewritten")) or bool(
        (out.get("reconcile") or {}).get("corrections")
    )
    out["index_rebuilt"] = False
    if changed:
        try:
            from core.memory_service import memory_workspace_service

            await memory_workspace_service.rebuild_index(owner_user_id=int(owner_id))
            out["index_rebuilt"] = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("consolidation: post-pass index rebuild failed: %s", exc)
    return out
