"""The real wait, from a person's message to the reply (2026-10-12)."""
from __future__ import annotations

import logging

from bots.common import reply_wait as rw


def test_stamp_keeps_the_first_arrival_and_moves_the_last() -> None:
    times = rw.stamp(None, now=100.0)
    assert times == (100.0, 100.0)
    assert rw.stamp(times, now=104.5) == (100.0, 104.5)


def test_merge_takes_the_earlier_first_and_the_later_last() -> None:
    assert rw.merge((100.0, 104.0), (102.0, 110.0)) == (100.0, 110.0)
    assert rw.merge((102.0, 110.0), (100.0, 104.0)) == (100.0, 110.0)
    assert rw.merge(None, (1.0, 2.0)) == (1.0, 2.0)
    assert rw.merge((1.0, 2.0), None) == (1.0, 2.0)
    assert rw.merge(None, None) is None


def test_last_arrival() -> None:
    assert rw.last_arrival(None) is None
    assert rw.last_arrival((100.0, 104.5)) == 104.5


def test_the_log_line_says_both_waits_and_the_typing(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        rw.log_reply_wait("discord", 555, (100.0, 104.0), typing=1.0, now=113.5)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["reply wait: 9.5s since the last message, 13.5s since the first; "
                     "typing shown 1.0s (discord 555)"]


def test_no_stamp_no_line(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        rw.log_reply_wait("telegram", 7, None, now=50.0)
    assert caplog.records == []


def test_a_run_that_more_messages_came_into_says_so(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        rw.log_reply_wait("discord", 555, (100.0, 104.0), typing=1.0, during_run=1, now=124.0)
        rw.log_reply_wait("discord", 555, (100.0, 104.0), typing=1.0, during_run=2, now=124.0)
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].endswith("(discord 555); 1 more message came in during the run")
    assert lines[1].endswith("(discord 555); 2 more messages came in during the run")


def test_the_line_names_a_mid_task_or_side_reply(caplog) -> None:
    """10-12: replies given while PaLLi works are timed too, and say which
    kind they are, so talking-while-working can be counted from the log."""
    import logging
    from bots.common import reply_wait
    with caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        reply_wait.log_reply_wait("discord", 5, (10.0, 12.0), now=14.0, kind="side conversation")
    [line] = [r.getMessage() for r in caplog.records]
    assert line.endswith("(discord 5, side conversation)")
