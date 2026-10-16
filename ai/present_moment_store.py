"""Present Moment store — dated-JSONL persistence for the moment document.

Slice 1 of the Present Moment organ (spec:
docs/superpowers/specs/2026-07-24-present-moment-design.md §2). The
night-cycle store pattern on purpose: one file per local day, one JSON
line per cycle, last line is the current moment. Append-only; history,
the interiority chip, and honest zeros all fall out of the file shape.
No SQLite — the GET-path schema-creation trap can't exist here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PRESENT_MOMENT_DIR = Path("data/present_moment")


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time root resolution (2026-07-26 polish, the sibling pattern from
    inner_monologue_store): the probe engine's hermetic_stores redirects this
    store via ``patch.object(module, "PRESENT_MOMENT_DIR", ...)``, which
    rebinds the MODULE attribute — but a ``root: Path = PRESENT_MOMENT_DIR``
    default arg was bound at definition time, so the redirect never actually
    applied and probe cycles wrote to the real data dir. Reading the module
    attribute here, at call time, makes the registration truly redirect."""
    return PRESENT_MOMENT_DIR if root is None else root

# Two-lock cap (spec §2): the prompt states this number; the writer clips.
MOMENT_CHAR_CAP = 1200


@dataclass
class MomentEntry:
    text: str
    written_at: float
    meta: Dict[str, Any] = field(default_factory=dict)


def _day_name(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def write_moment(
    *, text: str, meta: Dict[str, Any], root: Optional[Path] = None
) -> Path:
    """Append one moment line to today's file. Clips text as the second cap lock.

    Raises ValueError if text is empty after stripping — prevents phantom writes
    where the JSONL records differ from the reader's view. On-disk state and
    readers must never disagree about what was actually written.
    """
    root = _resolve_root(root)
    now = time.time()
    clipped = (text or "").strip()[:MOMENT_CHAR_CAP]
    if not clipped:
        raise ValueError(
            "moment text must be non-empty after strip. "
            "Empty/whitespace-only text creates a ghost record on disk that "
            "readers silently filter out, breaking the budget count and stale detection."
        )
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_day_name(now)}.jsonl"
    record = {"text": clipped, "written_at": now, "meta": dict(meta or {})}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _entries_from_file(path: Path) -> List[MomentEntry]:
    entries: List[MomentEntry] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            entries.append(
                MomentEntry(
                    text=str(raw.get("text") or ""),
                    written_at=float(raw.get("written_at") or 0.0),
                    meta=dict(raw.get("meta") or {}),
                )
            )
    except OSError:
        return []
    return entries


def _recent_files(root: Path, days_back: int) -> List[Path]:
    if not root.is_dir():
        return []
    cutoff = _day_name(time.time() - days_back * 86400)
    files = sorted(p for p in root.glob("*.jsonl") if p.stem >= cutoff)
    return files


def read_current(
    *,
    now: Optional[float] = None,
    stale_after_s: Optional[float] = None,
    root: Optional[Path] = None,
) -> Optional[Tuple[MomentEntry, float, bool]]:
    """Latest moment + its age. Never serves stale as current silently:
    the caller always receives (entry, age_seconds, is_stale)."""
    root = _resolve_root(root)
    now = time.time() if now is None else float(now)
    if stale_after_s is None:
        try:
            from core.config import config
            stale_after_s = float(
                getattr(config, "PRESENT_MOMENT_STALE_AFTER_SECONDS", 21600)
            )
        except Exception:
            stale_after_s = 21600.0
    for path in reversed(_recent_files(root, days_back=14)):
        entries = _entries_from_file(path)
        if entries:
            entry = entries[-1]
            age = max(0.0, now - entry.written_at)
            return entry, age, age > float(stale_after_s)
    return None


def read_day(day: str, *, root: Optional[Path] = None) -> List[MomentEntry]:
    """Every moment written on ``day`` (YYYY-MM-DD), oldest first — the
    night's surprise-marking read (organ 8, spec §4). [] on any failure."""
    return _entries_from_file(_resolve_root(root) / f"{str(day).strip()}.jsonl")


def read_recent(
    *, days_back: int = 7, limit: int = 30, root: Optional[Path] = None
) -> List[MomentEntry]:
    """Newest-first recent moments for the interiority panel."""
    root = _resolve_root(root)
    out: List[MomentEntry] = []
    for path in reversed(_recent_files(root, days_back)):
        for entry in reversed(_entries_from_file(path)):
            out.append(entry)
            if len(out) >= limit:
                return out
    return out


def count_today(*, root: Optional[Path] = None) -> int:
    """Cycles already written today — restores the daily budget across restarts."""
    path = _resolve_root(root) / f"{_day_name(time.time())}.jsonl"
    return len(_entries_from_file(path))


# ── Slice 2: the reply band (2026-07-27) ─────────────────────────────
# Deterministic age/staleness phrasing for the reply prompt's moment band.
# PRESENTATION, not meaning — the owner's LLM-over-heuristics rule exempts
# deterministic presentation (the events_block `_EVENT_PHRASES` precedent):
# these buckets decide only how an age READS, never what anything means.
# Spec: docs/superpowers/specs/2026-07-27-present-moment-slice-2-design.md §6.


def _age_phrase(age_seconds: float) -> str:
    if age_seconds < 90:
        return "just now"
    if age_seconds < 3600:
        minutes = max(1, int(age_seconds // 60))
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"
    hours = max(1, round(age_seconds / 3600))
    return "about an hour ago" if hours == 1 else f"about {hours} hours ago"


def _calendar_days_between(written_at: float, now: float) -> int:
    """Local calendar-day difference (the store's _day_name convention) —
    NOT a 24h window: written yesterday 23:50, read 00:10, is 1 day."""
    from datetime import date

    return (date.fromisoformat(_day_name(now)) - date.fromisoformat(_day_name(written_at))).days


def render_reply_band(
    entry: Optional[MomentEntry],
    age_seconds: float,
    is_stale: bool,
    *,
    now: Optional[float] = None,
) -> str:
    """Inner text of the reply prompt's moment band: one age line + her prose.

    The section header + framing law live in core/agent/system_prompt.py;
    this renders only what varies. Never raises; returns "" for a missing/
    empty entry (band absent, spec §4). Prose hard-clipped at
    MOMENT_CHAR_CAP as the second lock against a corrupt oversized line on
    disk (spec §2). A stale moment stays present with the gap NAMED —
    never served as current silently (parent spec §2)."""
    try:
        if entry is None:
            return ""
        prose = (entry.text or "").strip()[:MOMENT_CHAR_CAP]
        if not prose:
            return ""
        now_s = time.time() if now is None else float(now)
        if not is_stale:
            line = f"Your last moment, written {_age_phrase(age_seconds)}:"
        else:
            days = _calendar_days_between(entry.written_at, now_s)
            if days <= 0:
                origin = "from earlier today"
            elif days == 1:
                origin = "from yesterday"
            else:
                origin = f"from {days} days ago"
            line = f"Your last moment is {origin} — the present since is unwritten:"
        return f"{line}\n{prose}"
    except Exception:
        return ""
