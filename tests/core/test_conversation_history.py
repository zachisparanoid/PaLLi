from __future__ import annotations

from core.conversation_history import build_conversation_history_preview


def test_build_conversation_history_preview_includes_voice_decision_trace() -> None:
    preview = build_conversation_history_preview(
        [
            {
                "id": 1,
                "role": "user",
                "username": "Sam",
                "content": "Can you handle voice follow-ups more quietly?",
                "timestamp": 100.0,
                "metadata": {},
            },
            {
                "id": 2,
                "role": "assistant",
                "username": "PaLLi",
                "content": "Yeah, I'll stay quiet unless I'm directly addressed.",
                "timestamp": 101.0,
                "metadata": {
                    "response_to": "voice",
                    "voice_turn_decision": {
                        "should_reply": True,
                        "decision_reason": "direct_address",
                        "initiative_source": "direct_address",
                        "reply_posture": "brief_companion",
                        "should_mirror_text": True,
                        "identity_signals": {
                            "social_mode": "attentive",
                        },
                    },
                },
            },
        ],
        limit=3,
    )

    assert len(preview) == 2
    assert preview[1]["role_label"] == "PaLLi"
    assert preview[1]["voice_turn_decision_trace"]["behavior"] == "direct_response"
    assert preview[1]["voice_turn_decision_trace"]["mirror_policy_label"] == "Mirror to text"
    assert preview[1]["voice_turn_decision_trace"]["identity_cues"] == [
        {
            "key": "social_mode",
            "label": "social mode",
            "value": "attentive",
            "value_label": "attentive",
        }
    ]


def test_build_conversation_history_preview_skips_error_fallback_turns() -> None:
    preview = build_conversation_history_preview(
        [
            {
                "id": 1,
                "role": "assistant",
                "username": "PaLLi",
                "content": "Looks like I glitched on my end.",
                "timestamp": 100.0,
                "metadata": {
                    "type": "error_fallback",
                    "error": "empty_llm_response",
                },
            },
            {
                "id": 2,
                "role": "assistant",
                "username": "PaLLi",
                "content": "Retrying now with the normal voice lane.",
                "timestamp": 101.0,
                "metadata": {},
            },
        ],
        limit=3,
    )

    assert len(preview) == 1
    assert preview[0]["id"] == 2
    assert preview[0]["excerpt"] == "Retrying now with the normal voice lane."
