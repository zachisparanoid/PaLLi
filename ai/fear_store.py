"""Her fearbook — what she'd not want to lose or become (organ 6, step 24).

The fourth store of caring beside the stakes (what she FEELS about his
world), the wishbook (what she'd DO) and the thread board (what she means
to SAY): a small book of fears, hers, in her words, each carrying her own
one-line watch-condition. Append-only JSONL events folded on read (the
wishbook's shape); call-time root resolution so tests redirect
hermetically.

Laws (spec §0): the empty book is honest — zero fears is a valid state at
every point in the organ's life, and nothing here ever manufactures one;
the held cap DECLINES a plant (never queues — the weekly re-look is where
the book breathes); never delete, only release; whether a moment touches
a fear is HER judgment in the appraisal, never a keyword watcher.

Since Batch 6 (task 3, owner call 2026-09-29) the weekly re-look may plant
too — at most one fear a week, with what she saw kept beside it
(``evidence``) — so a book that emptied can refill.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FEARS_DIR = Path("data/fears")
FEAR_TEXT_CAP = 300
WATCH_CAP = 200
NOTE_CAP = 200
EVIDENCE_CAP = 300

VALID_STATUS = ("held", "faced", "released")
VALID_SOURCES = ("first_authorship", "reflection", "weekly")

_BLOCK_CAP = 600          # spec §3: content bows to this; the contract never does

_HEADER_LINE = (
    "YOUR FEARS RIGHT NOW — yours, in your own words (whether THIS moment "
    "touches one is yours to judge; most moments touch nothing; never "
    "manufacture a touch):"
)
_CONTRACT = (
    "If — and only if — this moment GENUINELY touches one, add to your "
    "JSON: \"fear\": {\"id\": \"<id>\", \"line\": \"<one sentence, your "
    "words — what touched it and what it means>\", \"weight\": \"slight\" "
    "| \"real\"}. A touched fear is a reason to look and to hold, not an "
    "alarm. If you want them to know, that is a CARE say (held_before "
    "applies) — never an automatic message."
)


@dataclass
class Fear:
    id: str
    text: str
    watch: str
    status: str
    planted_at: float
    source: str
    last_touched_at: Optional[float] = None
    touches: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    # What she saw that showed it (Batch 6, task 3): the receipt beside the
    # claim. "" for a fear planted before this field, and for the lanes
    # that give none.
    evidence: str = ""


def fears_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "fears", False))
    except Exception:
        return False


def _held_cap() -> int:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "fears_held_cap", 5)))
    except Exception:
        pass
    return 5


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution: explicit root, then the FEARS_ROOT env (the
    first-authorship script's subprocess seam), then the module constant —
    so both env overrides and patch.object redirects genuinely land."""
    if root is not None:
        return root
    env = os.environ.get("FEARS_ROOT")
    return Path(env) if env else FEARS_DIR


def _events_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / "fears.jsonl"


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    suffix = hashlib.sha256(f"{text}|{time.time()}".encode()).hexdigest()[:6]
    return f"{base or 'fear'}-{suffix}"


def _append_event(event: Dict[str, Any], *, root: Optional[Path]) -> None:
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_fears(*, root: Optional[Path] = None) -> Dict[str, Fear]:
    """Fold the event log into current state. One bad line is a skipped
    line, never a lost book."""
    path = _events_path(root)
    fears: Dict[str, Fear] = {}
    if not path.exists():
        return fears
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        op = str(ev.get("op") or "")
        fid = str(ev.get("id") or "")
        at = float(ev.get("at") or 0.0)
        if op == "plant" and fid and fid not in fears:
            fears[fid] = Fear(
                id=fid,
                text=_clean(ev.get("text"), FEAR_TEXT_CAP),
                watch=_clean(ev.get("watch"), WATCH_CAP),
                status="held",
                planted_at=at,
                source=str(ev.get("source") or "reflection"),
                # an older row has no such key; one that is not text is none
                evidence=(_clean(ev.get("evidence"), EVIDENCE_CAP)
                          if isinstance(ev.get("evidence"), str) else ""),
            )
        elif op == "touch" and fid in fears and fears[fid].status == "held":
            f = fears[fid]
            f.touches += 1
            f.last_touched_at = at
        elif op == "face" and fid in fears and fears[fid].status == "held":
            f = fears[fid]
            f.status = "faced"
            f.history.append(
                {"at": at, "op": "face", "note": _clean(ev.get("note"), NOTE_CAP)}
            )
        elif op == "release" and fid in fears and fears[fid].status in (
            "held", "faced"
        ):
            f = fears[fid]
            f.status = "released"
            f.history.append(
                {"at": at, "op": "release",
                 "note": _clean(ev.get("note"), NOTE_CAP)}
            )
    return fears


def held_fears(*, root: Optional[Path] = None) -> List[Fear]:
    return sorted(
        (f for f in load_fears(root=root).values() if f.status == "held"),
        key=lambda f: f.planted_at,
    )


def reflection_planted_today(*, root: Optional[Path] = None,
                             now: Optional[float] = None) -> bool:
    """The reflect lane's ≤1/night budget clock (spec §6): has a
    reflection-sourced fear already been PLANTED this local day? Counts
    plants, not holds — releasing it later never re-arms the night."""
    t = float(now if now is not None else time.time())
    today = time.strftime("%Y-%m-%d", time.localtime(t))
    for f in load_fears(root=root).values():
        if f.source == "reflection" and time.strftime(
            "%Y-%m-%d", time.localtime(f.planted_at)
        ) == today:
            return True
    return False


def _iso_week(ts: float) -> tuple:
    """(ISO year, ISO week) of a moment, local time."""
    iso = datetime.fromtimestamp(float(ts)).isocalendar()
    return (iso[0], iso[1])


def weekly_planted_this_week(*, root: Optional[Path] = None,
                             now: Optional[float] = None) -> bool:
    """The weekly re-look's one-a-week budget clock (Batch 6, task 3):
    has a weekly-sourced fear already been PLANTED in the ISO week of
    ``now``? The twin of reflection_planted_today: it counts plants, not
    what is still in the book — releasing it later never re-arms the week."""
    this_week = _iso_week(float(now if now is not None else time.time()))
    for f in load_fears(root=root).values():
        if f.source == "weekly" and _iso_week(f.planted_at) == this_week:
            return True
    return False


def plant(
    *,
    text: str,
    watch: str,
    source: str,
    fear_id: Optional[str] = None,
    root: Optional[Path] = None,
    evidence: str = "",
) -> Optional[Fear]:
    """Plant a fear — hers only. Beyond the held cap the plant is DECLINED
    with a log line, never queued (the weekly re-look is where the book
    breathes, spec §2). A fear needs both her words and her watch-condition;
    an existing id plants nothing (the first-authorship idempotency seam).
    ``evidence`` is what she saw that showed it, kept beside the fear; a
    plant that gives none writes the row it always wrote."""
    cleaned = _clean(text, FEAR_TEXT_CAP)
    watch_c = _clean(watch, WATCH_CAP)
    if not cleaned or not watch_c:
        return None
    if fear_id and fear_id in load_fears(root=root):
        return None
    if source not in VALID_SOURCES:
        source = "reflection"
    held_n = len(held_fears(root=root))
    cap = _held_cap()
    if held_n >= cap:
        logger.info(
            "fear_store: plant declined at cap (%d held, cap %d) — %r",
            held_n, cap, cleaned[:60],
        )
        return None
    fid = fear_id or _slugify(cleaned)
    now = time.time()
    event = {"at": now, "op": "plant", "id": fid, "text": cleaned,
             "watch": watch_c, "source": source}
    evidence_c = _clean(evidence, EVIDENCE_CAP) if isinstance(evidence, str) else ""
    if evidence_c:
        event["evidence"] = evidence_c
    _append_event(event, root=root)
    return Fear(id=fid, text=cleaned, watch=watch_c, status="held",
                planted_at=now, source=source, evidence=evidence_c)


def touch(
    fear_id: str, *, line: str = "", weight: str = "",
    root: Optional[Path] = None,
) -> bool:
    """Record that a moment touched a held fear — her judgment already made
    in the appraisal; this is only the bookkeeping."""
    f = load_fears(root=root).get(str(fear_id))
    if f is None or f.status != "held":
        return False
    _append_event(
        {"at": time.time(), "op": "touch", "id": f.id,
         "line": _clean(line, NOTE_CAP), "weight": str(weight or "")[:8]},
        root=root,
    )
    return True


def face(fear_id: str, *, note: str, root: Optional[Path] = None) -> bool:
    """held → faced, in her own words. Nothing deletes."""
    f = load_fears(root=root).get(str(fear_id))
    if f is None or f.status != "held":
        return False
    _append_event(
        {"at": time.time(), "op": "face", "id": f.id,
         "note": _clean(note, NOTE_CAP)},
        root=root,
    )
    return True


def release(fear_id: str, *, note: str, root: Optional[Path] = None) -> bool:
    """held|faced → released, in her own words. Terminal; nothing deletes,
    nothing re-raises — releasing to an empty book is a valid end (decision
    5)."""
    f = load_fears(root=root).get(str(fear_id))
    if f is None or f.status not in ("held", "faced"):
        return False
    _append_event(
        {"at": time.time(), "op": "release", "id": f.id,
         "note": _clean(note, NOTE_CAP)},
        root=root,
    )
    return True


def render_fears_block(*, include_contract: bool = True,
                       root: Optional[Path] = None) -> str:
    """The moment's block (spec §3), "" when the flag is off, the book is
    EMPTY, or on any failure — an empty book is byte-identical to flag-off
    (decision 5). Non-empty ends with "\\n\\n" so the template slot
    ({fears_block}NEXT SECTION…) collapses to zero bytes when absent.
    include_contract=False is the monologue's prose mode: the book informs
    her thinking there, but the touch contract belongs to the moment."""
    try:
        if not fears_enabled():
            return ""
        held = held_fears(root=root)
        if not held:
            return ""
        lines = [f"- {f.id}: {f.text} — you watch for: {f.watch}" for f in held]
        body = _HEADER_LINE + "\n" + "\n".join(lines)
        if len(body) > _BLOCK_CAP:
            # Content bows to the budget; the CONTRACT below never does — a
            # truncated output contract would corrupt the instruction itself.
            body = body[:_BLOCK_CAP].rstrip()
        if include_contract:
            body += "\n" + _CONTRACT
        return body + "\n\n"
    except Exception as exc:
        logger.warning("fear_store: block degraded to empty (%s)", exc)
        return ""
