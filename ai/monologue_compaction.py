from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ai import inner_monologue_store as store
from ai.llm_client import llm
from ai.prompt_loader import prompt_loader

logger = logging.getLogger(__name__)


@dataclass
class MonologueDigestEntry:
    digest_id: str
    tier: str
    period_start: str
    period_end: str
    created_at: float
    summary_text: str
    source_entry_count: int
    substantive_entry_count: int
    dominant_sentiment_tags: List[str]
    surfaced_candidates: List[str]
    summary_model: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonologueDigestEntry":
        return cls(
            digest_id=str(data.get("digest_id") or ""),
            tier=str(data.get("tier") or ""),
            period_start=str(data.get("period_start") or ""),
            period_end=str(data.get("period_end") or ""),
            created_at=float(data.get("created_at") or 0.0),
            summary_text=str(data.get("summary_text") or ""),
            source_entry_count=int(data.get("source_entry_count") or 0),
            substantive_entry_count=int(data.get("substantive_entry_count") or 0),
            dominant_sentiment_tags=list(data.get("dominant_sentiment_tags") or []),
            surfaced_candidates=list(data.get("surfaced_candidates") or []),
            summary_model=str(data.get("summary_model") or ""),
            metadata=dict(data.get("metadata") or {}),
        )

def load_latest_weekly_digest(*, owner_id: int | None = None) -> str:
    """Return the most recent weekly digest's summary text, or '' if none.

    `core/turn/recall.py:_safe_load_monologue_digest` calls this function;
    it was missing, so `monologue_digest` in TurnContext was always empty
    and long-range monologue summary never reached the prompt.

    `owner_id` is accepted for forward-compat but the inner monologue is
    currently single-owner (the local PaLLi user), so the same digest
    stream is shared across all callers. Reads the most recent
    `data/inner_monologue/digests/weekly/*.jsonl` by `period_end`.
    """
    weekly_dir = _digest_dir("weekly")
    if not weekly_dir.exists():
        return ""
    candidates: List[MonologueDigestEntry] = []
    for path in weekly_dir.glob("*.jsonl"):
        entry = _read_digest_entry(path)
        if entry is not None:
            candidates.append(entry)
    if not candidates:
        return ""
    candidates.sort(key=lambda e: e.period_end, reverse=True)
    return candidates[0].summary_text or ""


async def maybe_build_weekly_digest(*, week_start: date, force: bool = False) -> Optional[MonologueDigestEntry]:
    """Build one weekly digest if eligible; never mutates raw daily JSONL."""
    started_at = time.time()
    try:
        parent_cfg, cfg = _config()
        if not bool(getattr(cfg, "enabled", True)):
            _log_skipped("weekly", "disabled")
            return None
        start = _week_start(week_start)
        end = start + timedelta(days=6)
        key = _week_key(start)
        digest_path = _digest_path("weekly", key)
        if digest_path.exists() and not force:
            _log_skipped("weekly", "already_exists")
            return None
        if not force and not _period_is_old_enough(end, cfg):
            _log_skipped("weekly", "recent_period")
            return None
        entries, sources = _load_week_entries(start)
        if not entries:
            _log_skipped("weekly", "no_source_entries")
            return None
        result = await _summarize_week(start, end, entries, sources, parent_cfg, cfg)
        _write_digest(result, sources, digest_path)
        await _index_digest_best_effort(result, "weekly_digest")
        logger.info(
            "monologue_compaction: weekly_built week=%s source_entries=%s substantive=%s candidates=%s duration_ms=%s",
            key,
            result.source_entry_count,
            result.substantive_entry_count,
            len(result.surfaced_candidates),
            round((time.time() - started_at) * 1000.0, 3),
        )
        return result
    except Exception as exc:
        logger.warning("monologue_compaction: failed kind=weekly error=%s", exc)
        return None

async def maybe_build_monthly_digest(*, month_start: date, force: bool = False) -> Optional[MonologueDigestEntry]:
    """Build one monthly digest from four existing weekly digests."""
    started_at = time.time()
    try:
        parent_cfg, cfg = _config()
        if not bool(getattr(cfg, "enabled", True)):
            _log_skipped("monthly", "disabled")
            return None
        start = date(month_start.year, month_start.month, 1)
        end = _month_end(start)
        key = start.strftime("%Y-%m")
        digest_path = _digest_path("monthly", key)
        if digest_path.exists() and not force:
            _log_skipped("monthly", "already_exists")
            return None
        if not force and not _period_is_old_enough(end, cfg):
            _log_skipped("monthly", "recent_period")
            return None
        weekly_entries, sources = _load_month_weeklies(start)
        if len(weekly_entries) != 4:
            _log_skipped("monthly", "missing_weekly_digests")
            return None
        result = await _summarize_month(start, end, weekly_entries, sources, parent_cfg, cfg)
        _write_digest(result, sources, digest_path)
        await _index_digest_best_effort(result, "monthly_digest")
        logger.info(
            "monologue_compaction: monthly_built month=%s source_weeks=%s duration_ms=%s",
            key,
            len(weekly_entries),
            round((time.time() - started_at) * 1000.0, 3),
        )
        return result
    except Exception as exc:
        logger.warning("monologue_compaction: failed kind=monthly error=%s", exc)
        return None

async def run_scheduled_compaction(*, now: Optional[float] = None) -> Dict[str, Any]:
    """Daily scheduler entry point for monologue digest housekeeping."""
    parent_cfg, cfg = _config()
    if not bool(getattr(cfg, "enabled", True)):
        _log_skipped("weekly", "disabled")
        return {"weekly_built": [], "monthly_built": [], "skipped": ["disabled"]}
    today = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).date()
    state = _read_schedule_state()
    if state.get("last_run_date") == today.isoformat():
        _log_skipped("weekly", "already_ran_today")
        return {"weekly_built": [], "monthly_built": [], "skipped": ["already_ran_today"]}
    weekly = []
    for week in _discover_week_starts():
        result = await maybe_build_weekly_digest(week_start=week)
        if result:
            weekly.append(result.digest_id)
    monthly = []
    for month in _discover_month_starts():
        result = await maybe_build_monthly_digest(month_start=month)
        if result:
            monthly.append(result.digest_id)
    _write_schedule_state({"last_run_date": today.isoformat(), "last_run_at": float(now or time.time())})
    return {"weekly_built": weekly, "monthly_built": monthly, "skipped": []}

async def build_historical_digests(*, since: date, force: bool = False) -> Dict[str, Any]:
    """Manual backfill helper used by scripts/build_monologue_digests.py."""
    weekly = []
    for week in _discover_week_starts(since=since):
        result = await maybe_build_weekly_digest(week_start=week, force=force)
        if result:
            weekly.append(result.digest_id)
    monthly = []
    for month in _discover_month_starts(since=since.replace(day=1)):
        result = await maybe_build_monthly_digest(month_start=month, force=force)
        if result:
            monthly.append(result.digest_id)
    return {"weekly_built": weekly, "monthly_built": monthly}

def _config() -> tuple[Any, Any]:
    try:
        from core.settings_boot import settings_service

        parent = settings_service.snapshot().compaction
        return parent, parent.monologue
    except Exception:
        monologue = SimpleNamespace(
            enabled=True,
            digest_delay_days=7,
            weekly_summary_max_words=250,
            monthly_summary_max_words=300,
            summary_model="",
        )
        return SimpleNamespace(summary_model="qwen3:8b", monologue=monologue), monologue

async def _index_digest_best_effort(digest: MonologueDigestEntry, kind: str) -> None:
    try:
        from ai.inner_memory_index import index_digest

        await index_digest(digest)
    except Exception as exc:
        logger.warning("inner_memory: index_failed kind=%s error=%s", kind, exc)

async def _summarize_week(
    start: date,
    end: date,
    entries: List[store.MonologueEntry],
    sources: List[Dict[str, Any]],
    parent_cfg: Any,
    cfg: Any,
) -> MonologueDigestEntry:
    max_words = _int_config(cfg, "weekly_summary_max_words", 250, minimum=50)
    model = _summary_model(parent_cfg, cfg)
    prompt = _render_weekly_prompt(start=start, end=end, entries=entries, max_words=max_words)
    summary = await llm.chat_completion(
        [{"role": "system", "content": prompt}],
        model=model,
        temperature=0.1,
        max_tokens=max(400, max_words * 2),
        log_context=f"monologue_weekly_digest_{_week_key(start)}",
    )
    summary_text = " ".join(str(summary or "").split())
    if not summary_text:
        raise RuntimeError("empty_summary")
    sentiment_counts = _sentiment_counts(entries)
    return MonologueDigestEntry(
        digest_id=f"weekly_{_week_key(start)}",
        tier="weekly",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        created_at=time.time(),
        summary_text=summary_text,
        source_entry_count=len(entries),
        substantive_entry_count=sum(1 for entry in entries if entry.is_substantive),
        dominant_sentiment_tags=_top_tags(sentiment_counts),
        surfaced_candidates=_candidates_from_entries(entries),
        summary_model=model,
        metadata={"sentiment_counts": dict(sentiment_counts), "source_file_count": len(sources)},
    )

async def _summarize_month(
    start: date,
    end: date,
    weekly_entries: List[MonologueDigestEntry],
    sources: List[Dict[str, Any]],
    parent_cfg: Any,
    cfg: Any,
) -> MonologueDigestEntry:
    max_words = _int_config(cfg, "monthly_summary_max_words", 300, minimum=50)
    model = _summary_model(parent_cfg, cfg)
    prompt = _render_monthly_prompt(start=start, end=end, entries=weekly_entries, max_words=max_words)
    summary = await llm.chat_completion(
        [{"role": "system", "content": prompt}],
        model=model,
        temperature=0.1,
        max_tokens=max(500, max_words * 2),
        log_context=f"monologue_monthly_digest_{start:%Y-%m}",
    )
    summary_text = " ".join(str(summary or "").split())
    if not summary_text:
        raise RuntimeError("empty_summary")
    sentiment_counts = _merge_sentiment_counts(weekly_entries)
    return MonologueDigestEntry(
        digest_id=f"monthly_{start:%Y-%m}",
        tier="monthly",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        created_at=time.time(),
        summary_text=summary_text,
        source_entry_count=len(weekly_entries),
        substantive_entry_count=sum(entry.substantive_entry_count for entry in weekly_entries),
        dominant_sentiment_tags=_top_tags(sentiment_counts),
        surfaced_candidates=_merge_candidates(weekly_entries),
        summary_model=model,
        metadata={
            "sentiment_counts": dict(sentiment_counts),
            "source_digest_ids": [entry.digest_id for entry in weekly_entries],
            "source_file_count": len(sources),
        },
    )

def _render_weekly_prompt(*, start: date, end: date, entries: List[store.MonologueEntry], max_words: int) -> str:
    prompt = prompt_loader.load_prompt("monologue_weekly_digest") or "Raw monologue entries:\n{input_text}"
    lines = []
    for entry in sorted(entries, key=lambda item: item.timestamp):
        tag, confidence = _sentiment_label(entry)
        ts = datetime.fromtimestamp(entry.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] (tag: {tag}, confidence: {confidence}) {' '.join(entry.thought.split())}")
    return _replace_prompt_vars(prompt, start, end, "\n".join(lines) or "No entries.", max_words)

def _render_monthly_prompt(*, start: date, end: date, entries: List[MonologueDigestEntry], max_words: int) -> str:
    prompt = prompt_loader.load_prompt("monologue_monthly_digest") or "Weekly digests:\n{input_text}"
    lines = [
        f"[{entry.period_start} to {entry.period_end}] {entry.summary_text}"
        for entry in sorted(entries, key=lambda item: item.period_start)
    ]
    return _replace_prompt_vars(prompt, start, end, "\n".join(lines) or "No weekly digests.", max_words)

def _replace_prompt_vars(prompt: str, start: date, end: date, input_text: str, max_words: int) -> str:
    return (
        prompt.replace("{period_start}", start.isoformat())
        .replace("{period_end}", end.isoformat())
        .replace("{input_text}", input_text)
        .replace("{summary_max_words}", str(max_words))
    )

def _load_week_entries(start: date) -> tuple[List[store.MonologueEntry], List[Dict[str, Any]]]:
    entries: List[store.MonologueEntry] = []
    sources: List[Dict[str, Any]] = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        path = store.MONOLOGUE_DIR / f"{day.isoformat()}.jsonl"
        day_entries, line_count = _read_raw_entries(path)
        entries.extend(day_entries)
        sources.append(_source_manifest(path, line_count, day.isoformat(), day.isoformat()))
    entries.sort(key=lambda item: item.timestamp)
    return entries, sources

def _load_month_weeklies(start: date) -> tuple[List[MonologueDigestEntry], List[Dict[str, Any]]]:
    entries: List[MonologueDigestEntry] = []
    sources: List[Dict[str, Any]] = []
    for week in _weekly_starts_for_month(start):
        path = _digest_path("weekly", _week_key(week))
        entry = _read_digest_entry(path)
        if entry:
            entries.append(entry)
            sources.append(_source_manifest(path, 1, entry.period_start, entry.period_end))
    return entries, sources

def _read_raw_entries(path: Path) -> tuple[List[store.MonologueEntry], int]:
    entries: List[store.MonologueEntry] = []
    line_count = 0
    if not path.exists():
        return entries, line_count
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            try:
                entries.append(store.MonologueEntry.from_dict(json.loads(line)))
            except Exception as exc:
                logger.debug("monologue_compaction: skipped malformed line in %s: %s", path, exc)
    return entries, line_count

def _write_digest(entry: MonologueDigestEntry, sources: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(path)
    digest_tmp = path.with_name(path.name + ".tmp")
    manifest_tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    manifest = {
        "digest_id": entry.digest_id,
        "tier": entry.tier,
        "period_start": entry.period_start,
        "period_end": entry.period_end,
        "created_at": entry.created_at,
        "summary_model": entry.summary_model,
        "source_entry_count": entry.source_entry_count,
        "substantive_entry_count": entry.substantive_entry_count,
        "source_files": sources,
    }
    try:
        digest_tmp.write_text(json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        digest_tmp.replace(path)
        manifest_tmp.replace(manifest_path)
    finally:
        digest_tmp.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)

def _read_digest_entry(path: Path) -> Optional[MonologueDigestEntry]:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return MonologueDigestEntry.from_dict(json.loads(line))
    except Exception as exc:
        logger.debug("monologue_compaction: could not read digest %s: %s", path, exc)
    return None

def _discover_week_starts(*, since: Optional[date] = None) -> List[date]:
    starts = set()
    for path in store.MONOLOGUE_DIR.glob("*.jsonl"):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        start = _week_start(day)
        if since is None or start >= _week_start(since):
            starts.add(start)
    return sorted(starts)

def _discover_month_starts(*, since: Optional[date] = None) -> List[date]:
    months = set()
    weekly_dir = _digest_dir("weekly")
    if not weekly_dir.exists():
        return []
    for path in weekly_dir.glob("*.jsonl"):
        entry = _read_digest_entry(path)
        if not entry:
            continue
        start = date.fromisoformat(entry.period_start).replace(day=1)
        if since is None or start >= since.replace(day=1):
            months.add(start)
    return sorted(months)

def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())

def _week_key(start: date) -> str:
    iso_year, iso_week, _ = start.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

def _weekly_starts_for_month(start: date) -> List[date]:
    first = _week_start(start)
    return [first + timedelta(days=7 * idx) for idx in range(4)]

def _month_end(start: date) -> date:
    next_month = date(start.year + (1 if start.month == 12 else 0), 1 if start.month == 12 else start.month + 1, 1)
    return next_month - timedelta(days=1)

def _period_is_old_enough(period_end: date, cfg: Any) -> bool:
    delay = _int_config(cfg, "digest_delay_days", 7, minimum=0)
    today = datetime.now(tz=timezone.utc).date()
    return (today - period_end).days >= delay

def _digest_dir(tier: str) -> Path:
    return store.MONOLOGUE_DIR / "digests" / tier

def _digest_path(tier: str, key: str) -> Path:
    return _digest_dir(tier) / f"{key}.jsonl"

def _manifest_path(digest_path: Path) -> Path:
    return digest_path.with_suffix(".manifest.json")

def _schedule_state_path() -> Path:
    return store.MONOLOGUE_DIR / "digests" / ".scheduled_state.json"


def _read_schedule_state() -> Dict[str, Any]:
    try:
        return json.loads(_schedule_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_schedule_state(payload: Dict[str, Any]) -> None:
    path = _schedule_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _source_manifest(path: Path, line_count: int, period_start: str, period_end: str) -> Dict[str, Any]:
    return {
        "path": path.as_posix(),
        "exists": path.exists(),
        "line_count": int(line_count),
        "period_start": period_start,
        "period_end": period_end,
    }


def _sentiment_label(entry: store.MonologueEntry) -> tuple[str, str]:
    sentiment = entry.sentiment if isinstance(entry.sentiment, dict) else {}
    tag = str(sentiment.get("tag") or "unknown")
    confidence = sentiment.get("confidence")
    try:
        return tag, f"{float(confidence):.2f}"
    except Exception:
        return tag, "unknown"


def _sentiment_counts(entries: List[store.MonologueEntry]) -> Counter[str]:
    labels = (_sentiment_label(entry)[0] for entry in entries)
    return Counter(tag for tag in labels if tag and tag != "unknown")


def _merge_sentiment_counts(entries: List[MonologueDigestEntry]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        counts.update(dict(entry.metadata.get("sentiment_counts") or {}))
    return counts


def _top_tags(counts: Counter[str]) -> List[str]:
    return [tag for tag, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]]


def _candidates_from_entries(entries: List[store.MonologueEntry]) -> List[str]:
    return _unique([entry.proactive_candidate for entry in entries if entry.proactive_candidate])


def _merge_candidates(entries: List[MonologueDigestEntry]) -> List[str]:
    return _unique([candidate for entry in entries for candidate in entry.surfaced_candidates])


def _unique(values: List[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _summary_model(parent_cfg: Any, cfg: Any) -> str:
    """Resolve the model used for monologue digests.

    Companion-100 (2026-05-04, Workstream E, bug 14): the prior default
    was ``ollama::qwen3:8b`` regardless of whether Ollama was running.
    On systems without Ollama installed (or when the daemon isn't
    started), the digest job hit ``Connection refused`` on
    ``127.0.0.1:11434`` and silently failed. Boot already gates other
    Ollama-dependent paths (``Ollama not reachable at startup; skipping
    decision-model pre-warm``), but this digest path bypassed that gate.

    Resolution order now:
    1. Explicit override on cfg or parent_cfg (advanced users / tests).
    2. ``llm_routing.get_task_model("monologue_digest")`` so the YAML
       routing layer can pin the digest to whatever provider the user
       has configured. This is the path normal users go through.
    3. Fallback: ``ollama::qwen3:8b`` (preserves prior behavior when
       routing is unconfigured).
    """
    explicit = str(getattr(cfg, "summary_model", "") or getattr(parent_cfg, "summary_model", "") or "")
    if explicit:
        return explicit if "::" in explicit else f"ollama::{explicit}"
    try:
        from ai.llm_routing import get_task_model
        routed = get_task_model("monologue_digest", fallback=None)
        if routed:
            return routed if "::" in routed else f"ollama::{routed}"
    except Exception:
        pass
    return "ollama::qwen3:8b"


def _int_config(cfg: Any, name: str, default: int, *, minimum: int) -> int:
    try:
        return max(int(getattr(cfg, name)), minimum)
    except Exception:
        return max(int(default), minimum)


def _log_skipped(kind: str, reason: str) -> None:
    logger.debug("monologue_compaction: skipped kind=%s reason=%s", kind, reason)
