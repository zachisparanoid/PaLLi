# tests/core/test_settings_fears_knobs.py
"""Fears (organ 6, step 24) knobs (spec §7): the flag ships OFF in schema
(the owner's yaml flips it); a boost never demotes; a window is a clock;
the held cap is never a second off-switch. The organ registers NO tools —
the registry is byte-identical either way (spec §9)."""
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.settings_schema import CompanionConfig


def test_defaults_ship_off_and_measured():
    c = CompanionConfig()
    assert c.fears is False
    assert c.fear_salience_boost == 1.5
    assert c.fear_salience_minutes == 60
    assert c.fears_held_cap == 5


def test_bounds():
    with pytest.raises(ValidationError):
        CompanionConfig(fear_salience_boost=0.9)   # ge=1.0: never demotes
    with pytest.raises(ValidationError):
        CompanionConfig(fear_salience_minutes=0)   # ge=1: a window is a clock
    with pytest.raises(ValidationError):
        CompanionConfig(fears_held_cap=0)          # ge=1: the flag is the off-switch


def test_seed_yaml_carries_all_four():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "data" /
            "palli.yaml").read_text(encoding="utf-8")
    for key in ("fears:", "fear_salience_boost:", "fear_salience_minutes:",
                "fears_held_cap:"):
        assert key in text, key


def test_registry_untouched_by_the_flag():
    from core.agent.builtin_tools import build_default_registry

    baseline = build_default_registry().all_names()
    with patch("ai.fear_store.fears_enabled", return_value=True):
        assert build_default_registry().all_names() == baseline
