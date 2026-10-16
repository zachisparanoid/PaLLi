"""The spending check (owner decision T2b-6, 2026-10-04).

The guest queue stays unlimited, "and the spending alert is built now: a check
that tells the owner when a day's spending jumps well above normal. It never
stops her." So: the account balance is read every hour (the same probe the
boot uses), a day's spending is the sum of the drops between its readings (a
top-up is not spending), normal is the median of the last 14 complete days
with readings, and a day at or over max(multiple x normal, floor) tells the
owner once — a plain notice that says it is not her.

Nothing here reaches the network or a live file: the probe, the clock and the
delivery are fakes, and every file lives under tmp_path (PALLI_SPENDING_DIR).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from core import spending
from core.settings_schema import PalliSettings, SpendingConfig

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 4)


def _ts(day: date, hour: int, minute: int = 0) -> float:
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def _reading(day: date, hour: int, balance: float, *, provider: str = "deepseek",
             currency: str = "USD") -> dict:
    return {"ts": _ts(day, hour), "provider": provider, "currency": currency,
            "balance": balance}


def _day(day: date, spent: float, *, start: float = 100.0, **kw) -> list[dict]:
    """A day with two readings, `spent` apart."""
    return [_reading(day, 1, start, **kw), _reading(day, 23, start - spent, **kw)]


def _back(n: int) -> date:
    return TODAY - timedelta(days=n)


# ── a day's spending ──────────────────────────────────────────────────────

def test_a_days_spending_is_the_sum_of_the_drops_between_its_readings():
    rows = [_reading(TODAY, 1, 10.0), _reading(TODAY, 2, 9.5), _reading(TODAY, 3, 9.25)]
    assert spending.daily_spend(rows, TODAY) == pytest.approx(0.75)


def test_a_top_up_in_the_middle_is_not_spending_and_hides_none_of_it():
    rows = [
        _reading(TODAY, 1, 10.0),
        _reading(TODAY, 2, 9.0),    # spent 1.00
        _reading(TODAY, 3, 29.0),   # topped up 20 — ignored
        _reading(TODAY, 4, 28.5),   # spent 0.50
    ]
    assert spending.daily_spend(rows, TODAY) == pytest.approx(1.5)


def test_only_the_readings_of_that_local_day_count():
    """The drop from 23:00 to 00:00 belongs to neither day: a pair across
    midnight (or across days she was off) cannot say which day it was spent."""
    yesterday = _back(1)
    rows = [
        _reading(yesterday, 22, 20.0),
        _reading(yesterday, 23, 15.0),
        _reading(TODAY, 0, 12.0),
        _reading(TODAY, 5, 11.0),
    ]
    assert spending.daily_spend(rows, TODAY) == pytest.approx(1.0)
    assert spending.daily_spend(rows, yesterday) == pytest.approx(5.0)


def test_readings_out_of_order_are_put_in_order_first():
    rows = [_reading(TODAY, 3, 9.0), _reading(TODAY, 1, 10.0), _reading(TODAY, 2, 9.5)]
    assert spending.daily_spend(rows, TODAY) == pytest.approx(1.0)


def test_a_day_with_one_reading_has_nothing_to_measure():
    assert spending.daily_spend([_reading(TODAY, 9, 10.0)], TODAY) == 0.0
    assert spending.daily_spend([], TODAY) == 0.0


# ── a normal day ──────────────────────────────────────────────────────────

def test_normal_is_the_median_of_the_complete_days_before_today():
    rows: list[dict] = []
    for n, spent in enumerate([0.5, 0.3, 0.9, 0.4, 10.0], start=1):
        rows += _day(_back(n), spent)
    rows += _day(TODAY, 50.0)          # today never counts toward its own normal
    assert spending.normal_day(rows, TODAY) == pytest.approx(0.5)


def test_fewer_than_three_measured_days_is_no_baseline_yet():
    rows = _day(_back(1), 0.5) + _day(_back(2), 0.4)
    rows.append(_reading(_back(3), 12, 50.0))    # one reading: not a measured day
    assert spending.normal_day(rows, TODAY) is None

    rows += _day(_back(4), 0.6)
    assert spending.normal_day(rows, TODAY) == pytest.approx(0.5)


def test_only_the_last_fourteen_days_with_readings_count():
    """Fourteen days at $1 — reaching back past a week she was off — and
    fifteen older days at $100. Counted together the median would be $100."""
    rows: list[dict] = []
    recent = [n for n in range(1, 8)] + [n for n in range(15, 22)]   # a gap: days 8-14
    for n in recent:
        rows += _day(_back(n), 1.0)
    for n in range(40, 55):
        rows += _day(_back(n), 100.0)
    assert spending.normal_day(rows, TODAY) == pytest.approx(1.0)


# ── the threshold, and once a day ─────────────────────────────────────────

def test_the_threshold_is_the_multiple_or_the_floor_whichever_is_higher():
    assert spending.alert_threshold(0.3, 2.0, 1.0) == pytest.approx(1.0)    # the floor
    assert spending.alert_threshold(2.0, 2.0, 1.0) == pytest.approx(4.0)    # the multiple
    assert spending.alert_threshold(0.0, 2.0, 1.0) == pytest.approx(1.0)


def test_an_alert_at_or_over_the_threshold_and_not_under_it():
    kw = dict(normal=1.0, multiple=2.0, floor=1.0, alerted_today=False)
    assert spending.should_alert(spent_today=2.0, **kw) is True
    assert spending.should_alert(spent_today=7.5, **kw) is True
    assert spending.should_alert(spent_today=1.99, **kw) is False


def test_no_baseline_means_no_alert_however_much_was_spent():
    assert spending.should_alert(spent_today=500.0, normal=None, multiple=2.0,
                                 floor=1.0, alerted_today=False) is False


def test_the_floor_keeps_a_quiet_baseline_from_alerting_over_cents():
    # ten times a five-cent day is still fifty cents
    assert spending.should_alert(spent_today=0.5, normal=0.05, multiple=2.0,
                                 floor=1.0, alerted_today=False) is False


def test_nothing_spent_is_never_an_alert():
    assert spending.should_alert(spent_today=0.0, normal=0.0, multiple=2.0,
                                 floor=0.0, alerted_today=False) is False


def test_at_most_one_alert_a_day():
    assert spending.should_alert(spent_today=50.0, normal=1.0, multiple=2.0,
                                 floor=1.0, alerted_today=True) is False
    alerts = [{"day": "2026-10-03"}, {"day": "2026-10-04"}]
    assert spending.alerted_on(alerts, TODAY) is True
    assert spending.alerted_on(alerts[:1], TODAY) is False


# ── the words ─────────────────────────────────────────────────────────────

def test_the_alert_says_it_is_not_her_and_that_nothing_was_stopped():
    text = spending.alert_text(spent=2.5, normal=0.5, balance=12.34, currency="USD")
    assert text.startswith("Spending check (automatic, not PaLLi):")
    assert "today's AI spending is $2.50 so far" in text
    assert "about 5× a normal day ($0.50)" in text
    assert "Balance $12.34." in text
    assert text.endswith("Nothing was stopped.")


def test_a_normal_day_of_nothing_still_reads_plainly():
    text = spending.alert_text(spent=1.2, normal=0.0, balance=3.0, currency="USD")
    assert "×" not in text
    assert "$1.20" in text and "$0.00" in text and "Nothing was stopped." in text


def test_another_currency_is_named_rather_than_dollared():
    text = spending.alert_text(spent=2.5, normal=1.0, balance=40.0, currency="CNY")
    assert "$" not in text
    assert "2.50 CNY" in text and "40.00 CNY" in text and "about 2.5×" in text


# ── where it lives ────────────────────────────────────────────────────────

def test_the_files_live_under_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_SPENDING_DIR", str(tmp_path / "sp"))
    assert spending.samples_path() == tmp_path / "sp" / "balance.jsonl"
    assert spending.alerts_path() == tmp_path / "sp" / "alerts.jsonl"


def test_the_suite_never_touches_the_live_folder():
    assert spending.spending_dir().resolve() != (REPO / "data" / "spending").resolve()


def test_the_live_folder_is_gitignored():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/spending/" in lines


def test_a_broken_line_is_skipped_and_the_rest_read(tmp_path):
    path = tmp_path / "balance.jsonl"
    assert spending.append_line(path, {"ts": 1.0, "balance": 5.0}) is True
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write("[1, 2]\n")
    assert spending.append_line(path, {"ts": 2.0, "balance": 4.0}) is True
    assert [row["ts"] for row in spending.read_lines(path)] == [1.0, 2.0]
    assert spending.read_lines(tmp_path / "missing.jsonl") == []


# ── the settings ──────────────────────────────────────────────────────────

def test_the_spending_check_ships_on_with_the_owners_defaults():
    cfg = SpendingConfig()
    assert cfg.alert_enabled is True
    assert cfg.alert_multiple == 2.0
    assert cfg.alert_floor_usd == 1.0
    assert PalliSettings.model_fields["spending"].default_factory is SpendingConfig


def test_a_multiple_under_one_is_refused():
    with pytest.raises(ValidationError):
        SpendingConfig(alert_multiple=0.5)
    with pytest.raises(ValidationError):
        SpendingConfig(alert_floor_usd=-1.0)


def test_the_knobs_are_read_from_the_live_settings(monkeypatch):
    from types import SimpleNamespace

    from core import settings_boot

    live = SimpleNamespace(spending=SpendingConfig(alert_multiple=3.0, alert_floor_usd=5.0))
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: live)
    knobs = spending.SpendingWatch().knobs()
    assert knobs.alert_multiple == 3.0 and knobs.alert_floor_usd == 5.0


def test_unbooted_settings_mean_the_schema_defaults(monkeypatch):
    from core import settings_boot

    def _broken():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _broken)
    assert spending.SpendingWatch().knobs() == SpendingConfig()


# ── the hourly check, with fakes ──────────────────────────────────────────

class FakeProbe:
    """Stands in for core.launcher_support.probe_llm_balance (same shape)."""

    def __init__(self, *balances, supported: bool = True, skipped: bool = False,
                 currency: str = "USD") -> None:
        self.balances = list(balances)
        self.supported = supported
        self.skipped = skipped
        self.currency = currency
        self.calls: list[str] = []
        self.threads: list[int] = []

    def __call__(self, provider: str) -> dict:
        self.calls.append(provider)
        self.threads.append(threading.get_ident())
        blank = {"skipped": self.skipped, "supported": self.supported, "known": False,
                 "low": False, "threshold": 2.0, "currency": "", "total_balance": 0.0,
                 "available": False}
        if self.skipped or not self.supported:
            return blank
        balance = self.balances.pop(0)
        if balance is None:                       # the read failed
            return blank
        return {**blank, "known": True, "currency": self.currency,
                "total_balance": float(balance), "available": True}


class FakeDeliver:
    def __init__(self, result: str = "discord") -> None:
        self.result = result
        self.sent: list[str] = []

    async def __call__(self, text: str) -> str:
        self.sent.append(text)
        return self.result


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_SPENDING_DIR", str(tmp_path / "spending"))
    return tmp_path / "spending"


def _seed(rows: list[dict]) -> None:
    for row in rows:
        spending.append_line(spending.samples_path(), row)


def _baseline(days: int = 5, spent: float = 0.5, **kw) -> list[dict]:
    rows: list[dict] = []
    for n in range(1, days + 1):
        rows += _day(_back(n), spent, **kw)
    return rows


def _watch(probe, deliver, clock, **knobs) -> spending.SpendingWatch:
    return spending.SpendingWatch(probe=probe, deliver=deliver, clock=clock,
                                  provider="deepseek",
                                  knobs=lambda: SpendingConfig(**knobs))


def _alerts() -> list[dict]:
    return spending.read_lines(spending.alerts_path())


@pytest.mark.asyncio
async def test_each_tick_appends_one_reading(store):
    watch = _watch(FakeProbe(12.34), FakeDeliver(), Clock(_ts(TODAY, 9)))

    assert await watch.tick() is None

    rows = spending.read_lines(spending.samples_path())
    assert len(rows) == 1
    assert rows[0]["balance"] == 12.34
    assert rows[0]["provider"] == "deepseek" and rows[0]["currency"] == "USD"
    assert rows[0]["ts"] == _ts(TODAY, 9)


@pytest.mark.asyncio
async def test_a_jump_well_above_normal_tells_the_owner_once_that_day(store):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])
    deliver = FakeDeliver("discord")
    clock = Clock(_ts(TODAY, 10))
    watch = _watch(FakeProbe(98.5, 97.0), deliver, clock)

    alert = await watch.tick()                      # $1.50 against a $0.50 day

    assert alert is not None
    assert len(deliver.sent) == 1
    assert "not PaLLi" in deliver.sent[0] and "Nothing was stopped." in deliver.sent[0]
    assert "$1.50" in deliver.sent[0] and "about 3× a normal day ($0.50)" in deliver.sent[0]
    rows = _alerts()
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-10-04" and rows[0]["delivered"] == "discord"
    assert rows[0]["spent"] == pytest.approx(1.5) and rows[0]["normal"] == pytest.approx(0.5)

    clock.t = _ts(TODAY, 11)                        # $3.00 now — still today
    assert await watch.tick() is None
    assert len(deliver.sent) == 1 and len(_alerts()) == 1


@pytest.mark.asyncio
async def test_a_restart_does_not_alert_twice_the_same_day(store):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])
    first = FakeDeliver()
    await _watch(FakeProbe(98.0), first, Clock(_ts(TODAY, 10))).tick()
    assert len(first.sent) == 1

    second = FakeDeliver()                          # a new process, same files
    assert await _watch(FakeProbe(96.0), second, Clock(_ts(TODAY, 12))).tick() is None
    assert second.sent == []


@pytest.mark.asyncio
async def test_an_ordinary_day_says_nothing(store):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])
    deliver = FakeDeliver()
    assert await _watch(FakeProbe(99.4), deliver, Clock(_ts(TODAY, 10))).tick() is None
    assert deliver.sent == [] and _alerts() == []


@pytest.mark.asyncio
async def test_no_baseline_yet_means_readings_but_no_alert(store):
    _seed(_baseline(days=2) + [_reading(TODAY, 1, 100.0)])
    deliver = FakeDeliver()
    assert await _watch(FakeProbe(50.0), deliver, Clock(_ts(TODAY, 10))).tick() is None
    assert deliver.sent == []
    assert len(spending.read_lines(spending.samples_path())) == 6


@pytest.mark.asyncio
async def test_readings_from_another_brain_are_not_her_normal(store):
    _seed(_baseline(provider="openai") + [_reading(TODAY, 1, 100.0)])
    deliver = FakeDeliver()
    assert await _watch(FakeProbe(50.0), deliver, Clock(_ts(TODAY, 10))).tick() is None
    assert deliver.sent == []


@pytest.mark.asyncio
async def test_an_alert_no_door_took_is_still_logged_and_recorded(store, caplog):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])
    deliver = FakeDeliver("")                        # Discord and Telegram both down
    clock = Clock(_ts(TODAY, 10))
    watch = _watch(FakeProbe(97.0, 95.0), deliver, clock)

    with caplog.at_level(logging.WARNING):
        alert = await watch.tick()

    assert alert is not None and alert["delivered"] == ""
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not PaLLi" in warnings[0].getMessage()
    assert "no door" in warnings[0].getMessage()
    assert _alerts()[0]["delivered"] == ""

    clock.t = _ts(TODAY, 11)
    assert await watch.tick() is None and len(deliver.sent) == 1


@pytest.mark.asyncio
async def test_a_delivery_that_blows_up_never_breaks_the_check(store):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])

    async def _boom(text: str) -> str:
        raise RuntimeError("the door fell over")

    alert = await _watch(FakeProbe(97.0), _boom, Clock(_ts(TODAY, 10))).tick()
    assert alert is not None and alert["delivered"] == ""
    assert len(_alerts()) == 1


@pytest.mark.asyncio
async def test_switched_off_it_reads_nothing(store):
    probe = FakeProbe(10.0)
    watch = _watch(probe, FakeDeliver(), Clock(_ts(TODAY, 10)), alert_enabled=False)
    assert await watch.tick() is None
    assert probe.calls == []
    assert not spending.samples_path().exists()


@pytest.mark.asyncio
async def test_a_provider_with_no_balance_is_never_measured(store):
    probe = FakeProbe(supported=False)
    assert await _watch(probe, FakeDeliver(), Clock(_ts(TODAY, 10))).tick() is None
    assert not spending.samples_path().exists()


@pytest.mark.asyncio
async def test_a_skipped_or_failed_read_writes_nothing(store):
    assert await _watch(FakeProbe(skipped=True), FakeDeliver(),
                        Clock(_ts(TODAY, 10))).tick() is None
    assert await _watch(FakeProbe(None), FakeDeliver(),
                        Clock(_ts(TODAY, 10))).tick() is None
    assert not spending.samples_path().exists()


@pytest.mark.asyncio
async def test_the_probe_runs_off_the_event_loop(store):
    """probe_llm_balance runs its own asyncio.run — on the loop's thread it
    could not run at all."""
    probe = FakeProbe(10.0)
    await _watch(probe, FakeDeliver(), Clock(_ts(TODAY, 10))).tick()
    assert probe.threads and probe.threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_by_default_it_reads_the_boot_balance_probe_and_tells_the_owner_notice_seam(
        store, monkeypatch):
    from core import launcher_support, owner_notice

    _seed(_baseline() + [_reading(TODAY, 1, 100.0)])
    probe = FakeProbe(97.0)
    monkeypatch.setattr(launcher_support, "probe_llm_balance", probe)
    told: list[str] = []
    sent = AsyncMock(return_value="telegram")
    monkeypatch.setattr(owner_notice, "send_owner_notice",
                        lambda text: told.append(text) or sent(text))

    watch = spending.SpendingWatch(clock=Clock(_ts(TODAY, 10)), provider="deepseek",
                                   knobs=lambda: SpendingConfig())
    alert = await watch.tick()

    assert probe.calls == ["deepseek"]
    assert alert is not None and alert["delivered"] == "telegram"
    assert len(told) == 1 and "not PaLLi" in told[0]


@pytest.mark.asyncio
async def test_the_loop_outlives_a_tick_that_raises(store, monkeypatch):
    monkeypatch.setattr(spending, "BOOT_DELAY_S", 0.0)
    monkeypatch.setattr(spending, "SAMPLE_EVERY_S", 0.01)
    monkeypatch.setattr(spending, "ERROR_BACKOFF_S", 0.01)
    watch = spending.SpendingWatch()
    calls: list[int] = []

    async def _tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one bad hour")
        return None

    watch.tick = _tick
    await watch.start()
    await watch.start()                     # idempotent: one loop, not two
    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(calls) >= 3:
            break
    await watch.stop()
    assert len(calls) >= 3
    assert watch.is_running is False


# ── the boot wiring (core/main.py, beside the other engines) ──────────────

@pytest.mark.asyncio
async def test_the_app_starts_the_spending_check() -> None:
    from core import main as app_main

    with patch.object(spending.spending_watch, "start", AsyncMock()) as start:
        await app_main._start_spending_watch()
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_spending_check_that_will_not_start_never_stops_her_starting() -> None:
    from core import main as app_main

    with patch.object(spending.spending_watch, "start",
                      AsyncMock(side_effect=RuntimeError("boom"))):
        await app_main._start_spending_watch()      # must not raise


@pytest.mark.asyncio
async def test_the_app_stops_the_spending_check() -> None:
    from core import main as app_main

    with patch.object(spending.spending_watch, "stop", AsyncMock()) as stop:
        await app_main._stop_spending_watch()
    stop.assert_awaited_once()


# ── what palli_ctl spending reads ─────────────────────────────────────────

def test_the_summary_names_today_normal_and_the_last_alert(store):
    _seed(_baseline() + [_reading(TODAY, 1, 100.0), _reading(TODAY, 9, 99.2)])
    spending.append_line(spending.alerts_path(), {
        "ts": _ts(_back(2), 15), "day": _back(2).isoformat(), "spent": 2.1,
        "normal": 0.4, "currency": "USD", "delivered": "discord",
        "text": "Spending check (automatic, not PaLLi): ...",
    })

    lines = spending.report_lines(now=_ts(TODAY, 9, 30), provider="deepseek",
                                  knobs=SpendingConfig())
    text = "\n".join(lines)
    assert "Today so far: $0.80" in text
    assert "A normal day: $0.50" in text
    assert "Balance: $99.20" in text
    assert "Last alert: " + _back(2).isoformat() in text and "discord" in text


def test_the_summary_says_so_when_there_is_nothing_yet(store):
    lines = spending.report_lines(now=_ts(TODAY, 9), provider="deepseek",
                                  knobs=SpendingConfig())
    assert lines and "no balance readings yet" in lines[0].lower()


def _ctl():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "palli_ctl_spending_test_module", REPO / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_palli_ctl_spending_prints_the_summary(store, monkeypatch):
    ctl = _ctl()
    monkeypatch.setattr(spending, "report_lines", lambda **_kw: ["line one", "line two"])
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["spending"])
    assert ctl.run_spending_command(args, out=lines.append) == 0
    assert lines == ["line one", "line two"]


def test_palli_ctl_is_still_crlf_throughout():
    raw = (REPO / "scripts" / "palli_ctl.py").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


# ── the low-balance warning (the owner, 2026-10-05: "yes") ────────────────
# 10-05 01:22: the balance had fallen to $0.67 with no warning — the spike
# alert compares days, and a steady slide to zero is never a spike.

@pytest.mark.asyncio
async def test_a_balance_under_the_line_warns_the_owner_once_that_day(store):
    deliver = FakeDeliver()
    clock = Clock(_ts(TODAY, 9))
    watch = _watch(FakeProbe(1.80, 1.70), deliver, clock, low_balance_usd=2.0)

    await watch.tick()

    assert len(deliver.sent) == 1
    text = deliver.sent[0]
    assert "not PaLLi" in text and "Nothing was stopped." in text
    assert "$1.80" in text and "$2.00" in text
    rows = _alerts()
    assert rows[-1]["kind"] == "low_balance" and rows[-1]["day"] == "2026-10-04"

    clock.t = _ts(TODAY, 10)
    await watch.tick()
    assert len(deliver.sent) == 1                   # once a day


@pytest.mark.asyncio
async def test_a_second_warning_when_it_drops_under_a_quarter_of_the_line(store):
    deliver = FakeDeliver()
    clock = Clock(_ts(TODAY, 9))
    watch = _watch(FakeProbe(1.80, 0.45, 0.40), deliver, clock, low_balance_usd=2.0)

    for hour in (9, 10, 11):
        clock.t = _ts(TODAY, hour)
        await watch.tick()

    assert len(deliver.sent) == 2
    assert "$0.45" in deliver.sent[1] and "can't reply" in deliver.sent[1]
    assert [r["kind"] for r in _alerts()] == ["low_balance", "very_low_balance"]


@pytest.mark.asyncio
async def test_already_very_low_at_the_first_reading_sends_one_warning_not_two(store):
    deliver = FakeDeliver()
    await _watch(FakeProbe(0.40), deliver, Clock(_ts(TODAY, 9)), low_balance_usd=2.0).tick()
    assert len(deliver.sent) == 1
    assert [r["kind"] for r in _alerts()] == ["very_low_balance"]


@pytest.mark.asyncio
async def test_the_warning_says_how_long_at_the_last_days_pace(store):
    _seed([_reading(TODAY - timedelta(days=1), 9, 2.50)])     # $1.00 over 24 h
    deliver = FakeDeliver()
    await _watch(FakeProbe(1.50), deliver, Clock(_ts(TODAY, 9)), low_balance_usd=2.0).tick()
    assert "about 36 hours at the last day's pace" in deliver.sent[0]


@pytest.mark.asyncio
async def test_no_pace_is_claimed_without_a_days_readings(store):
    deliver = FakeDeliver()
    await _watch(FakeProbe(1.50), deliver, Clock(_ts(TODAY, 9)), low_balance_usd=2.0).tick()
    assert "pace" not in deliver.sent[0]


@pytest.mark.asyncio
async def test_a_low_balance_warning_never_blocks_the_spike_alert(store):
    rows: list[dict] = []
    for n in range(1, 6):
        rows += _day(_back(n), 0.05, start=1.95)
    _seed(rows + [_reading(TODAY, 1, 1.90)])
    deliver = FakeDeliver()

    alert = await _watch(FakeProbe(1.70), deliver, Clock(_ts(TODAY, 10)),
                         alert_floor_usd=0.1, low_balance_usd=2.0).tick()    # $0.20 against a $0.05 day

    assert alert is not None                         # the spike alert still went
    assert len(deliver.sent) == 2
    assert {r.get("kind", "spike") for r in _alerts()} == {"low_balance", "spike"}


@pytest.mark.asyncio
async def test_a_restart_does_not_warn_twice_the_same_day(store):
    first = FakeDeliver()
    await _watch(FakeProbe(1.80), first, Clock(_ts(TODAY, 9)), low_balance_usd=2.0).tick()
    second = FakeDeliver()
    await _watch(FakeProbe(1.75), second, Clock(_ts(TODAY, 11)), low_balance_usd=2.0).tick()
    assert len(first.sent) == 1 and second.sent == []


@pytest.mark.asyncio
async def test_zero_turns_the_low_balance_warning_off(store):
    deliver = FakeDeliver()
    await _watch(FakeProbe(0.10), deliver, Clock(_ts(TODAY, 9)), low_balance_usd=0).tick()
    assert deliver.sent == []


def test_the_low_balance_warning_ships_off():
    """The owner, 2026-10-05 07:04: "lets do away with the balance check" — off by
    default; a dollar amount in spending.low_balance_usd turns it back on."""
    assert SpendingConfig().low_balance_usd == 0.0


@pytest.mark.asyncio
async def test_with_the_default_a_low_balance_says_nothing(store):
    deliver = FakeDeliver()
    await _watch(FakeProbe(0.10), deliver, Clock(_ts(TODAY, 9))).tick()
    assert deliver.sent == []
