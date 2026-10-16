"""The surface→platform map learns a second word (telegram door, task 1).

``_platform_for_surface`` is the seam every persisted row goes through:
``core/turn/persistence.py`` stamps ``platform`` from it, and the public
``platform_for_surface`` wrapper decides which platform a reminder row is
written under. Teaching it ``telegram_text`` is what makes a Telegram turn
persist and recall under its own door instead of falling into the dashboard
bucket.

The Discord half of every case below is the regression guard: this task
changes NO Discord behaviour.
"""
from __future__ import annotations

from core.turn.recall import _platform_for_surface, platform_for_surface


def test_the_discord_surfaces_still_map_to_discord():
    assert _platform_for_surface("discord_text") == "discord"
    assert _platform_for_surface("discord_voice") == "discord"


def test_telegram_text_maps_to_telegram():
    assert _platform_for_surface("telegram_text") == "telegram"


def test_the_other_known_surfaces_are_unchanged():
    assert _platform_for_surface("voice_local") == "voice"
    assert _platform_for_surface("dashboard") == "dashboard"
    assert _platform_for_surface("proactive") is None
    assert _platform_for_surface("reminder_fire") is None


def test_an_unknown_or_blank_surface_is_still_none():
    assert _platform_for_surface("carrier_pigeon") is None
    assert _platform_for_surface("") is None
    assert _platform_for_surface("   ") is None


def test_the_map_is_still_case_and_whitespace_tolerant():
    assert _platform_for_surface("  TELEGRAM_TEXT  ") == "telegram"
    assert _platform_for_surface("  DISCORD_TEXT  ") == "discord"


def test_the_public_wrapper_agrees_and_still_falls_back_to_dashboard():
    """``platform_for_surface`` is the reminder write/read symmetry helper:
    it never returns None because its callers pass the value straight into
    ``platform=`` on a DB query."""
    assert platform_for_surface("discord_text") == "discord"
    assert platform_for_surface("discord_voice") == "discord"
    assert platform_for_surface("telegram_text") == "telegram"
    assert platform_for_surface("proactive") == "dashboard"
    assert platform_for_surface("reminder_fire") == "dashboard"
    assert platform_for_surface(None) == "dashboard"
