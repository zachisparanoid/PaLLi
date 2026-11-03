from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import math
import re
import time
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence

from core.database import brain
from core.memory_store import TODAY_DAILY_CAP, TODAY_TYPE
from core.settings_boot import settings_service

logger = logging.getLogger(__name__)

# Tokenizer for query/content overlap scoring (IM-CRIT-3).
# Same shape as ai/inner_memory_index._WORD_RE so behaviour is consistent
# across the two retrieval surfaces.
_WORD_RE = re.compile(r"[A-Za-z0-9_']+")

# Minimal English stopword set — enough to keep purely-functional words
# from polluting overlap scores without growing a dependency on NLTK or
# similar. The goal isn't perfect IR; it's "don't let 'the' and 'is'
# decide which fact a turn surfaces."
_STOPWORDS: FrozenSet[str] = frozenset(
    {
        "a", "an", "and", "any", "are", "as", "at", "be", "but", "by", "do",
        "for", "from", "had", "has", "have", "he", "her", "here", "hi", "him",
        "his", "how", "i", "if", "in", "is", "it", "its", "me", "my", "no",
        "not", "of", "ok", "on", "one", "or", "our", "out", "she", "so", "some",
        "than", "that", "the", "their", "them", "there", "they", "this", "to",
        "too", "us", "was", "we", "were", "what", "when", "where", "which",
        "who", "why", "will", "with", "you", "your", "yours",
    }
)

# Public alias (Wave 1a review fix): THE stopword set for fact/text relevance.
# introspect's exact-mode term picker and consolidation's reconcile prefilter
# reuse it so all retrieval surfaces agree on what a filler word is.
STOPWORDS: FrozenSet[str] = _STOPWORDS


def _normalize_token(token: str) -> str:
    """Lowercase, strip surrounding apostrophes, and drop the possessive 's.

    LLM-generated fact text leans on possessives ("user's job", "mom's
    birthday"); user queries are short and use the bare noun ("how is
    mom?"). Without dropping the trailing 's, "mom's" and "mom" never
    overlap. The same normalization runs on both sides (query tokens and
    fact tokens) so the comparison stays symmetric.
    """
    cleaned = token.lower().strip("'")
    if cleaned.endswith("'s"):
        cleaned = cleaned[:-2]
    return cleaned


def _query_tokens(query: Optional[str]) -> FrozenSet[str]:
    """Return the set of overlap-eligible tokens from a query string.

    Tokens are lowercased, apostrophe-trimmed, stripped of stopwords, and
    filtered to length >= 2 so that single punctuation runs, lone vowels,
    and bare quotation marks don't earn a fact a boost.
    """
    if not query:
        return frozenset()
    text = str(query).strip()
    if not text:
        return frozenset()
    tokens = set()
    for raw in _WORD_RE.findall(text):
        normalized = _normalize_token(raw)
        if len(normalized) >= 2 and normalized not in _STOPWORDS:
            tokens.add(normalized)
    return frozenset(tokens)


def _query_overlap(fact: Dict[str, Any], query_tokens: FrozenSet[str]) -> int:
    """Count how many query tokens occur in a fact's textual content.

    The user-facing fact text lives in `content`; we also pull any tags
    (a list[str] of tag labels) so explicit topic tagging earns a match
    even when the prose phrasing doesn't.
    """
    if not query_tokens:
        return 0
    parts: List[str] = []
    raw_content = fact.get("content")
    if raw_content:
        parts.append(str(raw_content))
    raw_tags = fact.get("tags")
    if isinstance(raw_tags, (list, tuple)):
        parts.extend(str(tag) for tag in raw_tags if tag)
    if not parts:
        return 0
    fact_tokens = {
        _normalize_token(token) for token in _WORD_RE.findall(" ".join(parts))
    }
    return len(query_tokens & fact_tokens)


@dataclass(frozen=True)
class DecayWeightedFact:
    fact: Dict[str, Any]
    effective_score: float
    recency_factor: float
    revisit_factor: float


async def get_user_facts_raw(
    *,
    user_id: int,
    brain_client: Any = None,
) -> List[Dict[str, Any]]:
    """Return all fact rows for audit without decay/status filtering."""
    active_brain = brain_client or brain
    conn = await active_brain.get_connection()
    try:
        rows = await conn.fetch(
            "SELECT * FROM user_memory WHERE user_id = $1 ORDER BY created_at DESC",
            int(user_id),
        )
        store = getattr(active_brain, "_memory_store", None)
        if store is not None and hasattr(store, "_user_memory_record"):
            return [store._user_memory_record(row) for row in rows]
        return [_record_from_row(row) for row in rows]
    finally:
        await conn.close()


async def get_decay_weighted_facts(
    *,
    user_id: int,
    max_results: Optional[int] = None,
    min_score: Optional[float] = None,
    now: Optional[float] = None,
    brain_client: Any = None,
    query: Optional[str] = None,
    exclude_memory_types: Optional[Iterable[str]] = None,
) -> List[DecayWeightedFact]:
    """Return active user facts ranked by conservative time-decay scoring.

    `exclude_memory_types` (Batch 5, task 4, ruling R5) leaves rows of those
    `memory_type`s out BEFORE ranking and the limit, so `max_results` counts
    what is left: the reply's "Things I Know About This User" band asks for
    the owner's facts without her own "moment" rows (her emotional reads of
    them — 154 of 186 live), which would otherwise take every slot. None or
    empty means what it always meant: every type.

    When `query` is a non-empty string with at least one non-stopword token,
    the post-decay pool is re-ranked so facts whose content overlaps query
    tokens come first. When the query produces non-empty tokens AND at least
    one fact in the decayed pool overlaps, overlap count is the dominant
    ranking signal; `effective_score` only breaks overlap ties. (So a
    score=0.30 fact with two overlapping tokens outranks a score=0.99 fact
    with one.) When `query` is None, empty, whitespace, or contains only
    stopwords/short tokens, ordering is the historical decay-only behaviour —
    and so is the case where no fact in the pool overlaps the query at all
    (we never starve the prompt of context). Resolves IM-CRIT-3.

    The today slot (R15, T12; fix round 1): the owner's ``today`` rows are
    set aside BEFORE the ranking, so they never count against
    ``max_results`` and never take a typed fact's place. While the slot's
    switch is on they are appended after the ranked facts — only those
    written since the slot's day began (``_today_since``), newest first,
    at most TODAY_DAILY_CAP. Off, no today row leaves this function: the
    answer is exactly what it was before the slot existed, and nothing of
    the slot is rendered or reference-bumped downstream.
    """
    cfg = _fact_memory_config()
    limit = max(1, int(max_results or getattr(cfg, "max_facts_per_prompt", 8) or 8))
    threshold = float(min_score if min_score is not None else getattr(cfg, "min_score_threshold", 0.15))
    half_life = max(1.0, float(getattr(cfg, "half_life_days", 30.0) or 30.0))
    current_time = float(now if now is not None else time.time())
    excluded = frozenset(str(kind) for kind in (exclude_memory_types or ()))
    facts = await get_user_facts_raw(user_id=user_id, brain_client=brain_client)

    scored: List[DecayWeightedFact] = []
    today_rows: List[Dict[str, Any]] = []
    for fact in facts:
        if str(fact.get("status") or "active") != "active":
            continue
        if fact.get("superseded_by_fact_id"):
            continue
        if excluded and str(fact.get("memory_type") or "") in excluded:
            continue
        if str(fact.get("memory_type") or "") == TODAY_TYPE:
            today_rows.append(fact)
            continue
        weighted = score_fact(fact, now=current_time, half_life_days=half_life)
        if weighted.effective_score >= threshold:
            scored.append(weighted)
    today = _todays_slot(today_rows, now=current_time, half_life=half_life)

    query_tokens = _query_tokens(query)
    if query_tokens:
        # Re-rank by query overlap, falling back to decay-only on ties so
        # the historical ordering is preserved when no facts (or all facts)
        # overlap. Storing the per-item overlap once avoids recomputation
        # during sort key evaluation.
        overlaps = [
            (item, _query_overlap(item.fact, query_tokens)) for item in scored
        ]
        if any(overlap > 0 for _, overlap in overlaps):
            overlaps.sort(
                key=lambda pair: (
                    pair[1],
                    pair[0].effective_score,
                    float(
                        pair[0].fact.get("last_referenced_at")
                        or pair[0].fact.get("created_at")
                        or 0.0
                    ),
                ),
                reverse=True,
            )
            return [item for item, _ in overlaps[:limit]] + today
        # Zero overlap across the whole pool: fall through to decay-only
        # so the prompt still gets useful context.

    scored.sort(
        key=lambda item: (
            item.effective_score,
            float(item.fact.get("last_referenced_at") or item.fact.get("created_at") or 0.0),
        ),
        reverse=True,
    )
    return scored[:limit] + today


def _today_since(now: float) -> float:
    """When the today slot's day began (R15, T12; fix round 1): the night's
    run time on the night ``now`` belongs to — the hour the night retires
    the slot, through the night cycle's own helper (``night_run_offset``,
    06:30 by default), with tonight's day-key arithmetic (``ai.tonight.
    day_for``). 19:00 on the 13th and 03:00 on the 14th both begin at 06:30
    on the 13th."""
    from ai.night_cycle import night_run_offset

    offset = night_run_offset()
    day = (datetime.fromtimestamp(float(now)) - offset).date()
    return (datetime.combine(day, datetime.min.time()) + offset).timestamp()


def _todays_slot(rows: List[Dict[str, Any]], *, now: float, half_life: float) -> List[DecayWeightedFact]:
    """The today rows that leave ``get_decay_weighted_facts``: none while the
    slot's switch is off (read at call time, so the suite's pin holds);
    on, those written since the slot's day began — a row the night failed
    to retire is not "earlier today" — newest first, at most
    TODAY_DAILY_CAP. A boundary that cannot be read gives none (a debug
    line)."""
    if not rows:
        return []
    from core.turn.companion_flags import today_slot_on

    if not today_slot_on():
        return []
    try:
        since = _today_since(now)
    except Exception as exc:
        logger.debug("fact_retrieval: the today slot's day could not be read: %s", exc)
        return []
    fresh = [row for row in rows if float(row.get("created_at") or 0.0) >= since]
    fresh.sort(key=lambda row: (float(row.get("created_at") or 0.0), int(row.get("id") or 0)), reverse=True)
    return [score_fact(row, now=now, half_life_days=half_life) for row in fresh[:TODAY_DAILY_CAP]]


def score_fact(fact: Dict[str, Any], *, now: float, half_life_days: float) -> DecayWeightedFact:
    base_confidence = max(0.0, float(fact.get("importance_score") or 0.0))
    salience = max(0.1, float(fact.get("salience_weight") or 1.0))
    last_reference = float(fact.get("last_referenced_at") or 0.0)
    if last_reference <= 0:
        last_reference = float(fact.get("created_at") or now)
    days_since_reference = max(0.0, (float(now) - last_reference) / 86400.0)
    recency_factor = math.exp(-days_since_reference / max(float(half_life_days), 1.0))
    reference_count = max(0, int(fact.get("reference_count") or 0))
    revisit_factor = 1.0 + (0.1 * min(reference_count, 10))
    effective = base_confidence * salience * recency_factor * revisit_factor
    return DecayWeightedFact(
        fact=dict(fact),
        effective_score=effective,
        recency_factor=recency_factor,
        revisit_factor=revisit_factor,
    )


def queue_reference_bump(*, user_id: int, facts: List[DecayWeightedFact], brain_client: Any = None) -> None:
    """Fire-and-forget reference bump for facts injected into prompts.

    Batch 5, task 5 (2026-09-28, rulings R24/R41): a thin wrapper — it takes
    the row ids off the weighted facts and hands them to
    ``queue_reference_bump_ids``, which does the work. Signature unchanged.
    """
    ids = [int(item.fact["id"]) for item in facts if item.fact.get("id") is not None]
    queue_reference_bump_ids(user_id=user_id, fact_ids=ids, brain_client=brain_client)


def queue_reference_bump_ids(
    *, user_id: int, fact_ids: Sequence[Any], brain_client: Any = None
) -> Optional[asyncio.Task[None]]:
    """Fire-and-forget reference bump for ``user_memory`` rows by id (Batch 5,
    task 5, 2026-09-28, ruling R41): the reflection's call for the facts the
    reply's "Things I Know About This User" band rendered — the first caller
    this bump has had. Each id once, in order; ``None`` and anything that is
    not an integer are skipped. With no running loop the bump is skipped at
    debug. Returns the task (a caller that wants to can wait for it), or
    ``None`` when nothing was queued.
    """
    ids: List[int] = []
    for raw in fact_ids or ():
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    if not ids:
        return None
    active_brain = brain_client or brain
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("fact_contradiction: skipped reference bump reason=no_running_loop")
        return None
    task = loop.create_task(_bump_reference_counts(user_id=int(user_id), memory_ids=ids, brain_client=active_brain))
    task.add_done_callback(_log_background_failure)
    return task


async def _bump_reference_counts(*, user_id: int, memory_ids: List[int], brain_client: Any) -> None:
    try:
        await brain_client.bump_user_memory_references(user_id=int(user_id), memory_ids=memory_ids)
        logger.debug("fact_contradiction: reference_bumped user=%s count=%s", user_id, len(memory_ids))
    except Exception as exc:
        logger.warning("fact_contradiction: reference_bump_failed user=%s error=%s", user_id, exc)


def _log_background_failure(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except Exception as exc:
        logger.warning("fact_contradiction: background_task_failed error=%s", exc)


def _fact_memory_config() -> Any:
    try:
        return settings_service.snapshot().fact_memory
    except Exception:
        return type(
            "FactMemoryDefaults",
            (),
            {
                "half_life_days": 30.0,
                "min_score_threshold": 0.15,
                "max_facts_per_prompt": 8,
            },
        )()


def _record_from_row(row: Any) -> Dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "memory_type": row[2],
        "content": row[3],
        "importance_score": row[4],
        "created_at": row[5],
        "last_accessed": row[6],
        "access_count": row[7],
        "tags": row[8],
        "last_referenced_at": row[9] if len(row) > 9 else row[5],
        "reference_count": row[10] if len(row) > 10 else row[7],
        "salience_weight": row[11] if len(row) > 11 else 1.0,
        "status": row[12] if len(row) > 12 else "active",
        "superseded_by_fact_id": row[13] if len(row) > 13 else None,
        "source_detail": row[14] if len(row) > 14 else {},
    }
