from __future__ import annotations

from ai.proactive_policy import can_surface_proactive_topic


def test_can_surface_proactive_topic_blocks_taboo_and_sensitive_topics() -> None:
    assert can_surface_proactive_topic({"state": "taboo"}, reason="proactive") is False
    assert can_surface_proactive_topic({"state": "sensitive"}, reason="proactive") is False
    assert can_surface_proactive_topic({"state": "sensitive"}, reason="user_reopened") is True
    assert can_surface_proactive_topic(None, reason="proactive") is True
