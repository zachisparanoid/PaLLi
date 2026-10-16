"""Present Moment store — the clip lock, staleness, and today's count.

Deterministic by construction: every call takes an explicit ``root=`` so
the live ``data/present_moment/`` tree is never touched, and staleness is
driven by explicit ``now=``/``stale_after_s=`` rather than the wall clock.
"""
from __future__ import annotations

import json
import time

import pytest

from ai.present_moment_store import (
    MOMENT_CHAR_CAP,
    count_today,
    read_current,
    write_moment,
)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time()))


def _write_raw(root, *, day: str, text: str, written_at: float, meta=None):
    """Hand-place a JSONL line so ages are exact (no wall-clock slop)."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"text": text, "written_at": written_at, "meta": meta or {}})
            + "\n"
        )
    return path


# ── write: the second cap lock, and the no-ghost-record rule ────────────


def test_write_then_read_current_roundtrip(tmp_path):
    write_moment(
        text="   I am here, and the house is quiet.  \n",
        meta={"impulse": {"kind": "none", "why": ""}},
        root=tmp_path,
    )
    got = read_current(root=tmp_path)
    assert got is not None
    entry, age, stale = got
    assert entry.text == "I am here, and the house is quiet."  # stripped
    assert entry.meta["impulse"]["kind"] == "none"
    assert age < 60.0 and stale is False


def test_write_clips_at_char_cap(tmp_path):
    write_moment(text="x" * (MOMENT_CHAR_CAP + 800), meta={}, root=tmp_path)
    entry, _age, _stale = read_current(root=tmp_path)
    assert len(entry.text) == MOMENT_CHAR_CAP


def test_write_rejects_empty_after_strip(tmp_path):
    for bad in ("", "   \n\t "):
        with pytest.raises(ValueError):
            write_moment(text=bad, meta={}, root=tmp_path)
    assert list(tmp_path.glob("*.jsonl")) == []  # no ghost record on disk


def test_write_appends_and_last_line_is_current(tmp_path):
    write_moment(text="first", meta={}, root=tmp_path)
    write_moment(text="second", meta={}, root=tmp_path)
    entry, _age, _stale = read_current(root=tmp_path)
    assert entry.text == "second"


# ── staleness: never served silently ───────────────────────────────────


def test_read_current_none_when_store_empty(tmp_path):
    assert read_current(root=tmp_path) is None


def test_read_current_fresh_is_not_stale(tmp_path):
    _write_raw(tmp_path, day=_today(), text="fresh", written_at=1000.0)
    _entry, age, stale = read_current(now=1599.0, stale_after_s=600.0, root=tmp_path)
    assert age == 599.0
    assert stale is False


def test_read_current_stale_past_threshold(tmp_path):
    _write_raw(tmp_path, day=_today(), text="old", written_at=1000.0)
    _entry, age, stale = read_current(now=1601.0, stale_after_s=600.0, root=tmp_path)
    assert age == 601.0
    assert stale is True


def test_read_current_boundary_age_is_not_stale(tmp_path):
    _write_raw(tmp_path, day=_today(), text="edge", written_at=1000.0)
    _entry, age, stale = read_current(now=1600.0, stale_after_s=600.0, root=tmp_path)
    assert age == 600.0
    assert stale is False  # strict >, so exactly-at-threshold is still current


def test_read_current_age_never_negative(tmp_path):
    _write_raw(tmp_path, day=_today(), text="clock skew", written_at=1000.0)
    _entry, age, stale = read_current(now=900.0, stale_after_s=60.0, root=tmp_path)
    assert age == 0.0
    assert stale is False


# ── count_today: the daily budget's restore path ───────────────────────


def test_count_today_zero_when_empty(tmp_path):
    assert count_today(root=tmp_path) == 0


def test_count_today_counts_written_lines(tmp_path):
    write_moment(text="one", meta={}, root=tmp_path)
    write_moment(text="two", meta={}, root=tmp_path)
    assert count_today(root=tmp_path) == 2


def test_count_today_skips_malformed_and_textless_lines(tmp_path):
    write_moment(text="real", meta={}, root=tmp_path)
    with (tmp_path / f"{_today()}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps({"text": "", "written_at": 1.0}) + "\n")
        fh.write("\n")
    assert count_today(root=tmp_path) == 1


def test_count_today_ignores_other_days(tmp_path):
    _write_raw(tmp_path, day="2020-01-01", text="ancient", written_at=1.0)
    _write_raw(tmp_path, day="2020-01-02", text="ancient too", written_at=2.0)
    assert count_today(root=tmp_path) == 0
