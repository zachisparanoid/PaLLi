from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai.ambient_generator import ambient_generator
from ai.ambient_identity_context import build_ambient_identity_snapshot


def _doc(frontmatter: dict[str, object], excerpt: str) -> SimpleNamespace:
    return SimpleNamespace(frontmatter=frontmatter, excerpt=excerpt)


def _compiled_identity() -> SimpleNamespace:
    return SimpleNamespace(
        text_snapshot=SimpleNamespace(
            documents={
                "core_identity": _doc(
                    {"doc_type": "core_identity"},
                    "PaLLi is a calm local companion-operator.",
                ),
                "operating_principles": _doc(
                    {"initiative_style": "beneficial_autonomy"},
                    "Stay grounded, useful, and trustworthy.",
                ),
                "personality_profile": _doc(
                    {
                        "tone": "gentle_companion",
                        "warmth": "high",
                        "proactivity_style": "calm_but_present",
                        "relationship_style": "trusted_friend_assistant",
                        "response_length": "concise",
                        "voice_style": "warm_natural_companion_like",
                    },
                    "Warm, gentle, and companion-like.",
                ),
            }
        ),
        voice_snapshot=SimpleNamespace(
            documents={
                "voice_identity": _doc(
                    {
                        "voice_presence": "warm_companion_like",
                        "reply_style": "concise_and_spoken",
                    },
                    "Voice should feel warm and naturally spoken.",
                ),
                "voice_preferences": _doc(
                    {
                        "interaction_mode": "direct_address",
                        "presence_profile": "balanced",
                        "focus_state": "responsive",
                    },
                    "Balanced presence with responsive focus.",
                ),
            }
        ),
        action_snapshot=SimpleNamespace(
            documents={
                "capabilities_and_boundaries": _doc(
                    {"doc_type": "capabilities_and_boundaries"},
                    "Stay bounded and helpful.",
                ),
                "action_preferences": _doc(
                    {
                        "initiative_level": "high",
                        "social_context_sensitivity": "high",
                        "quiet_hours_mode": "gentle",
                        "presence_awareness_mode": "arrival_only",
                        "arrival_useful_context_policy": "one_item",
                        "ambient_voice_output_policy": "local_speaker",
                        "ambient_discord_delivery_policy": "dm_only",
                    },
                    "Be gently present on arrival without overdoing it.",
                ),
                "relationship_state": _doc(
                    {
                        "relationship_phase": "trusted_companion",
                        "trust_level": "high",
                        "communication_rhythm": "ongoing",
                        "current_posture": "warm_supportive_present",
                    },
                    "Warm, supportive, and present.",
                ),
                "memory_summary": _doc(
                    {
                        "summary_focus": "goals_open_loops",
                        "memory_summary_text": "Open loops and current projects matter most.",
                    },
                    "Open loops and current projects matter most.",
                ),
                "routines": _doc(
                    {
                        "proactive_cadence": "adaptive",
                        "followthrough_style": "quiet_reliable",
                        "review_cadence": "continuous",
                    },
                    "Quiet reliable follow-through around open loops.",
                ),
            }
        ),
        action_policy=SimpleNamespace(
            initiative_level="high",
            social_context_sensitivity="high",
            quiet_hours_mode="gentle",
            presence_awareness_mode="arrival_only",
            arrival_useful_context_policy="one_item",
            ambient_voice_output_policy="local_speaker",
            ambient_discord_delivery_policy="dm_only",
        ),
    )


def test_build_ambient_identity_snapshot_materializes_arrival_posture() -> None:
    snapshot = build_ambient_identity_snapshot(_compiled_identity())

    assert "Creator-Owned Stable Layers" in snapshot.prompt_context
    assert "User-Authored Preference Layers" in snapshot.prompt_context
    assert "Agent-Maintained Evolving Layers" in snapshot.prompt_context
    assert "Derived Ambient Arrival Posture" in snapshot.prompt_context
    assert snapshot.arrival_plan.greeting_tone == "gentle"
    assert snapshot.arrival_plan.spoken_brevity == "tight"
    assert snapshot.arrival_plan.useful_context_policy == "one_item"
    assert snapshot.arrival_plan.tracked_item_style == "quiet_tracking"
    assert snapshot.arrival_plan.attention_item_style == "calm_attention"
    assert snapshot.arrival_plan.voice_presence == "warm_companion_like"
    assert "arrival_useful_context_policy=one_item" in snapshot.derived_posture_context
    assert "greeting_tone=gentle" in snapshot.derived_posture_context
    assert "tracked_item_style=quiet_tracking" in snapshot.derived_posture_context
    assert "attention_item_style=calm_attention" in snapshot.derived_posture_context


def test_build_ambient_identity_snapshot_defaults_cleanly_without_identity() -> None:
    snapshot = build_ambient_identity_snapshot(None)

    assert snapshot.prompt_context == ""
    assert snapshot.arrival_plan.greeting_tone == "warm"
    assert snapshot.arrival_plan.useful_context_policy == "one_item"
    assert snapshot.arrival_plan.tracked_item_style == "quiet_tracking"
    assert snapshot.arrival_plan.attention_item_style == "calm_attention"


@pytest.mark.asyncio
async def test_generate_arrival_bundle_uses_ambient_identity_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_compile_identity(**kwargs):
        captured["compile_kwargs"] = kwargs
        return _compiled_identity()

    async def fake_chat_completion(messages, **kwargs):
        captured["messages"] = messages
        captured["chat_kwargs"] = kwargs
        return (
            '{"spoken_text":"Welcome back.","dashboard_text":"Welcome back. One thing: review the draft.",'
            '"discord_dm_text":"Welcome back. Review the draft.",'
            '"useful_item_type":"workflow_followthrough","mirror_reason":"identity_guided"}'
        )

    monkeypatch.setattr(
        "ai.ambient_generator.identity_service.compile_identity",
        fake_compile_identity,
    )
    monkeypatch.setattr("ai.ambient_generator.llm.chat_completion", fake_chat_completion)

    bundle = await ambient_generator.generate_arrival_bundle(
        owner_user_id=7,
        event_type="owner_arrived",
        context_snapshot={
            "privacy_state": "private",
            "quiet_hours_active": False,
            "machine_state": "active",
        },
        delivery_surfaces=["dashboard", "local_voice"],
        useful_item={"type": "workflow_followthrough", "text": "review the draft"},
        reply_posture="practical_operator",
        style_notes="Keep it calm.",
    )

    assert captured["compile_kwargs"] == {
        "user_id": 7,
        "platform": "owner",
        "ensure_materialized": True,
        "identity_owner_id": 7,
    }
    system_messages = [
        message["content"]
        for message in captured["messages"]
        if message.get("role") == "system"
    ]
    user_messages = [
        message["content"]
        for message in captured["messages"]
        if message.get("role") == "user"
    ]
    assert any(
        "Your ambient arrival identity context:" in content
        and "Derived Ambient Arrival Posture" in content
        for content in system_messages
    )
    assert len(user_messages) == 1
    assert "- useful_item_attention_state: attention_needed" in user_messages[0]
    assert "- useful_item_subject: review the draft" in user_messages[0]
    assert "- useful_item_delivery_style: calm_attention" in user_messages[0]
    assert bundle["spoken_text"] == "Welcome back."
    assert bundle["useful_item_type"] == "workflow_followthrough"


@pytest.mark.asyncio
async def test_generate_arrival_bundle_threads_useful_item_attention_guidance(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "ai.ambient_generator.prompt_loader.load_prompt",
        lambda key: (
            "Attention: {useful_item_attention_state}\n"
            "Subject: {useful_item_subject}\n"
            "Delivery: {useful_item_delivery_style}\n"
            "Guidance: {useful_item_guidance}"
        ),
    )

    async def fake_compile_identity(**_kwargs):
        return _compiled_identity()

    async def fake_chat_completion(messages, **kwargs):
        captured["messages"] = messages
        captured["chat_kwargs"] = kwargs
        return (
            '{"spoken_text":"Welcome back.","dashboard_text":"Welcome back.",'
            '"discord_dm_text":"Welcome back.","useful_item_type":"workflow_followthrough",'
            '"mirror_reason":"attention_guided"}'
        )

    monkeypatch.setattr(
        "ai.ambient_generator.identity_service.compile_identity",
        fake_compile_identity,
    )
    monkeypatch.setattr("ai.ambient_generator.llm.chat_completion", fake_chat_completion)

    await ambient_generator.generate_arrival_bundle(
        owner_user_id=7,
        event_type="owner_arrived",
        context_snapshot={
            "privacy_state": "private",
            "quiet_hours_active": False,
            "machine_state": "active",
        },
        delivery_surfaces=["dashboard"],
        useful_item={
            "type": "workflow_followthrough",
            "text": 'Reminder: "Check the external validation service." Scheduled for later.',
            "attention_state": "tracking",
            "subject_text": "Check the external validation service.",
        },
    )

    user_messages = [
        message["content"]
        for message in captured["messages"]
        if message.get("role") == "user"
    ]
    assert len(user_messages) == 1
    assert "Attention: tracking" in user_messages[0]
    assert "Subject: Check the external validation service." in user_messages[0]
    assert "Delivery: quiet_tracking" in user_messages[0]
    assert "calm background tracking" in user_messages[0]


@pytest.mark.asyncio
async def test_generate_arrival_bundle_fallback_uses_tracking_language(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai.ambient_generator.prompt_loader.load_prompt",
        lambda key: None,
    )

    bundle = await ambient_generator.generate_arrival_bundle(
        owner_user_id=7,
        event_type="owner_arrived",
        context_snapshot={},
        delivery_surfaces=["dashboard"],
        useful_item={
            "type": "workflow_followthrough",
            "text": 'Reminder: "Check the external validation service." Scheduled for later.',
            "attention_state": "tracking",
            "subject_text": "Check the external validation service.",
        },
    )

    assert (
        bundle["spoken_text"]
        == "Hey. Welcome back. I'm already keeping track of this for later: Check the external validation service."
    )


@pytest.mark.asyncio
async def test_generate_arrival_bundle_fallback_uses_attention_needed_language(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai.ambient_generator.prompt_loader.load_prompt",
        lambda key: None,
    )

    bundle = await ambient_generator.generate_arrival_bundle(
        owner_user_id=7,
        event_type="owner_arrived",
        context_snapshot={},
        delivery_surfaces=["dashboard"],
        useful_item={
            "type": "workflow_followthrough",
            "text": "The operator workflow still needs your decision.",
            "attention_state": "attention_needed",
            "subject_text": "The operator workflow still needs your decision.",
        },
    )

    assert (
        bundle["spoken_text"]
        == "Hey. Welcome back. One thing to keep in view: The operator workflow still needs your decision."
    )
