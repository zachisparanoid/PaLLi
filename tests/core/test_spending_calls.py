"""``palli_ctl spending calls`` (the owner, 10-05: "call by call"): the call
ledger read back lane by lane, the costliest calls, and the ledger's total set
beside what the balance readings say left the account in the same hours."""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

from ai import call_ledger
from core import spending, spending_calls

REPO = Path(__file__).resolve().parents[2]
DAY = date(2026, 10, 3)  # a Saturday: no peak hours, so every price is the off-peak one
MODEL = "deepseek-v4-flash"


def _at(hour: int, minute: int = 0, day: date = DAY) -> float:
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def _call(lane: str, ts: float, *, prompt: int, hit: int = 0, out: int = 0, model: str = MODEL) -> None:
    call_ledger.record_call(lane=lane, provider="deepseek", model=model, prompt=prompt, hit=hit, out=out, now=ts)


def _report(**kw) -> list[str]:
    kw.setdefault("now", _at(12, day=date(2026, 10, 4)))  # the next day: DAY is complete
    return spending_calls.report_lines(day=DAY, **kw)


def test_the_table_puts_the_costliest_lane_first_with_its_share():
    _call("reply", _at(9), prompt=60_000, hit=54_000, out=100)        # mostly cached: cheap
    _call("reflect_turn", _at(9, 1), prompt=67_000, hit=5_000, out=300)
    _call("reflect_turn", _at(10, 1), prompt=67_000, hit=5_000, out=300)
    lines = _report()
    assert lines[0].startswith(f"Her AI calls, {DAY.isoformat()}: 3 calls, $0.02 ")
    table = [line.split() for line in lines[2:4]]
    assert [row[0] for row in table] == ["reflect_turn", "reply"]
    assert table[0][1] == "2" and table[0][-1] == "94%"
    assert "Peak-price" not in "\n".join(lines)  # a Saturday has no peak


def test_the_most_expensive_calls_and_the_cache_share_are_listed():
    _call("reply", _at(9), prompt=60_000, hit=54_000, out=100)
    _call("reflect_turn", _at(9, 1), prompt=67_000, hit=5_000, out=300)
    text = "\n".join(_report(top=1))
    assert "Input tokens served from the provider's cache: 46%" in text
    top = text.split("Most expensive single calls:\n", 1)[1].splitlines()
    assert len(top) == 1 and "reflect_turn" in top[0] and "62.0k uncached" in top[0]


def test_peak_calls_are_counted_on_a_weekday():
    """Pinned in UTC, so the machine's own zone (CI runs in UTC) cannot move
    a call across the peak line; both moments are Monday locally too."""
    monday = date(2026, 10, 5)
    _call("reflect_turn", datetime(2026, 10, 5, 8, tzinfo=timezone.utc).timestamp(), prompt=1_000_000)
    _call("reflect_turn", datetime(2026, 10, 5, 12, tzinfo=timezone.utc).timestamp(), prompt=1_000_000)
    lines = spending_calls.report_lines(day=monday, now=_at(23, day=monday))
    assert "Peak-price calls: 1 of 2, $0.30 of the $0.45." in lines


def test_the_ledger_is_set_beside_the_balance_readings_for_the_same_hours():
    for hour, balance in ((8, 3.00), (9, 2.90), (10, 2.95), (11, 2.85)):  # 10:00 is a top-up
        spending.append_line(spending.samples_path(), {"ts": _at(hour), "balance": balance})
    _call("reply", _at(7, 30), prompt=1_000_000)            # before the first reading: not compared
    _call("reflect_turn", _at(8, 30), prompt=1_000_000)     # $0.15 off-peak
    _call("reflect_turn", _at(10, 30), prompt=1_000_000)
    check = [line for line in _report() if line.startswith("Checked against the account")]
    assert check == ["Checked against the account: the balance dropped $0.20 between the readings at "
                     "08:00 and 11:00; the calls recorded in those hours add up to $0.30. "
                     "(The provider's balance can lag its calls by a few minutes.)"]


def test_readings_from_before_the_ledger_began_are_left_out_of_the_check():
    """10-05, the first live run: the check compared the balance's drop from
    00:22 to 09:28 with a ledger that began at 10:07 ("$0.32 … add up to
    $0.00"). Hours the ledger did not watch are not compared."""
    for hour, balance in ((8, 3.00), (9, 2.90), (10, 2.80), (11, 2.70)):
        spending.append_line(spending.samples_path(), {"ts": _at(hour), "balance": balance})
    _call("reflect_turn", _at(9, 30), prompt=1_000_000)     # the ledger's first line
    _call("reflect_turn", _at(10, 30), prompt=1_000_000)
    check = [line for line in _report() if line.startswith("Checked against the account")]
    assert check == ["Checked against the account: the balance dropped $0.10 between the readings at "
                     "10:00 and 11:00; the calls recorded in those hours add up to $0.15. "
                     "(The provider's balance can lag its calls by a few minutes.)"]


def test_no_check_until_two_readings_follow_the_ledgers_first_call():
    for hour, balance in ((8, 3.00), (9, 2.90), (10, 2.80)):
        spending.append_line(spending.samples_path(), {"ts": _at(hour), "balance": balance})
    _call("present_moment_cycle", _at(9, 30), prompt=6_000)
    assert not any(line.startswith("Checked against the account") for line in _report())


def test_one_lane_lists_every_call_and_an_unpriced_model_says_so():
    _call("loop:workbench", _at(9), prompt=200_000, hit=2_000, out=500)
    _call("loop:workbench", _at(9, 5), prompt=1_000, model="qwen3.6-palli")
    _call("reply", _at(9, 6), prompt=1_000)
    lines = _report(lane="loop:workbench", top=0)
    assert lines[0].startswith(f"Her AI calls in lane loop:workbench, {DAY.isoformat()}: 2 calls")
    assert "1 call on a model with no price here: tokens counted, cost unknown." in lines
    every = lines[lines.index("Every call, in time order:") + 1:]
    assert len(every) == 2 and every[0].lstrip().startswith("09:00:00") and every[1].rstrip().endswith("?")


def test_a_day_with_no_calls_says_where_the_ledger_lives():
    lines = _report()
    assert lines == [f"No calls recorded for {DAY.isoformat()}. The ledger starts at the restart that "
                     f"shipped it ({call_ledger.ledger_dir()}/calls-YYYY-MM.jsonl)."]


def test_several_days_read_back_with_dates_on_each_call():
    _call("reply", _at(9, day=date(2026, 10, 2)), prompt=1_000)
    _call("reply", _at(9), prompt=1_000)
    lines = _report(days=2, every=True)
    assert lines[0].startswith("Her AI calls, 2026-10-02 to 2026-10-03: 2 calls")
    assert lines[-2].lstrip().startswith("10-02 09:00:00") and lines[-1].lstrip().startswith("10-03 09:00:00")


def _ctl():
    spec = importlib.util.spec_from_file_location(
        "palli_ctl_spending_calls_test_module", REPO / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_palli_ctl_spending_calls_passes_its_options_through(monkeypatch):
    ctl = _ctl()
    seen: dict = {}
    monkeypatch.setattr(spending_calls, "report_lines", lambda **kw: seen.update(kw) or ["the table"])
    lines: list[str] = []
    args = ctl.build_parser().parse_args(
        ["spending", "calls", "--day", "2026-10-03", "--days", "2", "--lane", "reply", "--top", "3", "--every"])
    assert ctl.run_spending_command(args, out=lines.append) == 0
    assert lines == ["the table"]
    assert seen == {"day": DAY, "days": 2, "lane": "reply", "top": 3, "every": True}


def test_palli_ctl_spending_calls_refuses_a_day_it_cannot_read():
    ctl = _ctl()
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["spending", "calls", "--day", "yesterday"])
    assert ctl.run_spending_command(args, out=lines.append) == 2
    assert lines == ["--day wants YYYY-MM-DD, not 'yesterday'"]
