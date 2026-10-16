"""Day-gist store (Wave 1a): one gist per day, keyed by ITS day, overwrite."""
from __future__ import annotations

import json

import pytest

from ai.day_gist_store import (
    DayGistEntry,
    read_day_gist,
    read_recent,
    write_day_gist,
)


def test_write_is_keyed_by_the_gists_own_day(tmp_path):
    p = write_day_gist(day="2026-05-02", text="early May was the reminder-storm week.",
                       meta={"owner_id": 1}, root=tmp_path)
    assert p.name == "2026-05-02.jsonl"
    e = read_day_gist(day="2026-05-02", root=tmp_path)
    assert e is not None and e.day == "2026-05-02" and "reminder-storm" in e.text
    assert e.meta["owner_id"] == 1 and e.created_at > 0


def test_rerun_overwrites_never_duplicates(tmp_path):
    write_day_gist(day="2026-05-02", text="first attempt", root=tmp_path)
    p = write_day_gist(day="2026-05-02", text="second attempt", root=tmp_path)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "second attempt"


def test_invalid_day_raises(tmp_path):
    with pytest.raises(ValueError):
        write_day_gist(day="May 2nd", text="x", root=tmp_path)


def test_read_missing_day_is_none(tmp_path):
    assert read_day_gist(day="2026-05-03", root=tmp_path) is None


def test_read_recent_windows_and_orders_newest_first(tmp_path):
    import datetime as dt
    today = dt.date.today()
    d1 = (today - dt.timedelta(days=2)).isoformat()
    d2 = (today - dt.timedelta(days=5)).isoformat()
    d_old = (today - dt.timedelta(days=90)).isoformat()
    for d in (d2, d1, d_old):
        write_day_gist(day=d, text=f"gist for {d}", root=tmp_path)
    got = read_recent(days_back=30, limit=10, root=tmp_path)
    assert [e.day for e in got] == [d1, d2]
