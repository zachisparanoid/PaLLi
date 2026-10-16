"""The night reflection reads the day's real conversation (Batch 3, 1.4).

The "## The day with them" section used to read a memory-workspace day-log
whose newest file was 2026-04-30, so every reflection saw an empty or ancient
day. Now it reads the owner's conversation rows for the day from the
database, both doors, rendered as a plain ``HH:MM  them: …`` / ``HH:MM  me: …``
log and middle-clipped to a cap. Rendering is mechanical; nothing here
decides meaning."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from ai.night_cycle import _load_day_conversation, _render_day_conversation


def test_day_conversation_renders_both_voices_and_clips():
    rows = [(1.0, "discord", "user", "hey"), (2.0, "discord", "assistant", "hi"), (3.0, "telegram", "user", "x" * 10000)]
    out = _render_day_conversation(rows, cap=200)
    assert "  them: hey" in out and "  me: hi" in out and len(out) <= 230 and "…" in out


def test_empty_day_renders_nothing():
    assert _render_day_conversation([], cap=200) == ""


def test_unknown_role_and_empty_content_are_skipped():
    rows = [
        (1.0, "discord", "user", "morning"),
        (2.0, "discord", "system", "a system row"),
        (3.0, "discord", "assistant", "   "),
        (4.0, "discord", "assistant", "morning to you"),
    ]
    out = _render_day_conversation(rows)
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("  them: morning")
    assert lines[1].endswith("  me: morning to you")
    assert "system row" not in out


def _ts(day: str, hh: int, mm: int) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm).timestamp()


def test_load_keeps_only_the_owner_and_asks_for_the_days_bounds(monkeypatch):
    day = "2026-07-10"
    seen = {}

    async def fake_store(**kwargs):
        seen.update(kwargs)
        return [
            {"user_id": 1, "platform": "discord", "role": "user", "username": "owner",
             "content": "hers", "timestamp": _ts(day, 9, 5)},
            {"user_id": 2, "platform": "discord", "role": "user", "username": "guest",
             "content": "a guest's line", "timestamp": _ts(day, 9, 6)},
            {"user_id": 1, "platform": "discord", "role": "assistant", "username": "PaLLi",
             "content": "mine", "timestamp": _ts(day, 9, 7)},
        ]

    monkeypatch.setattr("core.database.brain.get_conversations_for_day", fake_store)
    rows = asyncio.run(_load_day_conversation(1, day))
    start = datetime.strptime(day, "%Y-%m-%d")
    assert seen["day_start_ts"] == start.timestamp()
    assert seen["day_end_ts"] == (start + timedelta(days=1)).timestamp()
    assert rows == [
        (_ts(day, 9, 5), "discord", "user", "hers"),
        (_ts(day, 9, 7), "discord", "assistant", "mine"),
    ]


def test_both_doors_render_in_timestamp_order(monkeypatch):
    day = "2026-07-10"

    async def fake_store(**kwargs):
        # the store's order is not trusted: telegram's evening first, discord's morning last
        return [
            {"user_id": 1, "platform": "telegram", "role": "user",
             "content": "evening on the phone", "timestamp": _ts(day, 21, 30)},
            {"user_id": 1, "platform": "discord", "role": "assistant",
             "content": "noon from me", "timestamp": _ts(day, 12, 0)},
            {"user_id": 1, "platform": "discord", "role": "user",
             "content": "morning on discord", "timestamp": _ts(day, 8, 15)},
        ]

    monkeypatch.setattr("core.database.brain.get_conversations_for_day", fake_store)
    out = _render_day_conversation(asyncio.run(_load_day_conversation(1, day)))
    assert out.splitlines() == [
        "08:15  them: morning on discord",
        "12:00  me: noon from me",
        "21:30  them: evening on the phone",
    ]


# ── the middle clip never leaves a fragment without its speaker (review fix, round 1) ──

_SPOKEN_LINE = re.compile(r"^\d\d:\d\d  (them|me): ")


def _busy_day():
    return [
        (float(i * 60), "discord", "user" if i % 2 == 0 else "assistant", f"message {i} " + "w" * 40)
        for i in range(40)
    ]


def test_clipped_day_keeps_a_speaker_on_every_line_after_the_marker():
    out = _render_day_conversation(_busy_day(), cap=600)
    assert "…" in out and len(out) <= 600
    _head, tail = out.split("\n…\n")
    for line in tail.splitlines():
        assert _SPOKEN_LINE.match(line), line


def test_clipped_day_keeps_whole_lines_on_both_sides_of_the_marker():
    rows = _busy_day()
    whole = _render_day_conversation(rows).splitlines()  # the uncapped day
    head, tail = (part.splitlines() for part in _render_day_conversation(rows, cap=600).split("\n…\n"))
    assert head and tail
    assert whole[: len(head)] == head  # the head ends on a whole line
    assert whole[-len(tail):] == tail  # the tail starts on a whole line


def test_a_single_huge_message_keeps_its_speaker_when_clipped():
    rows = [(1.0, "discord", "user", "hey"), (2.0, "discord", "assistant", "m" * 10000)]
    out = _render_day_conversation(rows, cap=200)
    assert len(out) <= 200
    head, tail = out.split("\n…\n")
    assert head.endswith("  them: hey")
    unclipped = _render_day_conversation(rows, cap=20_000)  # the default cap would clip this day too
    label = unclipped.splitlines()[1][: len("HH:MM  me: ")]
    assert tail.startswith(label) and re.fullmatch(r"\d\d:\d\d  me: m+", tail)
