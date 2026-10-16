"""Wanting knobs (re-spec §9): defaults + the settled conventions.

ge=1 on heartbeat/gap/capacity (lower, don't zero); ge=0 on the
budget-shaped daily max (0 = unlimited; the flag is the off switch).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.settings_schema import CompanionConfig


def test_wanting_defaults_ship_off_and_sparse():
    c = CompanionConfig()
    assert c.wanting is False
    assert c.daydream_heartbeat_minutes == 180
    assert c.daydream_min_gap_minutes == 45
    assert c.daydream_daily_max == 8
    assert c.quests_max_alive == 7
    assert c.quests_from_conversation is True
    assert c.curiosity_nas_dives is False


@pytest.mark.parametrize("field", [
    "daydream_heartbeat_minutes", "daydream_min_gap_minutes", "quests_max_alive",
])
def test_clock_and_capacity_knobs_reject_zero(field):
    with pytest.raises(ValidationError):
        CompanionConfig(**{field: 0})


def test_daily_max_accepts_zero_as_unlimited():
    assert CompanionConfig(daydream_daily_max=0).daydream_daily_max == 0


# ── own_work_share_delivery (2026-08-18) ────────────────────────────────
def test_share_delivery_defaults_to_a_message_and_rejects_strangers():
    c = CompanionConfig()
    assert c.own_work_share_delivery == "message"
    for ok in ("message", "thread", "room"):
        assert CompanionConfig(own_work_share_delivery=ok).own_work_share_delivery == ok
    with pytest.raises(ValidationError):
        CompanionConfig(own_work_share_delivery="pigeon")


def test_seed_yaml_carries_the_share_delivery_knob():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "data" / "palli.yaml").read_text(encoding="utf-8")
    assert "own_work_share_delivery:" in text


# ── stake_ledger_stale_days (2026-08-18) ────────────────────────────────
def test_stake_stale_days_defaults_and_accepts_zero_as_off():
    c = CompanionConfig()
    assert c.stake_ledger_stale_days == 45
    assert CompanionConfig(stake_ledger_stale_days=0).stake_ledger_stale_days == 0
    with pytest.raises(ValidationError):
        CompanionConfig(stake_ledger_stale_days=-1)


def test_seed_yaml_carries_the_stake_stale_knob():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "data" / "palli.yaml").read_text(encoding="utf-8")
    assert "stake_ledger_stale_days:" in text
