"""The first look's switch (R14, T0; plan docs/2026-10-12-one-first-look-plan.md §2.2).

- ``companion.first_look`` (schema default OFF, dark until the wrap) puts one
  look in PaLLi's own voice in place of the quick vote and the first read. It
  counts only while ``companion.she_decides`` is on too: with she_decides on
  and first_look off every path is today's, a one-line rollback that keeps
  "she decides".
- ``features.guest_web_search`` (schema default OFF; the owner, 10-12 21:20:
  "yes, friends should get web search") ships dark the same way, so the
  golden's guest turn keeps its bytes.
- The suite holds the look off, as it holds she_decides off; the golden
  (both switches off) still passes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def test_the_switches_ship_dark():
    from core.settings_schema import CompanionConfig, FeaturesConfig

    assert CompanionConfig.model_fields["first_look"].default is False
    assert FeaturesConfig.model_fields["guest_web_search"].default is False
    assert CompanionConfig().first_look is False
    assert FeaturesConfig().guest_web_search is False


def test_the_switch_fails_closed_on_unbooted_settings(monkeypatch):
    from core.agent import first_look, quiet_choice

    real = first_look._enabled_impl
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert real() is False

    def boom():
        raise RuntimeError("settings down")

    monkeypatch.setattr("core.turn.companion_flags.companion_settings", boom)
    assert real() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace())
    assert real() is False


def test_the_look_needs_she_decides_on(monkeypatch):
    from core.agent import first_look, quiet_choice

    real = first_look._enabled_impl
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=True, first_look=True))
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: False)
    assert real() is False
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    assert real() is True
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=True, first_look=False))
    assert real() is False


def test_the_look_reads_she_decides_from_the_settings_too(monkeypatch):
    """No pin in the way: both read the same live companion block."""
    from core.agent import first_look, quiet_choice

    monkeypatch.setattr(quiet_choice, "she_decides_enabled",
                        quiet_choice._she_decides_enabled_impl)
    real = first_look._enabled_impl
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=False, first_look=True))
    assert real() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=True, first_look=True))
    assert real() is True


def test_the_suite_holds_the_look_off(monkeypatch):
    """Its own pin, not the she_decides pin's shadow: with she_decides let
    through and both settings on, the look is still off."""
    from core.agent import first_look, quiet_choice

    assert first_look.enabled() is False
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=True, first_look=True))
    assert first_look.enabled() is False
    assert first_look._enabled_impl() is True


@pytest.mark.asyncio
async def test_the_golden_still_passes_with_both_switches_off(tmp_path, monkeypatch):
    from core.agent import first_look, quiet_choice
    from tests.agent import she_decides_golden as golden

    assert quiet_choice.she_decides_enabled() is False
    assert first_look.enabled() is False
    now = await golden.capture(tmp_path / "sessions", monkeypatch)
    assert golden.dumps(now) == golden.GOLDEN_PATH.read_text(encoding="utf-8")
    expected = json.loads(golden.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert now == expected
