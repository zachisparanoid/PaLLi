from __future__ import annotations

from types import SimpleNamespace

from ai.reply_identity_context import build_reply_identity_snapshot


def _doc(frontmatter: dict[str, object], excerpt: str) -> SimpleNamespace:
    return SimpleNamespace(frontmatter=frontmatter, excerpt=excerpt)


def _compiled_identity() -> SimpleNamespace:
    return SimpleNamespace(
        text_snapshot=SimpleNamespace(
            documents={
                "core_identity": _doc(
                    {"doc_type": "core_identity"},
                    "PaLLi is a steady local companion-operator.",
                ),
                "operating_principles": _doc(
                    {"initiative_style": "beneficial_autonomy"},
                    "Stay clear, useful, and quietly reliable.",
                ),
                "personality_profile": _doc(
                    {
                        "tone": "warm_companion",
                        "warmth": "high",
                        "humor": "light_and_human",
                        "challenge_level": "gentle",
                        "relationship_style": "trusted_friend_assistant",
                        "response_length": "concise",
                        "voice_style": "warm_natural_companion_like",
                    },
                    "Warm, natural, and companion-like.",
                ),
            }
        ),
        voice_snapshot=SimpleNamespace(
            documents={
                "voice_identity": _doc(
                    {
                        "voice_presence": "warm_companion_like",
                        "turn_taking": "natural",
                        "reply_style": "concise_and_spoken",
                    },
                    "Voice should feel warm, brief, and easy to trust.",
                ),
                "voice_preferences": _doc(
                    {
                        "interaction_mode": "direct_address",
                        "presence_profile": "balanced",
                        "focus_state": "responsive",
                        "local_transport_mode": "push_to_talk",
                    },
                    "Balanced presence with responsive focus.",
                ),
            },
            preferences={
                "presence_profile": "balanced",
                "focus_state": "responsive",
            },
        ),
        action_snapshot=SimpleNamespace(
            documents={
                "capabilities_and_boundaries": _doc(
                    {"doc_type": "capabilities_and_boundaries"},
                    "Stay bounded and trustworthy.",
                ),
                "action_preferences": _doc(
                    {
                        "initiative_level": "high",
                        "social_context_sensitivity": "medium",
                        "quiet_hours_mode": "off",
                        "group_voice_behavior": "socially_adaptive",
                        "followthrough_intensity": "steady",
                    },
                    "Act with initiative but stay socially aware.",
                ),
                "relationship_state": _doc(
                    {
                        "relationship_phase": "trusted_companion",
                        "trust_level": "high",
                        "communication_rhythm": "ongoing",
                        "current_posture": "warm_supportive_present",
                        "relationship_summary": "Stay warm and present without becoming clingy.",
                    },
                    "Warm, supportive, and present.",
                ),
                "memory_summary": _doc(
                    {
                        "summary_focus": "goals_preferences_open_loops_emotional_context",
                        "memory_summary_text": "Coding, music, and open loops matter most right now.",
                    },
                    "Coding, music, and open loops matter most right now.",
                ),
                "routines": _doc(
                    {
                        "proactive_cadence": "adaptive",
                        "followthrough_style": "quiet_reliable",
                        "review_cadence": "continuous",
                        "routine_summary": "Keep an eye on open loops without nagging.",
                    },
                    "Quiet reliable follow-through around open loops.",
                ),
            }
        ),
        action_policy=SimpleNamespace(
            initiative_level="high",
            social_context_sensitivity="medium",
            quiet_hours_mode="off",
            group_voice_behavior="socially_adaptive",
        ),
    )


def test_build_reply_identity_snapshot_materializes_cross_layer_context() -> None:
    snapshot = build_reply_identity_snapshot(_compiled_identity())

    assert "Creator-Owned Stable Layers" in snapshot.prompt_context
    assert "User-Authored Preference Layers" in snapshot.prompt_context
    assert "Agent-Maintained Evolving Layers" in snapshot.prompt_context
    assert "Derived Reply/Voice Posture" in snapshot.prompt_context
    assert snapshot.delivery_plan.social_permission == "warm"
    assert snapshot.delivery_plan.voice_pacing == "relaxed"
    assert snapshot.delivery_plan.voice_brevity == "tight"
    assert snapshot.delivery_plan.relationship_posture == "warm_supportive_present"
    assert snapshot.delivery_plan.followthrough_style == "quiet_reliable"
    assert "social_permission=warm" in snapshot.derived_posture_context
    assert "voice_brevity=tight" in snapshot.derived_posture_context


def test_build_reply_identity_snapshot_defaults_cleanly_without_identity() -> None:
    snapshot = build_reply_identity_snapshot(None)

    assert snapshot.prompt_context == ""
    assert snapshot.delivery_plan.social_permission == "guarded"
    assert snapshot.delivery_plan.voice_pacing == "measured"
