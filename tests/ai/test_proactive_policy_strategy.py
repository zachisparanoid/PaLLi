from __future__ import annotations

from ai.proactive_policy import can_surface_proactive_strategy


def test_can_surface_proactive_strategy_allows_selected_outward_actions() -> None:
    assert can_surface_proactive_strategy({"action": "soft_probe"}) is True
    assert can_surface_proactive_strategy({"action": "resume_thread"}) is True
    assert can_surface_proactive_strategy({"action": "propose_next_step"}) is True


def test_can_surface_proactive_strategy_blocks_answer_and_stay_quiet() -> None:
    assert can_surface_proactive_strategy({"action": "answer"}) is False
    assert can_surface_proactive_strategy({"action": "stay_quiet"}) is False
    assert can_surface_proactive_strategy({"action": "deflect"}) is False
