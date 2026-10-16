from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from core.identity.policy_access import policy_value


@dataclass(frozen=True)
class ReplyDeliveryIdentityPlan:
    social_permission: str = "guarded"
    voice_pacing: str = "measured"
    voice_brevity: str = "balanced"
    question_bias: str = "low"
    detail_bias: str = "balanced"
    relationship_posture: str = ""
    communication_rhythm: str = ""
    followthrough_style: str = ""
    proactive_cadence: str = ""
    routine_summary: str = ""


@dataclass(frozen=True)
class ReplyIdentitySnapshot:
    creator_owned_context: str = ""
    user_authored_context: str = ""
    agent_maintained_context: str = ""
    derived_posture_context: str = ""
    prompt_context: str = ""
    delivery_plan: ReplyDeliveryIdentityPlan = field(
        default_factory=ReplyDeliveryIdentityPlan
    )


def build_reply_identity_snapshot(compiled_identity: Any) -> ReplyIdentitySnapshot:
    if compiled_identity is None:
        return ReplyIdentitySnapshot()

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
    delivery_plan = _build_delivery_plan(
        text_documents=text_documents,
        voice_documents=voice_documents,
        action_documents=action_documents,
        action_policy=action_policy,
    )
    derived_posture_context = _render_derived_posture_context(
        voice_documents=voice_documents,
        action_documents=action_documents,
        action_policy=action_policy,
        delivery_plan=delivery_plan,
    )

    sections: list[str] = []
    if creator_owned_context:
        sections.append(f"## Creator-Owned Stable Layers\n{creator_owned_context}")
    if user_authored_context:
        sections.append(f"## User-Authored Preference Layers\n{user_authored_context}")
    if agent_maintained_context:
        sections.append(f"## Agent-Maintained Evolving Layers\n{agent_maintained_context}")
    if derived_posture_context:
        sections.append(f"## Derived Reply/Voice Posture\n{derived_posture_context}")

    return ReplyIdentitySnapshot(
        creator_owned_context=creator_owned_context,
        user_authored_context=user_authored_context,
        agent_maintained_context=agent_maintained_context,
        derived_posture_context=derived_posture_context,
        prompt_context="\n\n".join(sections).strip(),
        delivery_plan=delivery_plan,
    )


def build_reply_identity_context(compiled_identity: Any) -> str:
    return build_reply_identity_snapshot(compiled_identity).prompt_context


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
            "humor",
            "challenge_level",
            "relationship_style",
            "response_length",
            "voice_style",
        ),
    )
    if personality_summary:
        lines.append(f"- Personality profile: {personality_summary}")

    voice_summary = _render_document_fields(
        voice_documents.get("voice_preferences"),
        (
            "interaction_mode",
            "presence_profile",
            "focus_state",
            "local_transport_mode",
        ),
    )
    if voice_summary:
        lines.append(f"- Voice preferences: {voice_summary}")

    action_summary = _render_document_fields(
        action_documents.get("action_preferences"),
        (
            "initiative_level",
            "social_context_sensitivity",
            "quiet_hours_mode",
            "group_voice_behavior",
            "followthrough_intensity",
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
            ("summary_focus", "memory_summary_text"),
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
    ):
        summary = _render_document_fields(doc, fields)
        if summary:
            lines.append(f"- {label}: {summary}")
    return "\n".join(lines).strip()


def _render_derived_posture_context(
    *,
    voice_documents: Mapping[str, Any],
    action_documents: Mapping[str, Any],
    action_policy: Any,
    delivery_plan: ReplyDeliveryIdentityPlan,
) -> str:
    voice_identity = _frontmatter(voice_documents.get("voice_identity"))
    lines: list[str] = []
    if action_policy is not None:
        lines.extend(
            [
                f"- initiative_level={_clean(policy_value(action_policy, 'initiative_level', ''))}",
                f"- social_context_sensitivity={_clean(policy_value(action_policy, 'social_context_sensitivity', ''))}",
                f"- quiet_hours_mode={_clean(policy_value(action_policy, 'quiet_hours_mode', ''))}",
                f"- group_voice_behavior={_clean(policy_value(action_policy, 'group_voice_behavior', ''))}",
            ]
        )

    for field_name in ("voice_presence", "turn_taking", "reply_style"):
        value = _clean(voice_identity.get(field_name))
        if value:
            lines.append(f"- {field_name}={value}")

    lines.extend(
        [
            f"- social_permission={_clean(delivery_plan.social_permission)}",
            f"- voice_pacing={_clean(delivery_plan.voice_pacing)}",
            f"- voice_brevity={_clean(delivery_plan.voice_brevity)}",
            f"- question_bias={_clean(delivery_plan.question_bias)}",
            f"- detail_bias={_clean(delivery_plan.detail_bias)}",
        ]
    )
    if delivery_plan.relationship_posture:
        lines.append(f"- relationship_posture={_clean(delivery_plan.relationship_posture)}")
    if delivery_plan.communication_rhythm:
        lines.append(f"- communication_rhythm={_clean(delivery_plan.communication_rhythm)}")
    if delivery_plan.followthrough_style:
        lines.append(f"- followthrough_style={_clean(delivery_plan.followthrough_style)}")
    if delivery_plan.proactive_cadence:
        lines.append(f"- proactive_cadence={_clean(delivery_plan.proactive_cadence)}")
    if delivery_plan.routine_summary:
        lines.append(f"- routine_summary={_truncate(delivery_plan.routine_summary, 160)}")

    return "\n".join(line for line in lines if line and not line.endswith("=")).strip()


def _build_delivery_plan(
    *,
    text_documents: Mapping[str, Any],
    voice_documents: Mapping[str, Any],
    action_documents: Mapping[str, Any],
    action_policy: Any,
) -> ReplyDeliveryIdentityPlan:
    personality = _frontmatter(text_documents.get("personality_profile"))
    voice_identity = _frontmatter(voice_documents.get("voice_identity"))
    voice_preferences = _frontmatter(voice_documents.get("voice_preferences"))
    relationship = _frontmatter(action_documents.get("relationship_state"))
    routines = _frontmatter(action_documents.get("routines"))
    action_preferences = _frontmatter(action_documents.get("action_preferences"))

    response_length = _clean(personality.get("response_length")).lower()
    warmth = _clean(personality.get("warmth")).lower()
    tone = _clean(personality.get("tone")).lower()
    challenge_level = _clean(personality.get("challenge_level")).lower()
    relationship_style = _clean(personality.get("relationship_style")).lower()
    voice_style = _clean(personality.get("voice_style")).lower()
    relationship_posture = _clean(relationship.get("current_posture"))
    communication_rhythm = _clean(relationship.get("communication_rhythm"))
    followthrough_style = _clean(routines.get("followthrough_style"))
    proactive_cadence = _clean(routines.get("proactive_cadence"))
    routine_summary = _truncate(_clean(routines.get("routine_summary")), 160)
    focus_state = _clean(voice_preferences.get("focus_state")).lower()
    presence_profile = _clean(voice_preferences.get("presence_profile")).lower()
    reply_style = _clean(voice_identity.get("reply_style")).lower()
    voice_presence = _clean(voice_identity.get("voice_presence")).lower()

    quiet_hours_mode = _clean(
        policy_value(action_policy, "quiet_hours_mode", "")
    ).lower() or _clean(action_preferences.get("quiet_hours_mode")).lower()
    social_context_sensitivity = _clean(
        policy_value(action_policy, "social_context_sensitivity", "")
    ).lower() or _clean(action_preferences.get("social_context_sensitivity")).lower()
    group_voice_behavior = _clean(
        policy_value(action_policy, "group_voice_behavior", "")
    ).lower() or _clean(action_preferences.get("group_voice_behavior")).lower()

    tone_source = " ".join(
        part
        for part in (
            tone,
            warmth,
            relationship_style,
            voice_style,
            relationship_posture.lower(),
            voice_presence,
        )
        if part
    )

    social_permission = "balanced"
    if group_voice_behavior == "reserved" or social_context_sensitivity == "high":
        social_permission = "guarded"
    elif presence_profile == "social" or any(
        marker in tone_source
        for marker in ("warm", "companion", "friend", "human", "supportive")
    ):
        social_permission = "warm"

    voice_brevity = "balanced"
    if (
        "concise" in reply_style
        or response_length in {"short", "brief", "concise"}
        or focus_state == "quiet"
    ):
        voice_brevity = "tight"
    elif response_length in {"long", "detailed", "extended"}:
        voice_brevity = "expanded"

    voice_pacing = "measured"
    if focus_state == "quiet" or quiet_hours_mode in {"gentle", "adaptive"}:
        voice_pacing = "measured"
    elif any(marker in " ".join((challenge_level, voice_style, tone)) for marker in ("direct", "coach", "clear")):
        voice_pacing = "brisk"
    elif social_permission == "warm" or any(
        marker in tone_source for marker in ("warm", "companion", "friend")
    ):
        voice_pacing = "relaxed"

    question_bias = "low"
    if focus_state == "quiet" or social_context_sensitivity == "high":
        question_bias = "avoid"
    elif social_permission == "warm" and communication_rhythm.lower() in {"ongoing", "frequent"}:
        question_bias = "allow_light"

    detail_bias = "balanced"
    if voice_brevity == "tight":
        detail_bias = "compact"
    elif response_length in {"long", "detailed"} or followthrough_style in {"steady", "persistent"}:
        detail_bias = "written_detail"

    return ReplyDeliveryIdentityPlan(
        social_permission=social_permission,
        voice_pacing=voice_pacing,
        voice_brevity=voice_brevity,
        question_bias=question_bias,
        detail_bias=detail_bias,
        relationship_posture=relationship_posture,
        communication_rhythm=communication_rhythm,
        followthrough_style=followthrough_style,
        proactive_cadence=proactive_cadence,
        routine_summary=routine_summary,
    )


def _render_document_excerpt(doc: Any, *, max_length: int = 220) -> str:
    excerpt = _clean(getattr(doc, "excerpt", ""))
    return _truncate(excerpt, max_length)


def _render_document_fields(doc: Any, fields: Sequence[str]) -> str:
    frontmatter = _frontmatter(doc)
    items: list[str] = []
    for field_name in fields:
        cleaned = _clean(frontmatter.get(field_name))
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
