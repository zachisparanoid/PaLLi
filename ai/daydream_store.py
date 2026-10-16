"""The daydream store (Wanting stage 2, re-spec §4.1) — her lane's dated
record, the self-notes store's shape: append-only JSONL per day,
call-time root resolution so tests redirect hermetically. Plus the one
sticky workbench-intent line (re-spec §4.5): overwrite-on-write, read by
the session seed, hers to manage.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DAYDREAM_DIR = Path("data/daydream")
DAYDREAM_CHAR_CAP = 900
INTENT_FILENAME = "workbench_intent.txt"
INTENT_CHAR_CAP = 200


@dataclass
class DaydreamEntry:
    text: str
    written_at: float
    meta: Dict[str, Any] = field(default_factory=dict)


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution so patch.object(module, "DAYDREAM_DIR", ...)
    genuinely redirects (the step-02 lesson)."""
    return DAYDREAM_DIR if root is None else root


def _day_name(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def write_entry(
    *, text: str, meta: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Path:
    """Append one daydream to today's file. Raises ValueError on
    empty-after-strip — a phantom record would break count_today, and the
    daily cap is built on that count (the self-notes law)."""
    root = _resolve_root(root)
    clipped = (text or "").strip()[:DAYDREAM_CHAR_CAP]
    if not clipped:
        raise ValueError("a daydream must have words in it")
    now = time.time()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_day_name(now)}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"text": clipped, "written_at": now, "meta": dict(meta or {})},
            ensure_ascii=False,
        ) + "\n")
    return path


def _entries_from_file(path: Path) -> List[DaydreamEntry]:
    out: List[DaydreamEntry] = []
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
            out.append(DaydreamEntry(
                text=text,
                written_at=float(rec.get("written_at") or 0.0),
                meta=dict(rec.get("meta") or {}),
            ))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            continue  # one bad line is a skipped line, never a lost day
    return out


def read_recent(
    *, days_back: int = 7, limit: int = 30, root: Optional[Path] = None
) -> List[DaydreamEntry]:
    """Newest-first daydreams across the recent days."""
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    cutoff = time.time() - max(0, int(days_back)) * 86400
    files: List[Path] = []
    for p in sorted(root.glob("????-??-??.jsonl")):
        try:
            day = time.mktime(time.strptime(p.stem, "%Y-%m-%d"))
        except ValueError:
            continue
        if day >= cutoff - 86400:
            files.append(p)
    out: List[DaydreamEntry] = []
    for path in reversed(files):
        for entry in reversed(_entries_from_file(path)):
            out.append(entry)
            if len(out) >= max(0, int(limit)):
                return out
    return out


def count_today(*, root: Optional[Path] = None) -> int:
    """Today's tick count — the daily cap's input."""
    root = _resolve_root(root)
    return len(_entries_from_file(root / f"{_day_name(time.time())}.jsonl"))


def write_intent(text: str, *, root: Optional[Path] = None,
                 at: Optional[float] = None) -> None:
    """The one sticky line her next workbench session sees. Overwrite —
    she manages it; empty text clears it. Never raises.

    2026-08-18: the line carries WHEN it was written and whether a session
    has taken it up (``consume_intent``). It used to be a bare string, so a
    line written before her last session reached the NEXT session as if it
    were new — "the daydream still says the letter's unwritten, and I've
    verified it twice" (her 08-10/11/12/13/14 sign-offs)."""
    try:
        root = _resolve_root(root)
        root.mkdir(parents=True, exist_ok=True)
        clean = " ".join(str(text or "").split())[:INTENT_CHAR_CAP]
        record = {
            "text": clean,
            "written_at": float(at if at is not None else time.time()) if clean else None,
            "consumed_at": None,
        }
        (root / INTENT_FILENAME).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("daydream_store: intent write dropped (%s)", exc)


def read_intent_record(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """{"text", "written_at", "consumed_at"} — a legacy plain-text file reads
    as text with no timestamps. Never raises."""
    empty: Dict[str, Any] = {"text": "", "written_at": None, "consumed_at": None}
    try:
        path = _resolve_root(root) / INTENT_FILENAME
        if not path.exists():
            return empty
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return empty
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            rec = None
        if isinstance(rec, dict) and "text" in rec:
            wa = rec.get("written_at")
            ca = rec.get("consumed_at")
            return {
                "text": " ".join(str(rec.get("text") or "").split()),
                "written_at": float(wa) if wa is not None else None,
                "consumed_at": float(ca) if ca is not None else None,
            }
        return {"text": raw, "written_at": None, "consumed_at": None}
    except Exception:
        return empty


def read_intent(*, root: Optional[Path] = None) -> str:
    """The standing line for her NEXT session — "" once a session took it up."""
    rec = read_intent_record(root=root)
    if rec.get("consumed_at") is not None:
        return ""
    return str(rec.get("text") or "")


def consume_intent(*, root: Optional[Path] = None, at: Optional[float] = None) -> None:
    """A session that saw the line marks it taken up (idempotent, never raises)."""
    try:
        rec = read_intent_record(root=root)
        if not rec.get("text") or rec.get("consumed_at") is not None:
            return
        rec["consumed_at"] = float(at if at is not None else time.time())
        root_p = _resolve_root(root)
        root_p.mkdir(parents=True, exist_ok=True)
        (root_p / INTENT_FILENAME).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("daydream_store: intent consume dropped (%s)", exc)
