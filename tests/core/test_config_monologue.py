from __future__ import annotations

from core.config import config


def test_monologue_stall_threshold_hours_default_is_24():
    """Merge 5 uses this to decide when an active workflow is 'stalled'."""
    assert hasattr(config, "MONOLOGUE_STALL_THRESHOLD_HOURS")
    assert int(config.MONOLOGUE_STALL_THRESHOLD_HOURS) == 24
