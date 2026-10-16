"""Step 27 Phase A — the flag, the knobs, the block, the guard."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_schema_defaults_off_with_knob_floors():
    from core.settings_schema import CompanionConfig
    c = CompanionConfig()
    assert c.duplex is False
    assert c.duplex_overrun_factor == 1.5
    assert c.duplex_overrun_minutes_default == 10
    assert c.duplex_stop_grace_seconds == 30
    with pytest.raises(Exception):
        CompanionConfig(duplex_overrun_factor=1.0)        # gt=1.0
    with pytest.raises(Exception):
        CompanionConfig(duplex_stop_grace_seconds=4)      # ge=5


def test_guard_holds_flag_off_in_bare_pytest():
    from core.agent import duplex as dx
    assert dx.duplex_enabled() is False


def test_block_is_zero_bytes_when_off_and_law_when_on():
    from core.agent import duplex as dx
    assert dx.render_duplex_block() == ""
    with patch.object(dx, "duplex_enabled", lambda: True):
        block = dx.render_duplex_block()
    assert block.startswith("## When I'm working")
    assert "chime_in" in block and "ask_him" in block and "expect_minutes" in block
    assert "whenever and however I choose" in block   # the owner, 10-12: when and how to speak is hers
    assert dx.CHIME_MAX_CHARS == 300
