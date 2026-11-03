"""Day-gist storage — Selfhood Wave 1a (2026-07-15).

One compact first-person gist per lived day, as dated JSONL under
``data/day_gists/`` — ONE file per day, ONE line per file, keyed by the
day the gist is OF (honest date attribution: a backfilled gist for
2026-05-02 lives in 2026-05-02.jsonl no matter when it was written).
Unlike ``ai/night_cycle_store`` (append, last-line-wins), a re-run here
OVERWRITES: the newest consolidation of a day IS its record, which makes
backfill re-runs idempotent by construction.

Privacy: gists are distilled from the owner's days, so the directory is
gitignored — it never enters git history.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DAY_GIST_DIR = Path("data/day_gists")

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class DayGistEntry:
    """One consolidated day."""

    day: str  # "YYYY-MM-DD" — the day being gisted, NEVER the run day
    text: str
    created_at: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else DAY_GIST_DIR


def write_day_gist(
    *,
    day: str,
    text: str,
    meta: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Path:
    """Write (OVERWRITE) the single gist for ``day``. Returns the path."""
    clean_day = str(day or "").strip()
    if not _DAY_RE.fullmatch(clean_day):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")
    directory = _root(root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = DayGistEntry(
        day=clean_day, text=str(text), created_at=time.time(), meta=dict(meta or {})
    )
    filepath = directory / f"{clean_day}.jsonl"
    line = json.dumps(asdict(entry), ensure_ascii=False, separators=(",", ":"))
    filepath.write_text(line + "\n", encoding="utf-8")  # overwrite = idempotent
    logger.debug("Wrote day-gist %s (%d chars)", filepath.name, len(entry.text))
    return filepath


def read_day_gist(*, day: str, root: Optional[Path] = None) -> Optional[DayGistEntry]:
    """The gist for one day, or None. Malformed files read as None."""
    clean_day = str(day or "").strip()
    filepath = _root(root) / f"{clean_day}.jsonl"
    if not _DAY_RE.fullmatch(clean_day) or not filepath.exists():
        return None
    try:
        lines = [ln for ln in filepath.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None
        data = json.loads(lines[-1])
    except (OSError, json.JSONDecodeError):
        return None
    return DayGistEntry(
        day=str(data.get("day", "")) or clean_day,
        text=str(data.get("text", "")),
        created_at=float(data.get("created_at", 0.0) or 0.0),
        meta=dict(data.get("meta") or {}),
    )


def read_recent(
    *,
    days_back: int = 30,
    limit: int = 30,
    root: Optional[Path] = None,
) -> List[DayGistEntry]:
    """Recent day-gists, newest day first — mirrors night_cycle_store.read_recent."""
    directory = _root(root)
    if not directory.exists():
        return []
    today = _date.today()
    cutoff = today - timedelta(days=max(1, int(days_back)))
    dated: List[_date] = []
    for p in directory.glob("*.jsonl"):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d > today or d < cutoff:
            continue
        dated.append(d)
    dated.sort(reverse=True)
    out: List[DayGistEntry] = []
    for d in dated[: max(1, int(limit))]:
        entry = read_day_gist(day=d.isoformat(), root=directory)
        if entry is not None:
            out.append(entry)
    return out
