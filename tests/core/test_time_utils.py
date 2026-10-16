from __future__ import annotations

from datetime import datetime

from core import time_utils


def test_format_timestamp_for_llm_today_and_yesterday():
    current = datetime(2026, 2, 28, 15, 0).timestamp()
    same_day = datetime(2026, 2, 28, 10, 15).timestamp()
    yesterday = datetime(2026, 2, 27, 22, 30).timestamp()

    assert time_utils.format_timestamp_for_llm(same_day, current).startswith("Today ")
    assert time_utils.format_timestamp_for_llm(yesterday, current).startswith("Yesterday ")


def test_relative_time_description_ranges():
    now = datetime(2026, 2, 28, 15, 0).timestamp()
    assert time_utils.get_relative_time_description(now - 10, now) == "just now"
    assert time_utils.get_relative_time_description(now - 120, now) == "a few minutes ago"
    assert time_utils.get_relative_time_description(now - 1800, now).startswith("about ")
    assert time_utils.get_relative_time_description(now - 4000, now) == "about an hour ago"
    assert time_utils.get_relative_time_description(now - 90000, now) == "yesterday"


def test_time_of_day_greeting_bounds(monkeypatch):
    class DummyDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 2, 28, 9, 0)

    monkeypatch.setattr(time_utils, "datetime", DummyDateTime)
    assert time_utils.get_time_of_day_greeting() == "morning"


def test_current_time_context_is_platform_safe(monkeypatch):
    class DummyDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 2, 28, 15, 5)

    monkeypatch.setattr(time_utils, "datetime", DummyDateTime)
    assert time_utils.get_current_time_context() == "Saturday, Feb 28, 2026, 3:05 PM"


def test_format_timestamp_for_llm_same_year_and_different_year():
    current = datetime(2026, 12, 20, 15, 0).timestamp()
    same_year = datetime(2026, 1, 5, 8, 3).timestamp()
    prior_year = datetime(2025, 12, 5, 20, 4).timestamp()

    assert time_utils.format_timestamp_for_llm(same_year, current) == "Jan 5 at 8:03 AM"
    assert time_utils.format_timestamp_for_llm(prior_year, current) == "Dec 5, 2025 at 8:04 PM"
