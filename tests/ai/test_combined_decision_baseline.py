"""
Regression tests locking in parse_combined_decision_response output
before the Phase 9 delegation-tier extension.

If these fail after the combined_decision prompt change, the parser
needs a backward-compat default for the missing field.
"""
from __future__ import annotations

import json

import pytest

from ai.llm_decision import parse_combined_decision_response


def test_parse_combined_decision_full_example():
    """Locks in the current field surface before the delegation_tier extension."""
    response = json.dumps({
        "should_respond": True,
        "response_confidence": 0.9,
        "response_reasoning": "direct question",
        "length_bucket": "short",
        "intent": "question_answering",
        "intent_confidence": 0.9,
        "intent_reasoning": "user asked",
        "execution_path": "pipeline",
        "extracted_info": {},
        "agent_goal": None,
        "agent_confidence": 0.0,
        "agent_reasoning": "No execution-path reasoning provided.",
        "semantic_routing": {
            "agent_request": False,
            "operator_request": False,
            "operator_intent": "none",
            "workflow_dialogue_intent": "none",
            "reply_posture_hint": "social_script",
            "reminder_explicitness": "none",
            "search_recency": "stable",
        },
    })
    result = parse_combined_decision_response(response, user_message="hi")
    assert result["should_respond"] is True
    assert result["intent"] == "question_answering"
    assert result["execution_path"] == "pipeline"
    assert "semantic_routing" in result


def test_parse_combined_decision_minimal_response():
    """When response omits optional fields, parser uses documented defaults."""
    response = json.dumps({
        "should_respond": False,
        "response_reasoning": "chit-chat",
    })
    result = parse_combined_decision_response(response, user_message="lol")
    assert result["should_respond"] is False
    assert result["should_search"] is False


def test_parse_combined_decision_includes_delegation_defaults_when_absent():
    """When LLM omits delegation fields, parser defaults them safely."""
    response = json.dumps({"should_respond": True, "response_reasoning": "chat"})
    result = parse_combined_decision_response(response, user_message="hi")
    assert result["delegation_tier"] == 0
    assert result["delegation_task_hint"] == ""
    assert result["delegation_autonomy_context"] == "user_request"
    assert result["delegation_followup_reference"] is None


def test_parse_combined_decision_preserves_delegation_fields_when_present():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "find east coast laptops and send snapshots",
        "delegation_autonomy_context": "user_request",
        "delegation_followup_reference": None,
    })
    result = parse_combined_decision_response(response, user_message="send laptops")
    assert result["delegation_tier"] == 2
    assert result["delegation_task_hint"] == "find east coast laptops and send snapshots"
    assert result["delegation_autonomy_context"] == "user_request"
    assert result["delegation_followup_reference"] is None


def test_parse_combined_decision_rejects_invalid_delegation_tier():
    """Invalid tier values default to 0 safely."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "chat",
        "delegation_tier": 99,
    })
    result = parse_combined_decision_response(response, user_message="hi")
    assert result["delegation_tier"] == 0


def test_parse_combined_decision_preserves_followup_reference_when_string():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "asking about active delegation",
        "delegation_followup_reference": "deleg_abc123",
    })
    result = parse_combined_decision_response(response, user_message="how's it going")
    assert result["delegation_followup_reference"] == "deleg_abc123"


# ─── Fix #2: tier-2 normalization ─────────────────────────────────────
# When the LLM sets delegation_tier=2, it should not simultaneously set
# operator/settings/agent semantic flags — those are for the in-house
# routing rungs and tier=2 means "hand off to Claude Code instead."
# The parser normalizes (zeros out) the conflicting flags so downstream
# code doesn't have to disambiguate.


def test_parse_tier_2_clears_operator_request_flag():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "analyze the palli repo",
        "semantic_routing": {
            "operator_request": True,
            "operator_intent": "workspace_operator",
            "workflow_dialogue_intent": "none",
        },
    })
    result = parse_combined_decision_response(response, user_message="analyze code")
    assert result["delegation_tier"] == 2
    assert result["semantic_routing"]["operator_request"] is False
    assert result["semantic_routing"]["operator_intent"] == "none"


def test_parse_tier_2_clears_settings_change_request_flag():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "find east coast laptops",
        "semantic_routing": {
            "settings_change_request": True,
        },
    })
    result = parse_combined_decision_response(response, user_message="find laptops")
    assert result["delegation_tier"] == 2
    assert result["semantic_routing"]["settings_change_request"] is False


def test_parse_tier_2_clears_workflow_dialogue_intent():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "research and summarize",
        "semantic_routing": {
            "workflow_dialogue_intent": "status_query",
        },
    })
    result = parse_combined_decision_response(response, user_message="research this")
    assert result["delegation_tier"] == 2
    assert result["semantic_routing"]["workflow_dialogue_intent"] == "none"


def test_parse_tier_2_clears_agent_request_flag():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "browse and collect",
        "execution_path": "agent_loop",
        "semantic_routing": {
            "agent_request": True,
        },
    })
    result = parse_combined_decision_response(response, user_message="go do X")
    assert result["delegation_tier"] == 2
    assert result["semantic_routing"]["agent_request"] is False


def test_parse_tier_2_works_without_semantic_routing_block():
    """Regression: tier=2 with no semantic_routing field should not crash."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_tier": 2,
        "delegation_task_hint": "x",
    })
    result = parse_combined_decision_response(response, user_message="x")
    assert result["delegation_tier"] == 2
    assert result["semantic_routing"]["operator_request"] is False
    assert result["semantic_routing"]["settings_change_request"] is False


def test_parse_tier_0_preserves_operator_flags():
    """Regression: non-tier-2 traffic must keep its semantic_routing
    flags — the normalization is exclusive to tier=2."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "status check on workflow",
        "delegation_tier": 0,
        "semantic_routing": {
            "operator_request": True,
            "operator_intent": "workspace_operator",
            "workflow_dialogue_intent": "status_query",
        },
    })
    result = parse_combined_decision_response(response, user_message="status check")
    assert result["delegation_tier"] == 0
    assert result["semantic_routing"]["operator_request"] is True
    assert result["semantic_routing"]["operator_intent"] == "workspace_operator"
    assert result["semantic_routing"]["workflow_dialogue_intent"] == "status_query"


def test_parse_tier_1_preserves_settings_flag():
    """Regression: tier=1 (in-house single-tool) keeps its flags."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "toggle model",
        "delegation_tier": 1,
        "semantic_routing": {
            "settings_change_request": True,
        },
    })
    result = parse_combined_decision_response(response, user_message="use claude-sonnet please")
    assert result["delegation_tier"] == 1
    assert result["semantic_routing"]["settings_change_request"] is True


# ─── Fix #3: delegation_checklist chain-of-thought scaffolding ────────
# The prompt now asks the LLM to fill in three yes/no characteristics
# (requires_local_access, single_tool_call_suffices,
# benefits_from_ground_truth) BEFORE picking delegation_tier. These are
# exposed in the parser output for observability; they don't drive any
# routing decision yet — just make the LLM's reasoning visible.


def test_parse_delegation_checklist_passes_through_when_present():
    """LLM fills in checklist; parser preserves all three booleans."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": True,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 2,
        "delegation_task_hint": "read the README and summarize",
    })
    result = parse_combined_decision_response(response, user_message="read README")
    assert result["delegation_checklist"] == {
        "requires_multi_step_tools": False,
        "requires_local_access": True,
        "benefits_from_ground_truth": True,
    }


def test_parse_delegation_checklist_defaults_when_absent():
    """Backward compat: LLM on older prompt omits checklist → all false."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "chat",
        "delegation_tier": 0,
    })
    result = parse_combined_decision_response(response, user_message="hi")
    assert result["delegation_checklist"] == {
        "requires_multi_step_tools": False,
        "requires_local_access": False,
        "benefits_from_ground_truth": False,
    }


def test_parse_delegation_checklist_handles_invalid_types():
    """If the LLM returns junk in the checklist, parser coerces safely."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "chat",
        "delegation_checklist": "not even a dict",
        "delegation_tier": 0,
    })
    result = parse_combined_decision_response(response, user_message="hi")
    # When the whole block is junk, defaults apply.
    assert result["delegation_checklist"]["requires_multi_step_tools"] is False
    assert result["delegation_checklist"]["requires_local_access"] is False
    assert result["delegation_checklist"]["benefits_from_ground_truth"] is False


def test_parse_delegation_checklist_coerces_string_booleans():
    """String 'true'/'false' in booleans should coerce, not crash."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_checklist": {
            "requires_multi_step_tools": "true",
            "requires_local_access": "false",
            "benefits_from_ground_truth": 1,
        },
        "delegation_tier": 2,
    })
    result = parse_combined_decision_response(response, user_message="x")
    assert result["delegation_checklist"]["requires_multi_step_tools"] is True
    assert result["delegation_checklist"]["requires_local_access"] is False
    assert result["delegation_checklist"]["benefits_from_ground_truth"] is True


# ─── Parser-enforced tier derivation ──────────────────────────────────
# If the LLM fills in the checklist honestly but then picks a tier
# that doesn't match the derivation rule, the parser must override it.
# Direction of enforcement: tier UP (if checklist implies higher
# tier than LLM chose, override). Never override DOWN.


def test_parser_overrides_tier_1_to_2_when_multi_step_true():
    """Core multi-step-miss case: LLM sets multi_step=true but picks tier=1."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "multi-step web task",
        "delegation_checklist": {
            "requires_multi_step_tools": True,
            "requires_local_access": False,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 1,  # LLM got this wrong
        "delegation_task_hint": "find laptops",
    })
    result = parse_combined_decision_response(
        response, user_message="find laptop deals and send pics"
    )
    assert result["delegation_tier"] == 2
    assert result["delegation_tier_llm_raw"] == 1


def test_parser_overrides_tier_0_to_2_when_local_access_true():
    """README case: LLM sets local_access=true but picks tier=0."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "read local file",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": True,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 0,  # LLM got this wrong (answered from memory)
        "delegation_task_hint": "",
    })
    result = parse_combined_decision_response(
        response, user_message="read README and summarize"
    )
    assert result["delegation_tier"] == 2
    assert result["delegation_tier_llm_raw"] == 0


def test_parser_overrides_tier_0_to_1_when_ground_truth_only():
    """If checklist implies tier=1 but LLM chose tier=0, override up."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "news question",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": False,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 0,
        "delegation_task_hint": "",
    })
    result = parse_combined_decision_response(
        response, user_message="what's the latest kernel version"
    )
    assert result["delegation_tier"] == 1
    assert result["delegation_tier_llm_raw"] == 0


def test_parser_does_not_downgrade_llm_tier_choice():
    """If LLM chose tier=2 but checklist implies tier=0, don't downgrade."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "conservative delegation",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": False,
            "benefits_from_ground_truth": False,
        },
        "delegation_tier": 2,  # LLM chose to delegate anyway
        "delegation_task_hint": "be thorough",
    })
    result = parse_combined_decision_response(response, user_message="explain X")
    # Don't override down — LLM's tier=2 choice stands.
    assert result["delegation_tier"] == 2
    assert result["delegation_tier_llm_raw"] == 2


def test_parser_preserves_agreed_tier_unchanged():
    """When LLM and checklist agree, both fields match."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "chat",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": False,
            "benefits_from_ground_truth": False,
        },
        "delegation_tier": 0,
    })
    result = parse_combined_decision_response(response, user_message="how are you")
    assert result["delegation_tier"] == 0
    assert result["delegation_tier_llm_raw"] == 0


def test_parser_derive_tier_helper_matches_rules():
    """Unit test the four derivation rules of _derive_tier_from_checklist."""
    from ai.llm_decision import _derive_tier_from_checklist
    # Rule 1: multi_step wins
    assert _derive_tier_from_checklist({
        "requires_multi_step_tools": True,
        "requires_local_access": False,
        "benefits_from_ground_truth": False,
    }) == 2
    # Rule 2: local_access wins when multi_step is false
    assert _derive_tier_from_checklist({
        "requires_multi_step_tools": False,
        "requires_local_access": True,
        "benefits_from_ground_truth": False,
    }) == 2
    # Rule 3: ground_truth only
    assert _derive_tier_from_checklist({
        "requires_multi_step_tools": False,
        "requires_local_access": False,
        "benefits_from_ground_truth": True,
    }) == 1
    # Rule 4: all false → tier 0
    assert _derive_tier_from_checklist({
        "requires_multi_step_tools": False,
        "requires_local_access": False,
        "benefits_from_ground_truth": False,
    }) == 0


# ─── Parser tier boundaries ───────────────────────────────────────────
# Message-text semantics belong in LLM decision execution, not parser-level
# phrase or keyword matching. The parser may enforce checklist consistency,
# but it must not infer a tier from the raw user message itself.


def test_parser_does_not_override_tier_from_message_text_when_checklist_misses():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "classifier under-delegated",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": False,
            "benefits_from_ground_truth": False,
        },
        "delegation_tier": 1,
    })
    result = parse_combined_decision_response(
        response,
        user_message="see what laptop deals you can find on the east coast and send me pics",
    )
    assert result["delegation_tier"] == 1
    assert result["delegation_tier_llm_raw"] == 1


def test_parser_ground_truth_only_stays_tier_1_without_text_lookup():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "classifier under-delegated",
        "should_search": True,
        "search_confidence": 0.91,
        "search_query": "latest advancements in renewable energy technologies",
        "delegation_checklist": {
            "requires_multi_step_tools": False,
            "requires_local_access": False,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 0,
    })
    result = parse_combined_decision_response(
        response,
        user_message=(
            "what do you think of palli? (your codebase). look through it and "
            "get a good read on the python files and prompt files."
        ),
    )
    assert result["delegation_tier"] == 1
    assert result["delegation_tier_llm_raw"] == 0
    assert result["should_search"] is True
    assert result["search_query"] == "latest advancements in renewable energy technologies"


def test_parser_does_not_downgrade_tier_2():
    """If tier is already 2, parser enforcement is a no-op (no downgrade)."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "delegate",
        "delegation_checklist": {
            "requires_multi_step_tools": True,
            "requires_local_access": False,
            "benefits_from_ground_truth": False,
        },
        "delegation_tier": 2,
    })
    result = parse_combined_decision_response(
        response, user_message="totally ambiguous thing"
    )
    assert result["delegation_tier"] == 2


def test_parse_delegation_checklist_preserves_multi_step_for_multi_step_like_task():
    """Multi-step-style 'find X and send pics' should set multi_step=true, tier=2."""
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "multi-step web task",
        "delegation_checklist": {
            "requires_multi_step_tools": True,
            "requires_local_access": False,
            "benefits_from_ground_truth": True,
        },
        "delegation_tier": 2,
        "delegation_task_hint": "find laptops and send screenshots",
    })
    result = parse_combined_decision_response(
        response, user_message="find east coast laptop deals and send me pics"
    )
    assert result["delegation_checklist"]["requires_multi_step_tools"] is True
    assert result["delegation_tier"] == 2


# ─── Phase 3: per-turn sentiment refinement ───────────────────────────
# combined_decision now emits a sentiment block. The parser pipes it
# through ai.sentiment.inference.build_reading_from_llm_output so
# invalid tags coerce to neutral, confidence clamps to [0, 1], and
# the shift field normalizes to confirmed|shifted.


def test_parse_combined_decision_extracts_sentiment_field():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "hi",
        "sentiment": {
            "tag": "focused",
            "read": "message reads focused and direct",
            "confidence": 0.7,
            "shift": "confirmed",
        },
    })
    result = parse_combined_decision_response(response, user_message="let's ship")
    s = result.get("sentiment")
    assert s is not None
    assert s["tag"] == "focused"
    assert s["read"] == "message reads focused and direct"
    assert s["confidence"] == pytest.approx(0.7)
    assert s["shift"] == "confirmed"


def test_parse_combined_decision_sentiment_defaults_when_absent():
    response = json.dumps({"should_respond": True, "response_reasoning": "hi"})
    result = parse_combined_decision_response(response, user_message="hi")
    s = result.get("sentiment")
    assert s is not None
    assert s["tag"] == "neutral"
    assert s["confidence"] == 0.0
    assert s["shift"] in ("confirmed", "shifted")


def test_parse_combined_decision_sentiment_invalid_tag_coerces_to_neutral():
    response = json.dumps({
        "should_respond": True,
        "response_reasoning": "hi",
        "sentiment": {"tag": "spicy", "confidence": 0.8},
    })
    result = parse_combined_decision_response(response, user_message="x")
    assert result["sentiment"]["tag"] == "neutral"
    assert result["sentiment"]["confidence"] == 0.0
