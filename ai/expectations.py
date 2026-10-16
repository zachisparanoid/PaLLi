"""The expectations layer (organ 8, spec §2): what is USUAL right now, from
her own records — his timing (the step-22 rhythm file), his register (her
own past sentiment reads), her own cadence (bench/dives/daydream/projects).
Deterministic, budgeted, floor-guarded: every line is a count or an age,
never a verdict — whether NOW departs is hers to judge in the appraisal
(owner rule 2026-07-16; rendering is the presentation exemption). Every
source degrades to omission; all-empty ⇒ "" (byte-absent)."""
from __future__ import annotations

import collections
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

from ai.owner_rhythm import RHYTHM_FILE, bucket_key  # noqa: E402  (re-exported for tests)

_BLOCK_CAP = 700          # spec §2: the content budget; the contract rides intact above it
_MOOD_WINDOW_DAYS = 28
_MOOD_FLOOR = 20          # tagged reads required before a baseline line
_RUN_FLOOR = 3            # consecutive same-tag reads that make a "run"
_USUALLY_AROUND = 0.5     # active_days/observed_days ≥ this ⇒ "usually around"
_FLOORS = {"weekday": 15, "weekend": 6}   # the rhythm file's own observation floors

_HEADER_LINE = (
    "EXPECTATIONS — what is USUAL right now, from your own records "
    "(code-counted; whether NOW departs from it is yours to judge; most "
    "moments are usual):"
)
_CONTRACT = (
    "If — and only if — this moment GENUINELY departs from the usual above, "
    "add to your JSON: \"surprise\": {\"about\": \"him\" | \"me\", \"line\": "
    "\"<one sentence, your words — what is unusual and what it might mean>\", "
    "\"weight\": \"slight\" | \"real\"}. Most moments are usual; never "
    "manufacture one. A surprise about the owner is a reason to look and to hold, "
    "not an alarm — and only a REAL one is ever worth saying out loud (a say "
    "with \"sensitivity\": \"surprise\")."
)


def surprise_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "surprise", False))
    except Exception:
        return False


def _timing_line(*, now: float) -> str:
    """him, this hour — from the rhythm file + what Discord says right now."""
    try:
        raw = json.loads(Path(RHYTHM_FILE).read_text(encoding="utf-8"))
        dt = datetime.fromtimestamp(now)
        key = bucket_key(dt)
        b = (raw.get("buckets") or {}).get(key) or {}
        a, o = int(b.get("active_days", 0)), int(b.get("observed_days", 0))
        kind = key.split("-")[0]
        if o < _FLOORS.get(kind, 15):
            return ""
        if bool(b.get("quiet")):
            usual = "usually dark — almost never around at this hour"
        elif o and a / o >= _USUALLY_AROUND:
            usual = "usually around"
        else:
            usual = "sometimes around"
        line = f"- them, this hour ({key}): active {a} of {o} observed days — {usual}"
        try:
            from core.owner_presence import render_line
            actual = render_line(now=now)
        except Exception:
            actual = ""
        if actual:
            line += f"; right now: {actual}"
        return line
    except Exception:
        return ""


def _read_sentiment_entries():
    """Her monologue entries, OLDEST FIRST — the store returns newest-first
    (read_entries sorts reverse=True), and the run detection below walks the
    tail, so normalize here at the single seam."""
    from ai.inner_monologue_store import read_entries
    entries = read_entries(days_back=_MOOD_WINDOW_DAYS, substantive_only=False, limit=2000)
    return sorted(entries, key=lambda e: float(getattr(e, "timestamp", 0.0) or 0.0))


def _mood_lines() -> List[str]:
    """him, lately — a baseline over HER OWN past reads (sentiment tags)."""
    try:
        tags = [
            str((e.sentiment or {}).get("tag") or "")
            for e in _read_sentiment_entries()
            if isinstance(getattr(e, "sentiment", None), dict) and (e.sentiment or {}).get("tag")
        ]
        tags = [t for t in tags if t and t != "neutral"]
        if len(tags) < _MOOD_FLOOR:
            return []
        top2 = [t for t, _ in collections.Counter(tags).most_common(2)]
        out = [f"- them, lately: your own reads of them this month mostly run {'/'.join(top2)}"]
        newest = tags[-1]
        run = 0
        for t in reversed(tags):
            if t != newest:
                break
            run += 1
        if run >= _RUN_FLOOR and newest not in top2:
            out.append(f"- them, right now: the last {run} in a row read {newest}")
        return out
    except Exception:
        return []


def _cadence_lines(now: float) -> List[str]:
    """you — her own working rhythm, from her own records."""
    out: List[str] = []
    try:
        from ai import workbench_store
        wk = [r for r in workbench_store.read_recent(days_back=7, limit=100)
              if float(r.get("started") or 0) >= now - 7 * 86400]
        prior = [r for r in workbench_store.read_recent(days_back=28, limit=400)
                 if now - 28 * 86400 <= float(r.get("started") or 0) < now - 7 * 86400]
        if wk or prior:
            usual = round(len(prior) / 3.0) if prior else 0
            out.append(f"- you, your bench: {len(wk)} session(s) in the last 7 days"
                       + (f" (usual for you: ~{usual}/wk)" if usual else ""))
    except Exception:
        pass
    try:
        from ai.curiosity_research import recent_research_entries
        dives_wk = len(recent_research_entries(days_back=7, limit=50))
        dives_mo = len(recent_research_entries(days_back=28, limit=200))
        if dives_mo:
            out.append(f"- you, your looking: {dives_wk} dive(s) this week (usual: ~{round(dives_mo / 4.0)}/wk)")
    except Exception:
        pass
    try:
        from ai.daydream_store import read_recent
        entries = read_recent(days_back=7, limit=1)
        if entries:
            hrs = max(0, int((now - entries[0].written_at) / 3600))
            out.append(f"- you, your daydreams: last one ~{hrs}h ago")
    except Exception:
        pass
    out.extend(_project_lines(now))
    return out


def _project_lines(now: float) -> List[str]:
    """A project she worked steadily (≥5 sections inside 14 days) then left
    ≥5 days earns an 'untouched' line — the self-surprise substrate."""
    try:
        from ai.workbench_trace import WORKBENCH_MD, _HEADER, _BULLET
        body = Path(WORKBENCH_MD).read_text(encoding="utf-8")
    except Exception:
        return []
    touches: dict = {}
    cur_date = None
    for ln in body.splitlines():
        h = _HEADER.match(ln)
        if h:
            try:
                cur_date = datetime.strptime(h.group(1), "%Y-%m-%d").date()
            except ValueError:
                cur_date = None
            continue
        if cur_date is None:
            continue
        b = _BULLET.match(ln)
        if b:
            name = b.group(1).strip().rstrip(":").rstrip("/").split("/")[0].strip("* ")
            if name and name.lower() not in ("active", "set down"):
                touches.setdefault(name, []).append(cur_date)
    out: List[str] = []
    today = datetime.fromtimestamp(now).date()
    for name, days in touches.items():
        days = sorted(set(days))
        if len(days) < 5:
            continue
        steady = any(
            len([d for d in days if 0 <= (d - start).days <= 13]) >= 5
            for start in days
        )
        gap = (today - days[-1]).days
        if steady and gap >= 5:
            out.append(f"- you, {name}: untouched {gap} days after steady work")
    return out[:2]


def render_expectations_block(*, now: Optional[float] = None,
                              include_contract: bool = True) -> str:
    """The whole block, "" when off/empty. Non-empty ends with "\\n\\n" so the
    template slot ({expectations_block}NEXT SECTION…) collapses to zero
    bytes when absent — flag-off prompts stay byte-identical (spec §3)."""
    if not surprise_enabled():
        return ""
    t = float(now if now is not None else time.time())
    lines: List[str] = []
    timing = _timing_line(now=t)
    if timing:
        lines.append(timing)
    lines.extend(_mood_lines())
    lines.extend(_cadence_lines(t))
    if not lines:
        return ""
    body = _HEADER_LINE + "\n" + "\n".join(lines)
    if len(body) > _BLOCK_CAP:
        # Content bows to the budget; the CONTRACT below never does — a
        # truncated output contract would corrupt the instruction itself.
        body = body[:_BLOCK_CAP].rstrip()
    if include_contract:
        body += "\n" + _CONTRACT
    return body + "\n\n"
