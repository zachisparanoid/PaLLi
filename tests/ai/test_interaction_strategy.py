from __future__ import annotations

from ai.interaction_strategy import InteractionStrategy, strategy_allows_outward_action


def _strategy(action: str) -> InteractionStrategy:
    return InteractionStrategy(
        action=action,  # type: ignore[arg-type]
        initiative="user",
        floor_owner="user",
        confidence=0.9,
        reason="test",
        generation_directive="",
    )


def test_strategy_allows_outward_action_reads_a_strategy_and_a_mapping() -> None:
    """The voice interjection gate's one question (Batch 8 kept this, the
    planner went): stay_quiet and deflect hold her back, anything else lets
    her speak, and no strategy at all never blocks."""
    for action in ("answer", "soft_probe", "resume_thread", "propose_next_step"):
        assert strategy_allows_outward_action(_strategy(action)) is True
        assert strategy_allows_outward_action({"action": action}) is True
    for action in ("stay_quiet", "deflect"):
        assert strategy_allows_outward_action(_strategy(action)) is False
        assert strategy_allows_outward_action({"action": action.upper()}) is False
    assert strategy_allows_outward_action(None) is True
