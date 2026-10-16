"""Night Cycle daily-meaning storage (2026-07-11).

Stores PaLLi's nightly first-person "what today meant" reflections as dated
JSONL under ``data/night_cycle/`` — one file per date, one JSON line per
entry. Modeled on ``ai/inner_monologue_store`` but deliberately simpler: no
inner-memory index hook (these are surfaced directly by recall, not searched).

Privacy: the reflection is inherently about the owner's day, so this
directory is gitignored — it never enters git history.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NIGHT_CYCLE_DIR = Path("data/night_cycle")


@dataclass
class NightCycleEntry:
    """One nightly daily-meaning reflection."""

    date: str  # "YYYY-MM-DD" (the day being reflected on)
    text: str
    created_at: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else NIGHT_CYCLE_DIR


def write_daily_meaning(
    *,
    date: str,
    text: str,
    meta: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Path:
    """Append a daily-meaning entry to ``<root>/<date>.jsonl``. Returns the path."""
    directory = _root(root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = NightCycleEntry(
        date=str(date), text=str(text), created_at=time.time(), meta=dict(meta or {})
    )
    filepath = directory / f"{entry.date}.jsonl"
    line = json.dumps(asdict(entry), ensure_ascii=False, separators=(",", ":"))
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.debug("Wrote night-cycle reflection to %s", filepath.name)
    return filepath


def read_latest_daily_meaning(
    *,
    max_age_days: int = 2,
    root: Optional[Path] = None,
) -> Optional[NightCycleEntry]:
    """Return the most recent reflection whose date is within ``max_age_days``
    of today, or None. (If the newest entry is older than that, none is fresh.)"""
    directory = _root(root)
    if not directory.exists():
        return None
    today = _date.today()
    best: Optional[tuple[_date, Path]] = None
    for p in directory.glob("*.jsonl"):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d > today:
            continue  # ignore any future-dated file
        if best is None or d > best[0]:
            best = (d, p)
    if best is None:
        return None
    if (today - best[0]).days > max_age_days:
        return None
    try:
        lines = [ln for ln in best[1].read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return NightCycleEntry(
        date=str(data.get("date", "")),
        text=str(data.get("text", "")),
        created_at=float(data.get("created_at", 0.0) or 0.0),
        meta=dict(data.get("meta") or {}),
    )


def read_daily_meaning_for(
    *,
    date: str,
    root: Optional[Path] = None,
) -> Optional[NightCycleEntry]:
    """The reflection written FOR one specific date, or None — the Wave 1a
    consolidation context read (last line wins, matching the other readers)."""
    filepath = _root(root) / f"{str(date).strip()}.jsonl"
    if not filepath.exists():
        return None
    try:
        lines = [ln for ln in filepath.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None
        data = json.loads(lines[-1])
    except (OSError, json.JSONDecodeError):
        return None
    return NightCycleEntry(
        date=str(data.get("date", "")) or str(date),
        text=str(data.get("text", "")),
        created_at=float(data.get("created_at", 0.0) or 0.0),
        meta=dict(data.get("meta") or {}),
    )


def read_recent(
    *,
    days_back: int = 14,
    limit: int = 10,
    root: Optional[Path] = None,
) -> List[NightCycleEntry]:
    """Recent reflections, newest-first — the introspect tool's read path
    (2026-07-13 Selfhood evidence layer).

    One entry per dated file; the last line (the latest write for that date)
    wins, matching ``read_latest_daily_meaning``. ``days_back`` bounds the
    window; future-dated files are ignored and malformed files are skipped
    (same tolerance as the latest-reader). Never raises on a missing root —
    an empty list is the honest "nothing on file"."""
    directory = _root(root)
    if not directory.exists():
        return []
    today = _date.today()
    cutoff = today - timedelta(days=max(1, int(days_back)))
    dated: List[tuple[_date, Path]] = []
    for p in directory.glob("*.jsonl"):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d > today or d < cutoff:
            continue
        dated.append((d, p))
    dated.sort(key=lambda t: t[0], reverse=True)
    out: List[NightCycleEntry] = []
    for d, p in dated[: max(1, int(limit))]:
        try:
            lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                continue
            data = json.loads(lines[-1])
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            NightCycleEntry(
                date=str(data.get("date", "")) or d.strftime("%Y-%m-%d"),
                text=str(data.get("text", "")),
                created_at=float(data.get("created_at", 0.0) or 0.0),
                meta=dict(data.get("meta") or {}),
            )
        )
    return out
