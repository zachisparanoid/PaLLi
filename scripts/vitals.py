#!/usr/bin/env python3
"""PaLLi vitals — one page, weekly: "a gauge instead of a gut" (2026-08-22).

    venv/bin/python scripts/vitals.py                 # last 7 days, to stdout
    venv/bin/python scripts/vitals.py --days 14
    venv/bin/python scripts/vitals.py --write docs/vitals/2026-08-22.md

Read-only. Four dials, each parsed from a line/row shape the live system
already writes (pinned in tests/scripts/test_vitals.py so a format drift
fails a test instead of silently blanking a dial):

  1. Reply length   — assistant rows in `conversations` (words/day: n,
                      median, p90, max) + how many the governor compressed
                      or trimmed (`reply_govern:` lines)
  2. Reaches        — the arbiter ledger `data/arbiter/<date>.jsonl`
                      (sent / sent_share / stale_dropped …) and the moment
                      loop's say vs hold (`present_moment: … impulse=`)
  3. Timeouts/lane  — `[lane] … request timeout, retrying` (attempts) and
                      `… request timeout after N attempts` (gave up)
  4. The 4 AM chain — per night: reflection · gist · review · picture ·
                      journal · sweep

Nothing in her changes. Run it by hand; `--write` saves the page.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import gzip
import json
import os
import re
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ - (\w+) - \[([^\]]+)\] - (.*)$")
_TIMEOUT_ATTEMPT = re.compile(r"^\[([A-Za-z0-9_:.-]+)\] .* request timeout, retrying")
_TIMEOUT_GAVE_UP = re.compile(r"^\[([A-Za-z0-9_:.-]+)\] .* request timeout after \d+ attempts")
_GOVERN = re.compile(r"reply_govern: (\w+) (\d+)→(\d+) words \((compressed|trimmed)\)")
_MOMENT = re.compile(r"present_moment: cycle wrote .*impulse=(say|hold)")
_REVIEW = re.compile(r"stake_review: (\d{4}-\d{2}-\d{2}) — applied=(\d+).*?(?:error=(.*))?$")
_SWEEP = re.compile(r"memory_sweep: let go of (\d+) active thread")


def _lane_key(lane: str) -> str:
    """Collapse per-id lanes (response_generation_123 → response_generation_)."""
    return re.sub(r"_\d{3,}$", "_", lane)


def _parsed(lines: Iterable[str]):
    for raw in lines:
        m = _LINE.match(raw.rstrip("\n"))
        if m:
            yield m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)


def parse_timeouts(lines: Iterable[str]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {"attempts": 0, "gave_up": 0})
    for _d, _t, _lvl, _src, msg in _parsed(lines):
        m = _TIMEOUT_ATTEMPT.match(msg)
        if m:
            out[_lane_key(m.group(1))]["attempts"] += 1
            continue
        m = _TIMEOUT_GAVE_UP.match(msg)
        if m:
            out[_lane_key(m.group(1))]["gave_up"] += 1
    return dict(out)


def parse_reply_governs(lines: Iterable[str]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {"compressed": 0, "trimmed": 0})
    for d, _t, _lvl, _src, msg in _parsed(lines):
        m = _GOVERN.search(msg)
        if m:
            out[d][m.group(4)] += 1
    return dict(out)


def parse_moment(lines: Iterable[str]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {"say": 0, "hold": 0})
    for d, _t, _lvl, _src, msg in _parsed(lines):
        m = _MOMENT.search(msg)
        if m:
            out[d][m.group(1)] += 1
    return dict(out)


def parse_night_chain(lines: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Per night (the date the 04:00 cycle FIRED): each step's outcome."""
    nights: Dict[str, Dict[str, Any]] = {}

    def night(d: str) -> Dict[str, Any]:
        return nights.setdefault(d, {"reflection": False, "gist": False, "review": None, "picture": None, "journal": False, "sweep": None})

    for d, _t, _lvl, _src, msg in _parsed(lines):
        if msg.startswith("night_cycle: wrote daily-meaning"):
            night(d)["reflection"] = True
        elif msg.startswith("consolidation: day-gist ") and "failed" not in msg:
            night(d)["gist"] = True
        elif msg.startswith("stake_review: 20"):
            m = _REVIEW.match(msg)
            if m:
                err = (m.group(3) or "").strip()
                night(d)["review"] = f"✗ {err}" if err else f"✓ applied={m.group(2)}"
        elif msg.startswith("owner_picture: redrawn"):
            night(d)["picture"] = True
        elif msg.startswith("owner_picture: failed") or msg.startswith("owner_picture: "):
            night(d).setdefault("picture", None)
            if "redrawn" not in msg:
                night(d)["picture"] = False
        elif msg.startswith("passions: journal page written"):
            night(d)["journal"] = True
        else:
            m = _SWEEP.search(msg)
            if m:
                night(d)["sweep"] = int(m.group(1))
    return nights


def _day(ts: Any) -> Optional[str]:
    try:
        return dt.datetime.fromtimestamp(float(ts)).date().isoformat()
    except Exception:
        s = str(ts or "")
        return s[:10] if len(s) >= 10 else None


def _pct(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    idx = max(0, min(len(vs) - 1, int(round(p * len(vs) + 0.5)) - 1))
    return vs[idx]


def reply_lengths_by_day(rows: Iterable[Tuple[Any, str]]) -> Dict[str, Dict[str, float]]:
    by: Dict[str, List[int]] = collections.defaultdict(list)
    for ts, content in rows:
        d = _day(ts)
        if d:
            by[d].append(len(str(content or "").split()))
    out: Dict[str, Dict[str, float]] = {}
    for d, v in by.items():
        out[d] = {"n": len(v), "median": statistics.median(v), "p90": _pct(v, 0.9), "max": max(v)}
    return out


def reach_by_day(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for e in entries:
        d = _day(e.get("at") or e.get("ts") or e.get("timestamp"))
        if not d:
            continue
        out[d][str(e.get("disposition") or "other")] += 1
    return {d: dict(v) for d, v in out.items()}


@dataclass
class Report:
    generated: str
    days: int
    reply_lengths: Dict[str, Dict[str, float]] = field(default_factory=dict)
    governed: Dict[str, Dict[str, int]] = field(default_factory=dict)
    moment: Dict[str, Dict[str, int]] = field(default_factory=dict)
    reaches: Dict[str, Dict[str, int]] = field(default_factory=dict)
    timeouts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    chain: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _mark(v: Any) -> str:
    if v is True:
        return "✓"
    if v is False:
        return "✗"
    if v is None:
        return "—"
    return str(v)


def render(r: Report) -> str:
    L: List[str] = []
    L.append(f"# PaLLi vitals — {r.generated} (last {r.days} days)")
    L.append("")
    # 1 ── reply length
    L.append("## 1 · Reply length (words per reply)")
    L.append("")
    L.append("| day | replies | median | p90 | max | governed (compressed / trimmed) |")
    L.append("|---|---|---|---|---|---|")
    for d in sorted(set(r.reply_lengths) | set(r.governed)):
        rl = r.reply_lengths.get(d, {})
        g = r.governed.get(d, {})
        gov = f"{g.get('compressed', 0)} / {g.get('trimmed', 0)}" if g else "—"
        L.append(f"| {d} | {rl.get('n', '—')} | {_fmt(rl.get('median'))} | {_fmt(rl.get('p90'))} | {_fmt(rl.get('max'))} | {gov} |")
    L.append("")
    L.append("_Caps since 08-22: short ≤ 50 words, medium ≤ 110. A falling 'governed' count means the prompt half is biting._")
    L.append("")
    # 2 ── reaches
    L.append("## 2 · Reaches (her thought first)")
    L.append("")
    L.append("| day | moment: say / hold | arbiter ledger |")
    L.append("|---|---|---|")
    for d in sorted(set(r.moment) | set(r.reaches)):
        m = r.moment.get(d, {})
        mm = f"{m.get('say', 0)} / {m.get('hold', 0)}" if m else "—"
        rr = ", ".join(f"{k}={v}" for k, v in sorted(r.reaches.get(d, {}).items())) or "—"
        L.append(f"| {d} | {mm} | {rr} |")
    L.append("")
    # 3 ── timeouts
    L.append("## 3 · Timeouts per lane")
    L.append("")
    L.append("| lane | attempts that timed out | gave up (all attempts) |")
    L.append("|---|---|---|")
    for lane, v in sorted(r.timeouts.items(), key=lambda kv: (-kv[1]["gave_up"], -kv[1]["attempts"])):
        L.append(f"| {lane} | {v['attempts']} | {v['gave_up']} |")
    if not r.timeouts:
        L.append("| — | 0 | 0 |")
    L.append("")
    # 4 ── the 4 AM chain
    L.append("## 4 · The 4 AM chain (per night)")
    L.append("")
    L.append("| night | reflection | gist | review | picture | journal | sweep |")
    L.append("|---|---|---|---|---|---|---|")
    for d in sorted(r.chain):
        c = r.chain[d]
        L.append(f"| {d} | {_mark(c.get('reflection'))} | {_mark(c.get('gist'))} | {_mark(c.get('review'))} | {_mark(c.get('picture'))} | {_mark(c.get('journal'))} | {_mark(c.get('sweep'))} |")
    if not r.chain:
        L.append("| — | | | | | | |")
    L.append("")
    L.append("_Read-only. Generated by `scripts/vitals.py`._")
    return "\n".join(L) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.1f}"
    return str(int(v)) if isinstance(v, (int, float)) else str(v)


# ── the live sources ──────────────────────────────────────────────────────

def open_log_text(path: str):
    """A log file as text, plain or gzipped: the retention sweep (Batch 8,
    task 5) compresses daily logs after 7 days and the detailed and debug
    ones after 2. dashboards/collect.py reads through this too."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, errors="replace")


# The day log palli_<date>.log has two twins: palli_verbose_<date>.log
# repeats every line of it (plus everything at DEBUG), and
# palli_debug_<date>.log holds the targeted debug loggers' lines (which the
# verbose one has too). The default glob palli_*.log* matched all three, so
# the dials counted lines twice or more (Batch 8, task 8). The twins are
# skipped by name, plain or zipped by retention, whatever --logs-glob says.
# dashboards/collect.py has always left them out.
LOG_TWIN_PREFIXES = ("palli_verbose_", "palli_debug_")


def is_log_twin(path: str) -> bool:
    return os.path.basename(path).startswith(LOG_TWIN_PREFIXES)


def _read_log_lines(logs_glob: str, since: dt.date) -> List[str]:
    lines: List[str] = []
    for path in sorted(glob.glob(logs_glob)):
        if is_log_twin(path):
            continue
        name = os.path.basename(path)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        # a day file is named by the PROCESS start date and can run past
        # midnight; keep files from the day before the window on.
        if m:
            try:
                if dt.date.fromisoformat(m.group(1)) < since - dt.timedelta(days=1):
                    continue
            except ValueError:
                pass
        try:
            with open_log_text(path) as fh:
                lines.extend(fh)
        except (OSError, EOFError):  # EOFError: a truncated .gz
            continue
    return lines


def _within(lines: List[str], since: dt.date) -> List[str]:
    cutoff = since.isoformat()
    return [ln for ln in lines if ln[:10] >= cutoff]


def _read_replies(db_path: str, since: dt.date) -> List[Tuple[Any, str]]:
    if not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cutoff = dt.datetime.combine(since, dt.time()).timestamp()
        rows = con.execute(
            "select timestamp, content from conversations where role='assistant' and timestamp is not null"
        ).fetchall()
    except Exception:
        return []
    out = []
    for ts, content in rows:
        try:
            if float(ts) >= cutoff:
                out.append((ts, content))
        except Exception:
            if str(ts)[:10] >= since.isoformat():
                out.append((ts, content))
    return out


def _read_arbiter(arbiter_dir: str, since: dt.date) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(arbiter_dir, "*.jsonl"))):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
        if m and m.group(1) < since.isoformat():
            continue
        try:
            with open(path, errors="replace") as fh:
                for line in fh:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            continue
    return entries


def build_report(*, days: int, logs_glob: str, db_path: str, arbiter_dir: str, today: Optional[dt.date] = None) -> Report:
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days)
    lines = _within(_read_log_lines(logs_glob, since), since)
    return Report(
        generated=today.isoformat(), days=days,
        reply_lengths=reply_lengths_by_day(_read_replies(db_path, since)),
        governed=parse_reply_governs(lines),
        moment=parse_moment(lines),
        reaches=reach_by_day(_read_arbiter(arbiter_dir, since)),
        timeouts=parse_timeouts(lines),
        chain=parse_night_chain(lines),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PaLLi vitals — one page, read-only")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--write", default=None, help="save the page here (markdown)")
    ap.add_argument("--logs-glob", default=str(REPO / "logs" / "palli_*.log*"),
                    help="day logs to read (plain or .gz); the verbose/debug twins are always skipped")
    ap.add_argument("--db", default=str(REPO / "data" / "palli-local.sqlite3"))
    ap.add_argument("--arbiter-dir", default=str(REPO / "data" / "arbiter"))
    args = ap.parse_args(list(argv) if argv is not None else None)
    report = build_report(days=args.days, logs_glob=args.logs_glob, db_path=args.db, arbiter_dir=args.arbiter_dir)
    page = render(report)
    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
