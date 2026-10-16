"""Her wishbook — the quest store (Wanting stage 1, re-spec §3).

A quest is something she'd DO with her own hands and time — the third
store of caring beside the stakes (what she FEELS about his world) and
the thread board (what she means to SAY). Append-only JSONL event log,
state folded on read; call-time root resolution so tests redirect
hermetically (the self-notes store's pattern and lesson).

Laws (re-spec §2/§3): releasing is always free and never re-raised by
machinery; the alive cap forces prioritization — machinery filings land
DORMANT at cap, kept and never displacing; done/released are terminal
for machinery (only the owner re-plants); his words are never normalised.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

QUESTS_DIR = Path("data/quests")
QUEST_TEXT_CAP = 500
PROGRESS_NOTE_CAP = 200

VALID_STATUS = ("alive", "dormant", "done", "released")
VALID_SOURCES = ("him-spoken", "him-written", "hers")
VALID_HORIZONS = ("soon", "someday", "standing")

_HORIZON_ORDER = {"soon": 0, "standing": 1, "someday": 2}


@dataclass
class Quest:
    id: str
    text: str
    source: str
    born: float
    horizon: str
    status: str
    provenance: str
    progress: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution so patch.object(module, "QUESTS_DIR", ...)
    genuinely redirects (the step-02 lesson)."""
    return QUESTS_DIR if root is None else root


def _events_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / "quests.jsonl"


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    suffix = hashlib.sha256(f"{text}|{time.time()}".encode()).hexdigest()[:6]
    return f"{base or 'wish'}-{suffix}"


def _append_event(event: Dict[str, Any], *, root: Optional[Path]) -> None:
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _max_alive() -> int:
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "quests_max_alive", 7)))
    except Exception:
        pass
    return 7


def load_quests(*, root: Optional[Path] = None) -> Dict[str, Quest]:
    """Fold the event log into current state. One bad line is a skipped
    line, never a lost book."""
    path = _events_path(root)
    quests: Dict[str, Quest] = {}
    if not path.exists():
        return quests
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        op = str(ev.get("op") or "")
        qid = str(ev.get("id") or "")
        at = float(ev.get("at") or 0.0)
        if op == "plant" and qid and qid not in quests:
            quests[qid] = Quest(
                id=qid,
                text=_clean(ev.get("text"), QUEST_TEXT_CAP),
                source=str(ev.get("source") or "hers"),
                born=at,
                horizon=str(ev.get("horizon") or "someday"),
                status=str(ev.get("status") or "alive"),
                provenance=_clean(ev.get("provenance"), 300),
                updated_at=at,
            )
        elif op == "status" and qid in quests:
            q = quests[qid]
            q.status = str(ev.get("status") or q.status)
            note = _clean(ev.get("note"), PROGRESS_NOTE_CAP)
            if note:
                q.progress.append({"at": at, "note": note})
            q.updated_at = at
        elif op == "progress" and qid in quests:
            quests[qid].progress.append(
                {"at": at, "note": _clean(ev.get("note"), PROGRESS_NOTE_CAP)}
            )
            quests[qid].updated_at = at
        elif op == "text" and qid in quests:
            quests[qid].text = _clean(ev.get("text"), QUEST_TEXT_CAP)
            quests[qid].updated_at = at
    return quests


def alive_count(*, root: Optional[Path] = None) -> int:
    return sum(1 for q in load_quests(root=root).values() if q.status == "alive")


def plant(
    *,
    text: str,
    source: str,
    horizon: str = "someday",
    provenance: str = "",
    quest_id: Optional[str] = None,
    root: Optional[Path] = None,
) -> Tuple[Optional[Quest], bool]:
    """Plant a wish. Returns (quest, landed_dormant).

    At the alive cap the filing lands DORMANT (re-spec §3): kept and
    visible, never displacing, never silently dropped — promotion is hers.
    Empty text plants nothing. An explicit quest_id (the backfill's
    stable-id path) is idempotent: an existing id plants nothing.
    """
    cleaned = _clean(text, QUEST_TEXT_CAP)
    if not cleaned:
        return None, False
    if quest_id and quest_id in load_quests(root=root):
        return None, False
    if source not in VALID_SOURCES:
        source = "hers"
    if horizon not in VALID_HORIZONS:
        horizon = "someday"
    prov = _clean(provenance, 300)
    # Step 25 · Regret (spec §5): a repair quest carries provenance
    # "regret:<id>" — at most ONE per regret, ever (any status: a done or
    # released repair still counts). The door declines; nothing is queued.
    if prov.startswith("regret:") and any(
        q.provenance == prov for q in load_quests(root=root).values()
    ):
        logger.info("quest_store: repair quest declined — %s already has one", prov)
        return None, False
    at_cap = alive_count(root=root) >= _max_alive()
    status = "dormant" if at_cap else "alive"
    qid = quest_id or _slugify(cleaned)
    now = time.time()
    _append_event(
        {
            "at": now, "op": "plant", "id": qid, "text": cleaned,
            "source": source, "horizon": horizon, "status": status,
            "provenance": prov,
        },
        root=root,
    )
    return (
        Quest(id=qid, text=cleaned, source=source, born=now, horizon=horizon,
              status=status, provenance=prov, updated_at=now),
        at_cap,
    )


def set_status(
    quest_id: str, status: str, *, note: str = "", root: Optional[Path] = None
) -> bool:
    """Lifecycle op. done/released are TERMINAL for machinery (only the
    owner re-plants, re-spec §6); promote (dormant→alive) respects the
    cap — at cap it appends nothing and returns False."""
    if status not in VALID_STATUS:
        return False
    q = load_quests(root=root).get(str(quest_id))
    if q is None or q.status in ("done", "released"):
        return False
    if status == "alive" and q.status == "dormant":
        if alive_count(root=root) >= _max_alive():
            return False
    _append_event(
        {"at": time.time(), "op": "status", "id": q.id, "status": status,
         "note": _clean(note, PROGRESS_NOTE_CAP)},
        root=root,
    )
    return True


def add_progress(quest_id: str, note: str, *, root: Optional[Path] = None) -> bool:
    if str(quest_id) not in load_quests(root=root):
        return False
    _append_event(
        {"at": time.time(), "op": "progress", "id": str(quest_id),
         "note": _clean(note, PROGRESS_NOTE_CAP)},
        root=root,
    )
    return True


def update_text(quest_id: str, new_text: str, *, root: Optional[Path] = None) -> bool:
    cleaned = _clean(new_text, QUEST_TEXT_CAP)
    if not cleaned or str(quest_id) not in load_quests(root=root):
        return False
    _append_event(
        {"at": time.time(), "op": "text", "id": str(quest_id), "text": cleaned},
        root=root,
    )
    return True


def resolve_want_id(token: str, *, root: Optional[Path] = None) -> Optional[Quest]:
    """Exact-then-prefix resolve over OPEN (alive|dormant) quests — the
    rail's thread-resolve pattern (re-spec §5.3)."""
    token = str(token or "").strip()
    if not token:
        return None
    open_rows = [
        q for q in load_quests(root=root).values()
        if q.status in ("alive", "dormant")
    ]
    for q in open_rows:
        if q.id == token:
            return q
    for q in open_rows:
        if q.id.startswith(token):
            return q
    return None


def _age_days(now: float, ts: float) -> int:
    return max(0, int((now - ts) // 86400))


def render_board(*, budget_chars: int = 800, now: Optional[float] = None,
                 root: Optional[Path] = None) -> str:
    """The appraisal's wants board (re-spec §5.2): alive quests as [id8]
    lines, ages real, dormant as one count line. Deterministic
    presentation (the threads-digest law); the judgment stays hers."""
    quests = load_quests(root=root)
    alive = sorted(
        (q for q in quests.values() if q.status == "alive"),
        key=lambda q: (_HORIZON_ORDER.get(q.horizon, 2), q.born),
    )
    dormant_n = sum(1 for q in quests.values() if q.status == "dormant")
    if not alive and not dormant_n:
        return "Nothing in your wishbook."
    ts = float(now if now is not None else time.time())
    lines: List[str] = []
    used = 0
    for q in alive:
        line = f"- [{q.id[:8]}] [{q.horizon} | open {_age_days(ts, q.born)}d] {q.text}"
        if q.progress:
            line += f" (last moved {_age_days(ts, float(q.progress[-1]['at']))}d ago)"
        if used + len(line) + 1 > budget_chars:
            lines.append(f"(+{len(alive) - len(lines)} more not shown)")
            break
        lines.append(line)
        used += len(line) + 1
    if dormant_n:
        lines.append(f"({dormant_n} more waiting dormant)")
    return "\n".join(lines)


def render_wishbook(*, now: Optional[float] = None,
                    root: Optional[Path] = None) -> str:
    """The daydream prompt's fuller view (re-spec §4.3): alive AND dormant
    with the freshest progress note, so satisfaction is visible evidence."""
    quests = load_quests(root=root)
    rows = sorted(
        (q for q in quests.values() if q.status in ("alive", "dormant")),
        key=lambda q: (0 if q.status == "alive" else 1, q.born),
    )
    if not rows:
        return "(your wishbook is empty)"
    ts = float(now if now is not None else time.time())
    lines: List[str] = []
    for q in rows:
        tag = "" if q.status == "alive" else " — dormant; promote it if it pulls"
        line = (
            f"- [{q.id[:8]}] ({q.horizon}, open {_age_days(ts, q.born)}d, "
            f"from {q.source}) {q.text}{tag}"
        )
        if q.progress:
            last = q.progress[-1]
            line += (
                f"\n    last: {last['note']} "
                f"({_age_days(ts, float(last['at']))}d ago)"
            )
        lines.append(line)
    return "\n".join(lines)


# ── WISHES.md — his hand (re-spec §3/§6) ─────────────────────────────────

WISHES_FILENAME = "WISHES.md"


def _wishes_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / WISHES_FILENAME


def parse_wishes_md(text: str) -> List[str]:
    """One wish per '- ' bullet — HIS BYTES between the marker and EOL,
    stripped of surrounding whitespace only, never normalised (§0.9)."""
    out: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and stripped[2:].strip():
            out.append(stripped[2:].strip())
    return out


def _last_snapshot(*, root: Optional[Path]) -> List[str]:
    path = _events_path(root)
    snap: List[str] = []
    if not path.exists():
        return snap
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(ev.get("op")) == "file_snapshot":
            snap = [str(b) for b in (ev.get("bullets") or [])]
    return snap


def sync_wishes_file(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """Reconcile the store with his file. SNAPSHOT-DRIVEN: plant on
    APPEARED bullets, release open him-written rows on DISAPPEARED ones
    (un-planting is his same power), single-swap = an edit keeping the
    row's id and progress. A line persisting after SHE released is not
    re-raised — plant fires only on appearance; delete-then-readd is his
    re-plant and gets a NEW row even when a closed row shares the text."""
    summary: Dict[str, Any] = {
        "planted": [], "released": [], "edited": [], "dormant": [],
    }
    path = _wishes_path(root)
    try:
        bullets = (
            parse_wishes_md(path.read_text(encoding="utf-8"))
            if path.exists() else []
        )
    except OSError:
        return summary
    prev = _last_snapshot(root=root)
    if bullets == prev:
        return summary
    appeared = [b for b in bullets if b not in prev]
    disappeared = [b for b in prev if b not in bullets]

    def _open_him_written(text: str) -> Optional[Quest]:
        target = _clean(text, QUEST_TEXT_CAP)
        for q in load_quests(root=root).values():
            if (q.source == "him-written" and q.status in ("alive", "dormant")
                    and q.text == target):
                return q
        return None

    if len(appeared) == 1 and len(disappeared) == 1:
        row = _open_him_written(disappeared[0])
        if row is not None:
            update_text(row.id, appeared[0], root=root)
            summary["edited"].append(row.id)
            _append_event(
                {"at": time.time(), "op": "file_snapshot", "bullets": bullets},
                root=root,
            )
            return summary

    for text in disappeared:
        row = _open_him_written(text)
        if row is not None and set_status(
            row.id, "released", note="removed from WISHES.md — their hand",
            root=root,
        ):
            summary["released"].append(row.id)

    for text in appeared:
        q, was_dormant = plant(
            text=text, source="him-written", horizon="standing",
            provenance="WISHES.md", root=root,
        )
        if q is not None:
            summary["planted"].append(q.id)
            if was_dormant:
                summary["dormant"].append(q.id)

    _append_event(
        {"at": time.time(), "op": "file_snapshot", "bullets": bullets},
        root=root,
    )
    return summary
