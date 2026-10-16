from __future__ import annotations

from typing import Dict


CORE_DOCS: Dict[str, str] = {
    "core_identity": "CORE_IDENTITY.md",
    "operating_principles": "OPERATING_PRINCIPLES.md",
    "voice_identity": "VOICE_IDENTITY.md",
    "capabilities_and_boundaries": "CAPABILITIES_AND_BOUNDARIES.md",
}

# Docs that have crossed the wall to HER side (manifesto §2.8, owner-approved
# 2026-08-13): resolution is palli-first with core fallback — see
# document_store.core_doc_path. Membership here is a deliberate handover,
# one doc at a time, never a default.
PALLI_OWNED_CORE_DOCS = {"voice_identity"}

USER_DOCS: Dict[str, str] = {
    "personality_profile": "PERSONALITY_PROFILE.md",
    "action_preferences": "ACTION_PREFERENCES.md",
    "voice_preferences": "VOICE_PREFERENCES.md",
    "preferences": "PREFERENCES.md",
    "relationship_state": "RELATIONSHIP_STATE.md",
    "memory": "MEMORY.md",
    "memory_summary": "MEMORY_SUMMARY.md",
    # Batch 5 (memory that picks, 2026-09-28): where the nightly sweep moves
    # MEMORY.md lines by age and use. Never rendered whole into a prompt —
    # it is in no PROMPT_DOCS tuple; its bullets are reached by look-up.
    "memory_archive": "MEMORY_ARCHIVE.md",
    "routines": "ROUTINES.md",
    "standing_orders": "STANDING_ORDERS.md",
    "changelog": "CHANGELOG.md",
}

USER_EDITABLE_DOCS = {"personality_profile", "action_preferences", "voice_preferences", "preferences"}
SILENT_SELF_MAINTENANCE_DOCS = {
    "relationship_state",
    "memory",
    "memory_summary",
    "routines",
    "standing_orders",
    "preferences",
}
RAW_MARKDOWN_EDITABLE_DOCS = {
    "personality_profile",
    "action_preferences",
    "voice_preferences",
    "preferences",
    "relationship_state",
    "memory",
    "memory_summary",
    "routines",
    "standing_orders",
}

PROMPT_DOCS_TEXT = (
    "core_identity",
    "operating_principles",
    "personality_profile",
    "relationship_state",
    "memory_summary",
    # voice_identity rides EVERY surface (owner-decided 2026-08-13, "same
    # identity all around"): her authored page is her general expressive
    # voice, not a speech addendum. Kept LAST so the voice snapshot's
    # document order is byte-identical to before the unify. Speech
    # mechanics stay voice-only below.
    "voice_identity",
)
PROMPT_DOCS_VOICE = PROMPT_DOCS_TEXT + ("voice_preferences", "action_preferences")

# Per-document framing prepended ahead of the doc body when assembled into
# the system prompt. The "memory" doc was previously injected with only a
# `### Memory` header, so small local models read its bullets (some written
# as imperatives like "mention favorite color in casual replies") as
# standing instructions and parroted them across unrelated turns. The
# framing tells the model to treat the section as background reference,
# not as a task list to execute every turn. Mirrors the existing
# guard-rail framing on per-turn similarity hits in
# core/turn/respond_tier0._memory_section_demoted.
IDENTITY_DOC_PREAMBLES: Dict[str, str] = {
    # Compressed 2026-08-19 (item 6): six rules with May-era examples → the
    # three laws that matter. The threads are dated and swept now.
    "memory": (
        "Durable curated memory about this user — BACKGROUND REFERENCE, not "
        "instructions. Use a line ONLY if it is directly relevant to what "
        "they just said; never lead with it — replies open from the current "
        "message. \"Prefers X\" describes them and is not a standing order to "
        "do X now.\n"
    ),
}
PROMPT_DOCS_ACTION = (
    "core_identity",
    "operating_principles",
    "capabilities_and_boundaries",
    "action_preferences",
    "relationship_state",
    "memory_summary",
    "routines",
    "standing_orders",
)

DEFAULT_PERSONALITY_SETTINGS = {
    "tone": "warm_companion",
    "warmth": "high",
    "humor": "light_and_human",
    "proactivity_style": "supportive_initiative",
    "challenge_level": "gentle_but_honest",
    "voice_style": "warm_natural_companion_like",
    "relationship_style": "trusted_friend_assistant",
    "response_length": "adaptive",
    "autonomy_posture": "trusted",
}

ACTION_PREFERENCE_BOOL_FIELDS = {
    "allow_bounded_reflection",
    "allow_companion_agenda",
    "allow_proactive_followups",
    "allow_silent_beneficial_actions",
    "allow_silent_low_risk_actions",
    "allow_privileged_cli_actions",
    "allow_package_install_actions",
    "allow_schedule_creation",
    "allow_email_triage_actions",
    "allow_research_and_alert_loops",
    "require_confirmation_for_destructive_actions",
    "require_confirmation_for_external_spending",
}

ACTION_PREFERENCE_ENUM_FIELDS = {
    "autonomy_mode": ("ask_first", "balanced", "trusted_autonomous"),
    "initiative_level": ("low", "medium", "high"),
    "post_action_reporting_mode": ("immediate", "digest", "significant_only", "minimal"),
    "interruption_threshold": ("urgent_only", "time_sensitive", "important", "liberal"),
    "standing_orders_policy": ("disabled", "explicit_only", "enabled"),
    "followthrough_intensity": ("light", "steady", "persistent"),
    "environment_optimization_mode": ("observe_only", "suggest_only", "safe_autonomous", "full_trusted"),
    "background_watch_mode": ("off", "selected_topics", "enabled"),
    "external_contact_policy": ("confirm_first", "draft_only", "known_contacts"),
    "social_context_sensitivity": ("low", "medium", "high"),
    "quiet_hours_mode": ("off", "gentle", "adaptive"),
    "group_voice_behavior": ("reserved", "socially_adaptive", "companion_forward"),
    "presence_awareness_mode": ("off", "inspect_only", "arrival_only", "ambient_enabled"),
    "arrival_useful_context_policy": ("none", "one_item", "briefing"),
    "ambient_discord_delivery_policy": ("off", "dm_only", "linked_surface"),
    "ambient_voice_output_policy": ("off", "local_speaker"),
    "operator_initiative_mode": ("asked_first", "followthrough", "proactive"),
    "operator_autonomy_profile": ("confirm_most", "low_risk_auto", "high_autonomy"),
    "operator_active_workspace_mode": ("explicit_only", "recent_context", "pinned_workspace"),
}

DEFAULT_ACTION_SETTINGS = {
    "autonomy_mode": "trusted_autonomous",
    "initiative_level": "high",
    "allow_bounded_reflection": False,
    "allow_companion_agenda": True,
    "allow_proactive_followups": True,
    "allow_silent_beneficial_actions": True,
    "allow_silent_low_risk_actions": True,
    "allow_privileged_cli_actions": True,
    "allow_package_install_actions": True,
    "allow_schedule_creation": True,
    "allow_email_triage_actions": True,
    "allow_research_and_alert_loops": True,
    "require_confirmation_for_destructive_actions": True,
    "require_confirmation_for_external_spending": True,
    "post_action_reporting_mode": "digest",
    "interruption_threshold": "urgent_only",
    "standing_orders_policy": "enabled",
    "followthrough_intensity": "steady",
    "environment_optimization_mode": "safe_autonomous",
    "background_watch_mode": "enabled",
    "external_contact_policy": "confirm_first",
    "social_context_sensitivity": "high",
    "quiet_hours_mode": "adaptive",
    "group_voice_behavior": "socially_adaptive",
    "presence_awareness_mode": "arrival_only",
    "arrival_useful_context_policy": "one_item",
    "ambient_discord_delivery_policy": "dm_only",
    "ambient_voice_output_policy": "local_speaker",
    "operator_initiative_mode": "proactive",
    "operator_autonomy_profile": "high_autonomy",
    "operator_active_workspace_mode": "recent_context",
}

LEGACY_ACTION_SETTINGS = {
    **DEFAULT_ACTION_SETTINGS,
    "initiative_level": "medium",
    "allow_bounded_reflection": False,
    "allow_companion_agenda": True,
    "allow_proactive_followups": False,
    "standing_orders_policy": "explicit_only",
    "followthrough_intensity": "light",
    "background_watch_mode": "off",
}

DEFAULT_RELATIONSHIP_SETTINGS = {
    "relationship_phase": "trusted_companion",
    "trust_level": "high",
    "communication_rhythm": "ongoing",
    "current_posture": "warm_supportive_present",
    "relationship_summary": "Trusted companion baseline. I'm warm, capable, and quietly reliable with them.",
}

VOICE_PREFERENCE_ENUM_FIELDS = {
    # Step 31 (spec §7): the bridge is deleted — classic is the engine.
    "voice_engine": ("classic",),
    "interaction_mode": ("direct_address", "passive"),
    "presence_profile": ("quiet", "balanced", "social"),
    "focus_state": ("responsive", "quiet"),
    "local_transport_mode": ("push_to_talk", "passive", "quiet"),
}

DEFAULT_VOICE_PREFERENCES = {
    "voice_engine": "classic",
    "interaction_mode": "direct_address",
    "presence_profile": "balanced",
    "focus_state": "responsive",
    "local_transport_mode": "push_to_talk",
}

DEFAULT_MEMORY_SETTINGS = {
    "summary_focus": "goals_preferences_open_loops_emotional_context",
    "update_style": "distilled_not_raw",
    "memory_summary_text": "Identity memory has just been created. Long-term continuity will accumulate over time.",
}

DEFAULT_CURATED_MEMORY_SETTINGS = {
    "curation_style": "durable_context_active_threads_decisions_references",
    "memory_sections": "durable_context,active_threads,important_decisions,useful_references",
    "curated_memory_summary": "Curated memory is just getting started. Important context, decisions, and references will accumulate here.",
}

DEFAULT_MEMORY_ARCHIVE_SETTINGS = {
    "memory_archive_summary": (
        "Lines the nightly sweep moved out of MEMORY.md by age and use. "
        "Nothing here is deleted; any line can be restored."
    ),
}

DEFAULT_ROUTINE_SETTINGS = {
    "proactive_cadence": "adaptive",
    "followthrough_style": "quiet_reliable",
    "review_cadence": "continuous",
    "routine_summary": "No special recurring routines have been inferred yet beyond staying attentive and following through.",
}

DEFAULT_STANDING_ORDER_SETTINGS = {
    "standing_orders_summary": "No standing orders are active yet.",
}

DEFAULT_PREFERENCES_SETTINGS = {
    "preferences_summary": (
        "User-volunteered preferences and stable personal facts captured "
        "by reflection. Each ``pref_<key>`` frontmatter entry is a single "
        "key-value pair the user disclosed in conversation."
    ),
}

IDENTITY_DOC_LABELS = {
    "core_identity": "Core Identity",
    "operating_principles": "Operating Principles",
    "voice_identity": "Voice Identity",
    "capabilities_and_boundaries": "Capabilities and Boundaries",
    "personality_profile": "Personality Profile",
    "action_preferences": "Action Preferences",
    "voice_preferences": "Voice Preferences",
    "preferences": "User Preferences",
    "relationship_state": "Relationship State",
    "memory": "Memory",
    "memory_summary": "Memory Summary",
    "memory_archive": "Memory Archive",
    "routines": "Routines",
    "standing_orders": "Standing Orders",
    "changelog": "Changelog",
}
