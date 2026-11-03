"""What an interaction strategy allows: the voice interjection gate's one question.

voice/voice_interjection_analyzer.py asks strategy_allows_outward_action
whether a strategy lets her speak up. The planner that built strategies here
(plan_interaction_strategy and its restraint, summary and serializer
helpers) had no caller left and was deleted in Batch 8 (2026-10-01).
InteractionStrategy stays: the check accepts one as well as a mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


InteractionStrategyAction = Literal[
    "answer",
    "ask_clarifying",
    "stay_quiet",
    "resume_thread",
    "soft_probe",
    "push_back",
    "deflect",
    "propose_next_step",
    "execute_authorized_step",
]
InteractionInitiative = Literal["user", "palli", "shared"]
FloorOwner = Literal["user", "palli", "shared", "unclear"]


@dataclass(frozen=True, slots=True)
class InteractionStrategy:
    action: InteractionStrategyAction
    initiative: InteractionInitiative
    floor_owner: FloorOwner
    confidence: float
    reason: str
    generation_directive: str
    requires_user_confirmation: bool = False


def strategy_allows_outward_action(strategy: InteractionStrategy | Mapping[str, Any] | None) -> bool:
    if isinstance(strategy, InteractionStrategy):
        action = strategy.action
    elif isinstance(strategy, Mapping):
        action = str(strategy.get("action") or "").strip().lower()
    else:
        return True
    return action not in {"stay_quiet", "deflect"}
