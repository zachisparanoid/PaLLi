"""Read-only owner-facing interiority endpoints (Wave 0b, 2026-07-14).

GET-only by construction: renders PaLLi's inner record for the owner's
dashboard without touching any turn path — no writes, no LLM calls.

Honesty contract (the introspect tool's, reused): a flag-off organ reports
``enabled: false`` with no items; a flag-on organ with an empty store
reports an honest zero; a reader that raises marks its section
``"error": true`` — error is never rendered as absence.

Spec: docs/superpowers/specs/2026-07-14-interiority-page-v1-design.md
"""

from __future__ import annotations

import logging
import math
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from api.web_auth import _require_user
from core.local_mode import get_local_owner_refs

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_WINDOW_DAYS = 365
_DEFAULT_WINDOW_DAYS = 30

_REFLECTIONS_CAP = 30
_STAKES_CAP = 60
_TIMELINE_CAP = 50
_THREADS_CAP = 20
_MOMENT_RECENT_CAP = 30

_MONOLOGUE_PRESENCE_CAP = 200
_MONOLOGUE_RAW_CAP = 60
# Tiers that actually exist on disk (spec's example said "daily"; reality
# is weekly/monthly — see the Wave 0b build notes).
_DIGEST_TIERS = ("weekly", "monthly")


def _clamp_since(since: Optional[float], *, now: float) -> float:
    """Budgets-in-code: the window start always lands in [now - 365d, now]."""
    floor = now - _MAX_WINDOW_DAYS * 86400.0
    if since is None:
        return now - _DEFAULT_WINDOW_DAYS * 86400.0
    try:
        value = float(since)
    except (TypeError, ValueError):
        return now - _DEFAULT_WINDOW_DAYS * 86400.0
    return min(max(value, floor), now)


def _days_back(since: float, *, now: float) -> int:
    return max(1, math.ceil((now - since) / 86400.0))


async def _resolve_owner_id() -> int:
    refs = await get_local_owner_refs()
    app_user_id = refs.get("app_user_id")
    if app_user_id is None:
        raise HTTPException(status_code=503, detail="local owner could not be resolved")
    return int(app_user_id)


# --- organ gates (lazy imports: the organs' own canonical gates) ----------


def _night_cycle_on() -> bool:
    try:
        from ai.night_cycle import night_cycle_enabled

        return bool(night_cycle_enabled())
    except Exception:
        return False


def _stake_ledger_on() -> bool:
    try:
        from ai.stake_ledger import ledger_enabled

        return bool(ledger_enabled())
    except Exception:
        return False


def _caring_memory_on() -> bool:
    try:
        from core.turn.companion_flags import faculty_enabled

        return bool(faculty_enabled("caring_memory"))
    except Exception:
        return True


def _introspection_on() -> bool:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "introspection", False))
    except Exception:
        return False


def _own_work_on() -> bool:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "own_work", False))
    except Exception:
        return False


def _consolidation_on() -> bool:
    try:
        from ai.consolidation import consolidation_enabled

        return bool(consolidation_enabled())
    except Exception:
        return False


def _narrative_self_on() -> bool:
    try:
        from ai.narrative_self import narrative_self_enabled

        return bool(narrative_self_enabled())
    except Exception:
        return False


def _passions_on() -> bool:
    try:
        from ai.passions import passions_enabled

        return bool(passions_enabled())
    except Exception:
        return False


def _present_moment_on() -> bool:
    try:
        from core.turn.companion_flags import faculty_enabled

        return bool(faculty_enabled("present_moment"))
    except Exception:
        return False


def _wanting_on() -> bool:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "wanting", False))
    except Exception:
        return False


# --- organ chips (artifact dates, not flip dates) --------------------------


async def _table_exists(conn: Any, name: str) -> bool:
    rows = await conn.fetch(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = $1", name
    )
    return bool(rows)


def _empty_chip(key: str, enabled: bool) -> Dict[str, Any]:
    return {
        "key": key,
        "enabled": enabled,
        "first_artifact_ts": None,
        "last_artifact_ts": None,
        "count_since": 0,
    }


def _dated_files_chip(
    *,
    key: str,
    resolve_dir: Any,
    since: float,
    enabled: bool,
    pattern: str = "????-??-??.jsonl",
) -> Dict[str, Any]:
    """Chip for organs whose artifacts are one dated file per day — the
    filename IS the date, so no file is opened and no content is read.

    ``resolve_dir`` is a callable, not a Path, so the store import happens
    INSIDE this try: a broken import degrades one chip instead of 500-ing the
    whole overview, and the late read still honours a monkeypatched constant.

    ``pattern`` (Task 8, 2026-07-20): the glob shape differs by organ —
    night_cycle/consolidation dated JSONL, passions dated journal markdown.
    Default preserves every caller that predates the passions chip.
    """
    chip = _empty_chip(key, enabled)
    try:
        directory = resolve_dir()
        stamps: List[float] = []
        if directory.exists():
            for path in directory.glob(pattern):
                try:
                    stamps.append(datetime.strptime(path.stem, "%Y-%m-%d").timestamp())
                except ValueError:
                    continue
        if stamps:
            chip["first_artifact_ts"] = min(stamps)
            chip["last_artifact_ts"] = max(stamps)
            chip["count_since"] = sum(1 for ts in stamps if ts >= since)
    except Exception as exc:
        logger.debug("interiority: %s chip failed: %s", key, exc)
        chip["error"] = True
    return chip


def _night_cycle_dir():
    from ai.night_cycle_store import NIGHT_CYCLE_DIR

    return NIGHT_CYCLE_DIR


def _day_gist_dir():
    from ai.day_gist_store import DAY_GIST_DIR

    return DAY_GIST_DIR


def _passions_journal_dir():
    from ai.narrative_self import identity_palli_root

    return identity_palli_root() / "journal"


def _present_moment_dir():
    from ai.present_moment_store import PRESENT_MOMENT_DIR

    return PRESENT_MOMENT_DIR


def _workbench_dir():
    from ai.workbench_store import WORKBENCH_DIR

    return WORKBENCH_DIR


def _workbench_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    """Dates off her work-session files — one dated '.jsonl' per local day,
    the present-moment chip's shape exactly."""
    return _dated_files_chip(
        key="own_work",
        resolve_dir=_workbench_dir,
        since=since,
        enabled=enabled,
        pattern="*.jsonl",
    )


def _night_cycle_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    return _dated_files_chip(
        key="night_cycle", resolve_dir=_night_cycle_dir, since=since, enabled=enabled
    )


def _consolidation_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    return _dated_files_chip(
        key="consolidation", resolve_dir=_day_gist_dir, since=since, enabled=enabled
    )


def _passions_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    """Dates off her journal pages — one '????-??-??.md' file per written
    day, same shape as the night-cycle/consolidation dated-file chips."""
    return _dated_files_chip(
        key="passions",
        resolve_dir=_passions_journal_dir,
        since=since,
        enabled=enabled,
        pattern="????-??-??.md",
    )


def _present_moment_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    """Dates off her moment files — one dated '.jsonl' file per local day,
    same shape as the night-cycle/consolidation dated-file chips. Filenames
    only; the section reader below is what actually opens the store."""
    return _dated_files_chip(
        key="present_moment",
        resolve_dir=_present_moment_dir,
        since=since,
        enabled=enabled,
        pattern="*.jsonl",
    )


def _narrative_self_chip(*, since: float, enabled: bool) -> Dict[str, Any]:
    """Her self-revisions ARE her commits, so this chip dates off git log.

    A ``last_artifact_ts`` that stops advancing week over week is the signal
    a weekly pass died — the observability this page owes the organ. Strictly
    read-only: a missing repo is an honest zero, never an init (the GET path
    can never create state), and commit messages are her reasoning, so only
    timestamps cross this boundary."""
    chip = _empty_chip("narrative_self", enabled)
    try:
        from ai.narrative_self import identity_palli_root

        root = identity_palli_root()
        if not (root / ".git").is_dir():
            return chip  # no repo yet = honestly no artifacts
        proc = subprocess.run(
            ["git", "log", "--format=%ct"],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:  # includes the no-commits-yet case
            return chip
        stamps = [float(x) for x in proc.stdout.split() if x.strip().isdigit()]
        if stamps:
            chip["first_artifact_ts"] = min(stamps)
            chip["last_artifact_ts"] = max(stamps)
            chip["count_since"] = sum(1 for ts in stamps if ts >= since)
    except Exception as exc:
        logger.debug("interiority: narrative_self chip failed: %s", exc)
        chip["error"] = True
    return chip


async def _aggregate_chip(
    *, key: str, enabled: bool, sql: str, params: List[Any]
) -> Dict[str, Any]:
    """MIN/MAX/count-since chip off one aggregate row. ``sql`` must project
    ``first_ts``, ``last_ts``, ``count_since``."""
    chip = _empty_chip(key, enabled)
    try:
        from core.database import brain

        conn = await brain.get_connection()
        try:
            table = "stakes" if key == "stake_ledger" else "emotional_timeline"
            if not await _table_exists(conn, table):
                return chip  # never created yet = honestly no artifacts
            rows = await conn.fetch(sql, *params)
        finally:
            try:
                await conn.close()
            except Exception:
                pass
        if rows:
            row = dict(rows[0])
            first_ts = row.get("first_ts")
            last_ts = row.get("last_ts")
            chip["first_artifact_ts"] = float(first_ts) if first_ts is not None else None
            chip["last_artifact_ts"] = float(last_ts) if last_ts is not None else None
            chip["count_since"] = int(row.get("count_since") or 0)
    except Exception as exc:
        logger.debug("interiority: %s chip failed: %s", key, exc)
        chip["error"] = True
    return chip


# --- sections ---------------------------------------------------------------


def _reflections_section(*, since: float, now: float, enabled: bool) -> Dict[str, Any]:
    section: Dict[str, Any] = {"enabled": enabled, "items": []}
    if not enabled:
        return section
    try:
        from ai import night_cycle_store

        entries = night_cycle_store.read_recent(
            days_back=_days_back(since, now=now), limit=400
        )
        items = [
            {"date": e.date, "text": e.text, "created_at": float(e.created_at or 0.0)}
            for e in entries
        ]
        items = [i for i in items if datetime.strptime(i["date"], "%Y-%m-%d").timestamp() >= since]
        items.sort(key=lambda item: item["date"], reverse=True)
        section["items"] = items[:_REFLECTIONS_CAP]
    except Exception as exc:
        logger.debug("interiority: reflections read failed: %s", exc)
        section["error"] = True
    return section


def _wanting_section(*, enabled: bool) -> Dict[str, Any]:
    """The Wanting panel payload (re-spec §10): wishbook + recent
    daydreams. A WINDOW, not a stage — read-only; her prompts never
    reference it. Flag-off ⇒ enabled:false + nothing read; reader raise
    ⇒ error:true, never absence."""
    section: Dict[str, Any] = {"enabled": bool(enabled), "quests": [], "daydreams": []}
    if not enabled:
        return section
    try:
        from ai.daydream_store import read_recent
        from ai.quest_store import load_quests

        rows = sorted(
            load_quests().values(), key=lambda q: q.updated_at, reverse=True,
        )
        for q in rows[:30]:
            section["quests"].append({
                "id": q.id, "text": q.text, "source": q.source,
                "status": q.status, "horizon": q.horizon,
                "born": q.born, "updated_at": q.updated_at,
                "progress": q.progress[-3:],
            })
        for e in read_recent(days_back=7, limit=12):
            section["daydreams"].append({"ts": e.written_at, "text": e.text})
    except Exception:
        logger.exception("interiority: wanting section failed")
        section["error"] = True
    return section


def _moment_section(*, since: float, now: float, enabled: bool) -> Dict[str, Any]:
    """The Moment panel payload. Flag-off ⇒ enabled:false + nothing read;
    empty store ⇒ honest zero; reader raise ⇒ error:true, never absence."""
    section: Dict[str, Any] = {"enabled": bool(enabled), "current": None, "recent": []}
    if not enabled:
        return section
    try:
        from ai.present_moment_store import read_current, read_recent

        current = read_current(now=now)
        if current is not None:
            entry, age, stale = current
            section["current"] = {
                "ts": entry.written_at,
                "age_seconds": round(age, 1),
                "stale": bool(stale),
                "text": entry.text,
                "woke_on": entry.meta.get("woke_on") or [],
                "impulse": entry.meta.get("impulse") or {},
                "sense_health": entry.meta.get("sense_health") or {},
            }
            # Slice 3: the undercurrent — present only when non-empty (the
            # honesty ladder: absence means she has none, not a blank slot).
            uc = str(entry.meta.get("undercurrent") or "").strip()
            if uc:
                section["current"]["undercurrent"] = uc
        for e in read_recent(
            days_back=max(7, _days_back(since, now=now)), limit=_MOMENT_RECENT_CAP
        ):
            if e.written_at < since:
                continue
            section["recent"].append({
                "ts": e.written_at,
                "text": e.text,
                "impulse": (e.meta.get("impulse") or {}).get("kind", "none"),
                "impulse_why": (e.meta.get("impulse") or {}).get("why", ""),
                "woke_on": e.meta.get("woke_on") or [],
            })
    except Exception:
        logger.exception("interiority: moment section failed")
        section["error"] = True
    return section


def _workbench_section(*, enabled: bool) -> Dict[str, Any]:
    """Her workbench sessions. Flag-off ⇒ enabled:false; empty ⇒ honest zero;
    reader raise ⇒ error:true, never absence.

    Sign-offs only — the one line SHE wrote about where she stopped. The
    work itself lives in her workspace, which is his to read directly; the
    panel is a window, not a copy.
    """
    section: Dict[str, Any] = {"enabled": bool(enabled), "recent": []}
    if not enabled:
        return section
    try:
        from ai.workbench_store import read_recent

        for r in read_recent(days_back=7, limit=10):
            section["recent"].append({
                "ts": r.get("written_at"),
                "signoff": str(r.get("signoff") or ""),
                "shared": bool(r.get("shared")),
            })
    except Exception:
        logger.exception("interiority: workbench section failed")
        section["error"] = True
    return section


async def _stakes_section(*, since: float, now: float, enabled: bool) -> Dict[str, Any]:
    section: Dict[str, Any] = {"enabled": enabled, "open": [], "resolved": []}
    if not enabled:
        return section
    try:
        from ai import stake_ledger
        from core.database import brain

        conn = await brain.get_connection()
        try:
            if not await _table_exists(conn, "stakes"):
                return section  # never created yet = honestly nothing held
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        open_rows = await stake_ledger.open_stakes(limit=_STAKES_CAP)
        open_items = [
            {"id": s.id, "kind": s.kind, "stake": s.stake, "created_at": s.created_at}
            for s in open_rows
        ]
        open_items.sort(key=lambda item: item["created_at"], reverse=True)
        open_items = open_items[:_STAKES_CAP]

        resolved_items: List[Dict[str, Any]] = []
        budget = _STAKES_CAP - len(open_items)
        if budget > 0:
            recent = await stake_ledger.recent_stakes(
                days_back=_days_back(since, now=now),
                include_closed=True,
                limit=_STAKES_CAP,
            )
            resolved_items = [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "stake": s.stake,
                    "resolution": s.resolution,
                    "resolved_at": s.resolved_at,
                }
                for s in recent
                if s.status != "open"
            ]
            resolved_items.sort(key=lambda item: item["resolved_at"] or 0.0, reverse=True)
            resolved_items = resolved_items[:budget]
        section["open"] = open_items
        section["resolved"] = resolved_items
    except Exception as exc:
        logger.debug("interiority: stakes read failed: %s", exc)
        section["error"] = True
    return section


async def _timeline_section(*, owner_id: int, since: float, now: float, enabled: bool) -> Dict[str, Any]:
    section: Dict[str, Any] = {"enabled": enabled, "items": []}
    if not enabled:
        return section
    try:
        from ai.turn_read import recent_reads
        from core.database import brain

        conn = await brain.get_connection()
        try:
            if not await _table_exists(conn, "emotional_timeline"):
                return section  # never created yet = honestly nothing held
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        items = await recent_reads(
            owner_id=owner_id,
            days_back=_days_back(since, now=now),
            limit=_TIMELINE_CAP,
        )
        section["items"] = [i for i in items if i["created_at"] >= since]
    except Exception as exc:
        logger.debug("interiority: timeline read failed: %s", exc)
        section["error"] = True
    return section


async def _threads_section(*, owner_id: int) -> Dict[str, Any]:
    section: Dict[str, Any] = {"items": []}
    try:
        from core.database import brain

        threads = await brain.list_active_open_threads(owner_user_id=owner_id)
        items = [
            {
                "topic_summary": t.topic_summary,
                "why_it_matters": t.why_it_matters,
                "created_at": float(t.created_at or 0.0),
                "updated_at": float(t.updated_at or 0.0),
            }
            for t in threads
        ]
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        section["items"] = items[:_THREADS_CAP]
    except Exception as exc:
        logger.debug("interiority: threads read failed: %s", exc)
        section["error"] = True
    return section


# --- endpoints ---------------------------------------------------------------


@router.get("/interiority/overview")
async def get_interiority_overview(request: Request, since: Optional[float] = None):
    await _require_user(request)
    now = time.time()
    window_start = _clamp_since(since, now=now)
    owner_id = await _resolve_owner_id()

    night_on = _night_cycle_on()
    ledger_on = _stake_ledger_on()
    caring_on = _caring_memory_on()
    introspection_on = _introspection_on()
    consolidation_on = _consolidation_on()
    narrative_on = _narrative_self_on()
    passions_on = _passions_on()
    moment_on = _present_moment_on()
    own_work_on = _own_work_on()

    organs = [
        _night_cycle_chip(since=window_start, enabled=night_on),
        _consolidation_chip(since=window_start, enabled=consolidation_on),
        _narrative_self_chip(since=window_start, enabled=narrative_on),
        await _aggregate_chip(
            key="stake_ledger",
            enabled=ledger_on,
            sql=(
                "SELECT MIN(created_at) AS first_ts, MAX(created_at) AS last_ts, "
                "COUNT(CASE WHEN created_at >= $1 THEN 1 END) AS count_since "
                "FROM stakes"
            ),
            params=[float(window_start)],
        ),
        await _aggregate_chip(
            key="caring_memory",
            enabled=caring_on,
            sql=(
                "SELECT MIN(created_at) AS first_ts, MAX(created_at) AS last_ts, "
                "COUNT(CASE WHEN created_at >= $1 THEN 1 END) AS count_since "
                "FROM emotional_timeline WHERE owner_user_id = $2"
            ),
            params=[float(window_start), int(owner_id)],
        ),
        _empty_chip("introspection", introspection_on),
        _passions_chip(since=window_start, enabled=passions_on),
        _present_moment_chip(since=window_start, enabled=moment_on),
        _workbench_chip(since=window_start, enabled=own_work_on),
    ]

    return {
        "since": window_start,
        "organs": organs,
        "reflections": _reflections_section(since=window_start, now=now, enabled=night_on),
        "stakes": await _stakes_section(since=window_start, now=now, enabled=ledger_on),
        "timeline": await _timeline_section(
            owner_id=owner_id, since=window_start, now=now, enabled=caring_on
        ),
        "threads": await _threads_section(owner_id=owner_id),
        "moment": _moment_section(since=window_start, now=now, enabled=moment_on),
        "workbench": _workbench_section(enabled=own_work_on),
        "wanting": _wanting_section(enabled=_wanting_on()),
    }


@router.get("/interiority/monologue")
async def get_interiority_monologue(
    request: Request, since: Optional[float] = None, raw: int = 0
):
    """The diary door. Default is presence-not-content: timestamps,
    sentiment tags, and character counts prove the inner life is real
    without printing it. ``raw=1`` is the owner's deliberate act; it adds
    exactly one key per entry ("thought") and nothing else, ever."""
    await _require_user(request)
    now = time.time()
    window_start = _clamp_since(since, now=now)
    raw_mode = bool(raw)

    payload: Dict[str, Any] = {
        "since": window_start,
        "raw": raw_mode,
        "count": 0,
        "digests": {tier: 0 for tier in _DIGEST_TIERS},
        "entries": [],
    }
    try:
        from ai import inner_monologue_store as mono_store

        entries = mono_store.read_entries(
            days_back=_days_back(window_start, now=now) + 1,
            substantive_only=False,
            limit=None,
        )
        in_window = [e for e in entries if float(e.timestamp or 0.0) >= window_start]
        in_window.sort(key=lambda e: float(e.timestamp or 0.0), reverse=True)
        payload["count"] = len(in_window)

        cap = _MONOLOGUE_RAW_CAP if raw_mode else _MONOLOGUE_PRESENCE_CAP
        items: List[Dict[str, Any]] = []
        for entry in in_window[:cap]:
            sentiment = entry.sentiment if isinstance(entry.sentiment, dict) else {}
            tag = sentiment.get("tag")
            # Explicit allowlist — never serialize the entry object itself.
            item: Dict[str, Any] = {
                "ts": float(entry.timestamp or 0.0),
                "tag": str(tag) if tag else None,
                "chars": len(entry.thought or ""),
            }
            if raw_mode:
                item["thought"] = str(entry.thought or "")
            items.append(item)
        payload["entries"] = items

        digests: Dict[str, int] = {}
        for tier in _DIGEST_TIERS:
            tier_dir = mono_store.MONOLOGUE_DIR / "digests" / tier
            try:
                digests[tier] = (
                    sum(1 for _ in tier_dir.glob("*.jsonl")) if tier_dir.exists() else 0
                )
            except OSError:
                digests[tier] = 0
        payload["digests"] = digests
    except Exception as exc:
        logger.debug("interiority: monologue read failed: %s", exc)
        payload["error"] = True
    return payload
