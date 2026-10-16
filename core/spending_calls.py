"""``palli_ctl spending calls``: where her AI money went, lane by lane and
call by call (the owner, 10-05: "create some way of tracking what is using up
all her spending budget, call by call please").

Reads the call ledger (``ai/call_ledger.py``: one line per billed call) and,
to check it, the hourly balance readings (``core/spending.py``). Never asks
the provider; read-only.

The check matters because the ledger's costs are worked out from a price
table, while the balance readings are what actually left the account. When
the two agree, the table can be trusted to say which lane spent it; when they
do not, something bills that the ledger does not see.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ai import call_ledger
from core import spending


def _tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


def _usd(amount: Optional[float], places: int = 3) -> str:
    return "?" if amount is None else f"${amount:.{places}f}"


def window(day: date, days: int, now: float) -> Tuple[float, float]:
    """Local midnight ``days - 1`` days before ``day``, to the next midnight
    after it (or now, when that is sooner)."""
    start = datetime.combine(day - timedelta(days=max(1, days) - 1), dtime.min).timestamp()
    end = datetime.combine(day + timedelta(days=1), dtime.min).timestamp()
    return start, min(end, now)


def lane_totals(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One total per lane, the costliest first (a lane with no known cost
    sorts by its tokens, after every priced lane)."""
    lanes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        lane = lanes.setdefault(str(row.get("lane") or "?"), {
            "lane": str(row.get("lane") or "?"), "calls": 0, "hit": 0, "miss": 0, "out": 0,
            "usd": None,
        })
        lane["calls"] += 1
        for key in ("hit", "miss", "out"):
            lane[key] += int(row.get(key) or 0)
        if row.get("usd") is not None:
            lane["usd"] = (lane["usd"] or 0.0) + float(row["usd"])
    return sorted(lanes.values(), key=lambda lane: (
        lane["usd"] is None, -(lane["usd"] or 0.0), -(lane["hit"] + lane["miss"] + lane["out"])))


def call_line(row: Mapping[str, Any], *, with_date: bool) -> str:
    stamp = datetime.fromtimestamp(float(row["ts"])).strftime("%m-%d %H:%M:%S" if with_date else "%H:%M:%S")
    usd = row.get("usd")
    return (f"  {stamp}  {str(row.get('lane') or '?'):<30} "
            f"{_tokens(int(row.get('miss') or 0)):>7} uncached · {_tokens(int(row.get('hit') or 0)):>7} cached · "
            f"{_tokens(int(row.get('out') or 0)):>6} out  {_usd(None if usd is None else float(usd), 4):>8}"
            f"{'  peak' if row.get('peak') else ''}")


def balance_check(rows: List[Mapping[str, Any]], since: float, until: float) -> Optional[str]:
    """The ledger's cost between the first and last balance reading inside
    the window, beside what the balance dropped over the same hours. None
    with fewer than two readings there. Readings from before the window's
    first recorded call are left out: those hours the ledger did not watch
    (10-05, its first day: "$0.32 … add up to $0.00")."""
    if rows:
        since = max(since, float(rows[0]["ts"]))
    readings = sorted(
        (float(r["ts"]), float(r["balance"])) for r in spending.read_lines(spending.samples_path())
        if isinstance(r.get("ts"), (int, float)) and isinstance(r.get("balance"), (int, float))
        and since <= float(r["ts"]) < until
    )
    if len(readings) < 2:
        return None
    first, last = readings[0][0], readings[-1][0]
    dropped = sum(max(0.0, before - after) for (_, before), (_, after) in zip(readings, readings[1:]))
    counted = sum(float(r["usd"]) for r in rows
                  if r.get("usd") is not None and first < float(r["ts"]) <= last)
    span = (f"{datetime.fromtimestamp(first):%H:%M}" if (last - first) < 86400
            else f"{datetime.fromtimestamp(first):%m-%d %H:%M}")
    return (f"Checked against the account: the balance dropped {_usd(dropped, 2)} between the readings at "
            f"{span} and {datetime.fromtimestamp(last):%H:%M}; the calls recorded in those hours add up to "
            f"{_usd(counted, 2)}. (The provider's balance can lag its calls by a few minutes.)")


def report_lines(*, day: Optional[date] = None, days: int = 1, lane: Optional[str] = None,
                 top: int = 10, every: bool = False, now: Optional[float] = None) -> List[str]:
    import time as _time

    now = _time.time() if now is None else float(now)
    day = day or datetime.fromtimestamp(now).date()
    since, until = window(day, days, now)
    rows = call_ledger.read_calls(since, until)
    if lane:
        rows = [row for row in rows if row.get("lane") == lane]
    today = datetime.fromtimestamp(now).date()
    if days <= 1:
        span = f"{day.isoformat()}{' (today so far)' if day == today else ''}"
    else:
        span = f"{(day - timedelta(days=days - 1)).isoformat()} to {day.isoformat()}"
    where = f" in lane {lane}" if lane else ""
    if not rows:
        return [f"No calls recorded{where} for {span}. The ledger starts at the restart that shipped it "
                f"({call_ledger.ledger_dir()}/calls-YYYY-MM.jsonl)."]

    priced = [row for row in rows if row.get("usd") is not None]
    total = sum(float(row["usd"]) for row in priced)
    lines = [f"Her AI calls{where}, {span}: {len(rows)} call{'s' if len(rows) != 1 else ''}, "
             f"{_usd(total, 2)} at DeepSeek's price for the hour of each call"]
    lines.append(f"  {'lane':<30} {'calls':>5} {'uncached':>9} {'cached':>9} {'output':>8} {'cost':>9} {'share':>6}")
    for entry in lane_totals(rows):
        share = f"{100 * entry['usd'] / total:.0f}%" if entry["usd"] is not None and total > 0 else "-"
        lines.append(f"  {entry['lane']:<30} {entry['calls']:>5} {_tokens(entry['miss']):>9} "
                     f"{_tokens(entry['hit']):>9} {_tokens(entry['out']):>8} {_usd(entry['usd']):>9} {share:>6}")
    hit = sum(int(row.get("hit") or 0) for row in rows)
    miss = sum(int(row.get("miss") or 0) for row in rows)
    if hit + miss:
        lines.append(f"Input tokens served from the provider's cache: {100 * hit / (hit + miss):.0f}% "
                     f"(cached input costs a fiftieth of uncached).")
    peak = [row for row in priced if row.get("peak")]
    if peak:
        lines.append(f"Peak-price calls: {len(peak)} of {len(rows)}, "
                     f"{_usd(sum(float(r['usd']) for r in peak), 2)} of the {_usd(total, 2)}.")
    unpriced = len(rows) - len(priced)
    if unpriced:
        lines.append(f"{unpriced} call{'s' if unpriced != 1 else ''} on a model with no price here: "
                     f"tokens counted, cost unknown.")
    if not lane:
        check = balance_check(rows, since, until)
        if check:
            lines.append(check)
    with_date = days > 1
    if top > 0 and priced:
        lines.append("Most expensive single calls:")
        for row in sorted(priced, key=lambda r: -float(r["usd"]))[:top]:
            lines.append(call_line(row, with_date=with_date))
    if every or lane:
        lines.append("Every call, in time order:")
        lines.extend(call_line(row, with_date=with_date) for row in rows)
    return lines
