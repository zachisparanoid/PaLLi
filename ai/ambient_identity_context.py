from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from core.identity.policy_access import policy_value


@dataclass(frozen=True)
class AmbientArrivalIdentityPlan:
    greeting_tone: str = "warm"
    spoken_brevity: str = "tight"
    useful_context_policy: str = "one_item"
    tracked_item_style: str = "quiet_tracking"
    attention_item_style: str = "calm_attention"
    presence_awareness_mode: str = "arrival_only"
    social_context_sensitivity: str = "medium"
    ambient_voice_output_policy: str = "local_speaker"
    ambient_discord_delivery_policy: str = "dm_only"
    quiet_hours_mode: str = ""
    relationship_posture: str = ""
    followthrough_style: str = ""
    proactive_cadence: str = ""
    voice_presence: str = ""


@dataclass(frozen=True)
class AmbientIdentitySnapshot:
    creator_owned_context: str = ""
    user_authored_context: str = ""
    agent_maintained_context: str = ""
    derived_posture_context: str = ""
    prompt_context: str = ""
    arrival_plan: AmbientArrivalIdentityPlan = field(
        default_factory=AmbientArrivalIdentityPlan
    )


def build_ambient_identity_snapshot(compiled_identity: Any) -> AmbientIdentitySnapshot:
    if compiled_identity is None:
        return AmbientIdentitySnapshot()

    text_documents = dict(
        getattr(getattr(compiled_identity, "text_snapshot", None), "documents", {}) or {}
    )
    voice_documents = dict(
        getattr(getattr(compiled_identity, "voice_snapshot", None), "documents", {}) or {}
    )
    action_documents = dict(
        getattr(getattr(compiled_identity, "action_snapshot", None), "documents", {}) or {}
    )
    action_policy = getattr(compiled_identity, "action_policy", None)

    creator_owned_context = _render_creator_owned_context(
        text_documents=text_documents,
        voice_documents=voice_documents,
        action_documents=action_documents,
    )
    user_authored_context = _render_user_authored_context(
        text_documents=text_documents,
        voice_documents=voice_documents,
        action_documents=action_documents,
    )
    agent_maintained_context = _render_agent_maintained_context(action_documents)
    arrival_plan = _build_arrival_plan(
        text_documents=text_documents,
        voice_documents=voice_documents,
        action_documents=action_documents,
        action_policy=action_policy,
    )
    derived_posture_context = _render_derived_posture_context(
        action_documents=action_documents,
        action_policy=action_policy,
        arrival_plan=arrival_plan,
    )

    sections: list[str] = []
    if creator_owned_context:
        sections.append(f"## Creator-Owned Stable Layers\n{creator_owned_context}")
    if user_authored_context:
        sections.append(f"## User-Authored Preference Layers\n{user_authored_context}")
    if agent_maintained_context:
        sections.append(f"## Agent-Maintained Evolving Layers\n{agent_maintained_context}")
    if derived_posture_context:
        sections.append(f"## Derived Ambient Arrival Posture\n{derived_posture_context}")

    return AmbientIdentitySnapshot(
        creator_owned_context=creator_owned_context,
        user_authored_context=user_authored_context,
        agent_maintained_context=agent_maintained_context,
        derived_posture_context=derived_posture_context,
        prompt_context="\n\n".join(sections).strip(),
        arrival_plan=arrival_plan,
    )


def build_ambient_identity_context(compiled_identity: Any) -> str:
    return build_ambient_identity_snapshot(compiled_identity).prompt_context


def _render_creator_owned_context(
    *,
    text_documents: Mapping[str, Any],
    voice_documents: Mapping[str, Any],
    action_documents: Mapping[str, Any],
) -> str:
    lines: list[str] = []
    for label, doc in (
        ("Core identity", text_documents.get("core_identity")),
        ("Operating principles", text_documents.get("operating_principles")),
        ("Voice identity", voice_documents.get("voice_identity")),
        (
            "Capabilities and boundaries",
            action_documents.get("capabilities_and_boundaries"),
        ),
    ):
        summary = _render_document_excerpt(doc)
        if summary:
            lines.append(f"- {label}: {summary}")
    return "\n".join(lines).strip()


def _render_user_authored_context(
    *,
    text_documents: Mapping[str, Any],
    voice_documents: Mapping[str, Any],
    action_documents: Mapping[str, Any],
) -> str:
    lines: list[str] = []
    personality_summary = _render_document_fields(
        text_documents.get("personality_profile"),
        (
            "display_name",
            "tone",
            "warmth",
            "proactivity_style",
            "relationship_style",
            "response_length",
            "voice_style",
        ),
    )
    if personality_summary:
        lines.append(f"- Personality profile: {personality_summary}")

    voice_preferences = _render_document_fields(
        voice_documents.get("voice_preferences"),
        ("interaction_mode", "presence_profile", "focus_state", "local_transport_mode"),
    )
    if voice_preferences:
        lines.append(f"- Voice preferences: {voice_preferences}")

    action_summary = _render_document_fields(
        action_documents.get("action_preferences"),
        (
            "initiative_level",
            "social_context_sensitivity",
            "quiet_hours_mode",
            "presence_awareness_mode",
            "arrival_useful_context_policy",
            "ambient_voice_output_policy",
            "ambient_discord_delivery_policy",
        ),
    )
    if action_summary:
        lines.append(f"- Action preferences: {action_summary}")
    return "\n".join(lines).strip()


def _render_agent_maintained_context(action_documents: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for label, doc, fields in (
        (
            "Relationship state",
            action_documents.get("relationship_state"),
            (
                "relationship_phase",
                "trust_level",
                "communication_rhythm",
                "current_posture",
                "relationship_summary",
            ),
        ),
        (
            "Memory summary",
            action_documents.get("memory_summary"),
            ("summary_focus", "update_style", "memory_summary_text"),
        ),
        (
            "Routines",
            action_documents.get("routines"),
            (
                "proactive_cadence",
                "followthrough_style",
                "review_cadence",
                "routine_summary",
            ),
        ),
        (
            "Standing orders",
            action_documents.get("standing_orders"),
            ("standing_orders_summary",),
        ),
    ):
        summary = _render_document_fields(doc, fields)
        if summary:
            lines.append(f"- {label}: {summary}")
    return "\n".join(lines).strip()


def _render_derived_posture_context(
    *,
    action_documents: Mapping[str, Any],
    action_policy: Any,
    arrival_plan: AmbientArrivalIdentityPlan,
) -> str:
    relationship_frontmatter = _frontmatter(action_documents.get("relationship_state"))
    routines_frontmatter = _frontmatter(action_documents.get("routines"))

    lines: list[str] = []
    if action_policy is not None:
        lines.extend(
            [
                f"- initiative_level={_clean(policy_value(action_policy, 'initiative_level', ''))}",
                f"- social_context_sensitivity={_clean(policy_value(action_policy, 'social_context_sensitivity', ''))}",
                f"- quiet_hours_mode={_clean(policy_value(action_policy, 'quiet_hours_mode', ''))}",
                f"- presence_awareness_mode={_clean(policy_value(action_policy, 'presence_awareness_mode', ''))}",
                f"- arrival_useful_context_policy={_clean(policy_value(action_policy, 'arrival_useful_context_policy', ''))}",
                f"- ambient_voice_output_policy={_clean(policy_value(action_policy, 'ambient_voice_output_policy', ''))}",
                f"- ambient_discord_delivery_policy={_clean(policy_value(action_policy, 'ambient_discord_delivery_policy', ''))}",
            ]
        )

    relationship_posture = _clean(relationship_frontmatter.get("current_posture"))
    if relationship_posture:
        lines.append(f"- relationship_posture={relationship_posture}")

    communication_rhythm = _clean(relationship_frontmatter.get("communication_rhythm"))
    if communication_rhythm:
        lines.append(f"- communication_rhythm={communication_rhythm}")

    followthrough_style = _clean(routines_frontmatter.get("followthrough_style"))
    if followthrough_style:
        lines.append(f"- followthrough_style={followthrough_style}")

    proactive_cadence = _clean(routines_frontmatter.get("proactive_cadence"))
    if proactive_cadence:
        lines.append(f"- proactive_cadence={proactive_cadence}")

    lines.extend(
        [
            f"- greeting_tone={_clean(arrival_plan.greeting_tone)}",
            f"- spoken_brevity={_clean(arrival_plan.spoken_brevity)}",
            f"- useful_context_policy={_clean(arrival_plan.useful_context_policy)}",
            f"- tracked_item_style={_clean(arrival_plan.tracked_item_style)}",
            f"- attention_item_style={_clean(arrival_plan.attention_item_style)}",
            f"- voice_presence={_clean(arrival_plan.voice_presence)}",
        ]
    )

    return "\n".join(line for line in lines if line and not line.endswith("=")).strip()


def _build_arrival_plan(
    *,
    text_documents: Mapping[str, Any],
    voice_documents: Mapping[str, Any],
    action_documents: Mapping[str, Any],
    action_policy: Any,
) -> AmbientArrivalIdentityPlan:
    personality_frontmatter = _frontmatter(text_documents.get("personality_profile"))
    voice_identity_frontmatter = _frontmatter(voice_documents.get("voice_identity"))
    relationship_frontmatter = _frontmatter(action_documents.get("relationship_state"))
    routines_frontmatter = _frontmatter(action_documents.get("routines"))

    presence_awareness_mode = _clean(
        policy_value(action_policy, "presence_awareness_mode", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("presence_awareness_mode")
    ).lower() or "arrival_only"
    useful_context_policy = _clean(
        policy_value(action_policy, "arrival_useful_context_policy", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("arrival_useful_context_policy")
    ).lower() or "one_item"
    ambient_voice_output_policy = _clean(
        policy_value(action_policy, "ambient_voice_output_policy", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("ambient_voice_output_policy")
    ).lower() or "local_speaker"
    ambient_discord_delivery_policy = _clean(
        policy_value(action_policy, "ambient_discord_delivery_policy", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("ambient_discord_delivery_policy")
    ).lower() or "dm_only"
    social_context_sensitivity = _clean(
        policy_value(action_policy, "social_context_sensitivity", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("social_context_sensitivity")
    ).lower() or "medium"
    quiet_hours_mode = _clean(
        policy_value(action_policy, "quiet_hours_mode", "")
    ).lower() or _clean(
        _frontmatter(action_documents.get("action_preferences")).get("quiet_hours_mode")
    ).lower()

    relationship_posture = _clean(relationship_frontmatter.get("current_posture"))
    followthrough_style = _clean(routines_frontmatter.get("followthrough_style"))
    proactive_cadence = _clean(routines_frontmatter.get("proactive_cadence"))
    voice_presence = _clean(voice_identity_frontmatter.get("voice_presence"))

    tone_source = " ".join(
        part
        for part in (
            _clean(personality_frontmatter.get("tone")),
            _clean(personality_frontmatter.get("relationship_style")),
            relationship_posture,
            followthrough_style,
            voice_presence,
        )
        if part
    ).lower()
    spoken_style = " ".join(
        part
        for part in (
            _clean(voice_identity_frontmatter.get("reply_style")),
            _clean(personality_frontmatter.get("response_length")),
        )
        if part
    ).lower()

    greeting_tone = "warm"
    if (
        quiet_hours_mode == "gentle"
        or "gentle" in tone_source
        or "quiet" in tone_source
    ):
        greeting_tone = "gentle"
    elif (
        useful_context_policy == "briefing"
        or "direct" in tone_source
        or "coach" in tone_source
        or "operator" in tone_source
    ):
        greeting_tone = "practical"

    spoken_brevity = "tight"
    if (
        "detailed" in spoken_style
        or "long" in spoken_style
        or "expanded" in spoken_style
    ):
        spoken_brevity = "balanced"

    tracked_item_style = "quiet_tracking"
    if useful_context_policy == "briefing" and "quiet" not in followthrough_style.lower():
        tracked_item_style = "steady_tracking"

    attention_item_style = "calm_attention"
    if (
        greeting_tone == "practical"
        or "operator" in tone_source
        or "coach" in tone_source
    ):
        attention_item_style = "practical_attention"

    return AmbientArrivalIdentityPlan(
        greeting_tone=greeting_tone,
        spoken_brevity=spoken_brevity,
        useful_context_policy=useful_context_policy,
        tracked_item_style=tracked_item_style,
        attention_item_style=attention_item_style,
        presence_awareness_mode=presence_awareness_mode,
        social_context_sensitivity=social_context_sensitivity,
        ambient_voice_output_policy=ambient_voice_output_policy,
        ambient_discord_delivery_policy=ambient_discord_delivery_policy,
        quiet_hours_mode=quiet_hours_mode,
        relationship_posture=relationship_posture,
        followthrough_style=followthrough_style,
        proactive_cadence=proactive_cadence,
        voice_presence=voice_presence,
    )


def _render_document_excerpt(doc: Any, *, max_length: int = 220) -> str:
    excerpt = _clean(getattr(doc, "excerpt", ""))
    return _truncate(excerpt, max_length)


def _render_document_fields(doc: Any, fields: Sequence[str]) -> str:
    frontmatter = _frontmatter(doc)
    items: list[str] = []
    for field_name in fields:
        value = frontmatter.get(field_name)
        cleaned = _clean(value)
        if not cleaned:
            continue
        items.append(f"{field_name}={_truncate(cleaned, 140)}")
    return "; ".join(items)


def _frontmatter(doc: Any) -> Mapping[str, Any]:
    frontmatter = getattr(doc, "frontmatter", {}) or {}
    return frontmatter if isinstance(frontmatter, Mapping) else {}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).strip().split())


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."
