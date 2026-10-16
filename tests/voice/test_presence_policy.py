from __future__ import annotations

from voice.presence_policy import VoicePresencePolicy


def _warm_identity_plan() -> dict[str, str]:
    return {
        "social_permission": "warm",
        "voice_pacing": "relaxed",
        "voice_brevity": "tight",
        "question_bias": "allow_light",
        "detail_bias": "compact",
        "relationship_posture": "warm_supportive_present",
        "communication_rhythm": "ongoing",
        "followthrough_style": "quiet_reliable",
        "proactive_cadence": "adaptive",
    }


def _guarded_identity_plan() -> dict[str, str]:
    return {
        "social_permission": "guarded",
        "voice_pacing": "measured",
        "voice_brevity": "tight",
        "question_bias": "avoid",
        "detail_bias": "compact",
        "relationship_posture": "steady_reliable",
        "communication_rhythm": "sparse",
        "followthrough_style": "quiet_reliable",
        "proactive_cadence": "adaptive",
    }


def test_decide_turn_allows_identity_social_presence_for_local_passive_voice() -> None:
    decision = VoicePresencePolicy.decide_turn(
        transcript="good morning",
        social_context="local_private",
        interaction_mode="passive",
        presence_profile="balanced",
        focus_state="responsive",
        group_voice_behavior="socially_adaptive",
        identity_delivery_plan=_warm_identity_plan(),
        explicit_addressed=False,
        within_followup_window=False,
        recent_unsolicited_seconds=None,
        recent_assistant_seconds=None,
    )

    assert decision.should_reply is True
    assert decision.decision_reason == "identity_social_presence"
    assert decision.reply_posture == "light_social"
    assert decision.identity_signals["social_permission"] == "warm"
    assert decision.identity_signals["communication_rhythm"] == "ongoing"


def test_decide_passive_interjection_stays_quiet_when_identity_prefers_restraint() -> None:
    decision = VoicePresencePolicy.decide_passive_interjection(
        transcript="sam: just getting coffee and opening my laptop",
        social_context="local_private",
        presence_profile="balanced",
        focus_state="responsive",
        group_voice_behavior="socially_adaptive",
        identity_delivery_plan=_guarded_identity_plan(),
        analyzer_decision={"should_interject": True, "confidence": 0.91},
        recent_unsolicited_seconds=None,
        recent_assistant_seconds=None,
    )

    assert decision.should_reply is False
    assert decision.decision_reason == "identity_prefers_quiet"
    assert decision.identity_signals["followthrough_style"] == "quiet_reliable"
    assert decision.identity_signals["question_bias"] == "avoid"


def test_decide_passive_interjection_allows_warm_social_presence_when_identity_invites_it() -> None:
    decision = VoicePresencePolicy.decide_passive_interjection(
        transcript="sam: good morning",
        social_context="local_private",
        presence_profile="balanced",
        focus_state="responsive",
        group_voice_behavior="socially_adaptive",
        identity_delivery_plan=_warm_identity_plan(),
        analyzer_decision={"should_interject": True, "confidence": 0.91},
        recent_unsolicited_seconds=None,
        recent_assistant_seconds=None,
    )

    assert decision.should_reply is True
    assert decision.decision_reason == "identity_social_presence"
    assert decision.reply_posture == "light_social"
    assert decision.identity_signals["social_permission"] == "warm"
