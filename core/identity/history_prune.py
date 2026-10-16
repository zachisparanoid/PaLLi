"""Identity history keeps what's useful (Batch 8, task 3, item 2.11; owner
call B8-3, 2026-09-30).

Every change to one of her identity documents saves a full copy in
``identity_document_versions`` (``core/identity/profile_store.py``). Nothing
ever removed one, so the table grew to two thirds of her database — nearly
all of it full copies of CHANGELOG and MEMORY. The owner's rule, per
(user, platform, doc_key) stream:

- the newest ``keep_last`` copies (10);
- the newest copy of each day for the last ``daily_days`` days (30);
- the newest copy of each ISO week before that;
- and ALWAYS the oldest copy: first contact is ``MIN(created_at)`` over a
  user's rows (``core/first_contact_targets.py``), so the first copy ever is
  never removed.

The live documents are never touched — only old copies of them go. Days and
weeks are her local calendar (the night cycle's). Ties on ``created_at`` are
broken by id: of two copies saved in the same instant, the higher id is the
newer. ``keep_last`` 0 turns the prune off.

``plan_prune`` is pure; ``prune_history`` reads the stream metadata, plans,
and deletes in batches inside one transaction (``BEGIN IMMEDIATE``, so a copy
saved meanwhile waits rather than racing the plan). The freed pages stay in
the file until they are reused; ``palli_ctl db shrink`` gives them back to
the disk (a wrap step, by hand).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Hashable, Iterable, List, Mapping, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DAY_SECONDS = 86400.0
# The schema's defaults (core/settings_schema.py MemoryConfig), used when the
# settings cannot be read. 0 keeps the prune off until the owner's rows say
# otherwise (Batch 8 ruling G5: dark until the wrap).
DEFAULT_KEEP_LAST = 0
DEFAULT_DAILY_DAYS = 30
# Ids per DELETE: well under SQLite's oldest bound-variable limit (999).
DELETE_BATCH = 500

StreamKey = Tuple[Any, Any, Any]


def _stream_key(row: Mapping[str, Any]) -> StreamKey:
    return (row.get("user_id"), row.get("platform"), row.get("doc_key"))


def _order(row: Mapping[str, Any]) -> Tuple[float, int]:
    return (float(row.get("created_at") or 0.0), int(row["id"]))


def _bucket(created_at: float, cutoff: float) -> Hashable:
    """The calendar slot a copy competes in: its local day inside the daily
    window, its ISO week before it."""
    when = datetime.fromtimestamp(created_at)
    if created_at >= cutoff:
        return ("day", when.date())
    year, week, _ = when.isocalendar()
    return ("week", year, week)


def _plan_stream(rows: List[Mapping[str, Any]], *, keep_last: int, cutoff: float) -> Set[int]:
    """The ids of one stream to delete."""
    ordered = sorted(rows, key=_order)
    keep: Set[int] = {int(ordered[0]["id"]), int(ordered[-1]["id"])}   # the first ever, the newest
    keep.update(int(row["id"]) for row in ordered[-keep_last:])
    newest_in_slot: Dict[Hashable, int] = {}
    for row in ordered:   # oldest first, so the last write per slot is its newest copy
        newest_in_slot[_bucket(_order(row)[0], cutoff)] = int(row["id"])
    keep.update(newest_in_slot.values())
    return {int(row["id"]) for row in ordered} - keep


def plan_prune(
    rows: Iterable[Mapping[str, Any]],
    *,
    keep_last: int,
    daily_days: int = DEFAULT_DAILY_DAYS,
    now: float,
) -> Set[int]:
    """The ids to delete. ``rows`` carry ``id``, ``user_id``, ``platform``,
    ``doc_key`` and ``created_at`` (epoch seconds); anything else is ignored.
    Each stream is planned on its own. ``keep_last`` 0 (or less) means off:
    nothing is planned. The oldest and the newest copy of every stream are
    never in the result."""
    if int(keep_last) <= 0:
        return set()
    cutoff = float(now) - max(0, int(daily_days)) * DAY_SECONDS
    streams: Dict[StreamKey, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        streams[_stream_key(row)].append(row)
    doomed: Set[int] = set()
    for members in streams.values():
        doomed |= _plan_stream(members, keep_last=int(keep_last), cutoff=cutoff)
    return doomed


@dataclass(frozen=True)
class StreamPrune:
    """One (user, platform, doc_key) stream's share of a prune."""

    user_id: Any
    platform: Any
    doc_key: Any
    kept: int
    removed: int
    kept_bytes: int
    removed_bytes: int


@dataclass(frozen=True)
class PruneResult:
    """What a prune kept and removed (``bytes``: the removed copies' content
    and frontmatter, in bytes). A dry run removes nothing; its numbers say
    what a real run would."""

    kept: int
    removed: int
    bytes: int
    kept_bytes: int
    dry_run: bool
    streams: Tuple[StreamPrune, ...]


_SELECT_STREAMS = (
    "SELECT id, user_id, platform, doc_key, created_at, "
    "COALESCE(length(CAST(content AS BLOB)), 0) + COALESCE(length(CAST(frontmatter AS BLOB)), 0) "
    "FROM identity_document_versions"
)


def _result(rows: List[Dict[str, Any]], doomed: Set[int], *, dry_run: bool) -> PruneResult:
    per: Dict[StreamKey, List[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row in rows:
        tally = per[_stream_key(row)]
        if row["id"] in doomed:
            tally[1] += 1
            tally[3] += row["size"]
        else:
            tally[0] += 1
            tally[2] += row["size"]
    streams = tuple(
        StreamPrune(user_id=key[0], platform=key[1], doc_key=key[2],
                    kept=t[0], removed=t[1], kept_bytes=t[2], removed_bytes=t[3])
        for key, t in sorted(per.items(), key=lambda item: tuple(str(part) for part in item[0]))
    )
    return PruneResult(
        kept=sum(s.kept for s in streams),
        removed=sum(s.removed for s in streams),
        bytes=sum(s.removed_bytes for s in streams),
        kept_bytes=sum(s.kept_bytes for s in streams),
        dry_run=dry_run,
        streams=streams,
    )


async def prune_history(
    db: Any,
    *,
    keep_last: int,
    daily_days: int = DEFAULT_DAILY_DAYS,
    now: Optional[float] = None,
    dry_run: bool = False,
) -> PruneResult:
    """Prune ``identity_document_versions`` on ``db`` (a ``PalliBrain``).
    The plan is read and the deletes run in one transaction; a failure part
    way rolls every delete back. A dry run reads and plans only. Logs
    ``history_prune: kept K, removed R, bytes B``."""
    clock = time.time() if now is None else float(now)
    conn = await db.get_connection()
    try:
        if dry_run or int(keep_last) <= 0:
            fetched = await conn.fetch(_SELECT_STREAMS)
            rows = _rows(fetched)
            doomed = plan_prune(rows, keep_last=keep_last, daily_days=daily_days, now=clock)
        else:
            async with conn.transaction():
                fetched = await conn.fetch(_SELECT_STREAMS)
                rows = _rows(fetched)
                doomed = plan_prune(rows, keep_last=keep_last, daily_days=daily_days, now=clock)
                ordered = sorted(doomed)
                for start in range(0, len(ordered), DELETE_BATCH):
                    batch = ordered[start:start + DELETE_BATCH]
                    marks = ",".join(f"${i}" for i in range(1, len(batch) + 1))
                    await conn.execute(
                        f"DELETE FROM identity_document_versions WHERE id IN ({marks})", *batch
                    )
    finally:
        await conn.close()
    result = _result(rows, doomed, dry_run=dry_run)
    if dry_run:
        logger.info("history_prune (dry run): would keep %d, would remove %d, bytes %d",
                    result.kept, result.removed, result.bytes)
    else:
        logger.info("history_prune: kept %d, removed %d, bytes %d",
                    result.kept, result.removed, result.bytes)
    return result


def _rows(fetched: Iterable[Any]) -> List[Dict[str, Any]]:
    return [
        {"id": int(r[0]), "user_id": r[1], "platform": r[2], "doc_key": r[3],
         "created_at": float(r[4] or 0.0), "size": int(r[5] or 0)}
        for r in fetched
    ]


def _as_count(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def history_prune_settings(memory: Any = None) -> Tuple[int, int]:
    """``(memory.history_keep_last, memory.history_daily_days)``. With no
    ``memory`` block given, the settings snapshot's is read; the schema's
    defaults stand in for anything missing or unreadable (the
    ``memory.thread_archive_days`` reader's pattern)."""
    if memory is None:
        try:
            from core.settings_boot import settings_service

            memory = settings_service.snapshot().memory
        except Exception:
            memory = None
    return (
        _as_count(getattr(memory, "history_keep_last", DEFAULT_KEEP_LAST), DEFAULT_KEEP_LAST),
        _as_count(getattr(memory, "history_daily_days", DEFAULT_DAILY_DAYS), DEFAULT_DAILY_DAYS),
    )
