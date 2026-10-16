# tests/core/test_settings_surprise_knobs.py
"""Surprise (organ 8) + recall-bias knobs (spec §8): flags ship OFF; budgets
are code. surprise_daily_named 0 = NEVER named (outside the 0-unlimited
convention, like the arbiter ceiling — a naming budget must never
fat-finger to infinity)."""
import pytest
from pydantic import ValidationError

from core.settings_schema import CompanionConfig


def test_defaults_ship_off_and_measured():
    c = CompanionConfig()
    assert c.surprise is False
    assert c.surprise_daily_named == 1
    assert c.surprise_salience_boost == 1.5
    assert c.surprise_salience_minutes == 60
    assert c.moment_recall_bias is False
    assert c.recall_bias_boost == 1.15


def test_budget_bounds():
    assert CompanionConfig(surprise_daily_named=0).surprise_daily_named == 0
    with pytest.raises(ValidationError):
        CompanionConfig(surprise_daily_named=-1)
    with pytest.raises(ValidationError):
        CompanionConfig(surprise_salience_boost=0.9)   # ge=1.0: a boost never demotes
    with pytest.raises(ValidationError):
        CompanionConfig(recall_bias_boost=0.5)
    with pytest.raises(ValidationError):
        CompanionConfig(surprise_salience_minutes=0)   # ge=1: a window is a clock


def test_seed_yaml_carries_all_six():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "data" / "palli.yaml").read_text(encoding="utf-8")
    for key in ("surprise:", "surprise_daily_named:", "surprise_salience_boost:",
                "surprise_salience_minutes:", "moment_recall_bias:", "recall_bias_boost:"):
        assert key in text, key
