"""Night Cycle daily-meaning store — dated JSONL, gitignored (2026-07-11)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ai.night_cycle_store import read_latest_daily_meaning, write_daily_meaning


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def test_write_then_read_latest(tmp_path):
    write_daily_meaning(
        date=_today(),
        text="Quiet day; the reminder work finally landed and it felt like relief.",
        root=tmp_path,
    )
    got = read_latest_daily_meaning(root=tmp_path)
    assert got is not None
    assert got.date == _today()
    assert "relief" in got.text


def test_latest_returns_newest(tmp_path):
    write_daily_meaning(date=_yesterday(), text="older", root=tmp_path)
    write_daily_meaning(date=_today(), text="newer", root=tmp_path)
    assert read_latest_daily_meaning(root=tmp_path).text == "newer"


def test_none_when_empty(tmp_path):
    assert read_latest_daily_meaning(root=tmp_path) is None


def test_stale_filtered_by_max_age(tmp_path):
    write_daily_meaning(date="2020-01-01", text="ancient", root=tmp_path)
    assert read_latest_daily_meaning(max_age_days=2, root=tmp_path) is None


# ── read_recent (introspect tool read path, 2026-07-13) ──────────────────


def test_read_recent_empty(tmp_path):
    from ai.night_cycle_store import read_recent

    assert read_recent(root=tmp_path) == []


def test_read_recent_newest_first_within_window(tmp_path):
    from ai.night_cycle_store import read_recent

    write_daily_meaning(date=_yesterday(), text="older note", root=tmp_path)
    write_daily_meaning(date=_today(), text="newer note", root=tmp_path)
    got = read_recent(days_back=14, root=tmp_path)
    assert [e.text for e in got] == ["newer note", "older note"]


def test_read_recent_window_excludes_old(tmp_path):
    from ai.night_cycle_store import read_recent

    write_daily_meaning(date="2020-01-01", text="ancient", root=tmp_path)
    write_daily_meaning(date=_today(), text="fresh", root=tmp_path)
    assert [e.text for e in read_recent(days_back=7, root=tmp_path)] == ["fresh"]


def test_read_recent_respects_limit_newest_kept(tmp_path):
    from datetime import datetime, timedelta

    from ai.night_cycle_store import read_recent

    for i in range(5):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        write_daily_meaning(date=d, text=f"note {i}", root=tmp_path)
    got = read_recent(days_back=30, limit=3, root=tmp_path)
    assert len(got) == 3
    assert got[0].text == "note 0"


def test_read_recent_last_line_per_date_wins(tmp_path):
    from ai.night_cycle_store import read_recent

    write_daily_meaning(date=_today(), text="first draft", root=tmp_path)
    write_daily_meaning(date=_today(), text="second draft", root=tmp_path)
    assert [e.text for e in read_recent(root=tmp_path)] == ["second draft"]


def test_read_recent_ignores_future_and_malformed(tmp_path):
    from datetime import datetime, timedelta

    from ai.night_cycle_store import read_recent

    write_daily_meaning(date=_today(), text="real", root=tmp_path)
    future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    write_daily_meaning(date=future, text="future", root=tmp_path)
    (tmp_path / "junk.jsonl").write_text("not json\n", encoding="utf-8")
    (tmp_path / "2026-13-99.jsonl").write_text('{"date":"x","text":"bad"}\n', encoding="utf-8")
    assert [e.text for e in read_recent(days_back=7, root=tmp_path)] == ["real"]
