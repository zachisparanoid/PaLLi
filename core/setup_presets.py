from __future__ import annotations

from typing import Any, Dict


GUIDED_SETUP_STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "warm": {
        "profile_preset": "default",
        "personality": {
            "tone": "warm_companion",
            "warmth": "high",
            "humor": "light_and_human",
            "response_length": "adaptive",
        },
    },
    "direct": {
        "profile_preset": "professional",
        "personality": {
            "tone": "grounded_direct",
            "warmth": "medium",
            "humor": "none",
            "response_length": "short",
        },
    },
    "relaxed": {
        "profile_preset": "casual_slang",
        "personality": {
            "tone": "playful_companion",
            "warmth": "high",
            "humor": "playful",
            "response_length": "adaptive",
        },
    },
    "minimal": {
        "profile_preset": "minimalist",
        "personality": {
            "tone": "grounded_direct",
            "warmth": "low",
            "humor": "none",
            "response_length": "short",
        },
    },
    "calm": {
        "profile_preset": "zen",
        "personality": {
            "tone": "calm_supportive",
            "warmth": "medium",
            "humor": "none",
            "response_length": "adaptive",
        },
    },
}

GUIDED_SETUP_AUTONOMY_PRESETS: Dict[str, Dict[str, Any]] = {
    "cautious": {
        "autonomy_mode": "ask_first",
        "allow_silent_beneficial_actions": False,
        "allow_silent_low_risk_actions": False,
        "allow_privileged_cli_actions": False,
        "allow_package_install_actions": False,
        "environment_optimization_mode": "suggest_only",
        "external_contact_policy": "confirm_first",
        "post_action_reporting_mode": "immediate",
    },
    "balanced": {
        "autonomy_mode": "balanced",
        "allow_silent_beneficial_actions": True,
        "allow_silent_low_risk_actions": False,
        "allow_privileged_cli_actions": False,
        "allow_package_install_actions": False,
        "environment_optimization_mode": "suggest_only",
        "external_contact_policy": "confirm_first",
        "post_action_reporting_mode": "digest",
    },
    "trusted": {
        "autonomy_mode": "trusted_autonomous",
        "allow_silent_beneficial_actions": True,
        "allow_silent_low_risk_actions": True,
        "allow_privileged_cli_actions": True,
        "allow_package_install_actions": True,
        "environment_optimization_mode": "safe_autonomous",
        "external_contact_policy": "confirm_first",
        "post_action_reporting_mode": "minimal",
    },
}

GUIDED_SETUP_CHECKIN_OPTIONS = {"high", "normal", "low"}

GUIDED_SETUP_VOICE_POSTURES: Dict[str, Dict[str, Any]] = {
    "off": {
        "profile": {"voice_enabled": False},
        "voice_preferences": {
            "interaction_mode": "direct_address",
            "presence_profile": "quiet",
            "focus_state": "quiet",
            "local_transport_mode": "quiet",
        },
    },
    "quiet": {
        "profile": {"voice_enabled": True},
        "voice_preferences": {
            "interaction_mode": "direct_address",
            "presence_profile": "quiet",
            "focus_state": "quiet",
            "local_transport_mode": "push_to_talk",
        },
    },
    "balanced": {
        "profile": {"voice_enabled": True},
        "voice_preferences": {
            "interaction_mode": "direct_address",
            "presence_profile": "balanced",
            "focus_state": "responsive",
            "local_transport_mode": "push_to_talk",
        },
    },
    "social": {
        "profile": {"voice_enabled": True},
        "voice_preferences": {
            "interaction_mode": "passive",
            "presence_profile": "social",
            "focus_state": "responsive",
            "local_transport_mode": "passive",
        },
    },
}

GUIDED_SETUP_CHECKIN_POSTURES: Dict[str, Dict[str, Any]] = {
    "off": {"proactive_enabled": False, "check_in_freq": "low"},
    "rare": {"proactive_enabled": True, "check_in_freq": "low"},
    "normal": {"proactive_enabled": True, "check_in_freq": "normal"},
    "frequent": {"proactive_enabled": True, "check_in_freq": "high"},
}

GUIDED_SETUP_RELATIONSHIP_STYLES: Dict[str, str] = {
    "friend": "trusted_friend_assistant",
    "assistant": "assistant_first_but_human",
    "coach": "supportive_accountability_partner",
    "companion": "trusted_friend_assistant",
}

GUIDED_SETUP_TARGET_UI: Dict[str, Dict[str, str]] = {
    "user_name": {
        "title": "You",
        "prompt": "What should PaLLi call you most often?",
        "input_kind": "text",
        "placeholder": "Your name or nickname",
    },
    "companion_style": {
        "title": "Style",
        "prompt": "What kind of companion presence do you want from PaLLi?",
        "input_kind": "single_choice",
    },
    "autonomy_preset": {
        "title": "Autonomy",
        "prompt": "How much initiative should PaLLi take by default?",
        "input_kind": "single_choice",
    },
    "checkin_posture": {
        "title": "Check-ins",
        "prompt": "How often should PaLLi reach out first?",
        "input_kind": "single_choice",
    },
    "voice_posture": {
        "title": "Voice",
        "prompt": "How available should PaLLi feel in voice?",
        "input_kind": "single_choice",
    },
    "relationship_style": {
        "title": "Relationship",
        "prompt": "If you want, how should the relationship feel by default?",
        "input_kind": "single_choice",
    },
}

GUIDED_SETUP_OPTION_UI: Dict[str, Dict[str, Dict[str, str]]] = {
    "companion_style": {
        "warm": {"label": "Warm", "description": "Friendly, perceptive, and easy to stay with."},
        "direct": {"label": "Direct", "description": "Clear, grounded, and gently honest."},
        "relaxed": {"label": "Relaxed", "description": "Casual, bright, and easy to talk to."},
        "minimal": {"label": "Minimal", "description": "Quiet, low-noise, and practical."},
        "calm": {"label": "Calm", "description": "Steady, supportive, and less intense."},
    },
    "autonomy_preset": {
        "cautious": {"label": "Ask First", "description": "PaLLi checks before most actions."},
        "balanced": {"label": "Balanced", "description": "Handles safe things, asks on bigger ones."},
        "trusted": {"label": "Trusted", "description": "Takes more initiative on your machine."},
    },
    "checkin_posture": {
        "off": {"label": "Off", "description": "Stay quiet unless you start the conversation."},
        "rare": {"label": "Rare", "description": "Reach out once in a while when it matters."},
        "normal": {"label": "Normal", "description": "Follow up with a balanced, steady rhythm."},
        "frequent": {"label": "Frequent", "description": "Reach out often and keep more active tabs."},
    },
    "voice_posture": {
        "off": {"label": "Off", "description": "Keep PaLLi text-first for now."},
        "quiet": {"label": "Quiet", "description": "Use voice when addressed, but stay reserved."},
        "balanced": {"label": "Balanced", "description": "Make voice feel ready without being pushy."},
        "social": {"label": "Social", "description": "Let voice feel more ambient and available."},
    },
    "relationship_style": {
        "friend": {"label": "Friend", "description": "More warm, familiar, and conversational."},
        "assistant": {"label": "Assistant", "description": "More helper-first and practical."},
        "coach": {"label": "Coach", "description": "More accountable, challenging, and motivating."},
        "companion": {"label": "Companion", "description": "More steady, present, and personally supportive."},
    },
}
