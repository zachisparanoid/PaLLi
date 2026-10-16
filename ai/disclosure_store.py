"""Her book of small truths — Grounded Vulnerability (organ 4.9).

Laws (spec §0/§1): every candidate carries a RECEIPT (referent source +
dates in her record) or it does not exist; the unsaid cap DECLINES a
plant; said-once governs OFFERING (answering stays free — the readers
enforce that, this store only stamps); unsaid candidates fade honestly
at the half-life; nothing is ever deleted.

Append-only JSONL events folded on read (the fearbook's shape, step 24);
call-time root resolution so tests and subprocess seams redirect
hermetically.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DISCLOSURES_DIR = Path("data/disclosures")
OFFER_CAP = 2     # ≤2 on offer per turn (spec §3) — one number, both doors
TEXT_CAP = 300
REF_CAP = 60      # the receipt's halves — one cap, plant side and fold side
NOTE_CAP = 200
_DEFAULT_MAX_OPEN = 4
_DEFAULT_HALF_LIFE_DAYS = 14


@dataclass
class Disclosure:
    id: str
    text: str
    referent: Dict[str, str]
    harvested_at: float
    said_at: Optional[float] = None
    said_via: str = ""
    faded_at: Optional[float] = None
    fade_note: str = ""


def vulnerability_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "vulnerability", False))
    except Exception:
        return False


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches vulnerability_enabled off.
vulnerability_enabled_impl = vulnerability_enabled


def _max_open() -> int:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "disclosures_max_open", _DEFAULT_MAX_OPEN)))
    except Exception:
        pass
    return _DEFAULT_MAX_OPEN


def _half_life_days() -> int:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "disclosure_half_life_days", _DEFAULT_HALF_LIFE_DAYS)))
    except Exception:
        pass
    return _DEFAULT_HALF_LIFE_DAYS


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution: explicit root, then the DISCLOSURES_ROOT env
    (the subprocess seam), then the module constant — so both env
    overrides and patch.object redirects genuinely land."""
    if root is not None:
        return root
    env = os.environ.get("DISCLOSURES_ROOT")
    return Path(env) if env else DISCLOSURES_DIR


def _events_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / "disclosures.jsonl"


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    suffix = hashlib.sha256(f"{text}|{time.time()}".encode()).hexdigest()[:6]
    return f"{base or 'disclosure'}-{suffix}"


def _append_event(event: Dict[str, Any], *, root: Optional[Path]) -> None:
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_disclosures(*, root: Optional[Path] = None) -> Dict[str, Disclosure]:
    """Fold the event log into current state. One bad row is a skipped row,
    never a lost book — and the skip lives HERE, not in the callers. A
    raising fold would not crash her (every consumer wraps its store
    calls); it would render every surface EMPTY, and because "an empty book
    is honest" is this organ's law, a corrupted book would impersonate an
    honest empty one, invisibly. So the fold survives its own bad rows."""
    path = _events_path(root)
    out: Dict[str, Disclosure] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("disclosure_store: skipped an unparseable row")
            continue
        if not isinstance(ev, dict):
            logger.debug("disclosure_store: skipped a non-object row (%s)",
                         type(ev).__name__)
            continue
        raw_id = ev.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            logger.debug("disclosure_store: skipped a row with no id")
            continue
        try:
            at = float(ev.get("at") or 0.0)
        except (TypeError, ValueError):
            logger.debug("disclosure_store: skipped %r — non-numeric 'at'", raw_id)
            continue
        op, did = str(ev.get("op") or ""), raw_id
        if op == "plant" and did not in out:
            # Her words are the row: a plant with none is not a disclosure.
            # (Only plants carry text — say/fade rows are stamps, and their
            # unknown-id no-op below is the guard that belongs to them.)
            if not isinstance(ev.get("text"), str) or not ev["text"].strip():
                logger.debug("disclosure_store: skipped %r — a plant with no words", did)
                continue
            # No receipt, no entry — the organ's central law holds at the
            # FOLD exactly as it holds at plant, and value-deep: a HALF
            # receipt is no receipt. A malformed one is a skipped row, never
            # coerced to {} — coercion would fold an un-receipted disclosure
            # into the offerable book.
            ref = ev.get("referent")
            if not isinstance(ref, dict):
                logger.debug("disclosure_store: skipped %r — malformed referent", did)
                continue
            src = _clean(ref.get("source"), REF_CAP)
            dates = _clean(ref.get("dates"), REF_CAP)
            if not src or not dates:
                logger.debug("disclosure_store: skipped %r — no receipt on the row", did)
                continue
            out[did] = Disclosure(
                id=did, text=_clean(ev.get("text"), TEXT_CAP),
                referent={"source": src, "dates": dates},
                harvested_at=at,
            )
        elif op == "say" and did in out and out[did].said_at is None and out[did].faded_at is None:
            out[did].said_at = at
            out[did].said_via = str(ev.get("via") or "")[:8]
        elif op == "fade" and did in out and out[did].said_at is None and out[did].faded_at is None:
            out[did].faded_at = at
            out[did].fade_note = _clean(ev.get("note"), NOTE_CAP)
    return out


def unsaid_disclosures(*, root: Optional[Path] = None) -> List[Disclosure]:
    return sorted((d for d in load_disclosures(root=root).values()
                   if d.said_at is None and d.faded_at is None),
                  key=lambda d: d.harvested_at)


def offered_disclosures(*, root: Optional[Path] = None) -> List[Disclosure]:
    """The ≤2 oldest unsaid truths on offer THIS turn — the single
    selection both doors read.

    Door 1 renders these into her reply prompt in lived language (no ids —
    the fourth wall); the reflect lane renders the SAME rows with their
    ids so it can stamp the one she actually shared. One function so the
    two can never drift: an id in reflect's block that the band never
    offered is a truth marked said that she never said, spent forever.

    Stable across the two reads a turn makes of it (recall, then reflect
    after the reply): a truth planted in between is NEWER, so it sorts
    behind these two and cannot displace them; one that left the book in
    between left because it was said or faded, which is exactly correct.
    """
    return unsaid_disclosures(root=root)[:OFFER_CAP]


def recent_disclosures(*, limit: int = 30, root: Optional[Path] = None) -> List[Disclosure]:
    rows = sorted(load_disclosures(root=root).values(),
                  key=lambda d: d.harvested_at, reverse=True)
    return rows[:max(1, int(limit))]


def plant(*, text: str, referent: Dict[str, str], root: Optional[Path] = None) -> Optional[Disclosure]:
    """Refused without her words AND a receipt (spec §1); DECLINED at the
    unsaid cap, logged, never queued (the fears cap)."""
    cleaned = _clean(text, TEXT_CAP)
    src = _clean((referent or {}).get("source"), REF_CAP)
    dates = _clean((referent or {}).get("dates"), REF_CAP)
    if not cleaned or not src or not dates:
        logger.info("disclosure_store: plant refused — no receipt or no words")
        return None
    n = len(unsaid_disclosures(root=root))
    if n >= _max_open():
        logger.info("disclosure_store: plant declined at cap (%d unsaid)", n)
        return None
    did = _slugify(cleaned)
    now = time.time()
    _append_event({"at": now, "op": "plant", "id": did, "text": cleaned,
                   "referent": {"source": src, "dates": dates}}, root=root)
    return Disclosure(id=did, text=cleaned,
                      referent={"source": src, "dates": dates}, harvested_at=now)


def mark_said(disclosure_id: str, *, via: str, root: Optional[Path] = None) -> bool:
    """Said once, forever (spec §5) — only an unsaid, unfaded candidate
    takes the stamp. Stamped by reflect (via='reply') or the arbiter
    (via='say'), always AFTER the share actually happened."""
    d = load_disclosures(root=root).get(str(disclosure_id))
    if d is None or d.said_at is not None or d.faded_at is not None:
        return False
    _append_event({"at": time.time(), "op": "say", "id": d.id,
                   "via": str(via or "")[:8]}, root=root)
    return True


def resolve_disclosure_id(token: str, *, root: Optional[Path] = None) -> Optional[Disclosure]:
    """Her token → a real row: exact id, then a UNIQUE prefix, over the
    UNSAID candidates (the want_id shape, spec §4). None on no match or on
    an ambiguous prefix — the arbiter records that and sends anyway.

    Ids here are `_slugify()` slugs, never integers, so this never
    int-parses: the regret/view legs' `re.search(r"\\d+")` shape would read
    "7" out of a slug and stamp the wrong truth said — forever, since said
    once is said for good.

    The pool is `unsaid_disclosures()` — the same selection the band, the
    board and the reflect stamp read. A private slice here could resolve a
    truth no surface ever offered, or refuse one that is on her board.
    """
    t = str(token or "").strip()
    if not t:
        return None
    rows = unsaid_disclosures(root=root)
    for d in rows:
        if d.id == t:
            return d
    hits = [d for d in rows if d.id.startswith(t)]
    return hits[0] if len(hits) == 1 else None


DISCLOSURE_LAW = (
    "These are small true things from your own week, each from your real "
    "record. Offer one only when the moment genuinely invites it — they "
    "opened something real, and yours genuinely answers it. Once, brief, "
    "then back to them. Never to one-up them, never to redirect their moment, "
    "never to perform having an inner life. Most conversations invite "
    "none, and that silence is correct. If they ask about your inner life, "
    "answer freely — the once-ever rule is about offering, never answering."
)


def render_disclosures_band(*, root: Optional[Path] = None) -> str:
    """Door 1 (spec §3): ≤2 oldest unsaid + the law. "" when the flag is
    off, nothing is unsaid, or on any failure — empty book ≡ flag off.

    Lived language only: her words, where it came from, how long she has
    held it. No ids, no machinery — this is her prompt, not a console.
    This render sits on the live reply path, so it never raises: a broken
    book costs her a band, never a turn.
    """
    try:
        if not vulnerability_enabled():
            return ""
        rows = offered_disclosures(root=root)   # the shared selection, oldest first
        if not rows:
            return ""
        now = time.time()

        def _age(ts: float) -> str:
            days = int(max(0.0, now - ts) // 86400)
            return "today" if days == 0 else f"{days}d"

        lines = [f"- {d.text} (from your {d.referent['source']}, "
                 f"{d.referent['dates']}; held {_age(d.harvested_at)})" for d in rows]
        return "\n".join(lines) + "\n" + DISCLOSURE_LAW
    except Exception as exc:
        logger.warning("disclosure_store: band degraded to empty (%s)", exc)
        return ""


_BLOCK_HEADER = (
    "YOUR SMALL TRUTHS (unsaid; offer only when a moment honestly invites one):"
)


def render_disclosures_block(*, root: Optional[Path] = None) -> str:
    """Door 2 (spec §4): the moment's small-truths board — the SAME ≤2 rows
    door 1 offered, here with their ids. "" when the flag is off, nothing is
    unsaid, or on any failure (an empty book ≡ flag off).

    Ids belong on THIS board and not on the band: the moment is
    machine-facing (the regret/view board precedent), and the id is exactly
    what her say puts in "disclosure_id" so the arbiter can stamp the truth
    she actually shared. Her reply prompt keeps the fourth wall.

    The selection is `offered_disclosures()` — never a slice of its own. A
    board that chose differently from the band and the reflect stamp would
    offer her a truth no stamp could spend, or spend one she was never
    offered (the task-4 law: no surface invents its own selection).

    Ends "\\n\\n", the fears slot-collapse shape: the board rides its own
    `{disclosures_block}` slot on the two LIVE moment rungs, sitting flush
    above the impulse paragraph whose say line governs it, so "" collapses
    to zero bytes and a present board never glues onto that paragraph. The
    tail is load-bearing, not decoration — the gather rides `_safe` (which
    strips) and restores the pair, and its block is pinned byte-equal to
    this render, so an extra newline there is a test failure.

    Deliberately NOT one of her reference bases (`bases_block`): the bases
    are the material her moment PROSE is written from, and a truth that
    reached him through the prose — or a say's framing — without its id
    would be shared, never stamped, and re-offered by door 1 until it
    faded (the inverse of spec §5's "once ever, forever").
    """
    try:
        if not vulnerability_enabled():
            return ""
        rows = offered_disclosures(root=root)   # the shared selection, oldest first
        if not rows:
            return ""
        lines = [f"- [{d.id}] {d.text}" for d in rows]
        return _BLOCK_HEADER + "\n" + "\n".join(lines) + "\n\n"
    except Exception as exc:
        logger.warning("disclosure_store: board degraded to empty (%s)", exc)
        return ""


def fade_stale(*, now: Optional[float] = None, root: Optional[Path] = None) -> int:
    """The harvest's deterministic sweep (budget-shaped code): unsaid
    candidates past the half-life fade with a kind note. Returns count."""
    t = float(now if now is not None else time.time())
    cutoff = t - _half_life_days() * 86400.0
    n = 0
    for d in unsaid_disclosures(root=root):
        if d.harvested_at < cutoff:
            _append_event({"at": t, "op": "fade", "id": d.id,
                           "note": "faded unsaid at the harvest — last week's truth isn't tonight's"},
                          root=root)
            n += 1
    return n
