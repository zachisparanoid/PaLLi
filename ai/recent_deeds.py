"""Her recent DEEDS — actions, not words (impulse-freshness spec §3).

The 08-06 first-send incident: she delivered an MP3 and, minutes later,
asked the owner for inputs to make it — because nothing in her present
records what she DOES, only what she says. This is the plain record: a
tiny jsonl ring, deterministic phrases (the events_block precedent — the
owner's presentation exemption to the LLM rule), read by the moment
gather as one sense line. A sense, never custody: every failure here is
logged and dropped.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEEDS_PATH = Path("data/recent_deeds.jsonl")
_RING_CAP = 50

# v1 deed-shaped builtins (spec §3). Workbench sessions are deliberately
# absent — the own_work sense already carries them.
DEED_TOOLS = frozenset(
    {"deliver_artifact", "create_reminder", "cancel_reminder", "note_to_self",
     # Step 27 · Duplex (2026-08-27): her mid-task acts are deeds too.
     "chime_in", "ask_him"}
)


def _path(root: Optional[Path]) -> Path:
    return (Path(root) / "recent_deeds.jsonl") if root else DEEDS_PATH


def record_deed(kind: str, detail: str, *, root: Optional[Path] = None) -> None:
    try:
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows: List[str] = []
        if path.exists():
            rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows.append(json.dumps(
            {"at": time.time(), "kind": str(kind), "detail": str(detail)[:200]},
            ensure_ascii=False,
        ))
        path.write_text("\n".join(rows[-_RING_CAP:]) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("recent_deeds: write dropped (%s)", exc)


def record_tool_deed(tool_name: str, tool_input: Dict[str, Any], *,
                     root: Optional[Path] = None) -> None:
    name = str(tool_name or "")
    if name not in DEED_TOOLS:
        return
    inp = tool_input or {}
    if name == "deliver_artifact":
        fname = str(inp.get("path") or "").rsplit("/", 1)[-1] or "a file"
        detail = f"delivered a file: {fname}"
    elif name == "create_reminder":
        detail = f"set a reminder: {str(inp.get('message') or '')[:80]}"
    elif name == "cancel_reminder":
        detail = "cancelled a reminder"
    elif name == "chime_in":
        detail = f"said mid-task: {str(inp.get('text') or '')[:80]}"
    elif name == "ask_him":
        detail = f"asked them mid-task: {str(inp.get('question') or '')[:80]}"
    else:  # note_to_self
        detail = "wrote a note to yourself"
    record_deed(name, detail, root=root)


def read_recent_deeds(*, limit: int = 8, max_age_hours: float = 48.0,
                      root: Optional[Path] = None) -> List[Dict[str, Any]]:
    try:
        path = _path(root)
        if not path.exists():
            return []
        cutoff = time.time() - max_age_hours * 3600.0
        out: List[Dict[str, Any]] = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if float(d.get("at") or 0.0) >= cutoff:
                out.append(d)
        out.sort(key=lambda d: float(d.get("at") or 0.0), reverse=True)
        return out[: max(0, int(limit))]
    except Exception as exc:
        logger.warning("recent_deeds: read failed (%s)", exc)
        return []
