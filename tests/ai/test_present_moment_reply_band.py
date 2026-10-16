"""Slice 2 renderer: deterministic age/staleness phrasing for the reply
band. Presentation, not meaning — buckets decide how an age READS, never
what anything means (spec §2/§6). Calendar-day staleness naming: written
yesterday 23:50 read at 00:10 is "yesterday", not a 24h window."""
from __future__ import annotations

import time

from ai.present_moment_store import (
    MOMENT_CHAR_CAP,
    MomentEntry,
    render_reply_band,
)

PROSE = "The quiet feels like concentration. I'm holding the same patience."


def _entry(text: str = PROSE, written_at: float = 1000.0) -> MomentEntry:
    return MomentEntry(text=text, written_at=written_at, meta={})


def _epoch(y: int, mo: int, d: int, h: int, mi: int) -> float:
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


# ── fresh phrasing ────────────────────────────────────────────────


def test_fresh_just_now_under_90s():
    out = render_reply_band(_entry(), 30.0, False, now=2000.0)
    assert out.splitlines()[0] == "Your last moment, written just now:"
    assert out.splitlines()[1] == PROSE


def test_fresh_minutes_singular():
    out = render_reply_band(_entry(), 100.0, False, now=2000.0)
    assert out.startswith("Your last moment, written 1 minute ago:")


def test_fresh_minutes_plural():
    out = render_reply_band(_entry(), 19 * 60.0, False, now=2000.0)
    assert out.startswith("Your last moment, written 19 minutes ago:")


def test_fresh_one_hour():
    out = render_reply_band(_entry(), 3600.0, False, now=2000.0)
    assert out.startswith("Your last moment, written about an hour ago:")


def test_fresh_hours_rounded():
    out = render_reply_band(_entry(), int(2.6 * 3600), False, now=2000.0)
    assert out.startswith("Your last moment, written about 3 hours ago:")


# ── stale phrasing (calendar-day comparison, spec §6) ─────────────


def test_stale_same_day():
    written = _epoch(2026, 7, 27, 9, 0)
    now = _epoch(2026, 7, 27, 20, 0)
    out = render_reply_band(_entry(written_at=written), now - written, True, now=now)
    assert out.splitlines()[0] == (
        "Your last moment is from earlier today — the present since is unwritten:"
    )
    assert PROSE in out


def test_stale_yesterday_calendar_not_24h():
    # The spec's explicit example: written 23:50, read 00:10 — "yesterday".
    written = _epoch(2026, 7, 26, 23, 50)
    now = _epoch(2026, 7, 27, 0, 10)
    out = render_reply_band(_entry(written_at=written), now - written, True, now=now)
    assert "from yesterday — the present since is unwritten:" in out.splitlines()[0]


def test_stale_days_ago():
    written = _epoch(2026, 7, 24, 9, 0)
    now = _epoch(2026, 7, 27, 20, 0)
    out = render_reply_band(_entry(written_at=written), now - written, True, now=now)
    assert "from 3 days ago — the present since is unwritten:" in out.splitlines()[0]


# ── belts ─────────────────────────────────────────────────────────


def test_none_entry_renders_empty():
    assert render_reply_band(None, 0.0, False, now=2000.0) == ""


def test_empty_text_renders_empty():
    assert render_reply_band(_entry(text="   "), 30.0, False, now=2000.0) == ""


def test_oversized_prose_clipped_at_cap():
    out = render_reply_band(_entry(text="x" * 5000), 30.0, False, now=2000.0)
    assert len(out.splitlines()[1]) == MOMENT_CHAR_CAP
