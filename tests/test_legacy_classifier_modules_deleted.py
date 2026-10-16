"""After E.3 Wave 2, the 10 classifier-output modules must not be importable.

E.3 (Phase E, 2026-05-13) deleted 13 classifier-output tables (~700 rows
across all 13; last writes 2026-04-30 to 2026-05-02) and the 9 store
modules + behavior_control_service that wrote/read them. The cognitive
decisions these tables tracked are now LLM-judged per-turn via
core/turn/decide.py BehaviorFrame.
"""
import pytest


@pytest.mark.parametrize("module", [
    "core.behavior_trace_store",
    "core.pragmatic_perception_store",
    "core.interaction_mode_store",
    "core.emotional_state_store",
    "core.topic_boundary_store",
    "core.relationship_calibration_store",
    "core.behavior_control_store",
    "core.behavior_control_service",
    "core.theory_of_mind_store",
    "core.interruption_stack_store",
    # Great Excision stages 2-3 (2026-07-09): the bounded-reflection engine,
    # the fact-contradiction auditor, and the second-brain husk layer
    # (Direction-C casualties; ran in prod only 2026-04-30 to 2026-05-02).
    # Ideas live on in caring memory, the stake ledger, momentum, and the
    # proactive rail. Kept voice-deferral exceptions (NOT in this list):
    # ai.interaction_strategy and ai.reply_posture, cut in Batch 8 to the
    # halves voice uses; ai.relationship_calibration and ai.tonal_register
    # went in Batch 8 (tests/architecture/test_dead_modules_stay_dead.py).
    "ai.bounded_reflection",
    "ai.fact_contradiction_auditor",
    "ai.emotional_state",
    "ai.theory_of_mind",
    "ai.interaction_mode",
    "ai.linguistic_alignment",
    "ai.topic_boundaries",
    "ai.pragmatic_inquiry",
    "ai.pragmatic_perception",
    "ai.proactive_memory_analyzer",
    "ai.proactive_generation_execution",
    "ai.user_profile_memory",
    "ai.identity_memory",
    "ai.user_fact_memory",
    "core.mcp_palli_context",
    "core.memory_cooldown",
])
def test_legacy_classifier_module_deleted(module):
    with pytest.raises(ImportError):
        __import__(module)
