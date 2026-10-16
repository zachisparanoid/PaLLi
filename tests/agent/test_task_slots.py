"""Step 27 Phase B — per-channel task slots (her_time, generalized) + the talk lane."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_schema_knobs_default_and_floor():
    from core.settings_schema import CompanionConfig
    c = CompanionConfig()
    assert c.duplex_max_tasks == 2 and c.duplex_pause_ttl_minutes == 120
    with pytest.raises(Exception):
        CompanionConfig(duplex_max_tasks=0)


def test_cap_two_then_full_then_release():
    from core.agent import task_slots as ts
    ts.reset_for_tests()
    with patch.object(ts, "cap", lambda: 2):
        assert ts.acquire("discord:1", "the run") is True
        assert ts.acquire("discord:1", "sidecar-1") is True
        assert ts.acquire("discord:1", "sidecar-2") is False          # full, never blocks
        assert ts.held("discord:1") == ["the run", "sidecar-1"]
        ts.release("discord:1", "the run")
        assert ts.acquire("discord:1", "sidecar-2") is True
        assert ts.held("discord:2") == []                              # per channel


def test_release_of_unknown_label_is_harmless():
    from core.agent import task_slots as ts
    ts.reset_for_tests()
    ts.release("discord:9", "never-held")
    assert ts.held("discord:9") == []


def test_talk_lane_is_one_at_a_time_and_never_counts_against_the_cap():
    """Correction 1: a conversation is not a task. Two tasks may fill the
    house and he can still talk to her full self; a second talk while one
    runs waits (falls back to the ordinary lane)."""
    from core.agent import task_slots as ts
    ts.reset_for_tests()
    with patch.object(ts, "cap", lambda: 2):
        assert ts.acquire("discord:1", "the run") and ts.acquire("discord:1", "sidecar-1")
        assert ts.talk_held("discord:1") is False
        assert ts.talk_acquire("discord:1") is True                    # house full, talk still opens
        assert ts.talk_held("discord:1") is True
        assert ts.talk_acquire("discord:1") is False                   # one at a time
        assert ts.held("discord:1") == ["the run", "sidecar-1"]        # the cap untouched
        ts.talk_release("discord:1")
        assert ts.talk_held("discord:1") is False
        ts.talk_release("discord:1")                                   # harmless twice
