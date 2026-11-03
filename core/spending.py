"""The spending check (owner decision T2b-6, 2026-10-04).

The guest queue stays unlimited (the owner dropped the limit), "and the
spending alert is built now: a check that tells the owner when a day's
spending jumps well above normal. It never stops her." This is that check.
It reads, it compares, it tells the owner — and that is all it can do: it has
no hand on a queue, a model or a turn.

How it measures. Once an hour, in her own process, the brain's account
balance is read with the same probe the boot uses
(``core.launcher_support.probe_llm_balance`` — DeepSeek today; a provider with
no balance endpoint is simply never measured, so this is harmless anywhere)
and appended to ``data/spending/balance.jsonl``. No price table: what left the
account is what was spent.

- A day's spending is the sum of the balance DROPS between consecutive
  readings taken that local day. A rise is a top-up and counts for nothing —
  it neither adds spending nor hides the spending around it. A pair of
  readings across midnight (or across days she was off) belongs to no day: it
  cannot say when the money went. That loses the same hour from every day, so
  today and the normal day stay comparable.
- A normal day is the median spending of the last 14 complete days that have
  at least two readings (one reading measures nothing). Fewer than 3 such
  days: no normal day yet, so no alert — a new install, or a new brain, says
  nothing until it knows what normal is.
- An alert: today's spending so far is at least
  ``max(spending.alert_multiple x normal, spending.alert_floor_usd)``. The
  floor keeps a cheap baseline from alerting over cents. At most one alert a
  local day, remembered in ``data/spending/alerts.jsonl`` so a restart does
  not alert twice.

How it tells. A plain notice, marked as not hers ("Spending check (automatic,
not PaLLi): ..."), through ``core.owner_notice``: the owner's Discord DMs, or
the owner's Telegram chat when Discord cannot take it. Never through her
model, never into her conversation history. Whether or not a door took it,
one WARNING line is logged and the alert is written to alerts.jsonl.
``palli_ctl spending`` reads both files.

Paths: ``PALLI_SPENDING_DIR`` overrides the folder, which is how the test
suite keeps off the live files (tests/conftest.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from core.settings_schema import SpendingConfig

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_EVERY_S = 3600.0
# Sit out startup, like the other engines: the first reading a few minutes in,
# when the Discord gateway is up to carry an alert.
BOOT_DELAY_S = 300.0
# An hour's check that failed outright (a disk error, say) is tried again
# sooner than the next hour, and never in a tight loop.
ERROR_BACKOFF_S = 600.0
BASELINE_DAYS = 14
MIN_BASELINE_DAYS = 3


# ── where it lives ────────────────────────────────────────────────────────

def spending_dir() -> Path:
    raw = os.environ.get("PALLI_SPENDING_DIR")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "spending"


def samples_path() -> Path:
    return spending_dir() / "balance.jsonl"


def alerts_path() -> Path:
    return spending_dir() / "alerts.jsonl"


def append_line(path: Path, row: Mapping[str, Any]) -> bool:
    """One JSON object as one line. False (and a log line) when the disk said
    no — a reading or an alert that cannot be written never stops anything."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("spending check: could not write %s (%s)", path, exc)
        return False


def read_lines(path: Path) -> List[Dict[str, Any]]:
    """Every JSON object in the file, in file order. A line that is not one
    is skipped; a missing file is an empty list."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("spending check: could not read %s (%s)", path, exc)
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# ── the arithmetic (pure) ─────────────────────────────────────────────────

def local_day(ts: float) -> date:
    return datetime.fromtimestamp(float(ts)).date()


def _points(samples: Iterable[Mapping[str, Any]]) -> List[Tuple[float, float]]:
    """(ts, balance) for every reading that has both, oldest first."""
    points: List[Tuple[float, float]] = []
    for row in samples:
        try:
            points.append((float(row["ts"]), float(row["balance"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda point: point[0])
    return points


def _by_day(samples: Iterable[Mapping[str, Any]]) -> Dict[date, List[float]]:
    days: Dict[date, List[float]] = {}
    for ts, balance in _points(samples):
        days.setdefault(local_day(ts), []).append(balance)
    return days


def _drops(balances: List[float]) -> float:
    total = 0.0
    for before, after in zip(balances, balances[1:]):
        if after < before:
            total += before - after
    # Float noise must not decide a threshold exactly met (100.0 - 98.0).
    return round(total, 6)


def daily_spend(samples: Iterable[Mapping[str, Any]], day: date) -> float:
    """The sum of the balance drops between consecutive readings of ``day``
    (local). A rise between two readings is a top-up and is ignored."""
    return _drops(_by_day(samples).get(day, []))


def measured_days(samples: Iterable[Mapping[str, Any]]) -> Dict[date, float]:
    """Each day with at least two readings, and what it spent."""
    return {day: _drops(balances) for day, balances in _by_day(samples).items()
            if len(balances) >= 2}


def baseline_days(samples: Iterable[Mapping[str, Any]], today: date,
                  *, days: int = BASELINE_DAYS) -> List[Tuple[date, float]]:
    """The last ``days`` complete days (before ``today``) with at least two
    readings, newest first — however far back that reaches."""
    measured = measured_days(samples)
    complete = sorted((day for day in measured if day < today), reverse=True)[:days]
    return [(day, measured[day]) for day in complete]


def normal_day(samples: Iterable[Mapping[str, Any]], today: date, *,
               days: int = BASELINE_DAYS,
               min_days: int = MIN_BASELINE_DAYS) -> Optional[float]:
    """The median spending of the baseline days, or None with fewer than
    ``min_days`` of them (no normal day yet)."""
    recent = baseline_days(samples, today, days=days)
    if len(recent) < min_days:
        return None
    return float(statistics.median(spent for _, spent in recent))


def alert_threshold(normal: float, multiple: float, floor: float) -> float:
    return max(float(multiple) * float(normal), float(floor))


def should_alert(*, spent_today: float, normal: Optional[float], multiple: float,
                 floor: float, alerted_today: bool) -> bool:
    if alerted_today or normal is None:
        return False
    if spent_today <= 0:
        # Nothing spent is nothing to say, whatever a zero floor allows.
        return False
    return spent_today >= alert_threshold(normal, multiple, floor)


def alerted_on(alerts: Iterable[Mapping[str, Any]], day: date, kind: str = "spike") -> bool:
    """Was the owner already told ``kind`` on ``day``? A row without a kind
    is the spike alert (the rows written before the low-balance warning)."""
    wanted = day.isoformat()
    return any(str(row.get("day") or "") == wanted
               and str(row.get("kind") or "spike") == kind for row in alerts)


# ── the low-balance warning (the owner, 2026-10-05) ───────────────────────

LOW = "low_balance"
VERY_LOW = "very_low_balance"
PACE_WINDOW_S = 24 * 3600.0


def low_balance_kind(balance: float, line: float) -> Optional[str]:
    """Which warning a balance calls for, or None: under the line is "low",
    under a quarter of it "very low"; a line of 0 is the warning off."""
    if line <= 0 or balance >= line:
        return None
    return VERY_LOW if balance < line / 4.0 else LOW


def pace_per_hour(samples: Iterable[Mapping[str, Any]], now: float) -> Optional[float]:
    """Spending per hour over the last day's readings (drops only — a top-up
    is not spending), or None with under an hour of readings to go on."""
    window = [(ts, bal) for ts, bal in _points(samples) if now - PACE_WINDOW_S <= ts <= now]
    if len(window) < 2:
        return None
    span_h = (window[-1][0] - window[0][0]) / 3600.0
    if span_h < 1.0:
        return None
    spent = _drops([bal for _, bal in window])
    return spent / span_h if spent > 0 else None


def _time_left(balance: float, per_hour: Optional[float]) -> str:
    if not per_hour:
        return ""
    hours = balance / per_hour
    if hours < 48:
        return f", about {max(1, round(hours))} hours at the last day's pace"
    return f", about {round(hours / 24)} days at the last day's pace"


def low_balance_text(*, kind: str, balance: float, line: float, currency: str,
                     per_hour: Optional[float]) -> str:
    """Marked as not hers in its first words, like the spike alert."""
    left = _time_left(balance, per_hour)
    if kind == VERY_LOW:
        head = f"her AI account balance is down to {money(balance, currency)}{left}"
    else:
        head = (f"her AI account balance is {money(balance, currency)}, under your "
                f"{money(line, currency)} warning line{left}")
    return (f"Balance check (automatic, not PaLLi): {head}. When it reaches "
            f"{money(0, currency)} she can't reply until it's topped up. Nothing was stopped.")


# ── the words ─────────────────────────────────────────────────────────────

def money(amount: float, currency: str = "USD") -> str:
    code = str(currency or "").strip().upper()
    if code in ("", "USD"):
        return f"${float(amount):,.2f}"
    return f"{float(amount):,.2f} {code}"


def _times(ratio: float) -> str:
    if ratio >= 10:
        return f"{ratio:.0f}"
    text = f"{ratio:.1f}"
    return text[:-2] if text.endswith(".0") else text


def alert_text(*, spent: float, normal: float, balance: float, currency: str) -> str:
    """The notice the owner reads. Marked as not hers in its first words: it
    arrives in her DM thread, from her account, and must not read as her."""
    if normal >= 0.005:
        compared = f"about {_times(spent / normal)}× a normal day ({money(normal, currency)})"
    else:
        # "About 300× a normal day ($0.00)" says less than this does.
        compared = f"and a normal day costs next to nothing ({money(normal, currency)})"
    return (
        "Spending check (automatic, not PaLLi): today's AI spending is "
        f"{money(spent, currency)} so far, {compared}. "
        f"Balance {money(balance, currency)}. Nothing was stopped."
    )


# ── the hourly check ──────────────────────────────────────────────────────

def _settings_knobs() -> Any:
    try:
        from core.settings_boot import settings_service

        knobs = getattr(settings_service.snapshot(), "spending", None)
        if knobs is not None:
            return knobs
    except Exception:  # noqa: BLE001 — unbooted settings mean the defaults
        pass
    return SpendingConfig()


class SpendingWatch:
    """Read → record → compare → tell, with every collaborator injectable.
    ``tick`` is the testable unit; the loop only schedules it. Nothing
    raises out of a tick into her, and nothing here can stop her."""

    def __init__(self, *, probe: Optional[Callable[[str], Mapping[str, Any]]] = None,
                 deliver: Optional[Callable[[str], Any]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 provider: Optional[str] = None,
                 knobs: Optional[Callable[[], Any]] = None) -> None:
        self._probe = probe
        self._deliver = deliver
        self._clock = clock or time.time
        self._provider = provider
        self._knobs = knobs
        self._alerted_day: Optional[date] = None
        self._warned: Dict[str, date] = {}
        self._task: Optional[asyncio.Task] = None
        self.is_running = False

    # ── knobs (read live; the owner's yaml is the source) ────────────────
    def knobs(self) -> Any:
        if self._knobs is not None:
            return self._knobs()
        return _settings_knobs()

    def provider_name(self) -> str:
        if self._provider is not None:
            return str(self._provider)
        try:
            from core.config import config

            if not getattr(config, "brain_ready", True):
                return ""          # setup mode: no brain, no account to read
            return str(getattr(config, "LLM_PROVIDER", "") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _read_balance(self, provider: str) -> Mapping[str, Any]:
        """On a worker thread: the probe runs its own asyncio.run and a
        blocking HTTP read, neither of which may touch her event loop."""
        probe = self._probe
        if probe is None:
            # Looked up per call, so the boot's probe is the one read.
            from core import launcher_support

            probe = launcher_support.probe_llm_balance
        return probe(provider)

    async def _send(self, text: str) -> Tuple[str, str]:
        """(door, error). Never raises."""
        try:
            if self._deliver is not None:
                door = await self._deliver(text)
            else:
                from core import owner_notice

                door = await owner_notice.send_owner_notice(text)
            return str(door or ""), ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — logged in the alert's line
            return "", f"{type(exc).__name__}: {exc}"

    async def tick(self) -> Optional[Dict[str, Any]]:
        """One hour's check. Returns the alert's record when it alerted."""
        knobs = self.knobs()
        if not bool(getattr(knobs, "alert_enabled", True)):
            return None
        provider = self.provider_name()
        if not provider:
            return None
        reading = await asyncio.to_thread(self._read_balance, provider)
        if not isinstance(reading, Mapping) or reading.get("skipped"):
            return None
        if not reading.get("known"):
            if reading.get("supported"):
                logger.info("spending check: the %s balance could not be read this hour", provider)
            return None
        now = float(self._clock())
        currency = str(reading.get("currency") or "").strip().upper()
        balance = float(reading.get("total_balance") or 0.0)
        await asyncio.to_thread(append_line, samples_path(), {
            "ts": now,
            "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "provider": provider,
            "currency": currency,
            "balance": balance,
        })
        return await self._check(now=now, provider=provider, currency=currency,
                                 balance=balance, knobs=knobs)

    async def _check(self, *, now: float, provider: str, currency: str,
                     balance: float, knobs: Any) -> Optional[Dict[str, Any]]:
        await self._low_balance(now=now, provider=provider, currency=currency,
                                balance=balance, knobs=knobs)
        return await self._spike(now=now, provider=provider, currency=currency,
                                 balance=balance, knobs=knobs)

    async def _low_balance(self, *, now: float, provider: str, currency: str,
                           balance: float, knobs: Any) -> Optional[Dict[str, Any]]:
        """The owner, 2026-10-05: once a day under the line, and once more
        that day under a quarter of it. Never blocks the spike alert."""
        line = float(getattr(knobs, "low_balance_usd", 2.0))
        kind = low_balance_kind(balance, line)
        if kind is None:
            return None
        today = local_day(now)
        if self._warned.get(kind) == today:
            return None
        samples, alerts = await asyncio.to_thread(
            lambda: (read_lines(samples_path()), read_lines(alerts_path())))
        if alerted_on(alerts, today, kind):
            self._warned[kind] = today          # an earlier process told the owner
            return None
        self._warned[kind] = today
        if kind == VERY_LOW:
            self._warned[LOW] = today          # one warning, never two in a tick
        mine = [row for row in samples if row.get("provider") == provider
                and str(row.get("currency") or "").strip().upper() == currency]
        per_hour = pace_per_hour(mine, now)
        text = low_balance_text(kind=kind, balance=balance, line=line,
                                currency=currency, per_hour=per_hour)
        door, error = await self._send(text)
        outcome = (f"told the owner on {door}" if door else
                   "no door could take it; this line and data/spending/alerts.jsonl are the warning"
                   + (f" ({error})" if error else ""))
        essential_logger.warning("%s [%s; %s]", text, provider, outcome)
        record = {
            "ts": now,
            "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "day": today.isoformat(),
            "kind": kind,
            "provider": provider,
            "currency": currency,
            "balance": balance,
            "line": line,
            "per_hour": round(per_hour, 4) if per_hour else None,
            "delivered": door,
            "text": text,
        }
        await asyncio.to_thread(append_line, alerts_path(), record)
        return record

    async def _spike(self, *, now: float, provider: str, currency: str,
                     balance: float, knobs: Any) -> Optional[Dict[str, Any]]:
        today = local_day(now)
        if self._alerted_day == today:
            return None
        samples, alerts = await asyncio.to_thread(
            lambda: (read_lines(samples_path()), read_lines(alerts_path())))
        if alerted_on(alerts, today):
            self._alerted_day = today          # an earlier process told the owner
            return None
        # One account's readings: another brain's balance is not her normal.
        mine = [row for row in samples if row.get("provider") == provider
                and str(row.get("currency") or "").strip().upper() == currency]
        spent = daily_spend(mine, today)
        normal = normal_day(mine, today)
        multiple = float(getattr(knobs, "alert_multiple", 2.0))
        floor = float(getattr(knobs, "alert_floor_usd", 1.0))
        if not should_alert(spent_today=spent, normal=normal, multiple=multiple,
                            floor=floor, alerted_today=False):
            return None

        # Marked BEFORE the send: a slow door must not let the next hour in.
        # And once a day even when no door took it — the log line and the
        # file are the alert then, and an hourly repeat would only be noise.
        self._alerted_day = today
        text = alert_text(spent=spent, normal=normal, balance=balance, currency=currency)
        door, error = await self._send(text)
        if door:
            outcome = f"told the owner on {door}"
        else:
            outcome = "no door could take it; this line and data/spending/alerts.jsonl are the alert"
            if error:
                outcome += f" ({error})"
        essential_logger.warning("%s [%s; %s]", text, provider, outcome)
        record = {
            "ts": now,
            "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "day": today.isoformat(),
            "provider": provider,
            "currency": currency,
            "spent": round(spent, 4),
            "normal": round(normal, 4),
            "multiple": multiple,
            "floor": floor,
            "threshold": round(alert_threshold(normal, multiple, floor), 4),
            "balance": balance,
            "delivered": door,
            "text": text,
        }
        await asyncio.to_thread(append_line, alerts_path(), record)
        return record

    # ── lifecycle (the engines' shape) ───────────────────────────────────
    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop(), name="spending_watch")
        essential_logger.info("Spending check started (the balance is read every hour)")

    async def stop(self) -> None:
        self.is_running = False
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        essential_logger.info("Spending check stopped")

    async def _loop(self) -> None:
        await asyncio.sleep(BOOT_DELAY_S)
        while self.is_running:
            try:
                await self.tick()
                await asyncio.sleep(SAMPLE_EVERY_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop must outlive any hour
                logger.warning("spending check: this hour's check failed (%s); trying again later", exc)
                await asyncio.sleep(ERROR_BACKOFF_S)


spending_watch = SpendingWatch()


# ── what palli_ctl spending prints ────────────────────────────────────────

def report_lines(*, now: Optional[float] = None, provider: Optional[str] = None,
                 knobs: Any = None) -> List[str]:
    """Today so far, the normal day, the threshold, the balance and the last
    alert — read from the two files, never from the provider. ``provider``
    omitted: the account of the newest reading."""
    now = time.time() if now is None else float(now)
    knobs = knobs if knobs is not None else _settings_knobs()
    samples = read_lines(samples_path())
    points = sorted(
        (row for row in samples if isinstance(row.get("ts"), (int, float))),
        key=lambda row: float(row["ts"]),
    )
    if provider is not None:
        points = [row for row in points if row.get("provider") == provider]
    if not points:
        return [
            "No balance readings yet. Her process reads the brain's balance every hour "
            "while spending.alert_enabled is on and the provider has a balance to read "
            f"({samples_path()})."
        ]
    latest = points[-1]
    provider = str(latest.get("provider") or "")
    currency = str(latest.get("currency") or "").strip().upper()
    mine = [row for row in points if row.get("provider") == provider
            and str(row.get("currency") or "").strip().upper() == currency]
    today = local_day(now)
    spent = daily_spend(mine, today)
    readings_today = sum(1 for row in mine if local_day(float(row["ts"])) == today)
    recent = baseline_days(mine, today)
    normal = normal_day(mine, today)
    multiple = float(getattr(knobs, "alert_multiple", 2.0))
    floor = float(getattr(knobs, "alert_floor_usd", 1.0))

    lines = [f"Spending check ({provider}, one balance reading an hour; it never stops her)"]
    if not bool(getattr(knobs, "alert_enabled", True)):
        lines.append("It is switched off (spending.alert_enabled: false): no readings, no alerts.")
    lines.append(f"Today so far: {money(spent, currency)} "
                 f"({readings_today} reading{'s' if readings_today != 1 else ''} today)")
    if normal is None:
        lines.append(
            f"A normal day: not known yet — it needs {MIN_BASELINE_DAYS} complete days "
            f"with readings ({len(recent)} so far); no alert until then")
    else:
        lines.append(f"A normal day: {money(normal, currency)} "
                     f"(the median of the last {len(recent)} complete days with readings)")
        lines.append(f"An alert comes at: {money(alert_threshold(normal, multiple, floor), currency)} "
                     f"in a day ({multiple:g}× normal, never under {money(floor, currency)})")
    lines.append(f"Balance: {money(float(latest.get('balance') or 0.0), currency)} "
                 f"(read {latest.get('at') or datetime.fromtimestamp(float(latest['ts'])).isoformat(timespec='seconds')})")

    alerts = [row for row in read_lines(alerts_path()) if isinstance(row.get("ts"), (int, float))]
    if not alerts:
        lines.append("Last alert: none")
    else:
        last = max(alerts, key=lambda row: float(row["ts"]))
        when = datetime.fromtimestamp(float(last["ts"])).strftime("%H:%M")
        cur = str(last.get("currency") or currency)
        door = str(last.get("delivered") or "")
        told = f"told the owner on {door}" if door else "no door could take it (logged only)"
        lines.append(
            f"Last alert: {last.get('day')} {when} — {money(float(last.get('spent') or 0.0), cur)} "
            f"against a normal day of {money(float(last.get('normal') or 0.0), cur)}; {told}")
    return lines
