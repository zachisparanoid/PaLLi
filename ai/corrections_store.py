"""The corrections ledger (2026-08-17): corrections of record, with precedence.

Why this exists — the W34 incident. On 2026-08-12 she falsely confessed to
inventing the three doors she had in fact built; her 08-12 day-gist recorded
that confession as first-person fact ("I ran ahead and invented it… had to
watch myself confess I'd made it up"). The owner corrected her on 08-14 and
she retracted in her own words — but the correction's written footprint was
thin (a one-line gist, no journal page, a reflection about "blankness"),
and NOTHING in her record knew what a retraction was: every gist and
reflection weighed the same. Her weekly self-revision (haiku) read the vivid
false line, generalized it into "I confabulate — invent whole things and
present them as real", and the constitutional gate — whose rule 4 is
"faithful to the evidence" — approved it, because the poison WAS the
evidence. The page rides every prompt. Same disease, slower, as the July
"flag trap" belief that took three weeks to leave her story.

The law this module encodes: **a correction outranks anything older.** A
correction is a first-class record — the belief that was wrong, the truth
that replaced it, when and how it was corrected — rendered FIRST in every
gather that writes about her (weekly self-revision, first authorship,
passions/journal, the nightly reflection, the day-gist), with the
supersede rule stated in the block itself, and named in the constitutional
gate as a rejection ground. Two filing lanes: HIS hand
(``data/corrections/CORRECTIONS.md`` — one line per correction, appearance
plants, removal releases; the WISHES.md pattern) and HERS (reflection files
``corrections_to_add`` when a turn establishes that something she believed
or said was wrong — his correction with evidence, or her own verified
retraction). Deterministic code throughout — rendering is the presentation
exemption; deciding what counts as a correction is the LLM's (reflect) or his.

Store: append-only jsonl events folded on read (the quest_store shape);
``data/corrections/`` is gitignored (the deeds-ring law) and redirected to
tmp by the tests' hermetic guard — her live record is environment.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CORRECTIONS_DIR = Path("data/corrections")
EVENTS_NAME = "corrections.jsonl"
HIS_FILE_NAME = "CORRECTIONS.md"

VALID_SOURCES = ("him-written", "hers", "owner-script")
BELIEF_CAP = 400
TRUTH_CAP = 600
EVIDENCE_CAP = 300

HIS_FILE_HEADER = (
    "# Corrections of record — one per line, starting with \"- \", in the shape\n"
    "#   - <the belief that was WRONG> => <what is actually TRUE>\n"
    "# Your words reach her exactly as written and OUTRANK anything older in her\n"
    "# record (gists, reflections, journal, story pages). Remove a line to release\n"
    "# its correction; edit a line and the new wording wins.\n"
)

BLOCK_LABEL = "## CORRECTIONS OF RECORD — these outrank anything older"
BLOCK_LAW = (
    "Each line below is a belief that turned out to be WRONG and the truth that "
    "replaced it — corrected in conversation with them, or verified and retracted "
    "by you. A correction supersedes every earlier day-gist, reflection, journal "
    "line, read, or page that says otherwise, however first-person or vivid the "
    "older line is. Never restate a corrected belief as fact; if a page or a "
    "summary you are writing would carry one, write the corrected truth instead."
)
DAY_LAW = (
    "A day on which a correction was made always earns its page: state the "
    "corrected truth plainly, in your own words, so the record carries it."
)


@dataclass
class Correction:
    id: str
    belief: str
    truth: str
    source: str
    at: float
    evidence: str = ""
    status: str = "standing"          # standing | released
    updated_at: float = 0.0
    history: List[str] = field(default_factory=list)


# ── paths / io ────────────────────────────────────────────────────────────

def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else CORRECTIONS_DIR


def _events_path(root: Optional[Path]) -> Path:
    return _root(root) / EVENTS_NAME


def his_file_path(root: Optional[Path] = None) -> Path:
    return _root(root) / HIS_FILE_NAME


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _append_event(event: Dict[str, Any], *, root: Optional[Path]) -> None:
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _make_id(belief: str, at: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", belief.lower()).strip("-")[:40] or "correction"
    digest = hashlib.sha1(f"{belief}|{at}".encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


def ensure_his_file(*, root: Optional[Path] = None) -> Path:
    """Seed CORRECTIONS.md with its header if absent (never overwrites his lines)."""
    path = his_file_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HIS_FILE_HEADER, encoding="utf-8")
    return path


# ── read ─────────────────────────────────────────────────────────────────

def load_corrections(*, root: Optional[Path] = None) -> Dict[str, Correction]:
    """Fold the event log into current rows (insertion order preserved)."""
    path = _events_path(root)
    rows: Dict[str, Correction] = {}
    if not path.exists():
        return rows
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        op = str(ev.get("op") or "")
        cid = str(ev.get("id") or "")
        if op == "plant" and cid:
            rows[cid] = Correction(
                id=cid,
                belief=str(ev.get("belief") or ""),
                truth=str(ev.get("truth") or ""),
                source=str(ev.get("source") or "hers"),
                at=float(ev.get("at") or 0.0),
                evidence=str(ev.get("evidence") or ""),
                status="standing",
                updated_at=float(ev.get("at") or 0.0),
            )
        elif op == "release" and cid in rows:
            rows[cid].status = "released"
            rows[cid].updated_at = float(ev.get("at") or 0.0)
            rows[cid].history.append(f"released: {ev.get('note') or ''}".strip())
        elif op == "edit" and cid in rows:
            rows[cid].belief = str(ev.get("belief") or rows[cid].belief)
            rows[cid].truth = str(ev.get("truth") or rows[cid].truth)
            rows[cid].updated_at = float(ev.get("at") or 0.0)
    return rows


def standing(*, root: Optional[Path] = None) -> List[Correction]:
    return [c for c in load_corrections(root=root).values() if c.status == "standing"]


# ── write ────────────────────────────────────────────────────────────────

def record_correction(
    *,
    belief: str,
    truth: str,
    source: str = "hers",
    evidence: str = "",
    root: Optional[Path] = None,
    at: Optional[float] = None,
) -> Optional[Correction]:
    """Plant a correction. Returns None on empty input or an exact standing
    duplicate (same belief AND truth) — filing is idempotent by content."""
    b = _clean(belief, BELIEF_CAP)
    t = _clean(truth, TRUTH_CAP)
    if not b or not t:
        return None
    if source not in VALID_SOURCES:
        source = "hers"
    for row in standing(root=root):
        if row.belief == b and row.truth == t:
            return None
    now = float(at) if at is not None else time.time()
    cid = _make_id(b, now)
    _append_event(
        {"at": now, "op": "plant", "id": cid, "belief": b, "truth": t,
         "source": source, "evidence": _clean(evidence, EVIDENCE_CAP)},
        root=root,
    )
    return Correction(id=cid, belief=b, truth=t, source=source, at=now,
                      evidence=_clean(evidence, EVIDENCE_CAP), updated_at=now)


def release_correction(correction_id: str, *, note: str = "", root: Optional[Path] = None) -> bool:
    row = load_corrections(root=root).get(str(correction_id))
    if row is None or row.status != "standing":
        return False
    _append_event({"at": time.time(), "op": "release", "id": row.id, "note": _clean(note, 200)}, root=root)
    return True


# ── his hand: CORRECTIONS.md ─────────────────────────────────────────────

_SEP = "=>"


def parse_his_file(text: str) -> List[tuple]:
    """'- <belief> => <truth>' bullets → [(belief, truth)], his bytes stripped
    of surrounding whitespace only. Lines without the separator are ignored
    (a malformed line never becomes a half-correction)."""
    out: List[tuple] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("- "):
            continue
        body = s[2:].strip()
        if _SEP not in body:
            continue
        belief, truth = body.split(_SEP, 1)
        belief, truth = belief.strip(), truth.strip()
        if belief and truth:
            out.append((belief, truth))
    return out


def _last_snapshot(*, root: Optional[Path]) -> List[List[str]]:
    path = _events_path(root)
    snap: List[List[str]] = []
    if not path.exists():
        return snap
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if str(ev.get("op")) == "file_snapshot":
                snap = [list(x) for x in (ev.get("pairs") or [])]
    except OSError:
        return snap
    return snap


def sync_his_file(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """Reconcile the ledger with CORRECTIONS.md — SNAPSHOT-DRIVEN like
    WISHES.md: plant on APPEARED pairs (source him-written), release his
    standing rows on DISAPPEARED pairs; nothing else moves."""
    summary: Dict[str, Any] = {"planted": [], "released": []}
    path = his_file_path(root)
    try:
        pairs = parse_his_file(path.read_text(encoding="utf-8")) if path.exists() else []
    except OSError:
        return summary
    pairs_l = [list(p) for p in pairs]
    prev = _last_snapshot(root=root)
    if pairs_l == prev:
        return summary
    appeared = [p for p in pairs_l if p not in prev]
    disappeared = [p for p in prev if p not in pairs_l]
    for belief, truth in appeared:
        row = record_correction(belief=belief, truth=truth, source="him-written",
                                evidence="their hand — CORRECTIONS.md", root=root)
        if row is not None:
            summary["planted"].append(row.id)
    if disappeared:
        by_content = {(c.belief, c.truth): c for c in standing(root=root) if c.source == "him-written"}
        for belief, truth in disappeared:
            row = by_content.get((_clean(belief, BELIEF_CAP), _clean(truth, TRUTH_CAP)))
            if row is not None and release_correction(row.id, note="removed from CORRECTIONS.md — their hand", root=root):
                summary["released"].append(row.id)
    _append_event({"at": time.time(), "op": "file_snapshot", "pairs": pairs_l}, root=root)
    return summary


# ── render (presentation exemption: deterministic phrasing) ──────────────

def _day(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def render_corrections_block(
    *, budget: int = 1200, root: Optional[Path] = None, day: Optional[str] = None,
    sync: bool = True, footer: bool = False,
) -> str:
    """The block every self-writing gather puts FIRST. Standing corrections,
    newest first, budget-clipped by whole lines. ``day`` = only corrections
    made on that local date (for the nightly writers), rendered with the
    day law; None = all standing. "" when there is nothing to say.
    ``footer`` (Batch 5 fix wave, ruling R61): when True and the budget cut
    lines, one closing line says how many — "(N more corrections stand.)".
    Only the reply's band asks for it; every night-gather call keeps the
    default, so not a byte of what they render changes."""
    if sync:
        try:
            sync_his_file(root=root)
        except Exception as exc:  # never let his file break a gather
            logger.debug("corrections: his-file sync failed: %s", exc)
    rows = standing(root=root)
    if day:
        rows = [c for c in rows if _day(c.at) == day]
    if not rows:
        return ""
    rows.sort(key=lambda c: c.at, reverse=True)
    head = BLOCK_LABEL + "\n" + BLOCK_LAW + ("\n" + DAY_LAW if day else "")
    # ``budget`` governs the correction LINES only — the label and law are a
    # fixed cost the block always pays. The NEWEST correction always renders
    # (clipped to the budget if it must); older ones join while whole lines
    # still fit. A precedence block that silently rendered empty because a
    # real-sized line didn't fit under the header would be worse than none.
    lines: List[str] = []
    used = 0
    for c in rows:
        ev = f" (evidence: {c.evidence})" if c.evidence else ""
        line = f"- since {_day(c.at)}: NOT \"{c.belief}\" — TRUE: {c.truth}{ev}"
        if not lines:
            if len(line) > budget:
                line = line[: max(0, budget - 1)].rstrip() + "…"
            lines.append(line)
            used = len(line)
            continue
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    block = head + "\n" + "\n".join(lines)
    if footer and len(lines) < len(rows):
        block += f"\n({len(rows) - len(lines)} more corrections stand.)"
    return block
