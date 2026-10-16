"""Cut the Cost, Task 1: the cache report reads the counters back per lane."""
from __future__ import annotations

import datetime as dt
import logging

from scripts.cache_report import _lines_since, _log_files, main, summarize

LOG = """2026-09-27 01:00:00,000 - INFO - [core.agent.llm_client:52] - llm_call session=discord:1 iter=1 provider=openai_compat model=m prompt_tokens=1000 completion_tokens=10 latency_ms=5 cache_hit=640 cache_miss=360
2026-09-27 01:01:00,000 - INFO - [ai.llm_client:1] - [reflect_turn] cache_hit=6000 cache_miss=300
"""


def test_summarize_groups_by_lane_and_computes_share():
    out = summarize(LOG.splitlines())
    assert out["reply"] == {"hit": 640, "miss": 360, "share": 0.64}
    assert out["reflect_turn"]["share"] > 0.95


def test_summarize_reads_the_line_the_real_formatter_writes(caplog):
    """Round trip (whole-branch review, 2026-09-27): the reply lane's line as
    ``log_llm_call`` really formats it, under the live log's header, reads
    back with its counts — so the formatter and the report cannot drift."""
    from core.agent.observability import log_llm_call

    with caplog.at_level(logging.INFO, logger="core.agent.llm_client"):
        log_llm_call(
            session_key="discord:1", iteration_n=1, provider="openai_compat", model="deepseek-chat",
            prompt_tokens=63000, completion_tokens=120, latency_ms=4000,
            cache_hit_tokens=60000, cache_miss_tokens=3000,
        )
    messages = [r.getMessage() for r in caplog.records if r.name == "core.agent.llm_client"]
    assert len(messages) == 1
    line = "2026-09-27 01:00:00,000 - INFO - [core.agent.llm_client:52] - " + messages[0]
    out = summarize([line])
    assert out["reply"]["hit"] == 60000
    assert out["reply"]["miss"] == 3000
    assert set(out) == {"reply"}


def test_summarize_adds_up_a_lane_and_ignores_other_lines():
    lines = [
        # an llm_call line from before the counters existed: not counted
        "2026-09-26 23:00:00,000 - INFO - [core.agent.llm_client:52] - llm_call session=s iter=1 "
        "provider=p model=m prompt_tokens=10 completion_tokens=1 latency_ms=5",
        "2026-09-27 01:00:00,000 - INFO - [core.agent.llm_client:52] - llm_call session=s iter=1 "
        "provider=p model=m prompt_tokens=10 completion_tokens=1 latency_ms=5 cache_hit=1 cache_miss=2",
        "2026-09-27 01:00:01,000 - INFO - [core.agent.llm_client:52] - llm_call session=s iter=2 "
        "provider=p model=m prompt_tokens=10 completion_tokens=1 latency_ms=5 cache_hit=3 cache_miss=4",
        "2026-09-27 01:00:02,000 - INFO - [ai.llm_client:1] - 🧠 [DeepSeek] Sending request ...",
        "2026-09-27 01:00:03,000 - INFO - [ai.llm_client:960] - [present_moment_cycle] cache_hit=0 cache_miss=0",
    ]
    out = summarize(lines)
    assert out["reply"] == {"hit": 4, "miss": 6, "share": 0.4}
    # a lane with no prompt tokens at all: share 0.0, never a ZeroDivisionError
    assert out["present_moment_cycle"] == {"hit": 0, "miss": 0, "share": 0.0}
    assert set(out) == {"reply", "present_moment_cycle"}


def _lane_line(day: str, clock: str, hit: int, miss: int) -> str:
    return f"{day} {clock},000 - INFO - [ai.llm_client:960] - [reflect_turn] cache_hit={hit} cache_miss={miss}\n"


def _write_real_naming(logs):
    """The naming main.py really produces: ONE base file named for the day the
    process started (09-24), rotated at midnight into ``._<day>`` copies. So
    the lines of 09-26 and 09-27 live in files named for 09-24."""
    (logs / "palli_2026-09-24.log._2026-09-24").write_text(_lane_line("2026-09-24", "12:00:00", 9000, 0))
    (logs / "palli_2026-09-24.log._2026-09-25").write_text(_lane_line("2026-09-25", "12:00:00", 9000, 0))
    # A file may hold lines from both sides of --since (a record stamped
    # 23:59:59.9 can be emitted after the midnight rollover): per line, not per file.
    (logs / "palli_2026-09-24.log._2026-09-26").write_text(
        _lane_line("2026-09-25", "23:59:59", 9000, 0)
        + _lane_line("2026-09-26", "00:00:01", 100, 0)
        + "Traceback (most recent call last): [x] cache_hit=9000 cache_miss=0\n"
        + _lane_line("2026-09-26", "18:00:00", 50, 50)
    )
    (logs / "palli_2026-09-24.log").write_text(_lane_line("2026-09-27", "01:00:00", 0, 100))
    # Not daily logs: never read.
    (logs / "palli_verbose_2026-09-27.log").write_text(_lane_line("2026-09-27", "01:00:00", 9000, 0))
    (logs / "palli_debug_2026-09-27.log").write_text(_lane_line("2026-09-27", "01:00:00", 9000, 0))


def test_lines_since_keeps_each_line_by_its_own_date(tmp_path):
    _write_real_naming(tmp_path)
    kept = list(_lines_since(_log_files(str(tmp_path)), dt.date(2026, 9, 26)))
    assert [ln[:19] for ln in kept] == [
        "2026-09-27 01:00:00",   # palli_2026-09-24.log (the current file)
        "2026-09-26 00:00:01",   # palli_2026-09-24.log._2026-09-26
        "2026-09-26 18:00:00",
    ]


def test_main_counts_every_line_since_even_in_files_named_for_an_older_day(tmp_path, capsys):
    """Reviewer's repro (fix round 1): a process started 09-24; --since 09-26
    printed 0 files and zeros because no file is NAMED for 09-26."""
    _write_real_naming(tmp_path)

    main(["--since", "2026-09-26", "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out

    rows = {ln.split()[0]: ln.split()[1:] for ln in out.splitlines() if ln.strip()}
    assert rows["reflect_turn"] == ["150", "150", "50.0%"]
    assert rows["total"] == ["150", "150", "50.0%"]


def test_main_with_no_counters_prints_a_zero_total(tmp_path, capsys):
    main(["--since", dt.date.today().isoformat(), "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    total = [ln.split() for ln in out.splitlines() if ln.startswith("total")]
    assert total == [["total", "0", "0", "0.0%"]]


# ── Batch 8, task 5 (ruling T5-5): a daily log over a week old is gzipped ──

def _gzip_in_place(path):
    import gzip as _gzip

    with _gzip.open(str(path) + ".gz", "wt", encoding="utf-8") as fh:
        fh.write(path.read_text())
    path.unlink()


def test_a_compressed_daily_log_is_read_like_a_plain_one(tmp_path):
    """After 7 days the retention sweep gzips a daily log
    (``palli_<start>.log._<day>.gz``); its lines still count."""
    _write_real_naming(tmp_path)
    _gzip_in_place(tmp_path / "palli_2026-09-24.log._2026-09-26")
    files = _log_files(str(tmp_path))
    assert str(tmp_path / "palli_2026-09-24.log._2026-09-26.gz") in files
    kept = list(_lines_since(files, dt.date(2026, 9, 26)))
    assert [ln[:19] for ln in kept] == [
        "2026-09-27 01:00:00",
        "2026-09-26 00:00:01",
        "2026-09-26 18:00:00",
    ]


def test_main_counts_a_compressed_day_and_a_broken_one_costs_nothing(tmp_path, capsys):
    _write_real_naming(tmp_path)
    _gzip_in_place(tmp_path / "palli_2026-09-24.log._2026-09-26")
    (tmp_path / "palli_2026-09-24.log._2026-09-27.gz").write_bytes(b"not gzip at all")

    main(["--since", "2026-09-26", "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out

    rows = {ln.split()[0]: ln.split()[1:] for ln in out.splitlines() if ln.strip()}
    assert rows["reflect_turn"] == ["150", "150", "50.0%"]
