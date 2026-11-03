"""Phase 4 — monologue-fed proactive candidate bridge.

This module is the one place that reads BOTH the monologue JSONL (via
`ai/inner_monologue_store.py`) and the proactive-record history (via
`brain.list_recent_proactive_actions_for_user`, which post-C1.5 reads
``open_threads WHERE source_kind='proactive_record'`` per the B5
Decision-5 design) to decide whether a recent monologue hunch is
eligible to drive a proactive send.

Keeping this logic in its own module preserves separation: the monologue
store stays free of proactive-action knowledge, and the proactive
orchestration stays free of JSONL-parsing knowledge.

`get_eligible_candidate()` returns at most one entry — the most-recent
unconsumed non-trivial candidate within the staleness window. A candidate
is "consumed" when a prior proactive-record row for this owner cites
the candidate's tick_timestamp in its `trigger_reason` payload (now
JSON-decoded by the writer; the consumed-check accepts either dict or
string for legacy compatibility).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ai.inner_monologue_store import MonologueEntry, read_recent_with_candidates
from core.config import config
from core.database import brain

logger = logging.getLogger(__name__)

# Rumination suppressions already announced at INFO this process (log-once).
_RUMINATION_LOGGED: set[str] = set()


# How many recent proactive-record rows to scan when checking whether a
# candidate tick has been consumed. A small window is fine because the
# staleness window is short (default 6h) and MAX_DAILY_PROACTIVE_MESSAGES
# caps activity per day — 20 rows is ~a week of history.
# (Backed by open_threads source_kind='proactive_record' post-C1.5.)
_CONSUMED_CHECK_WINDOW = 20


@dataclass(frozen=True)
class EligibleCandidate:
    """A monologue candidate that is fresh AND not yet consumed."""

    entry: MonologueEntry

    @property
    def tick_timestamp(self) -> float:
        return self.entry.timestamp

    @property
    def text(self) -> str:
        return self.entry.proactive_candidate or ""

    @property
    def kind(self) -> str:
        """Either 'observation' (speak about it) or 'action' (do it)."""
        raw = (self.entry.candidate_kind or "observation").strip().lower()
        return raw if raw in {"observation", "action"} else "observation"

    @property
    def action_hint(self) -> str:
        """Natural-language hint for the action when kind=='action'."""
        return self.entry.candidate_action_hint or ""

    @property
    def timestamp(self) -> float:
        return self.entry.timestamp


def _format_tick_timestamp(ts: float) -> str:
    """Canonical string form for the tick_timestamp field in metadata.

    Using a fixed %.6f format on BOTH writer and reader keeps the string
    comparison deterministic regardless of float->str rounding differences.
    """
    return f"{ts:.6f}"


async def _is_candidate_consumed(
    *, owner_user_id: int, tick_timestamp: float,
) -> bool:
    """Has a proactive_action already been logged citing this tick?

    Scans the last N proactive-action rows for this user, parses each
    row's `trigger_reason` JSON blob, and returns True if any cite the
    given tick_timestamp in `candidate_tick_timestamp`.

    Fails closed: on any unexpected error we assume "consumed" so a
    transient DB hiccup never causes a double-send.
    """
    target = _format_tick_timestamp(float(tick_timestamp))
    try:
        rows = await brain.list_recent_proactive_actions_for_user(
            int(owner_user_id),
            limit=_CONSUMED_CHECK_WINDOW,
        )
    except Exception as exc:
        logger.warning(
            "phase4: consumed-check DB fetch failed for user %s tick %s: %s",
            owner_user_id, target, exc,
        )
        return True

    for row in rows or []:
        trigger_reason = row.get("trigger_reason")
        if not trigger_reason:
            continue
        if isinstance(trigger_reason, str):
            try:
                payload: Dict[str, Any] = json.loads(trigger_reason)
            except (ValueError, TypeError):
                continue
        elif isinstance(trigger_reason, dict):
            payload = trigger_reason
        else:
            continue
        if str(payload.get("candidate_tick_timestamp") or "") == target:
            return True
    return False


async def get_eligible_candidate(
    *,
    owner_user_id: int,
    staleness_hours: Optional[float] = None,
) -> Optional[EligibleCandidate]:
    """Return the most-recent unconsumed non-trivial candidate, or None.

    Phase 4 policy: "most-recent wins." If the most-recent candidate is
    already consumed, walk back through the staleness window until we
    find an unconsumed one OR run out.

    2026-05-06 rumination fix: a same-hunch candidate emitted under a
    new tick_timestamp wasn't considered "consumed" by the prior
    proactive_action row. The result was the SAME outreach text firing
    8+ times across an evening, just at different ticks. Now we also
    suppress fresh candidates whose text is near-identical to ANY
    candidate that fired in the past `_RUMINATION_DEDUP_HOURS` hours.

    Returns None when:
    - no monologue entries within the window carry a candidate
    - the window is zero (`PROACTIVE_CANDIDATE_STALENESS_HOURS=0`)
    - every fresh candidate has been consumed
    - every fresh candidate is a near-duplicate of one that fired recently
    - the consumed-check DB query fails (fail-closed)
    """
    window = float(
        staleness_hours
        if staleness_hours is not None
        else getattr(config, "PROACTIVE_CANDIDATE_STALENESS_HOURS", 6)
    )
    if window <= 0:
        return None

    fresh = read_recent_with_candidates(max_age_hours=window)
    if not fresh:
        return None

    # Build the recent-fired set once for fuzzy dedup
    rumination_hours = float(getattr(config, "PROACTIVE_RUMINATION_DEDUP_HOURS", 12) or 12)
    recently_fired_norms = await _recent_fired_candidate_norms(
        owner_user_id=owner_user_id,
        lookback_hours=rumination_hours,
    )

    for entry in fresh:  # already newest-first
        candidate_text = (entry.proactive_candidate or "").strip()
        norm = _candidate_norm(candidate_text)
        if norm and norm in recently_fired_norms:
            # Announce each suppressed candidate ONCE at INFO; the outreach
            # tick re-encounters the same candidate every ~10min for hours
            # and re-logging it is pure noise (owner report, 2026-07-14).
            level = logger.debug if norm in _RUMINATION_LOGGED else logger.info
            _RUMINATION_LOGGED.add(norm)
            level(
                "proactive_candidate_source: suppressing rumination loop — "
                "candidate %r matches a recently-fired hunch within %sh",
                candidate_text[:80], rumination_hours,
            )
            continue
        consumed = await _is_candidate_consumed(
            owner_user_id=owner_user_id,
            tick_timestamp=entry.timestamp,
        )
        if not consumed:
            return EligibleCandidate(entry=entry)

    return None


_RUMINATION_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "to", "for", "with", "of", "in",
    "on", "at", "by", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "i", "me", "my", "we", "you",
    "your", "their", "theirs", "they", "them", "him", "her", "his", "she",
    "he", "it", "its", "this", "that", "these", "those", "if", "when",
    "while", "as", "so", "from", "up", "about", "into", "out", "now",
    "today", "tonight", "evening", "morning", "yesterday", "tomorrow",
    "later", "soon", "again", "still", "really", "very", "just", "kind",
    "sort", "would", "could", "should", "might", "may", "will", "shall",
})


def _candidate_norm(text: str) -> str:
    """Normalize candidate text for fuzzy dedup.

    Lowercase, strip punctuation, drop stopwords, sort tokens, take the
    first 8 content words. Two candidates that mean the same thing in
    slightly different words ("gentle check-in this evening" vs. "send
    a low-pressure check-in tonight") collapse to similar normalized
    keys. Imperfect but blocks the most common rumination patterns.
    """
    if not text:
        return ""
    import re

    cleaned = re.sub(r"[^\w\s]+", " ", text.lower())
    tokens = [t for t in cleaned.split() if t and t not in _RUMINATION_STOPWORDS]
    # Top-N sorted to make order-independence — "check broker today" and
    # "today check broker" collapse together.
    head = sorted(tokens)[:8]
    return " ".join(head)


async def _recent_fired_candidate_norms(
    *,
    owner_user_id: int,
    lookback_hours: float,
) -> set[str]:
    """Pull recently-fired proactive_action rows, normalize their candidate
    text, return the set. Used to suppress near-duplicate hunches."""
    if lookback_hours <= 0:
        return set()
    try:
        rows = await brain.list_recent_proactive_actions_for_user(
            int(owner_user_id),
            limit=30,
        )
    except Exception:
        return set()
    cutoff = 0.0
    if lookback_hours > 0:
        import time as _t

        cutoff = _t.time() - (lookback_hours * 3600.0)
    norms: set[str] = set()
    for row in rows or []:
        scheduled_time = row.get("scheduled_time") or row.get("executed_time") or 0
        try:
            ts = float(scheduled_time)
        except (TypeError, ValueError):
            ts = 0.0
        if ts and ts < cutoff:
            continue
        # The candidate text lives in trigger_reason JSON or in content
        trigger_reason = row.get("trigger_reason") or ""
        candidate_text = ""
        if isinstance(trigger_reason, str) and trigger_reason.startswith("{"):
            try:
                payload = json.loads(trigger_reason)
                candidate_text = str(payload.get("candidate_text") or "")
            except (ValueError, TypeError):
                pass
        if not candidate_text:
            candidate_text = str(row.get("content") or "")
        norm = _candidate_norm(candidate_text)
        if norm:
            norms.add(norm)
    return norms
