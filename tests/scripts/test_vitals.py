"""The vitals page (2026-08-22) — "a gauge instead of a gut".

Promised in the 08-22 narration: one page, weekly, three dials (reply
length · reaches · timeouts per lane) plus the 4 AM chain per night. The
script is read-only; every parser is a pure function over the line/row
shapes the live system already writes, pinned here on fixtures so a log
format drift fails a test instead of silently blanking a dial."""
from __future__ import annotations

from scripts import vitals


LOG = """\
2026-08-21 04:00:09,123 - INFO - [palli.essential:1] - night_cycle: wrote daily-meaning for 2026-08-20 (681 chars)
2026-08-21 04:00:15,000 - INFO - [palli.essential:1] - consolidation: day-gist 2026-08-20 (483 chars)
2026-08-21 04:03:51,000 - WARNING - [ai.llm_client:895] - [stake_review] DeepSeek request timeout, retrying in 4.7s (attempt 3/4)
2026-08-21 04:04:20,000 - ERROR - [ai.llm_client:905] - [stake_review] DeepSeek request timeout after 4 attempts
2026-08-21 04:04:21,000 - INFO - [palli.essential:1] - stake_review: 2026-08-20 — applied=0 backstop_faded=0 dropped_opens=0 error=review output unparseable
2026-08-21 04:06:43,000 - INFO - [palli.essential:1] - owner_picture: redrawn (memory_summary, relationship_state) — 801 chars
2026-08-21 04:07:30,000 - INFO - [palli.essential:1] - passions: journal page written for 2026-08-20 (85e4cf64)
2026-08-21 07:21:25,000 - WARNING - [ai.llm_client:868] - [general] DeepSeek request timeout, retrying in 1.1s (attempt 1/4)
2026-08-21 12:00:00,000 - INFO - [core.agent.runner:378] - loop_start session=discord:1 cadence_summary=register=neutral length=short beats=1
2026-08-21 12:00:05,000 - INFO - [palli.essential:1] - reply_govern: short 142→38 words (compressed)
2026-08-21 12:30:00,000 - INFO - [ai.present_moment:835] - present_moment: cycle wrote 984 chars (woke_on=['silence_bucket_crossed'], impulse=hold)
2026-08-21 12:50:00,000 - INFO - [ai.present_moment:835] - present_moment: cycle wrote 900 chars (woke_on=['owner_online'], impulse=say)
2026-08-22 04:00:07,000 - INFO - [palli.essential:1] - night_cycle: wrote daily-meaning for 2026-08-21 (791 chars)
2026-08-22 04:06:13,000 - INFO - [palli.essential:1] - stake_review: 2026-08-21 — applied=8 backstop_faded=0 dropped_opens=0
2026-08-22 04:06:43,000 - INFO - [palli.essential:1] - owner_picture: redrawn (memory_summary, relationship_state) — 801 chars
2026-08-22 04:02:45,000 - INFO - [palli.essential:1] - memory_sweep: let go of 84 active thread(s) older than 30 days
2026-08-22 09:00:00,000 - INFO - [palli.essential:1] - reply_govern: medium 190→95 words (trimmed)
"""


def test_timeouts_per_lane_count_attempts_and_give_ups():
    t = vitals.parse_timeouts(LOG.splitlines())
    assert t["stake_review"] == {"attempts": 1, "gave_up": 1}
    assert t["general"] == {"attempts": 1, "gave_up": 0}


def test_governed_replies_are_counted_by_day_and_action():
    g = vitals.parse_reply_governs(LOG.splitlines())
    assert g["2026-08-21"] == {"compressed": 1, "trimmed": 0}
    assert g["2026-08-22"] == {"compressed": 0, "trimmed": 1}


def test_moment_say_hold_by_day():
    m = vitals.parse_moment(LOG.splitlines())
    assert m["2026-08-21"] == {"say": 1, "hold": 1}


def test_the_four_am_chain_is_scored_per_night():
    chain = vitals.parse_night_chain(LOG.splitlines())
    n21 = chain["2026-08-21"]
    assert n21["reflection"] is True and n21["gist"] is True and n21["picture"] is True and n21["journal"] is True
    assert n21["review"] == "✗ review output unparseable"
    n22 = chain["2026-08-22"]
    assert n22["review"] == "✓ applied=8" and n22["sweep"] == 84 and n22["gist"] is False


def test_reply_lengths_by_day_from_rows():
    rows = [(1787000000.0, "one two three"), (1787000100.0, " ".join(["w"] * 120)), (1787086400.0, "x y")]
    by = vitals.reply_lengths_by_day(rows)
    days = sorted(by)
    assert len(days) == 2
    first = by[days[0]]
    assert first["n"] == 2 and first["median"] == 61.5 and first["p90"] == 120 and first["max"] == 120


def test_reach_ledger_counts_dispositions_by_day():
    entries = [
        {"at": 1787124051.0, "disposition": "sent_share"},
        {"at": 1787146378.0, "disposition": "sent"},
        {"at": 1787169071.0, "disposition": "stale_dropped"},
    ]
    r = vitals.reach_by_day(entries)
    assert sum(sum(v.values()) for v in r.values()) == 3
    assert any(v.get("sent") == 1 for v in r.values())


def test_render_is_one_page_with_the_four_dials():
    report = vitals.Report(
        generated="2026-08-22", days=7,
        reply_lengths={"2026-08-21": {"n": 27, "median": 625, "p90": 1498, "max": 3894}},
        governed={"2026-08-21": {"compressed": 3, "trimmed": 1}},
        moment={"2026-08-21": {"say": 11, "hold": 43}},
        reaches={"2026-08-21": {"sent": 1, "stale_dropped": 2}},
        timeouts={"general": {"attempts": 32, "gave_up": 5}},
        chain={"2026-08-22": {"reflection": True, "gist": True, "review": "✓ applied=8", "picture": True, "journal": True, "sweep": 84}},
    )
    page = vitals.render(report)
    for heading in ("Reply length", "Reaches", "Timeouts per lane", "4 AM chain"):
        assert heading in page
    assert "2026-08-21" in page and "general" in page and "applied=8" in page
    assert page.count("\n") < 80   # one page


def test_cli_runs_read_only_and_writes_only_on_request(tmp_path, monkeypatch):
    out = tmp_path / "vitals.md"
    rc = vitals.main(["--days", "1", "--write", str(out), "--logs-glob", str(tmp_path / "nothing*.log"),
                      "--db", str(tmp_path / "missing.sqlite3"), "--arbiter-dir", str(tmp_path / "no-arbiter")])
    assert rc == 0 and out.exists() and "PaLLi vitals" in out.read_text()


# ── Batch 8, task 8 (6.6, ruling T8-1): each log line counted once ─────────
# The day log palli_<date>.log has two twins: palli_verbose_<date>.log
# repeats every line of it, and palli_debug_<date>.log holds the targeted
# debug loggers' lines. The default glob palli_*.log* matched all three, so
# the dials counted lines twice or more. The twins are skipped by name in
# _read_log_lines, which also serves --logs-glob; after retention a twin can
# be a .gz, and it is skipped too.

GOVERN = "2026-09-20 09:00:00,000 - INFO - [palli.essential:1] - reply_govern: short 142→38 words (compressed)\n"


def _gz(path, text):
    import gzip
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def test_the_verbose_and_debug_twins_are_read_once(tmp_path):
    import datetime as dt
    (tmp_path / "palli_2026-09-20.log").write_text(GOVERN)
    (tmp_path / "palli_verbose_2026-09-20.log").write_text(GOVERN)
    (tmp_path / "palli_debug_2026-09-20.log").write_text(GOVERN)
    _gz(tmp_path / "palli_verbose_2026-09-19.log._2026-09-21.gz", GOVERN)
    _gz(tmp_path / "palli_debug_2026-09-19.log._2026-09-21.gz", GOVERN)
    lines = vitals._read_log_lines(str(tmp_path / "palli_*.log*"), dt.date(2026, 9, 19))
    assert lines == [GOVERN]


def test_a_dial_counts_a_twinned_line_once(tmp_path):
    import datetime as dt
    (tmp_path / "palli_2026-09-20.log").write_text(GOVERN)
    (tmp_path / "palli_verbose_2026-09-20.log").write_text(GOVERN)
    _gz(tmp_path / "palli_debug_2026-09-20.log._2026-09-22.gz", GOVERN)
    report = vitals.build_report(days=7, logs_glob=str(tmp_path / "palli_*.log*"),
                                 db_path=str(tmp_path / "missing.sqlite3"),
                                 arbiter_dir=str(tmp_path / "no-arbiter"), today=dt.date(2026, 9, 21))
    assert report.governed == {"2026-09-20": {"compressed": 1, "trimmed": 0}}


def test_a_main_day_log_named_like_no_twin_is_still_read(tmp_path):
    import datetime as dt
    (tmp_path / "palli_2026-09-20.log").write_text(GOVERN)
    _gz(tmp_path / "palli_2026-09-19.log._2026-09-21.gz", GOVERN)
    lines = vitals._read_log_lines(str(tmp_path / "palli_*.log*"), dt.date(2026, 9, 19))
    assert lines == [GOVERN, GOVERN]


def test_the_dashboard_day_logs_are_found_in_any_year(tmp_path, monkeypatch):
    import os
    import time
    import dashboards.collect as collect

    logs = tmp_path / "logs"; logs.mkdir()
    day_2027 = logs / "palli_2027-01-02.log"; day_2027.write_text(GOVERN)
    day_2026 = logs / "palli_2026-12-31.log"; day_2026.write_text(GOVERN)
    (logs / "palli_verbose_2027-01-02.log").write_text(GOVERN)
    (logs / "palli_debug_2027-01-02.log").write_text(GOVERN)
    now = time.time()
    for p in logs.iterdir():
        os.utime(p, (now, now))
    monkeypatch.setattr(collect, "REPO", tmp_path)
    assert collect._plain_log_files("2000-01-01") == sorted([str(day_2026), str(day_2027)])
