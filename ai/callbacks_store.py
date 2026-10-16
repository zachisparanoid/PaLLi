"""Her book of callbacks — Shared Idiolect, the provenance half (organ 4.12).

Laws (spec §0/§1): every entry carries a RECEIPT — who said it, when, the
exact line — or it does not exist, at plant AND at fold; a duplicate of an
OPEN bit is refused here (three writers, one rail); the only transition is
open → retired, by his word; nothing is ever deleted; no cap, no fade — the
quota half was her call to drop.

Append-only JSONL events folded on read (the disclosure store's shape);
call-time root resolution so tests and subprocess seams redirect
hermetically. The receipt is why she can say "that one's ours"; it never
renders in her mouth unprompted (§0.5) — the readers enforce that, this
store only holds it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CALLBACKS_DIR = Path("data/callbacks")
BIT_CAP = 80
LINE_CAP = 240
REASON_CAP = 240
SOURCE_CAP = 120
KINDS = ("nickname", "bit", "shorthand")
COINERS = ("him", "her", "both")
WHO = ("him", "her")

# `role: "user"` is a TRANSPORT map, not a speaker map (task 3 review I1,
# final review I1). On the reminder-fire and proactive surfaces every user
# row is machine-authored, and since DEFECT_RECORD §16 the synthesized row
# QUOTES HER RECENT LINES back inside it. Reading one of those and stamping
# `who: "him"` would put her own words in his mouth on a durable entry,
# which is the exact law this organ exists to keep. So only the records of
# the two of them TALKING are ever searched (ai/callbacks_lookup.py) or
# written from (the retire door in core/turn/reflect.py; since Batch 6 the
# births come from the weekly harvest, ai/callbacks_harvest.py, which reads
# only the rows stored under these surfaces' platforms). One
# tuple, one law — it lives here because every reader already imports this
# module and none should own another's rule.
# Telegram door (2026-09-14, task 2): the second chat door joins the law.
# A bit of THEIRS is born where the two of them TALK, and that is now true
# in one more room — leaving it out would mean a phrase coined on Telegram
# could never be remembered, and one coined on Discord could never be found
# there.
# The phone app (the mobile door, 2026-10-13) is a room they talk in too.
CONVERSATIONAL_SURFACES = ("discord_text", "discord_voice", "voice_local",
                           "dashboard", "telegram_text", "mobile_text")
_DEFAULT_BAND_MAX = 12


@dataclass
class Callback:
    id: str
    bit: str
    kind: str
    coined_by: str
    receipt: Dict[str, str]          # who, when (ISO 8601), line, source
    born_at: float
    retired_at: Optional[float] = None
    retired_reason: str = ""


def idiolect_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "idiolect", False))
    except Exception:
        return False


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches idiolect_enabled off.
idiolect_enabled_impl = idiolect_enabled


def _band_max() -> int:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        if comp is not None:
            return max(1, int(getattr(comp, "callbacks_band_max", _DEFAULT_BAND_MAX)))
    except Exception:
        pass
    return _DEFAULT_BAND_MAX


def _resolve_root(root: Optional[Path]) -> Path:
    """Call-time resolution: explicit root, then the CALLBACKS_ROOT env (the
    subprocess seam), then the module constant."""
    if root is not None:
        return root
    env = os.environ.get("CALLBACKS_ROOT")
    return Path(env) if env else CALLBACKS_DIR


def _events_path(root: Optional[Path]) -> Path:
    return _resolve_root(root) / "callbacks.jsonl"


def _clean(text: Any, cap: int) -> str:
    """Compose, collapse, THEN cut — in that order.

    The NFC fold has to happen before the cap, not only inside normalize():
    a decomposed spelling is longer in codepoints than its composed twin, so
    a cap applied first cuts the two at different words and files them as
    two bits (review round 3, N2). Composing here also means the stored form
    is the composed one, so her book reads back the way she typed it.
    """
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())[:cap]


_ZWJ = "\u200d"   # zero-width JOINER — structure inside an emoji cluster


def _is_noise(ch: str) -> bool:
    """Punctuation, or an invisible format character that is not the ZWJ.

    Category "Cf" is ZWSP, the BOM, the bidi marks — characters she never
    typed on purpose, that arrive with a paste and render as nothing. Left
    in, one of them splits a bit into a second open row that no lookup can
    reach. U+200D is the one that must survive: it JOINS the codepoints of
    an emoji cluster, and dropping it shatters one family into three
    people (review round 3, N1).
    """
    cat = unicodedata.category(ch)
    return cat.startswith("P") or (cat == "Cf" and ch != _ZWJ)


def normalize(text: Any) -> str:
    """The ONE comparison every rail shares — dedup here, the birth-line
    check in reflect, the phrase search in the lookup: fold to NFC,
    casefold, drop the noise, collapse whitespace.

    Noise only (`_is_noise`): punctuation, plus the invisible format
    characters, minus the ZWJ. The first cut kept only ASCII letters,
    digits and whitespace and dropped everything else, which folded every
    non-Latin bit to the empty string: `find_open_by_bit` short-circuits on
    an empty key, so "🍍" planted twice landed TWICE, "café" both missed
    "cafe" and collided with "caf", and task 3's phrase search inherited
    the same blindness (review I1). Her words — emoji, accents, any
    script — survive as themselves; only the marks around them go.

    A mark becomes a SPACE rather than nothing, so "pine-apple" still
    normalizes to "pine apple" and dedups against the spelled-out bit.

    NFC runs FIRST, before the casefold: an e with a combining acute and a
    precomposed e-acute are the same word typed on two keyboards, and
    without the fold her book files them as two bits that can never find
    each other (review round 2).
    """
    folded = unicodedata.normalize("NFC", str(text or "")).casefold()
    bare = "".join(" " if _is_noise(ch) else ch for ch in folded)
    return " ".join(bare.split())


def _valid_when(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _clean_receipt(raw: Any) -> Optional[Dict[str, str]]:
    """A receipt is who + when + line, all non-blank and real; None
    otherwise. `source` is optional provenance for the peek."""
    if not isinstance(raw, dict):
        return None
    who = _clean(raw.get("who"), 8).lower()
    when = _clean(raw.get("when"), 40)
    line = _clean(raw.get("line"), LINE_CAP)
    if who not in WHO or not line or not _valid_when(when):
        return None
    return {"who": who, "when": when, "line": line,
            "source": _clean(raw.get("source"), SOURCE_CAP)}


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    suffix = hashlib.sha256(f"{text}|{time.time()}".encode()).hexdigest()[:6]
    return f"{base or 'callback'}-{suffix}"


def _append_event(event: Dict[str, Any], *, root: Optional[Path]) -> None:
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_callbacks(*, root: Optional[Path] = None) -> Dict[str, Callback]:
    """Fold the event log. One bad row is a skipped row, never a lost book
    — and the receipts law holds here exactly as at plant: a plant row
    with no receipt, a half receipt, an unreal when, an unknown kind or
    coiner, or no bit is skipped, never coerced into the book."""
    path = _events_path(root)
    out: Dict[str, Callback] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("callbacks_store: skipped an unparseable row")
            continue
        if not isinstance(ev, dict):
            logger.debug("callbacks_store: skipped a non-object row")
            continue
        raw_id = ev.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            logger.debug("callbacks_store: skipped a row with no id")
            continue
        try:
            at = float(ev.get("at") or 0.0)
        except (TypeError, ValueError):
            logger.debug("callbacks_store: skipped %r — non-numeric 'at'", raw_id)
            continue
        op, cid = str(ev.get("op") or ""), raw_id
        if op == "plant" and cid not in out:
            bit = _clean(ev.get("bit"), BIT_CAP) if isinstance(ev.get("bit"), str) else ""
            kind = str(ev.get("kind") or "")
            coined_by = str(ev.get("coined_by") or "")
            receipt = _clean_receipt(ev.get("receipt"))
            if not bit or kind not in KINDS or coined_by not in COINERS or receipt is None:
                logger.debug("callbacks_store: skipped %r — no receipt, no bit, or bad shape", cid)
                continue
            # The C1 rail, value-deep at BOTH ends — the same promise this
            # docstring already makes about the receipt itself. A row whose
            # line does not carry its bit is malformed however it got here,
            # and the fold is where a book that was written by something
            # other than `plant` gets read back.
            if normalize(bit) not in normalize(receipt["line"]):
                logger.debug("callbacks_store: skipped %r — the line does not contain the bit", cid)
                continue
            out[cid] = Callback(id=cid, bit=bit, kind=kind, coined_by=coined_by,
                                receipt=receipt, born_at=at)
        elif op == "retire" and cid in out and out[cid].retired_at is None:
            out[cid].retired_at = at
            out[cid].retired_reason = _clean(ev.get("reason"), REASON_CAP)
    return out


def open_callbacks(*, root: Optional[Path] = None) -> List[Callback]:
    return sorted((c for c in load_callbacks(root=root).values() if c.retired_at is None),
                  key=lambda c: c.born_at)


def recent_callbacks(*, limit: int = _DEFAULT_BAND_MAX, root: Optional[Path] = None) -> List[Callback]:
    rows = sorted((c for c in load_callbacks(root=root).values() if c.retired_at is None),
                  key=lambda c: c.born_at, reverse=True)
    return rows[:max(1, int(limit))]


def all_callbacks(*, root: Optional[Path] = None) -> List[Callback]:
    return sorted(load_callbacks(root=root).values(), key=lambda c: c.born_at)


def find_callback(callback_id: str, *, root: Optional[Path] = None) -> Optional[Callback]:
    return load_callbacks(root=root).get(str(callback_id or "").strip())


def find_open_by_bit(bit: str, *, root: Optional[Path] = None) -> Optional[Callback]:
    key = normalize(bit)
    if not key:
        return None
    for c in open_callbacks(root=root):
        if normalize(c.bit) == key:
            return c
    return None


def plant(*, bit: str, kind: str, coined_by: str, receipt: Dict[str, str],
          root: Optional[Path] = None) -> Optional[Callback]:
    """Refused without a bit AND a whole receipt (spec §1); refused as a
    duplicate of an OPEN bit (normalized). Never raises on bad input."""
    cleaned = _clean(bit, BIT_CAP) if isinstance(bit, str) else ""
    rec = _clean_receipt(receipt)
    if (not cleaned or not normalize(cleaned) or kind not in KINDS
            or coined_by not in COINERS or rec is None):
        logger.info("callbacks_store: plant refused — no receipt, no bit, or bad shape")
        return None
    # A receipt IS the line the bit was born in, so the bit must be IN it.
    # Task 2 put this rail at reflect's door; task 3 opened a second door
    # (the lookup) whose 240-char cap could cut the bit out of its own proof
    # (review C1). Here it covers EVERY writer — "three writers, one rail"
    # is only true if the rail lives where they all pass.
    if normalize(cleaned) not in normalize(rec["line"]):
        logger.info("callbacks_store: plant refused — the line does not contain the bit")
        return None
    if find_open_by_bit(cleaned, root=root) is not None:
        logger.info("callbacks_store: plant refused — %r is already in the book", cleaned)
        return None
    cid = _slugify(cleaned)
    now = time.time()
    _append_event({"at": now, "op": "plant", "id": cid, "bit": cleaned, "kind": kind,
                   "coined_by": coined_by, "receipt": rec}, root=root)
    return Callback(id=cid, bit=cleaned, kind=kind, coined_by=coined_by, receipt=rec, born_at=now)


def retire(callback_id: str, *, reason: str, root: Optional[Path] = None) -> bool:
    """His word retires a bit (spec §0.4) — the entry stays, marked. Refuses
    an unknown or already-retired id, or a blank reason."""
    why = _clean(reason, REASON_CAP)
    if not why:
        return False
    c = load_callbacks(root=root).get(str(callback_id or "").strip())
    if c is None or c.retired_at is not None:
        return False
    _append_event({"at": time.time(), "op": "retire", "id": c.id, "reason": why, "by": "him"},
                  root=root)
    return True


# ── the reply door (spec §4) ────────────────────────────────────────────

CALLBACKS_LAW = (
    "These are words and bits that are genuinely yours together, each with a real "
    "origin in your record. Use them the way you'd naturally use them, or not at all. "
    "Claim a shared history only from here; if a bit isn't here, don't perform "
    "remembering it — ask, or look it up. The origins are yours to know, not to "
    "recite: say where something came from only when they ask."
)

_COINER_WORD = {"him": "theirs", "her": "hers", "both": "both of yours"}


def _born_day(when: str) -> str:
    """The receipt's day in HIS local time — the organ's convention, set at
    the lookup in task 3 and shared here so one bit reads the same day
    wherever it is named.

    `.astimezone()` is load-bearing, not decoration: a receipt stamped
    02:11 UTC is the night of the 13th on his wall, and rendering the UTC
    day would hand her a date he never lived. Naive stamps are already
    local, so the conversion is a no-op for them.

    Catches EVERY failure, not just the unparseable-string one: the render
    above promises that a broken book costs her a band and never a turn,
    and the same argument one level down says a broken ROW costs a day and
    never the band. A non-string `when` (which raises AttributeError on
    `.replace`, not ValueError) is unreachable today — `_clean_receipt`
    guarantees a real ISO string at plant AND at fold — but an unreachable
    guard is exactly where the wrong failure mode sits waiting.
    """
    try:
        return (datetime.fromisoformat(when.replace("Z", "+00:00"))
                .astimezone().strftime("%b %d"))
    except Exception:
        return "sometime"


def render_callbacks_band(*, root: Optional[Path] = None) -> str:
    """The reply door (spec §4): the most recent open bits as LIGHT MEMORY
    — the bit, whose it is, the day — under the law. Never the raw line,
    an id, or a source (§0.5: the receipt is why she can say it, never how
    she says it). "" when dark, empty, or on any failure; never raises —
    this sits on her live reply path, so a broken book costs her a band,
    never a turn."""
    try:
        if not idiolect_enabled():
            return ""
        rows = recent_callbacks(limit=_band_max(), root=root)
        if not rows:
            return ""
        lines = [f'- "{c.bit}" — {_COINER_WORD.get(c.coined_by, c.coined_by)}, '
                 f'{_born_day(c.receipt["when"])}' for c in rows]
        return "\n".join(lines) + "\n" + CALLBACKS_LAW
    except Exception as exc:
        logger.warning("callbacks_store: band degraded to empty (%s)", exc)
        return ""
