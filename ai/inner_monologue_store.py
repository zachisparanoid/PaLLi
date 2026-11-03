"""
Inner monologue storage layer.

Stores PaLLi's private thoughts as dated JSONL files under data/inner_monologue/.
Each line is a timestamped entry with the thought, situation snapshot, and tick metadata.
"""

from __future__ import annotations

import json
import logging
import os
import time
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MONOLOGUE_DIR = Path("data/inner_monologue")


# Phase 4 — candidate strings too shallow to drive a proactive cycle.
# Collapsed to None at parse time so they never hit storage or retrieval.
# The goal is to protect against the monologue LLM emitting throwaway
# tokens just because the field exists in the schema.
_TRIVIAL_CANDIDATE_MARKERS = frozenset({
    "",
    "check in",
    "checkin",
    "check-in",
    "say hi",
    "hi",
    "n/a",
    "na",
    "nothing",
    "none",
    "no candidate",
    "no hunch",
    "null",
})


def _normalize_candidate(raw: Any) -> Optional[str]:
    """Collapse empty/whitespace/trivial candidate strings to None.

    Phase 4 contract: `proactive_candidate` is optional. Most ticks emit
    none. When the LLM emits a throwaway like "check in", we drop it so
    it never triggers a proactive cycle.
    """
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.lower() in _TRIVIAL_CANDIDATE_MARKERS:
        return None
    return cleaned


@dataclass
class MonologueEntry:
    """A single inner monologue thought."""

    timestamp: float
    thought: str
    situation_snapshot: Dict[str, Any]
    tick_metadata: Dict[str, Any] = field(default_factory=dict)
    # Phase 3: structured sentiment reading for the user at this tick.
    # Optional for backward compatibility — pre-Phase-3 records don't
    # have it, and the trend aggregator tolerates its absence.
    sentiment: Optional[Dict[str, Any]] = None
    # Phase 4: optional hunch the proactive pipeline may act on. Absent
    # on most ticks — only set when the monologue LLM noticed something
    # worth reaching out about. Never shown raw to the user; consumed
    # by proactive generation as a topic hint.
    proactive_candidate: Optional[str] = None
    # Jarvis-direction (2026-05-05): when proactive_candidate is set,
    # candidate_kind tags whether the hunch is "observation" (worth
    # speaking about — synthesize a Discord/voice message) or "action"
    # (worth doing autonomously — route through local_shell/read_file
    # via the proactive-action path). Default "observation" for backward
    # compatibility with pre-action ticks. None means "no candidate."
    candidate_kind: Optional[str] = None
    # Jarvis-direction: when candidate_kind="action", the LLM may suggest
    # the actual command/path to run. Free-form natural-language hint,
    # interpreted downstream by the local_shell intent classifier or
    # read_file path resolver — not executed verbatim.
    candidate_action_hint: Optional[str] = None

    @property
    def is_substantive(self) -> bool:
        """Whether this tick produced a meaningful thought (not a 'nothing to note')."""
        return bool(self.tick_metadata.get("substantive", True))

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def datetime_local(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_readable(self) -> str:
        """Human-readable format for injection into conversation context."""
        local_time = self.datetime_local.strftime("%I:%M %p").lstrip("0")
        return f"[{local_time}] {self.thought}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MonologueEntry:
        sentiment_raw = data.get("sentiment")
        sentiment = dict(sentiment_raw) if isinstance(sentiment_raw, dict) else None
        candidate = _normalize_candidate(data.get("proactive_candidate"))
        kind_raw = data.get("candidate_kind")
        kind = str(kind_raw).strip().lower() if isinstance(kind_raw, str) else None
        if kind not in {"observation", "action"}:
            kind = None
        action_hint_raw = data.get("candidate_action_hint")
        action_hint = (
            str(action_hint_raw).strip()
            if isinstance(action_hint_raw, str) and action_hint_raw.strip()
            else None
        )
        # If we have a candidate but no kind, default to "observation" so
        # legacy pre-action entries flow through the existing message path.
        if candidate is not None and kind is None:
            kind = "observation"
        return cls(
            timestamp=float(data.get("timestamp", 0)),
            thought=str(data.get("thought", "")),
            situation_snapshot=dict(data.get("situation_snapshot") or {}),
            tick_metadata=dict(data.get("tick_metadata") or {}),
            sentiment=sentiment,
            proactive_candidate=candidate,
            candidate_kind=kind,
            candidate_action_hint=action_hint,
        )


def _date_filename(dt: Optional[datetime] = None) -> str:
    """Return the JSONL filename for a given date."""
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%d") + ".jsonl"


def _ensure_dir() -> Path:
    MONOLOGUE_DIR.mkdir(parents=True, exist_ok=True)
    return MONOLOGUE_DIR


def write_entry(entry: MonologueEntry) -> Path:
    """Append a monologue entry to today's JSONL file. Returns the file path."""
    directory = _ensure_dir()
    filepath = directory / _date_filename(entry.datetime_local)
    line = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":"))
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.debug("Wrote monologue entry to %s", filepath.name)
    _queue_inner_memory_index(entry)
    # Present Moment sense F (2026-07-24): a thought landed — substance
    # flag only, never the thought.
    try:
        from ai.present_moment import notice as _pm_notice
        _pm_notice(
            "monologue_thought",
            substantive=bool((entry.tick_metadata or {}).get("substantive")),
        )
    except Exception:
        pass
    return filepath


def _queue_inner_memory_index(entry: MonologueEntry) -> None:
    """Best-effort Phase 7c index hook; never blocks JSONL writes."""
    try:
        from ai.inner_memory_index import index_entry

        task = asyncio.get_running_loop().create_task(index_entry(entry))

        def _log_failure(done_task: asyncio.Task[Any]) -> None:
            try:
                done_task.result()
            except Exception as exc:
                logger.warning("inner_memory: index_failed kind=monologue error=%s", exc)

        task.add_done_callback(_log_failure)
    except RuntimeError:
        # Sync context (no running loop): index inline via asyncio.run so
        # the entry still lands in the FTS index instead of being dropped.
        try:
            asyncio.run(index_entry(entry))
        except Exception as exc:
            logger.warning("inner_memory: index_failed kind=monologue error=%s", exc)
    except Exception as exc:
        logger.warning("inner_memory: index_failed kind=monologue error=%s", exc)


def read_entries(
    *,
    days_back: int = 1,
    substantive_only: bool = False,
    limit: Optional[int] = None,
) -> List[MonologueEntry]:
    """
    Read recent monologue entries, newest first.

    Args:
        days_back: How many days of files to read (1 = today only, 2 = today + yesterday, etc.)
        substantive_only: If True, skip entries where the tick produced nothing meaningful.
        limit: Maximum number of entries to return.
    """
    directory = _ensure_dir()
    entries: List[MonologueEntry] = []

    now = datetime.now()
    for day_offset in range(days_back):
        offset_date = datetime.fromtimestamp(now.timestamp() - day_offset * 86400)
        filepath = directory / _date_filename(offset_date)
        if not filepath.exists():
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = MonologueEntry.from_dict(data)
                        if substantive_only and not entry.is_substantive:
                            continue
                        entries.append(entry)
                    except (json.JSONDecodeError, KeyError, TypeError) as exc:
                        logger.debug("Skipping malformed monologue line: %s", exc)
        except OSError as exc:
            logger.warning("Could not read monologue file %s: %s", filepath, exc)

    # Sort newest first
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    if limit is not None:
        entries = entries[:limit]
    return entries


def read_entries_for_day(
    *,
    day: str,
    substantive_only: bool = True,
) -> List[MonologueEntry]:
    """All monologue entries for one specific calendar day (its dated file),
    oldest first — the Wave 1a consolidation input. Unlike read_entries
    (today-relative days_back), this reaches ANY archived day."""
    filepath = MONOLOGUE_DIR / f"{str(day).strip()}.jsonl"
    entries: List[MonologueEntry] = []
    if not filepath.exists():
        return entries
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = MonologueEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    logger.debug("Skipping malformed monologue line: %s", exc)
                    continue
                if substantive_only and not entry.is_substantive:
                    continue
                entries.append(entry)
    except OSError as exc:
        logger.warning("Could not read monologue file %s: %s", filepath, exc)
    entries.sort(key=lambda e: e.timestamp)
    return entries


def read_recent_for_context(
    *,
    max_entries: int = 5,
    max_age_hours: float = 24.0,
) -> List[MonologueEntry]:
    """
    Read the most recent substantive entries suitable for injection into
    conversation context or the next monologue tick.
    """
    cutoff = time.time() - (max_age_hours * 3600)
    entries = read_entries(
        days_back=2,
        substantive_only=True,
        limit=max_entries * 3,  # over-fetch to allow filtering
    )
    filtered = [e for e in entries if e.timestamp >= cutoff]
    return filtered[:max_entries]


def read_recent_with_candidates(
    *,
    max_age_hours: float,
) -> List[MonologueEntry]:
    """Return recent substantive entries carrying a non-trivial proactive_candidate.

    Phase 4. Newest-first so callers can take the most-recent unconsumed
    one without additional sorting. `max_age_hours` bounds the staleness
    window. Entries without a candidate are excluded.
    """
    cutoff = time.time() - (max_age_hours * 3600)
    entries = read_entries(
        days_back=2,
        substantive_only=True,
        limit=None,
    )
    return [
        e for e in entries
        if e.timestamp >= cutoff and e.proactive_candidate
    ]


def format_entries_for_prompt(entries: List[MonologueEntry]) -> str:
    """Format monologue entries as readable text for prompt injection."""
    if not entries:
        return "No recent thoughts."
    lines = [entry.to_readable() for entry in entries]
    return "\n".join(lines)


def count_today_ticks() -> int:
    """Count how many monologue ticks have run today (for adaptive frequency)."""
    filepath = _ensure_dir() / _date_filename()
    if not filepath.exists():
        return 0
    count = 0
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except OSError:
        pass
    return count


def read_recent_digest_context(
    *,
    max_age_days: int = 90,
    tier: str = "weekly",
) -> List[Any]:
    """Read recent monologue digest entries newest-first.

    This is an opt-in long-range context path. It does not replace any raw
    monologue readers used by ticks, reply context, or Phase 4 candidates.
    """
    from ai.monologue_compaction import MonologueDigestEntry

    tiers = ["weekly", "monthly"] if tier == "all" else [tier]
    cutoff = time.time() - (max_age_days * 86400)
    digests: List[MonologueDigestEntry] = []
    for selected_tier in tiers:
        directory = MONOLOGUE_DIR / "digests" / selected_tier
        if not directory.exists():
            continue
        for path in directory.glob("*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        entry = MonologueDigestEntry.from_dict(json.loads(line))
                        if entry.created_at >= cutoff:
                            digests.append(entry)
                        break
            except Exception as exc:
                logger.debug("Could not read monologue digest %s: %s", path, exc)
    digests.sort(key=lambda entry: (entry.period_end, entry.created_at), reverse=True)
    return digests
