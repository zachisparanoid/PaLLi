from __future__ import annotations

from datetime import datetime

import pytest

from ai.llm_decision import (
    DecisionPromptContext,
    build_combined_decision_messages,
    build_should_respond_fallback,
    build_should_respond_messages,
    parse_combined_decision_response,
    parse_should_respond_response,
)


def test_build_should_respond_messages_renders_dm_prompt_context() -> None:
    messages = build_should_respond_messages(
        "User={username}\nChannel={channel_name}\nDM={is_dm}\nCriteria={decision_criteria}\n"
        "Profile={user_profile}\nHistory={conversation_history}\nMessage={current_message}",
        context=DecisionPromptContext(
            user_message="Hey PaLLi",
            history_context="User: Earlier question",
            user_profile_context="Preferred name: Sam",
            username="Sam",
            platform="discord",
            channel_name="direct",
            is_dm=True,
        ),
    )

    assert messages[0]["role"] == "system"
    assert "strict JSON only" in messages[0]["content"]
    assert "User=Sam" in messages[1]["content"]
    assert "DM=True" in messages[1]["content"]
    assert "Criteria=Direct message - typically should respond unless clearly rhetorical" in messages[1]["content"]
    assert "Profile=Preferred name: Sam" in messages[1]["content"]
    assert "History=User: Earlier question" in messages[1]["content"]
    assert "Message=Hey PaLLi" in messages[1]["content"]


def test_build_combined_decision_messages_includes_time_context() -> None:
    messages = build_combined_decision_messages(
        "Time={current_time_context}\nDay={current_day}\nDate={current_date}\nMessage={current_message}",
        context=DecisionPromptContext(
            user_message="What changed today?",
            history_context="No history",
            user_profile_context="No profile",
        ),
        current_time_context="1:30 PM local",
        now=datetime(2026, 4, 1, 13, 30),
    )

    assert messages[0]["role"] == "system"
    assert "web search" in messages[0]["content"]
    assert "Time=1:30 PM local" in messages[1]["content"]
    assert "Day=Wednesday" in messages[1]["content"]
    assert "Date=April 01, 2026" in messages[1]["content"]
    assert "Message=What changed today?" in messages[1]["content"]


def test_parse_should_respond_response_normalizes_legacy_reason_and_length_bucket() -> None:
    result = parse_should_respond_response(
        '{"confidence":"0.7","length_bucket":"MEDIUM","reason":"user asked directly"}'
    )

    assert result["should_respond"] is True
    assert result["confidence"] == 0.7
    assert result["length_bucket"] == "medium"
    assert result["reasoning"] == "user asked directly"


def test_build_should_respond_fallback_uses_safe_defaults() -> None:
    result = build_should_respond_fallback("No response from LLM")

    assert result == {
        "should_respond": True,
        "confidence": 0.5,
        "length_bucket": "short",
        "reason": "No response from LLM",
        "reasoning": "No response from LLM",
    }


def test_parse_combined_decision_response_normalizes_aliases_and_agent_defaults() -> None:
    result = parse_combined_decision_response(
        (
            '{"should_respond":"true","response_confidence":"0.82","length_bucket":"LONG",'
            '"response_reasoning":"needs a crafted reply","should_search":false,'
            '"intent":"creative_writing","intent_confidence":"bad",'
            '"execution_path":"agent_loop","agent_goal":"","agent_confidence":"2.4",'
            '"agent_reasoning":"","semantic_routing":{"agent_request":true,"reply_posture_hint":"social_script"},'
            '"extracted_info":{"topics":["poetry"]}}'
        ),
        user_message="write me a poem",
    )

    assert result["should_respond"] is True
    assert result["response_confidence"] == 0.82
    assert result["length_bucket"] == "long"
    assert result["intent"] == "creative_request"
    assert result["intent_confidence"] == 0.82
    assert result["execution_path"] == "agent_loop"
    assert result["agent_goal"] == "write me a poem"
    assert result["agent_confidence"] == 1.0
    assert result["agent_reasoning"] == "No execution-path reasoning provided."
    assert result["semantic_routing"] == {
        "agent_request": True,
        "operator_request": False,
        "operator_intent": "none",
        "workflow_dialogue_intent": "none",
        "reply_posture_hint": "social_script",
        "reminder_explicitness": "none",
        "search_recency": "stable",
        "settings_change_request": False,
    }
    assert result["extracted_info"] == {"topics": ["poetry"]}


def test_parse_combined_decision_response_suppresses_search_when_not_responding() -> None:
    result = parse_combined_decision_response(
        (
            '{"should_respond":false,"response_confidence":0.2,"response_reasoning":"not directed at PaLLi",'
            '"should_search":true,"search_confidence":0.9,"search_query":"latest weather",'
            '"search_reasoning":"freshness required","intent":"mystery_intent"}'
        ),
        user_message="they were not talking to you",
    )

    assert result["should_respond"] is False
    assert result["should_search"] is False
    assert result["search_query"] == ""
    assert result["intent"] == "general_conversation"
    assert result["intent_reasoning"] == "not directed at PaLLi"
    assert result["semantic_routing"]["reply_posture_hint"] == "brief_companion"


def test_parse_combined_decision_response_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="Invalid JSON in combined decision response"):
        parse_combined_decision_response("{", user_message="hello")
