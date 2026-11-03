from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ai import inner_monologue_store as store

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9_']+")
_MAX_RESULTS = 3
_SCHEMA_READY: set[str] = set()


@dataclass(frozen=True)
class IndexHit:
    entry_id: str
    source_kind: str
    source_path: str
    timestamp: float
    text_content: str
    sentiment_tag: str
    surfaced_candidate: str
    relevance_score: float
    age_days: float


def _config() -> Any:
    try:
        from core.settings_boot import settings_service

        return settings_service.snapshot().inner_memory
    except Exception:
        return SimpleNamespace(
            enabled=True,
            max_results_per_query=3,
            min_relevance=0.5,
            max_age_days=180,
            recency_half_life_days=60.0,
            query_timeout_ms=1500,
            reply_turn_enabled=True,
            proactive_enabled=True,
            reflection_enabled=True,
        )


_DEFAULT_QUERY_TIMEOUT_MS = 1500
_MIN_QUERY_TIMEOUT_S = 0.25


def _effective_timeout_seconds(cfg: Any = None) -> float:
    """The passive search's budget in seconds: ``query_timeout_ms`` (1.5 s
    when unset), floored at 250 ms in code. 40 ms timed out on 23 of 17
    turns on 2026-09-25 -- her own past thoughts never reached the reply --
    and a config typo must not bring that back."""
    if cfg is None:
        cfg = _config()
    configured = getattr(cfg, "query_timeout_ms", _DEFAULT_QUERY_TIMEOUT_MS) or _DEFAULT_QUERY_TIMEOUT_MS
    return max(float(configured) / 1000.0, _MIN_QUERY_TIMEOUT_S)


def _surprise_boost() -> float:
    """Salience stamped on entries indexed inside a surprising window
    (organ 8, spec §4). 1.0 whenever the organ is off or unreadable —
    a boost never demotes and never fails loud."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "surprise", False)):
            return 1.0
        return max(1.0, float(getattr(comp, "surprise_salience_boost", 1.5)))
    except Exception:
        return 1.0


def _fear_boost() -> float:
    """The fear window's twin (organ 6, step 24 §4) — same laws: 1.0
    whenever the organ is off or unreadable; a boost never demotes."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "fears", False)):
            return 1.0
        return max(1.0, float(getattr(comp, "fear_salience_boost", 1.5)))
    except Exception:
        return 1.0


def _reset_schema_cache_for_tests() -> None:
    _SCHEMA_READY.clear()


# Recall bias (organ 4's second half, spec §6): congruence sets are fixed
# vocabulary presentation (the sentiment tagger's own words), never meaning.
_WARM_TAGS = {"playful", "curious", "content", "open"}
_HEAVY_TAGS = {"tired", "stressed", "frustrated", "vulnerable"}


def _recall_bias_lean_impl():
    """The current lean, or None when the flag is off / no moment / the
    moment is stale (a stale mood must not tilt recall) / the lean is
    level. Never raises."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "moment_recall_bias", False)):
            return None
        from ai.present_moment_store import read_current

        current = read_current()
        if current is None:
            return None
        entry, _age, is_stale = current
        if is_stale:
            return None
        lean = str((getattr(entry, "meta", {}) or {}).get("lean") or "")
        return lean if lean in ("warm", "heavy") else None
    except Exception:
        return None


# The root conftest pins THIS name to None for all tests (the owner-picture
# guard class — the live yaml flips moment_recall_bias on and lazy-loads
# into bare pytest); gate tests exercise _recall_bias_lean_impl directly
# (the introspection/passions _impl alias pattern).
_recall_bias_lean = _recall_bias_lean_impl


def _recall_bias_boost() -> float:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return max(1.0, float(getattr(comp, "recall_bias_boost", 1.15)))
    except Exception:
        return 1.15


# One truth for the table shape: creation and the salience migration's
# swap table share it (organ 8, spec §4 — the column is UNINDEXED data,
# NULL on every pre-surprise row, and NULL scores as 1.0).
def _create_sql(table: str) -> str:
    return f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(
                entry_id UNINDEXED,
                source_kind,
                source_path UNINDEXED,
                timestamp UNINDEXED,
                text_content,
                sentiment_tag,
                surfaced_candidate,
                salience UNINDEXED,
                tokenize = 'porter unicode61'
            )
            """


async def ensure_schema(*, brain_client: Any = None) -> None:
    """Create the local FTS5 inner-memory index if needed — and self-migrate
    an old table to the salience shape at first touch (spec §4, the owner's
    trust-check change: no manual rebuild step, no unmigrated limbo).

    FTS5 cannot ALTER TABLE ADD COLUMN, so the migration is an atomic
    copy-swap in ONE transaction: create the new-shape table, copy every
    row (salience NULL ⇒ scores 1.0), drop the old, rename. A failed
    migration rolls back and KEEPS the old table (spec §9) — the key is
    not cached, so the next touch retries; the rebuild script remains the
    manual fallback."""
    from core.database import brain as default_brain

    db = brain_client or default_brain
    key = str(getattr(db, "db_path", id(db)))
    if key in _SCHEMA_READY:
        return
    conn = await db.get_connection()
    try:
        cols = [
            str(row[1])
            for row in await conn.fetch("PRAGMA table_info(monologue_memory_fts)")
        ]
        if cols and "salience" not in cols:
            logger.warning(
                "inner_memory: migrating index for salience (one-time copy-swap)"
            )
            try:
                async with conn.transaction():
                    await conn.execute(_create_sql("monologue_memory_fts_new"))
                    await conn.execute(
                        "INSERT INTO monologue_memory_fts_new "
                        "(entry_id, source_kind, source_path, timestamp, "
                        "text_content, sentiment_tag, surfaced_candidate) "
                        "SELECT entry_id, source_kind, source_path, timestamp, "
                        "text_content, sentiment_tag, surfaced_candidate "
                        "FROM monologue_memory_fts"
                    )
                    await conn.execute("DROP TABLE monologue_memory_fts")
                    await conn.execute(
                        "ALTER TABLE monologue_memory_fts_new "
                        "RENAME TO monologue_memory_fts"
                    )
            except Exception as exc:
                logger.warning(
                    "inner_memory: salience migration failed (old table kept, "
                    "will retry at next touch): %s",
                    exc,
                )
                return
        else:
            await conn.execute(_create_sql("monologue_memory_fts"))
        _SCHEMA_READY.add(key)
    finally:
        await conn.close()


async def index_entry(entry: store.MonologueEntry, *, brain_client: Any = None) -> None:
    """Upsert one raw monologue entry into the FTS index."""
    from core.database import brain as default_brain

    db = brain_client or default_brain
    await ensure_schema(brain_client=db)
    conn = await db.get_connection()
    try:
        await _index_entry_with_conn(conn, entry)
    finally:
        await conn.close()


async def index_digest(digest: Any, *, brain_client: Any = None) -> None:
    """Upsert one Phase 7b digest into the FTS index."""
    from core.database import brain as default_brain

    db = brain_client or default_brain
    await ensure_schema(brain_client=db)
    conn = await db.get_connection()
    try:
        await _index_digest_with_conn(conn, digest)
    finally:
        await conn.close()


async def index_day_gist(
    *, day: str, text: str, source_path: str = "", brain_client: Any = None
) -> None:
    """Upsert one Wave-1a day-gist into the FTS index (source_kind='day_gist').

    Timestamped at LOCAL NOON of the day it gists — never the run day — so a
    backfilled gist for 2026-05-02 renders as early May everywhere downstream
    (honest date attribution, spec §4.1)."""
    from core.database import brain as default_brain

    db = brain_client or default_brain
    await ensure_schema(brain_client=db)
    conn = await db.get_connection()
    try:
        await _index_day_gist_with_conn(conn, day=day, text=text, source_path=source_path)
    finally:
        await conn.close()


async def query(
    *,
    text: str,
    max_results: int = 3,
    max_age_days: Optional[int] = None,
    min_relevance: Optional[float] = None,
    brain_client: Any = None,
) -> List[IndexHit]:
    """Return up to three relevant inner-memory hits for prompt injection."""
    cfg = _config()
    if not bool(getattr(cfg, "enabled", True)):
        return []
    limit = int(max_results or 0)
    if limit > _MAX_RESULTS:
        logger.warning("inner_memory: max_results clamped requested=%s max=%s", limit, _MAX_RESULTS)
    limit = max(1, min(_MAX_RESULTS, limit or int(getattr(cfg, "max_results_per_query", 3) or 3)))
    timeout = _effective_timeout_seconds(cfg)
    await ensure_schema(brain_client=brain_client)
    try:
        return await asyncio.wait_for(
            _query_inner(
                text=text,
                max_results=limit,
                max_age_days=int(max_age_days if max_age_days is not None else getattr(cfg, "max_age_days", 180)),
                min_relevance=float(min_relevance if min_relevance is not None else getattr(cfg, "min_relevance", 0.5)),
                half_life_days=float(getattr(cfg, "recency_half_life_days", 60.0) or 60.0),
                brain_client=brain_client,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("inner_memory: query_timeout text=%r timeout_ms=%s", str(text or "")[:60], int(timeout * 1000))
        return []



_INTROSPECTION_MAX_RESULTS = 10


async def query_for_introspection(
    text: str,
    *,
    max_results: int = 8,
    days_back: int = 30,
    min_relevance: float = 0.15,
    timeout_ms: int = 500,
    brain_client: Any = None,
) -> List[IndexHit]:
    """Deliberate self-search for the introspect tool (2026-07-13).

    Unlike ``query()`` -- tuned for sparse passive injection on the hot reply
    path (3-hit clamp, 1.5 s budget, recency half-life, 0.5 floor) -- this
    read is on-demand: a bigger (still code-clamped) budget, a ``days_back``
    window instead of recency decay ("have you EVER thought about X" must
    surface a strong old match), and a lower relevance floor. The passive
    reader's tuning is untouched.
    """
    limit = max(1, min(_INTROSPECTION_MAX_RESULTS, int(max_results or 0) or 8))
    window_days = max(1, min(365, int(days_back or 30)))
    timeout = max(float(timeout_ms or 500) / 1000.0, 0.001)
    await ensure_schema(brain_client=brain_client)
    try:
        return await asyncio.wait_for(
            _query_introspection_inner(
                text=text,
                max_results=limit,
                days_back=window_days,
                min_relevance=float(min_relevance),
                brain_client=brain_client,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "inner_memory: introspection_query_timeout text=%r timeout_ms=%s",
            str(text or "")[:60],
            int(timeout * 1000),
        )
        return []


async def _query_introspection_inner(
    *,
    text: str,
    max_results: int,
    days_back: int,
    min_relevance: float,
    brain_client: Any,
) -> List[IndexHit]:
    from core.database import brain as default_brain

    fts_query = _fts_query(text)
    if not fts_query:
        return []
    db = brain_client or default_brain
    conn = await db.get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT entry_id, source_kind, source_path, timestamp, text_content,
                   sentiment_tag, surfaced_candidate, bm25(monologue_memory_fts) AS rank
            FROM monologue_memory_fts
            WHERE monologue_memory_fts MATCH $1
            ORDER BY rank
            LIMIT $2
            """,
            fts_query,
            max(max_results * 12, 24),
        )
    except Exception as exc:
        logger.warning(
            "inner_memory: introspection_query_failed text=%r error=%s",
            str(text or "")[:60],
            exc,
        )
        return []
    finally:
        await conn.close()
    now = _now()
    cutoff = now - days_back * 86400.0
    surviving = [row for row in rows if float(row[3] or 0.0) >= cutoff]
    best_bm25 = max((abs(float(row[7] or 0.0)) for row in surviving), default=0.0)
    hits: List[IndexHit] = []
    for row in surviving:
        lexical = (abs(float(row[7] or 0.0)) / best_bm25) if best_bm25 > 0.0 else 1.0
        if lexical < min_relevance:
            continue
        timestamp = float(row[3] or 0.0)
        hits.append(
            IndexHit(
                entry_id=str(row[0] or ""),
                source_kind=str(row[1] or ""),
                source_path=str(row[2] or ""),
                timestamp=timestamp,
                text_content=str(row[4] or ""),
                sentiment_tag=str(row[5] or ""),
                surfaced_candidate=str(row[6] or ""),
                relevance_score=max(0.0, min(1.0, lexical)),
                age_days=max(0.0, (now - timestamp) / 86400.0),
            )
        )
    hits.sort(key=lambda hit: (hit.relevance_score, hit.timestamp), reverse=True)
    return _dedupe_hits(hits)[:max_results]


async def rebuild_from_archive(*, since: Optional[date] = None, brain_client: Any = None) -> Dict[str, Any]:
    """Rebuild the FTS index from raw monologue JSONL files and 7b digests."""
    from ai.monologue_compaction import MonologueDigestEntry
    from core.database import brain as default_brain

    started = time.time()
    db = brain_client or default_brain
    await ensure_schema(brain_client=db)
    counts = {"monologue_rows": 0, "weekly_digests": 0, "monthly_digests": 0, "day_gists": 0}
    conn = await db.get_connection()
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM monologue_memory_fts")
            for path in sorted(store.MONOLOGUE_DIR.glob("*.jsonl")):
                day = _date_from_stem(path.stem)
                if since is not None and day is not None and day < since:
                    continue
                for entry in _read_entries_from_path(path):
                    await _index_entry_with_conn(conn, entry)
                    counts["monologue_rows"] += 1
            for tier in ("weekly", "monthly"):
                for path in sorted((store.MONOLOGUE_DIR / "digests" / tier).glob("*.jsonl")):
                    digest = _read_digest_from_path(path, MonologueDigestEntry)
                    if digest is None:
                        continue
                    period_start = _date_from_iso(getattr(digest, "period_start", ""))
                    if since is not None and period_start is not None and period_start < since:
                        continue
                    await _index_digest_with_conn(conn, digest)
                    key = "weekly_digests" if tier == "weekly" else "monthly_digests"
                    counts[key] += 1
            # Wave 1a: day-gists live in the same index; a rebuild that
            # dropped them would amputate consolidated history from search.
            # Per-file tolerance + the VALIDATED filename stem as the key
            # (review fix): one hand-edited day field must not roll back the
            # whole rebuild, and stem-vs-content disagreements resolve to the
            # stem every other walker here trusts.
            from ai import day_gist_store

            for path in sorted(day_gist_store.DAY_GIST_DIR.glob("*.jsonl")):
                day = _date_from_stem(path.stem)
                if day is None or (since is not None and day < since):
                    continue
                try:
                    gist = day_gist_store.read_day_gist(
                        day=path.stem, root=day_gist_store.DAY_GIST_DIR
                    )
                    if gist is None or not gist.text.strip():
                        continue
                    await _index_day_gist_with_conn(
                        conn, day=day.isoformat(), text=gist.text,
                        source_path=path.as_posix(),
                    )
                    counts["day_gists"] += 1
                except Exception as exc:
                    logger.debug("inner_memory: skipped day-gist file %s: %s", path, exc)
    finally:
        await conn.close()
    counts["duration_seconds"] = round(time.time() - started, 3)
    logger.info(
        "inner_memory: rebuild duration_seconds=%s rows=%s",
        counts["duration_seconds"],
        counts["monologue_rows"] + counts["weekly_digests"] + counts["monthly_digests"],
    )
    return counts


def format_hits_for_prompt(hits: List[IndexHit]) -> str:
    """Render sparse memory hits for prompt injection."""
    selected = list(hits or [])[:_MAX_RESULTS]
    if not selected:
        return ""
    lines = ["## Recently noticed (from your own prior observations)"]
    for hit in selected:
        tag = hit.sentiment_tag or "noted"
        lines.append(f"- [{_age_label(hit.age_days)}, {tag}] {_clip(hit.text_content, 220)}")
    lines.append(
        "This is continuity context, not a tone or style instruction. Ignore this section for simple greetings, acknowledgements, and utility turns unless it contains a concrete fact needed for the answer."
    )
    lines.append(
        "Do not turn presence notes into availability phrases like 'I'm here whenever', 'happy to help', or 'anything you need'."
    )
    return "\n".join(lines)


def audit_injection(*, context: str, hits: List[IndexHit]) -> None:
    selected = list(hits or [])[:_MAX_RESULTS]
    if selected:
        logger.info(
            "inner_memory: injected context=%s entry_ids=%s scores=%s",
            context,
            [hit.entry_id for hit in selected],
            [round(hit.relevance_score, 3) for hit in selected],
        )


async def _query_inner(
    *,
    text: str,
    max_results: int,
    max_age_days: int,
    min_relevance: float,
    half_life_days: float,
    brain_client: Any,
) -> List[IndexHit]:
    from core.database import brain as default_brain

    started = time.time()
    fts_query = _fts_query(text)
    if not fts_query:
        return []
    db = brain_client or default_brain
    conn = await db.get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT entry_id, source_kind, source_path, timestamp, text_content,
                   sentiment_tag, surfaced_candidate, bm25(monologue_memory_fts) AS rank,
                   salience
            FROM monologue_memory_fts
            WHERE monologue_memory_fts MATCH $1
            ORDER BY rank
            LIMIT $2
            """,
            fts_query,
            max(max_results * 12, 24),
        )
    except Exception as exc:
        logger.warning("inner_memory: query_failed text=%r error=%s", str(text or "")[:60], exc)
        return []
    finally:
        await conn.close()
    # Wave 1a review fix: day-gists are deliberate-introspection material —
    # ambient-recall integration was scoped OUT of v1 (spec §3). The passive
    # reply-path reader must not serve undated whole-day summaries, and a
    # consolidation rollback must actually remove them from replies.
    rows = [row for row in rows if str(row[1] or "") != "day_gist"]
    # Recall bias (spec §6): the lean is read ONCE per query; None (flag
    # off / no moment / stale / level) means the multiply never runs —
    # today's ranking, byte-identical.
    lean = _recall_bias_lean()
    hits = _score_rows(
        rows,
        max_age_days=max_age_days,
        min_relevance=min_relevance,
        half_life_days=half_life_days,
        lean=lean,
    )
    selected = _dedupe_hits(hits)[:max_results]
    duration_ms = round((time.time() - started) * 1000.0, 3)
    event = logger.info if selected else logger.debug
    event(
        "inner_memory: query text=%r hits=%s min_score=%s duration_ms=%s",
        str(text or "")[:60],
        len(selected),
        round(min((hit.relevance_score for hit in selected), default=0.0), 3),
        duration_ms,
    )
    return selected


async def _index_entry_with_conn(conn: Any, entry: store.MonologueEntry) -> None:
    entry_id = _entry_id(entry)
    # Salience (organ 8, spec §4): a thought written inside a surprising
    # window is remembered harder — stamped at WRITE time from the window
    # state, by the entry's own timestamp (a rebuild keeps honest dates).
    salience = 1.0
    try:
        from ai.surprise_state import active_window

        if active_window(now=float(entry.timestamp)) is not None:
            salience = _surprise_boost()
    except Exception:
        salience = 1.0
    # The fear window (organ 6, step 24 §4) composes by MAX, never sum —
    # overlapping windows must not multiply; a failed read costs nothing.
    try:
        from ai.fear_state import active_window as _fear_window

        if _fear_window(now=float(entry.timestamp)) is not None:
            salience = max(salience, _fear_boost())
    except Exception:
        pass
    await conn.execute("DELETE FROM monologue_memory_fts WHERE entry_id = $1", entry_id)
    await conn.execute(
        """
        INSERT INTO monologue_memory_fts
        (entry_id, source_kind, source_path, timestamp, text_content, sentiment_tag, surfaced_candidate, salience)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        entry_id,
        "monologue",
        _entry_source_path(entry),
        float(entry.timestamp),
        str(entry.thought or ""),
        _entry_sentiment(entry),
        str(entry.proactive_candidate or ""),
        float(salience),
    )
    logger.debug("inner_memory: indexed kind=monologue entry_id=%s", entry_id)


async def _index_digest_with_conn(conn: Any, digest: Any) -> None:
    entry_id = f"digest:{getattr(digest, 'digest_id', '')}"
    source_kind = f"{getattr(digest, 'tier', 'weekly')}_digest"
    await conn.execute("DELETE FROM monologue_memory_fts WHERE entry_id = $1", entry_id)
    await conn.execute(
        """
        INSERT INTO monologue_memory_fts
        (entry_id, source_kind, source_path, timestamp, text_content, sentiment_tag, surfaced_candidate)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        entry_id,
        source_kind,
        _digest_source_path(digest),
        _digest_timestamp(digest),
        str(getattr(digest, "summary_text", "") or ""),
        str((list(getattr(digest, "dominant_sentiment_tags", []) or []) or [""])[0]),
        "; ".join(str(item) for item in list(getattr(digest, "surfaced_candidates", []) or [])),
    )
    logger.debug("inner_memory: indexed kind=%s entry_id=%s", source_kind, entry_id)


async def _index_day_gist_with_conn(
    conn: Any, *, day: str, text: str, source_path: str = ""
) -> None:
    entry_id = f"day_gist:{day}"
    timestamp = datetime.strptime(str(day), "%Y-%m-%d").replace(hour=12).timestamp()
    await conn.execute("DELETE FROM monologue_memory_fts WHERE entry_id = $1", entry_id)
    await conn.execute(
        """
        INSERT INTO monologue_memory_fts
        (entry_id, source_kind, source_path, timestamp, text_content, sentiment_tag, surfaced_candidate)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        entry_id,
        "day_gist",
        str(source_path or ""),
        timestamp,
        str(text or ""),
        "",
        "",
    )
    logger.debug("inner_memory: indexed kind=day_gist entry_id=%s", entry_id)


def _score_rows(rows: List[Any], *, max_age_days: int, min_relevance: float, half_life_days: float, lean=None) -> List[IndexHit]:
    now = _now()
    # FTS5 bm25() is more negative for stronger matches, and its magnitude swings with
    # corpus statistics (near zero when query terms appear in most rows). Normalising
    # against the best magnitude in this candidate set keeps lexical monotonic-increasing
    # in match strength, in [0, 1], and preserves min_relevance semantics: the best
    # lexical match scores 1.0 so relevance = recency for it, exactly as thresholds assume.
    # Normalise over age-surviving rows only: a lexically strong but expired row
    # must not depress every survivor's score below min_relevance.
    surviving = [
        row for row in rows
        if max(0.0, (now - float(row[3] or 0.0)) / 86400.0) <= max_age_days
    ]
    best_bm25 = max((abs(float(row[7] or 0.0)) for row in surviving), default=0.0)
    hits: List[IndexHit] = []
    for row in surviving:
        timestamp = float(row[3] or 0.0)
        age_days = max(0.0, (now - timestamp) / 86400.0)
        lexical = (abs(float(row[7] or 0.0)) / best_bm25) if best_bm25 > 0.0 else 1.0
        recency = 0.5 ** (age_days / max(half_life_days, 1.0))
        relevance = max(0.0, min(1.0, lexical * recency))
        # Salience (organ 8, spec §4): multiplied AFTER recency (a boosted
        # stale row still dies of age — the max_age cut above already ran)
        # and BEFORE the min_relevance cut (thresholds compare, not
        # normalize). NULL/short-row/garbage ⇒ 1.0.
        try:
            relevance *= max(1.0, float(row[8] if row[8] is not None else 1.0))
        except (IndexError, KeyError, TypeError, ValueError):
            pass
        # Recall bias (spec §6): congruent-sentiment entries lean closer;
        # incongruent ones are NEVER penalized, nothing is excluded.
        if lean is not None:
            congruent = _WARM_TAGS if lean == "warm" else _HEAVY_TAGS
            if str(row[5] or "") in congruent:
                relevance *= _recall_bias_boost()
        if relevance < min_relevance:
            continue
        hits.append(
            IndexHit(
                entry_id=str(row[0] or ""),
                source_kind=str(row[1] or ""),
                source_path=str(row[2] or ""),
                timestamp=timestamp,
                text_content=str(row[4] or ""),
                sentiment_tag=str(row[5] or ""),
                surfaced_candidate=str(row[6] or ""),
                relevance_score=relevance,
                age_days=age_days,
            )
        )
    hits.sort(key=lambda hit: (hit.relevance_score, hit.timestamp), reverse=True)
    return hits


def _dedupe_hits(hits: List[IndexHit]) -> List[IndexHit]:
    selected: List[IndexHit] = []
    for hit in hits:
        if any(_same_day(hit, prior) and _word_overlap(hit.text_content, prior.text_content) > 0.70 for prior in selected):
            continue
        selected.append(hit)
    return selected


def _fts_query(text: str) -> str:
    tokens = []
    seen = set()
    for token in _WORD_RE.findall(str(text or "").lower()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token.replace('"', '""'))
        if len(tokens) >= 12:
            break
    return " OR ".join(f'"{token}"' for token in tokens)


def _read_entries_from_path(path: Path) -> List[store.MonologueEntry]:
    entries: List[store.MonologueEntry] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(store.MonologueEntry.from_dict(json.loads(line)))
    except Exception as exc:
        logger.debug("inner_memory: skipped archive file %s: %s", path, exc)
    return entries


def _read_digest_from_path(path: Path, digest_cls: Any) -> Any:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return digest_cls.from_dict(json.loads(line))
    except Exception as exc:
        logger.debug("inner_memory: skipped digest file %s: %s", path, exc)
    return None


def _entry_id(entry: store.MonologueEntry) -> str:
    return f"monologue:{float(entry.timestamp):.6f}"


def _entry_source_path(entry: store.MonologueEntry) -> str:
    return (store.MONOLOGUE_DIR / f"{entry.datetime_local:%Y-%m-%d}.jsonl").as_posix()


def _entry_sentiment(entry: store.MonologueEntry) -> str:
    sentiment = entry.sentiment if isinstance(entry.sentiment, dict) else {}
    return str(sentiment.get("tag") or "")


def _digest_source_path(digest: Any) -> str:
    tier = str(getattr(digest, "tier", "weekly") or "weekly")
    digest_id = str(getattr(digest, "digest_id", "") or "")
    key = digest_id.replace(f"{tier}_", "", 1)
    return (store.MONOLOGUE_DIR / "digests" / tier / f"{key}.jsonl").as_posix()


def _digest_timestamp(digest: Any) -> float:
    end = _date_from_iso(str(getattr(digest, "period_end", "") or ""))
    if end is not None:
        return datetime.combine(end, dt_time(hour=12), tzinfo=timezone.utc).timestamp()
    return float(getattr(digest, "created_at", 0.0) or 0.0)


def _date_from_stem(stem: str) -> Optional[date]:
    return _date_from_iso(stem)


def _date_from_iso(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _same_day(left: IndexHit, right: IndexHit) -> bool:
    return datetime.fromtimestamp(left.timestamp).date() == datetime.fromtimestamp(right.timestamp).date()


def _word_overlap(left: str, right: str) -> float:
    a = set(_WORD_RE.findall(str(left or "").lower()))
    b = set(_WORD_RE.findall(str(right or "").lower()))
    return (len(a & b) / max(len(a | b), 1)) if a and b else 0.0


def _age_label(age_days: float) -> str:
    if age_days < 1:
        return "today"
    if age_days < 2:
        return "yesterday"
    if age_days < 14:
        return f"{int(age_days)} days ago"
    if age_days < 60:
        return f"{max(2, int(age_days // 7))} weeks ago"
    return f"{int(age_days // 30)} months ago"


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: max(0, limit - 1)].rstrip() + "..."


def _now() -> float:
    return time.time()
