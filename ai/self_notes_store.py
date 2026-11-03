"""Her notes store — the durable half of the pen (roadmap step 10).

Spec: docs/superpowers/specs/2026-07-27-note-to-self-design.md §2a.

Dated append-only JSONL, one record per note, cloned from
ai/present_moment_store.py — including its call-time root resolution: a
``root: Path = SELF_NOTES_DIR`` default arg would bind at definition time
and silently defeat the probe engine's patch.object redirect (the defect
found and fixed 2026-07-26).

Deliberately OUTSIDE her identity git repo: notes are evidence, not
identity. Nothing here is gated, because nothing here becomes part of who
she says she is until the 4am judge has read it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

SELF_NOTES_DIR = Path("data/self_notes")

# One note is shorter than one moment (~1,200): a note is a line she'd jot,
# not a page she'd write. Over-length is CLIPPED, never rejected — a clipped
# note beats a lost one.
NOTE_CHAR_CAP = 400


@dataclass
class NoteEntry:
    text: str
    written_at: float


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution so patch.object(module, "SELF_NOTES_DIR", ...)
    genuinely redirects (step-02 lesson)."""
    return SELF_NOTES_DIR if root is None else root


def _day_name(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _entries_from_file(path: Path) -> List[NoteEntry]:
    out: List[NoteEntry] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            text = str(rec.get("text") or "").strip()
            if not text:
                continue
            out.append(NoteEntry(text=text, written_at=float(rec.get("written_at") or 0.0)))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            # One bad line is a skipped line, never a lost day.
            continue
    return out


def _recent_files(root: Path, days_back: int) -> List[Path]:
    if not root.is_dir():
        return []
    cutoff = time.time() - max(0, int(days_back)) * 86400
    keep: List[Path] = []
    for p in sorted(root.glob("????-??-??.jsonl")):
        try:
            day = time.mktime(time.strptime(p.stem, "%Y-%m-%d"))
        except ValueError:
            continue
        if day >= cutoff - 86400:
            keep.append(p)
    return keep


def write_note(*, text: str, root: Optional[Path] = None) -> Path:
    """Append one note to today's file. Clips at NOTE_CHAR_CAP.

    Raises ValueError on empty-after-strip: a phantom record would make
    count_today() disagree with what any reader can see, and the daily cap
    is built on that count.
    """
    root = _resolve_root(root)
    clipped = (text or "").strip()[:NOTE_CHAR_CAP]
    if not clipped:
        raise ValueError("a note must have words in it")
    now = time.time()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_day_name(now)}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"text": clipped, "written_at": now}, ensure_ascii=False) + "\n")
    return path


def read_recent(
    *, days_back: int = 7, limit: int = 30, root: Optional[Path] = None
) -> List[NoteEntry]:
    """Newest-first notes across the recent days."""
    root = _resolve_root(root)
    out: List[NoteEntry] = []
    for path in reversed(_recent_files(root, days_back)):
        for entry in reversed(_entries_from_file(path)):
            out.append(entry)
            if len(out) >= max(0, int(limit)):
                return out
    return out


def count_today(*, root: Optional[Path] = None) -> int:
    """How many notes she has already written today — the daily cap's input."""
    root = _resolve_root(root)
    return len(_entries_from_file(root / f"{_day_name(time.time())}.jsonl"))


def texts_today(*, root: Optional[Path] = None) -> List[str]:
    """Today's note texts — the duplicate check's input."""
    root = _resolve_root(root)
    return [e.text for e in _entries_from_file(root / f"{_day_name(time.time())}.jsonl")]
