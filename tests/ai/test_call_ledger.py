"""The call ledger (the owner, 10-05: "create some way of tracking what is
using up all her spending budget, call by call please"): one line per billed
call in data/spending/calls-YYYY-MM.jsonl, priced at DeepSeek's rate for the
hour of the call. The suite's PALLI_SPENDING_DIR (tests/conftest.py) keeps
every write here off the live folder."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from ai import call_ledger

# Monday 2026-10-05: 08:00 UTC is inside the 06-10 peak window, 12:00 is not.
PEAK_TS = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc).timestamp()
OFF_PEAK_TS = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc).timestamp()
MODEL = "deepseek-v4-flash"


def test_a_million_of_each_costs_the_published_price_at_peak_and_half_off_peak():
    assert call_ledger.call_cost(MODEL, hit=1_000_000, miss=1_000_000, out=1_000_000,
                                 peak=True) == pytest.approx(0.006 + 0.30 + 1.20)
    assert call_ledger.call_cost(MODEL, hit=1_000_000, miss=1_000_000, out=1_000_000,
                                 peak=False) == pytest.approx((0.006 + 0.30 + 1.20) / 2)


def test_a_model_with_no_price_has_no_cost_but_keeps_its_tokens():
    assert call_ledger.call_cost("qwen3.6-palli", hit=1, miss=1, out=1, peak=True) is None
    row = call_ledger.call_row(lane="reply", provider="local_openai", model="qwen3.6-palli",
                               prompt=500, hit=100, out=20, now=PEAK_TS)
    assert (row["hit"], row["miss"], row["out"], row["usd"], row["peak"]) == (100, 400, 20, None, False)


def test_the_row_splits_the_prompt_and_prices_by_the_hour_of_the_call():
    peak = call_ledger.call_row(lane="reflect_turn", provider="deepseek", model=MODEL,
                                prompt=67_832, hit=5_120, out=274, now=PEAK_TS)
    assert (peak["hit"], peak["miss"], peak["out"], peak["peak"]) == (5_120, 62_712, 274, True)
    assert peak["usd"] == pytest.approx((5_120 * 0.006 + 62_712 * 0.30 + 274 * 1.20) / 1e6, abs=1e-7)
    off = call_ledger.call_row(lane="reflect_turn", provider="deepseek", model=MODEL,
                               prompt=67_832, hit=5_120, out=274, now=OFF_PEAK_TS)
    assert off["peak"] is False and off["usd"] == pytest.approx(peak["usd"] / 2, abs=1e-7)


def test_a_cached_count_above_the_prompt_never_makes_a_negative_miss():
    row = call_ledger.call_row(lane="x", provider="deepseek", model=MODEL, prompt=100, hit=150,
                               out=0, now=OFF_PEAK_TS)
    assert (row["hit"], row["miss"]) == (100, 0)


def test_a_recorded_call_is_one_line_in_the_months_file_and_reads_back():
    call_ledger.record_call(lane="present_moment_cycle", provider="deepseek", model=MODEL,
                            prompt=6_073, hit=0, out=242, now=OFF_PEAK_TS)
    call_ledger.record_call(lane="reply", provider="openai_compat", model=MODEL, prompt=55_457,
                            hit=6_912, out=102, session="discord:1", now=OFF_PEAK_TS + 1)
    path = call_ledger.ledger_path(OFF_PEAK_TS)
    assert path.name == f"calls-{datetime.fromtimestamp(OFF_PEAK_TS):%Y-%m}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["lane"] for line in lines] == ["present_moment_cycle", "reply"]
    rows = call_ledger.read_calls(OFF_PEAK_TS, OFF_PEAK_TS + 2)
    assert [row["lane"] for row in rows] == ["present_moment_cycle", "reply"]
    assert rows[1]["session"] == "discord:1" and "session" not in rows[0]
    assert call_ledger.read_calls(OFF_PEAK_TS + 1, OFF_PEAK_TS + 2)[0]["lane"] == "reply"


def test_reading_a_window_across_months_reads_both_files_and_skips_junk():
    sept = datetime(2026, 9, 30, 23, 0).timestamp()
    octo = datetime(2026, 10, 1, 1, 0).timestamp()
    call_ledger.record_call(lane="a", provider="deepseek", model=MODEL, prompt=1, hit=0, out=1, now=sept)
    call_ledger.record_call(lane="b", provider="deepseek", model=MODEL, prompt=1, hit=0, out=1, now=octo)
    with call_ledger.ledger_path(octo).open("a", encoding="utf-8") as fh:
        fh.write("not json\n[1, 2]\n")
    assert [row["lane"] for row in call_ledger.read_calls(sept - 1, octo + 1)] == ["a", "b"]


def test_a_ledger_that_cannot_be_written_never_raises(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "a-file-not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("PALLI_SPENDING_DIR", str(blocker))
    with caplog.at_level(logging.WARNING, logger="ai.call_ledger"):
        call_ledger.record_call(lane="reply", provider="deepseek", model=MODEL, prompt=1, hit=0, out=1)
    assert any("could not record a reply call" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("origin, lane", [
    ("user", "reply"), ("dashboard_user", "reply"), ("guest", "reply"), ("", "reply"),
    ("workbench", "loop:workbench"), ("reminder", "loop:reminder"), ("duplex_chime", "loop:duplex_chime"),
])
def test_her_loop_files_answers_as_reply_and_the_rest_by_origin(origin, lane):
    assert call_ledger.loop_lane(origin) == lane


def test_cached_tokens_reads_deepseek_or_openai_usage_and_survives_junk():
    assert call_ledger.cached_tokens({"prompt_cache_hit_tokens": 6000}) == 6000
    assert call_ledger.cached_tokens({"prompt_tokens_details": {"cached_tokens": 512}}) == 512
    assert call_ledger.cached_tokens({"prompt_cache_hit_tokens": "n/a"}) == 0
    assert call_ledger.cached_tokens({}) == 0
