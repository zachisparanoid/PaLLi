"""Batch 8, task 5 (ruling T5-5, grounding D#34): once retention gzips the
logs, every reader of ``logs/palli_*.log*`` reads a ``.gz`` through
``gzip.open`` — ``scripts/vitals.py`` and ``dashboards/collect.py`` (its
14-day window outlives the 7-day main-log gzip). A broken ``.gz`` costs that
file only. Tmp trees only.
"""
from __future__ import annotations

import datetime as dt
import gzip
import os

from scripts import vitals

LINE_A = "2026-09-20 10:00:00,000 - INFO - [x:1] - a plain day\n"
LINE_B = "2026-09-21 10:00:00,000 - INFO - [x:1] - a zipped day\n"


def _write_gz(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def test_vitals_reads_a_gzipped_log(tmp_path):
    (tmp_path / "palli_2026-09-20.log").write_text(LINE_A)
    _write_gz(tmp_path / "palli_2026-09-20.log._2026-09-21.gz", LINE_B)
    lines = vitals._read_log_lines(str(tmp_path / "palli_*.log*"), dt.date(2026, 9, 19))
    assert sorted(lines) == [LINE_A, LINE_B]


def test_vitals_skips_a_broken_gz_and_keeps_the_rest(tmp_path):
    (tmp_path / "palli_2026-09-20.log").write_text(LINE_A)
    (tmp_path / "palli_2026-09-20.log._2026-09-21.gz").write_bytes(b"not gzip")
    good = gzip.compress(LINE_B.encode())
    (tmp_path / "palli_2026-09-20.log._2026-09-22.gz").write_bytes(good[: len(good) // 2])   # truncated
    lines = vitals._read_log_lines(str(tmp_path / "palli_*.log*"), dt.date(2026, 9, 19))
    assert LINE_A in lines


def test_vitals_opens_plain_and_gzipped_logs_as_text(tmp_path):
    plain = tmp_path / "a.log"; plain.write_text(LINE_A)
    zipped = tmp_path / "a.log.gz"; _write_gz(zipped, LINE_B)
    with vitals.open_log_text(str(plain)) as fh:
        assert fh.read() == LINE_A
    with vitals.open_log_text(str(zipped)) as fh:
        assert fh.read() == LINE_B


def test_the_dashboard_reads_a_gzipped_daily_log_in_its_window(tmp_path, monkeypatch):
    import dashboards.collect as collect

    logs = tmp_path / "logs"; logs.mkdir()
    plain = logs / "palli_2026-09-20.log"; plain.write_text(LINE_A)
    zipped = logs / "palli_2026-09-20.log._2026-09-21.gz"; _write_gz(zipped, LINE_B)
    (logs / "palli_verbose_2026-09-20.log._2026-09-21.gz").write_bytes(gzip.compress(b"twin\n"))
    t = dt.datetime(2026, 9, 22).timestamp()
    for p in logs.iterdir():
        os.utime(p, (t, t))                       # retention keeps the original mtime on a .gz
    monkeypatch.setattr(collect, "REPO", tmp_path)

    files = collect._plain_log_files("2026-09-15")
    assert files == sorted([str(plain), str(zipped)])          # the verbose twin stays out
    assert sorted(collect._lines(files)) == [LINE_A, LINE_B]


def test_the_dashboard_skips_a_broken_gz(tmp_path):
    import dashboards.collect as collect

    good = tmp_path / "palli_2026-09-20.log"; good.write_text(LINE_A)
    bad = tmp_path / "palli_2026-09-20.log._2026-09-21.gz"; bad.write_bytes(b"not gzip")
    assert list(collect._lines([str(bad), str(good)])) == [LINE_A]
