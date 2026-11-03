"""Workbench session store — dated-JSONL records of her own work sessions.

The present-moment store pattern on purpose (one file per local day, one
JSON line per session, call-time root resolution for the hermetic probe
redirect). App-side state — behind the wall; her shell user cannot touch it.
Spec: docs/superpowers/specs/2026-07-28-her-own-work-design.md §4d.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKBENCH_DIR = Path("data/workbench")


def _resolve_root(root: Optional[Path]) -> Path:
    return WORKBENCH_DIR if root is None else root


def _day_name(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def write_session(*, record: Dict[str, Any], root: Optional[Path] = None) -> Path:
    root = _resolve_root(root)
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {"written_at": now, **dict(record or {})}
    path = root / f"{_day_name(now)}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _entries(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        return []
    return out


def count_today(*, root: Optional[Path] = None) -> int:
    path = _resolve_root(root) / f"{_day_name(time.time())}.jsonl"
    return len(_entries(path))


def count_since(since_ts: float, *, root: Optional[Path] = None) -> int:
    """Sessions started at or after ``since_ts`` (the day-boundary window,
    2026-08-06). A window that opens before midnight spans two calendar
    files, so both candidate days are read (deduped) and filtered by
    timestamp."""
    resolved = _resolve_root(root)
    paths = {resolved / f"{_day_name(ts)}.jsonl" for ts in (since_ts, time.time())}
    total = 0
    for path in paths:
        for row in _entries(path):
            try:
                if float(row.get("started", 0.0)) >= since_ts:
                    total += 1
            except (TypeError, ValueError):
                continue
    return total


def read_recent(*, days_back: int = 7, limit: int = 20,
                root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    cutoff = _day_name(time.time() - days_back * 86400)
    files = sorted((p for p in root.glob("*.jsonl") if p.stem >= cutoff))
    out: List[Dict[str, Any]] = []
    for path in reversed(files):
        for row in reversed(_entries(path)):
            out.append(row)
            if len(out) >= limit:
                return out
    return out


# ── pending shares (2026-08-18) ──────────────────────────────────────────
# A SHARE the arbiter's read of the moment HELD (his quiet hours, mid-
# conversation, no channel yet) waits here — one small JSON list, rewritten
# whole — and the engine's loop offers it again each tick. The arbiter keeps
# no retry state on purpose ("the moment IS the pending queue"); for shares
# the workbench is the moment, so the queue lives on this side of the wall.
# App-side state; her shell user cannot touch it.

PENDING_SHARES_NAME = "pending_shares.json"


def _pending_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / PENDING_SHARES_NAME


def read_pending_shares(*, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = _pending_path(root)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get("text") or "").strip()] \
        if isinstance(rows, list) else []


def _write_pending(rows: List[Dict[str, Any]], *, root: Optional[Path]) -> None:
    path = _pending_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _tidy_share_text(text: str) -> str:
    """Whitespace tidied per line; blank-line paragraph breaks kept (the
    letter of 2026-08-19 had paragraphs — collapsing everything sent it as
    one block). A one-sentence SHARE is unchanged by this."""
    paras = []
    for para in str(text or "").replace("\r", "").split("\n\n"):
        lines = [" ".join(ln.split()) for ln in para.split("\n")]
        joined = " ".join(ln for ln in lines if ln)
        if joined:
            paras.append(joined)
    return "\n\n".join(paras)


def write_pending_share(*, text: str, owner_id: int, at: Optional[float] = None,
                        root: Optional[Path] = None) -> Dict[str, Any]:
    """Queue one held share (idempotent on text: re-offering the same line
    twice must not make two messages)."""
    clean = _tidy_share_text(text)
    rows = read_pending_shares(root=root)
    for r in rows:
        if r.get("text") == clean:
            return r
    row = {"text": clean, "owner_id": int(owner_id),
           "at": float(at if at is not None else time.time()), "offers": 0}
    rows.append(row)
    _write_pending(rows, root=root)
    return row


def replace_pending_shares(rows: List[Dict[str, Any]], *, root: Optional[Path] = None) -> None:
    """The drain's write-back: whatever is still waiting, in order."""
    _write_pending([r for r in rows if isinstance(r, dict)], root=root)
