"""Her AI calls, one line each (the owner, 10-05: "create some way of
tracking what is using up all her spending budget, call by call please").

The spending check (``core/spending.py``) reads what left the account once an
hour: it can say THAT money went, not WHERE. This ledger says where. Every
call the provider billed is one JSON line in
``data/spending/calls-YYYY-MM.jsonl`` (the local month): when, which lane,
the model, the tokens the provider counted (cached input, uncached input,
output), whether DeepSeek's peak price applied, and what the call cost by the
price table below. ``palli_ctl spending calls`` reads it
(``core/spending_calls.py``) and sets its total beside the balance readings
for the same hours, so the table can be checked against the account.

The lane is the call's ``log_context`` (``reflect_turn``,
``present_moment_cycle`` …). Her loop's steps are ``reply`` when they answer
a person, and ``loop:<origin>`` otherwise (``loop:workbench`` is her own time).

Two places write it, and between them they cover every call that bills today:
- ``ai/llm_openai_compatible.py``: every side lane on an OpenAI-compatible
  brain, each 200 the provider billed (a budget-retried one too, the way the
  cache log counts it).
- ``core/agent/runner.py``: each step of her loop.
Not written: a STREAMED side call (its provider reports no usage, and no lane
streams today) and the CLI brains (billed by their own accounts).

Prices are DeepSeek's only, read off its pricing page 2026-10-05. A model not
in the table is recorded with its tokens and ``usd: null``: the counts are
still true, the cost is unknown. One line is a few hundred bytes appended on
the calling thread; a write the disk refuses is logged and dropped, and never
fails a call.

``PALLI_SPENDING_DIR`` moves the folder (the test suite's hermetic copy), the
same variable ``core/spending.py`` reads.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ai.deepseek_peak import is_deepseek_peak

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# USD per million tokens at DeepSeek's PEAK: (cached input, uncached input,
# output). Off-peak is half of each; ai/deepseek_peak.py says when.
PEAK_PRICES_PER_M: Dict[str, Tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.006, 0.30, 1.20),
}
OFF_PEAK_FACTOR = 0.5

REPLY_ORIGINS = frozenset({"user", "dashboard_user", "guest"})


def ledger_dir() -> Path:
    raw = os.environ.get("PALLI_SPENDING_DIR")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "spending"


def ledger_path(ts: float) -> Path:
    """The month's file for the local moment ``ts``."""
    return ledger_dir() / f"calls-{datetime.fromtimestamp(float(ts)):%Y-%m}.jsonl"


def loop_lane(origin: str) -> str:
    """The lane a step of her loop is filed under."""
    origin = str(origin or "user")
    return "reply" if origin in REPLY_ORIGINS else f"loop:{origin}"


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def cached_tokens(usage: Mapping[str, Any]) -> int:
    """The cached prompt tokens in a provider's usage block: DeepSeek's
    ``prompt_cache_hit_tokens``, else OpenAI's ``prompt_tokens_details``."""
    if "prompt_cache_hit_tokens" in usage:
        return _count(usage.get("prompt_cache_hit_tokens"))
    details = usage.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        return _count(details.get("cached_tokens"))
    return 0


def call_cost(model: str, *, hit: int, miss: int, out: int, peak: bool) -> Optional[float]:
    """What one call cost in USD, or None for a model with no price here."""
    prices = PEAK_PRICES_PER_M.get(str(model or "").strip().lower())
    if prices is None:
        return None
    cached, uncached, output = prices
    usd = (hit * cached + miss * uncached + out * output) / 1_000_000
    return usd if peak else usd * OFF_PEAK_FACTOR


def call_row(*, lane: str, provider: str, model: str, prompt: int, hit: int, out: int,
             session: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """The line for one call. ``prompt`` is every input token, cached or not;
    the uncached part is what is left once the cached part is taken out."""
    ts = time.time() if now is None else float(now)
    prompt, hit, out = _count(prompt), _count(hit), _count(out)
    hit = min(hit, prompt) if prompt else hit
    miss = max(0, prompt - hit)
    priced = str(model or "").strip().lower() in PEAK_PRICES_PER_M
    peak = priced and is_deepseek_peak(datetime.fromtimestamp(ts, tz=timezone.utc))
    usd = call_cost(model, hit=hit, miss=miss, out=out, peak=peak)
    row: Dict[str, Any] = {
        "ts": round(ts, 3),
        "at": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
        "lane": str(lane or "?"),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "hit": hit,
        "miss": miss,
        "out": out,
        "peak": peak,
        "usd": None if usd is None else round(usd, 7),
    }
    if session:
        row["session"] = str(session)
    return row


def record_call(*, lane: str, provider: str, model: str, prompt: int, hit: int, out: int,
                session: str = "", now: Optional[float] = None) -> None:
    """Append one call's line. Never raises: a ledger that cannot be written
    must not cost her the reply it was counting."""
    try:
        row = call_row(lane=lane, provider=provider, model=model, prompt=prompt, hit=hit,
                       out=out, session=session, now=now)
        path = ledger_path(row["ts"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — counted, never fatal
        logger.warning("call ledger: could not record a %s call (%s)", lane, exc)


def read_calls(since_ts: float, until_ts: float) -> List[Dict[str, Any]]:
    """Every recorded call with ``since_ts <= ts < until_ts``, oldest first,
    from each month's file the window touches."""
    months: List[str] = []
    cursor = datetime.fromtimestamp(float(since_ts)).replace(day=1, hour=0, minute=0,
                                                            second=0, microsecond=0)
    end = datetime.fromtimestamp(float(until_ts))
    while cursor <= end:
        months.append(f"{cursor:%Y-%m}")
        cursor = cursor.replace(year=cursor.year + (cursor.month == 12),
                                month=cursor.month % 12 + 1)
    rows: List[Dict[str, Any]] = []
    for month in months:
        path = ledger_dir() / f"calls-{month}.jsonl"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("call ledger: could not read %s (%s)", path, exc)
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
                ts = float(row["ts"])
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(row, dict) and since_ts <= ts < until_ts:
                rows.append(row)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows
