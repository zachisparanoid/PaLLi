from __future__ import annotations

from ai.proactive_policy import get_user_frequency_settings


def test_get_user_frequency_settings_reduces_outreach_when_baseline_stress_high() -> None:
    settings = get_user_frequency_settings(
        {"preferences": {"check_in_frequency": "high"}},
        None,
        default_max_frequency_hours=12,
        default_daily_cap=2,
        default_daily_min=1,
        emotional_state={
            "baseline": {"stress": 0.82},
            "confidence": 0.85,
        },
    )

    assert settings["min_gap_seconds"] >= 24 * 3600
    assert settings["daily_cap"] == 1
    assert settings["daily_min"] == 0
