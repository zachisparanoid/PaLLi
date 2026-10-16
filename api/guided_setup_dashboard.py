"""Guided-setup dashboard surface, carved out of core/first_contact.py.

Phase C / C5 step 2 (2026-05-12): the legacy 1,389-line state machine
in core/first_contact.py is being deleted in step 3. Six functions are
needed by api/web_account.py to power the dashboard's onboarding wizard
flow:

  * get_guided_setup_state — read current onboarding progress + identity
    snapshot, derive which targets are satisfied, return GuidedSetupState
  * build_guided_setup_contract — render the dashboard's wizard schema
    (question order, options, provenance) for the React UI
  * build_guided_setup_draft — derive the "best guess" form values from
    the owner's existing identity docs
  * derive_dashboard_checkin_posture — translate proactive_enabled +
    check_in_freq -> "off"|"rare"|"normal"|"frequent"
  * derive_dashboard_voice_posture — translate voice_enabled -> "balanced"|"off"
  * persist_guided_setup_completion — mark the onboarding as done

These were originally bundled into FirstContactService alongside a
multi-turn chat state machine that has since been replaced by recall +
decide + reflect (see Wave 2 of the subtractive refactor). The chat
side is going; the dashboard side stays.

Carved out as-is. The only differences from the old methods:
  * Module-level functions instead of class methods (module is the
    namespace; no shared mutable state).
  * Bootstrap doc path lazy-loaded via _load_document (no constructor).

The shared helpers (_build_target_provenance, _evaluate_target_completion_rule,
_combine_profiles, _build_default_document_frontmatter, etc.) come along
as private module functions.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

from core.config import config
from core.database import brain
from core.identity import identity_service
from core.identity.constants import IDENTITY_DOC_LABELS
from core.identity.frontmatter import parse_frontmatter
from core.setup_presets import (
    GUIDED_SETUP_AUTONOMY_PRESETS,
    GUIDED_SETUP_OPTION_UI,
    GUIDED_SETUP_RELATIONSHIP_STYLES,
    GUIDED_SETUP_STYLE_PRESETS,
    GUIDED_SETUP_TARGET_UI,
    GUIDED_SETUP_VOICE_POSTURES,
)

logger = logging.getLogger(__name__)


_ACTIVE_STEP = "guided_setup"
_COMPLETE_STEP = "guided_setup_complete"


@dataclass(slots=True)
class FirstContactDocument:
    frontmatter: Dict[str, Any]
    content: str
    required_targets: list[str] = field(default_factory=list)
    optional_targets: list[str] = field(default_factory=list)
    question_order: list[str] = field(default_factory=list)
    question_options: Dict[str, list[str]] = field(default_factory=dict)
    target_aliases: Dict[str, Dict[str, list[str]]] = field(default_factory=dict)
    target_completion_rules: Dict[str, list[Dict[str, Any]]] = field(default_factory=dict)
    target_notes: Dict[str, str] = field(default_factory=dict)
    pending_name_blocklist: list[str] = field(default_factory=list)
    max_questions_per_turn: int = 1


@dataclass(slots=True)
class GuidedSetupState:
    doc: FirstContactDocument
    current_step: str
    completed_steps: list[str] = field(default_factory=list)
    setup_completed: bool = False
    profile: Dict[str, Any] = field(default_factory=dict)
    bundle: Any = None
    target_provenance: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _bootstrap_doc_path() -> str:
    identity_root = os.path.abspath(getattr(config, "IDENTITY_ROOT_PATH", "identity"))
    return os.path.join(identity_root, "bootstrap", "FIRST_CONTACT.md")


def _load_document() -> FirstContactDocument:
    path = _bootstrap_doc_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            markdown = handle.read()
    except FileNotFoundError:
        logger.warning("First-contact document missing: %s", path)
        return FirstContactDocument(frontmatter={}, content="")

    frontmatter, content = parse_frontmatter(markdown)
    return FirstContactDocument(
        frontmatter=frontmatter,
        content=content,
        required_targets=list(frontmatter.get("required_targets") or []),
        optional_targets=list(frontmatter.get("optional_targets") or []),
        question_order=list(
            frontmatter.get("question_order") or frontmatter.get("required_targets") or []
        ),
        question_options=dict(frontmatter.get("question_options") or {}),
        target_aliases=_normalize_target_aliases(frontmatter.get("target_aliases")),
        target_completion_rules=_normalize_target_completion_rules(
            frontmatter.get("target_completion_rules")
        ),
        target_notes=dict(frontmatter.get("target_notes") or {}),
        pending_name_blocklist=[
            str(value or "").strip().lower()
            for value in list(frontmatter.get("pending_name_blocklist") or [])
            if str(value or "").strip()
        ],
        max_questions_per_turn=int(frontmatter.get("max_questions_per_turn") or 1),
    )


def _normalize_target_aliases(raw_aliases: Any) -> Dict[str, Dict[str, list[str]]]:
    if not isinstance(raw_aliases, Mapping):
        return {}

    normalized: Dict[str, Dict[str, list[str]]] = {}
    for target, option_map in raw_aliases.items():
        if not isinstance(option_map, Mapping):
            continue
        normalized_options: Dict[str, list[str]] = {}
        for option, raw_phrases in option_map.items():
            option_key = str(option or "").strip()
            if not option_key:
                continue
            phrases: list[str] = []
            for raw_phrase in raw_phrases if isinstance(raw_phrases, list) else []:
                phrase = str(raw_phrase or "").strip().lower()
                if phrase and phrase not in phrases:
                    phrases.append(phrase)
            normalized_options[option_key] = phrases
        if normalized_options:
            normalized[str(target or "").strip()] = normalized_options
    return normalized


def _normalize_target_completion_rules(raw_rules: Any) -> Dict[str, list[Dict[str, Any]]]:
    if not isinstance(raw_rules, Mapping):
        return {}

    normalized: Dict[str, list[Dict[str, Any]]] = {}
    for target, raw_target_rules in raw_rules.items():
        target_name = str(target or "").strip()
        if not target_name or not isinstance(raw_target_rules, list):
            continue
        rules: list[Dict[str, Any]] = []
        for raw_rule in raw_target_rules:
            if not isinstance(raw_rule, Mapping):
                continue
            source = str(raw_rule.get("source") or "").strip().lower()
            match = str(raw_rule.get("match") or "").strip().lower()
            field_name = str(raw_rule.get("field") or "").strip()
            fields = [
                str(item or "").strip()
                for item in list(raw_rule.get("fields") or [])
                if str(item or "").strip()
            ]
            doc_key = str(raw_rule.get("doc") or "").strip()
            value = raw_rule.get("value")
            values = list(raw_rule.get("values") or [])
            if source not in {"profile", "preferences", "document"} or not match:
                continue
            if source == "document" and not doc_key:
                continue
            if match == "non_default" and not fields:
                continue
            if match != "non_default" and not field_name:
                continue
            rules.append(
                {
                    "source": source,
                    "doc": doc_key,
                    "match": match,
                    "field": field_name,
                    "fields": fields,
                    "value": value,
                    "values": values,
                }
            )
        if rules:
            normalized[target_name] = rules
    return normalized


def _normalize_completed_targets(raw_steps: Any) -> list[str]:
    if not isinstance(raw_steps, Iterable) or isinstance(raw_steps, (str, bytes, dict)):
        return []
    normalized: list[str] = []
    for item in raw_steps:
        value = str(item or "").strip()
        if not value or value == "profile_setup":
            continue
        normalized.append(value)
    return normalized


def _stable_completed_step_order(
    *, doc: FirstContactDocument, completed_targets: set[str],
) -> list[str]:
    order = list(doc.question_order)
    for target in doc.required_targets:
        if target not in order:
            order.append(target)
    for target in doc.optional_targets:
        if target not in order:
            order.append(target)
    order.append("profile_setup")
    return [step for step in order if step in completed_targets]


def _all_required_complete(doc: FirstContactDocument, completed_targets: set[str]) -> bool:
    return bool(doc.required_targets) and all(
        target in completed_targets for target in doc.required_targets
    )


async def _mark_complete(
    account_user_id: int,
    *,
    doc: FirstContactDocument,
    completed_targets: set[str],
) -> None:
    completed = set(completed_targets)
    completed.add("profile_setup")
    await brain.update_onboarding_progress(
        user_id=account_user_id,
        current_step=_COMPLETE_STEP,
        completed_steps=_stable_completed_step_order(doc=doc, completed_targets=completed),
    )


# ─── target provenance + completion rule evaluation ─────────────────────


def _combine_profiles(
    *, profile: Dict[str, Any], owner_profile: Dict[str, Any],
) -> Dict[str, Any]:
    combined = dict(profile or {})
    if owner_profile:
        combined.update(owner_profile)

    combined_preferences = dict(profile.get("preferences") or {})
    combined_preferences.update(dict(owner_profile.get("preferences") or {}))
    combined["preferences"] = combined_preferences
    return combined


def _build_default_document_frontmatter(
    *,
    storage_user_id: int,
    storage_platform: str,
    profile: Dict[str, Any],
    bundle: Any,
) -> Dict[str, Dict[str, Any]]:
    defaults: Dict[str, Dict[str, Any]] = {}
    document_store = identity_service.document_store

    for doc_key in getattr(bundle, "user_documents", {}).keys():
        try:
            rendered = document_store.render_user_template(
                doc_key=doc_key,
                user_id=storage_user_id,
                platform=storage_platform,
                profile=profile,
            )
        except Exception:
            continue
        frontmatter, _ = parse_frontmatter(rendered)
        defaults[doc_key] = frontmatter

    return defaults


def _build_target_provenance(
    *,
    doc: FirstContactDocument,
    storage_user_id: int,
    storage_platform: str,
    profile: Dict[str, Any],
    owner_profile: Dict[str, Any],
    bundle: Any,
) -> Dict[str, Dict[str, Any]]:
    completion_rules = doc.target_completion_rules
    if not completion_rules:
        return {}

    combined_profile = _combine_profiles(profile=profile, owner_profile=owner_profile)
    combined_preferences = dict(combined_profile.get("preferences") or {})
    default_documents = _build_default_document_frontmatter(
        storage_user_id=storage_user_id,
        storage_platform=storage_platform,
        profile=owner_profile or combined_profile,
        bundle=bundle,
    )

    ordered_targets = list(
        dict.fromkeys(
            list(doc.question_order)
            + list(doc.required_targets)
            + list(doc.optional_targets)
            + list(completion_rules.keys())
        )
    )
    target_provenance: Dict[str, Dict[str, Any]] = {}
    for target in ordered_targets:
        artifacts = [
            artifact
            for rule in completion_rules.get(target, [])
            if (
                artifact := _evaluate_target_completion_rule(
                    rule=rule,
                    profile=combined_profile,
                    preferences=combined_preferences,
                    bundle=bundle,
                    default_documents=default_documents,
                )
            )
            is not None
        ]
        if artifacts:
            target_provenance[target] = {
                "completed": True,
                "summary": _build_target_provenance_summary(artifacts),
                "artifacts": artifacts,
            }
        elif target in completion_rules:
            target_provenance[target] = {
                "completed": False,
                "summary": None,
                "artifacts": [],
            }
    return target_provenance


def _completed_targets_from_provenance(
    target_provenance: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    return {
        str(target or "").strip()
        for target, details in target_provenance.items()
        if str(target or "").strip() and bool(details.get("completed"))
    }


def _evaluate_target_completion_rule(
    *,
    rule: Mapping[str, Any],
    profile: Dict[str, Any],
    preferences: Dict[str, Any],
    bundle: Any,
    default_documents: Dict[str, Dict[str, Any]],
) -> Dict[str, Any] | None:
    source = str(rule.get("source") or "").strip().lower()
    match = str(rule.get("match") or "").strip().lower()

    if source == "document":
        doc_key = str(rule.get("doc") or "").strip()
        document = getattr(bundle, "user_documents", {}).get(doc_key)
        frontmatter = dict(getattr(document, "frontmatter", {}) or {})
        default_frontmatter = dict(default_documents.get(doc_key) or {})
        if match == "non_default":
            changed_fields = [
                {
                    "field": field_name,
                    "label": _humanize_field_path(field_name),
                    "currentValue": (
                        _get_nested_value(frontmatter, field_name)
                        if _mapping_has_path(frontmatter, field_name)
                        else _get_nested_value(default_frontmatter, field_name)
                    ),
                    "defaultValue": default_frontmatter.get(field_name),
                }
                for field_name in rule.get("fields") or []
                if _normalize_rule_value(
                    _get_nested_value(frontmatter, field_name)
                    if _mapping_has_path(frontmatter, field_name)
                    else _get_nested_value(default_frontmatter, field_name)
                )
                != _normalize_rule_value(_get_nested_value(default_frontmatter, field_name))
            ]
            if not changed_fields:
                return None
            field_labels = [field["label"] for field in changed_fields if field.get("label")]
            return {
                "source": source,
                "artifactKey": doc_key,
                "artifactLabel": _document_label(doc_key),
                "artifactPath": str(getattr(document, "path", "") or "").strip() or None,
                "fieldPath": None,
                "fieldLabel": None,
                "fields": [field["field"] for field in changed_fields],
                "fieldLabels": field_labels,
                "changedFields": changed_fields,
                "reason": f"Changed from the default on {_join_labels(field_labels)}.",
            }
        field_path = str(rule.get("field") or "")
        current_value = (
            _get_nested_value(frontmatter, field_path)
            if _mapping_has_path(frontmatter, field_path)
            else _get_nested_value(default_frontmatter, field_path)
        )
        if not _rule_value_matches(
            match=match,
            current_value=current_value,
            expected=rule.get("value"),
            options=rule.get("values") or [],
        ):
            return None
        field_path = field_path.strip()
        field_label = _humanize_field_path(field_path)
        return {
            "source": source,
            "artifactKey": doc_key,
            "artifactLabel": _document_label(doc_key),
            "artifactPath": str(getattr(document, "path", "") or "").strip() or None,
            "fieldPath": field_path or None,
            "fieldLabel": field_label,
            "currentValue": current_value,
            "reason": _describe_rule_match(field_label=field_label, current_value=current_value),
        }

    source_mapping = profile if source == "profile" else preferences
    current_value = _get_nested_value(source_mapping, str(rule.get("field") or ""))
    if not _rule_value_matches(
        match=match,
        current_value=current_value,
        expected=rule.get("value"),
        options=rule.get("values") or [],
    ):
        return None
    field_path = str(rule.get("field") or "").strip()
    field_label = _humanize_field_path(field_path)
    return {
        "source": source,
        "artifactKey": source,
        "artifactLabel": "Profile" if source == "profile" else "Preferences",
        "artifactPath": None,
        "fieldPath": field_path or None,
        "fieldLabel": field_label,
        "currentValue": current_value,
        "reason": _describe_rule_match(field_label=field_label, current_value=current_value),
    }


def _rule_value_matches(
    *,
    match: str,
    current_value: Any,
    expected: Any,
    options: Iterable[Any],
) -> bool:
    normalized_value = _normalize_rule_value(current_value)
    normalized_expected = _normalize_rule_value(expected)
    normalized_options = {_normalize_rule_value(option) for option in options}

    if match == "equals":
        return normalized_value == normalized_expected
    if match == "not_equals":
        return normalized_value != normalized_expected
    if match == "in":
        return normalized_value in normalized_options
    if match == "not_in":
        return normalized_value not in normalized_options
    if match == "truthy":
        return bool(current_value)
    return False


def _get_nested_value(mapping: Any, field_path: str) -> Any:
    if not field_path:
        return None
    current = mapping
    for segment in field_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _mapping_has_path(mapping: Any, field_path: str) -> bool:
    if not field_path:
        return False
    current = mapping
    for segment in field_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current.get(segment)
    return True


def _normalize_rule_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _document_label(doc_key: str) -> str:
    normalized = str(doc_key or "").strip()
    return IDENTITY_DOC_LABELS.get(normalized, normalized.replace("_", " ").title())


def _humanize_field_path(field_path: str) -> str:
    normalized = str(field_path or "").strip().replace(".", " ").replace("_", " ")
    return normalized.title() if normalized else "Value"


def _describe_rule_match(*, field_label: str, current_value: Any) -> str:
    formatted_value = _format_rule_value(current_value)
    if formatted_value == "blank":
        return f"{field_label} is blank."
    return f"{field_label} is set to {formatted_value}."


def _format_rule_value(value: Any) -> str:
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    text = str(value).strip()
    if not text:
        return "blank"
    return f"`{text}`"


def _build_target_provenance_summary(artifacts: list[Dict[str, Any]]) -> str | None:
    labels: list[str] = []
    for artifact in artifacts:
        label = str(artifact.get("artifactLabel") or "").strip()
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return None
    return f"Already satisfied by {_join_labels(labels)}."


def _join_labels(labels: Iterable[str]) -> str:
    parts = [str(label or "").strip() for label in labels if str(label or "").strip()]
    if not parts:
        return "current state"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


# ─── draft-derivation helpers ────────────────────────────────────────────


def _mapping_matches_preset(
    current_values: Mapping[str, Any],
    preset_values: Mapping[str, Any],
) -> bool:
    return all(
        _normalize_rule_value(current_values.get(key)) == _normalize_rule_value(value)
        for key, value in preset_values.items()
    )


def _derive_companion_style(
    *,
    profile: Mapping[str, Any],
    personality_frontmatter: Mapping[str, Any],
) -> str:
    for style, preset in GUIDED_SETUP_STYLE_PRESETS.items():
        if _mapping_matches_preset(personality_frontmatter, preset["personality"]):
            return style

    preset = str(profile.get("personality_preset") or "default").strip().lower()
    preset_map = {
        "default": "warm",
        "professional": "direct",
        "casual_slang": "relaxed",
        "minimalist": "minimal",
        "zen": "calm",
    }
    return preset_map.get(preset, "warm")


def _derive_autonomy_preset(*, action_frontmatter: Mapping[str, Any]) -> str:
    for autonomy_preset, preset in GUIDED_SETUP_AUTONOMY_PRESETS.items():
        if _mapping_matches_preset(action_frontmatter, preset):
            return autonomy_preset

    autonomy_mode = str(action_frontmatter.get("autonomy_mode") or "").strip().lower()
    allow_privileged = bool(action_frontmatter.get("allow_privileged_cli_actions"))
    allow_low_risk = bool(action_frontmatter.get("allow_silent_low_risk_actions"))

    if autonomy_mode == "ask_first" and not allow_privileged and not allow_low_risk:
        return "cautious"
    if autonomy_mode == "trusted_autonomous" or allow_privileged or allow_low_risk:
        return "trusted"
    return "balanced"


def _proactive_enabled(preferences: Mapping[str, Any]) -> bool:
    consent = preferences.get("proactive_consent")
    if isinstance(consent, Mapping):
        return bool(consent.get("enabled"))
    if isinstance(consent, bool):
        return consent
    return True


def _derive_checkin_posture(preferences: Mapping[str, Any]) -> str:
    if not _proactive_enabled(preferences):
        return "off"

    raw_freq = str(preferences.get("check_in_frequency") or "normal").strip().lower()
    if raw_freq in {"never", "off"}:
        return "off"
    if raw_freq in {"low", "weekly", "rare"}:
        return "rare"
    if raw_freq in {"high", "hourly", "frequent"}:
        return "frequent"
    return "normal"


def _project_dashboard_checkin_frequency(checkin_posture: str) -> str:
    if checkin_posture in {"off", "rare"}:
        return "low"
    if checkin_posture == "frequent":
        return "high"
    return "normal"


def _derive_voice_posture(
    *,
    preferences: Mapping[str, Any],
    voice_frontmatter: Mapping[str, Any],
) -> str:
    if not bool(preferences.get("voice_enabled", True)):
        return "off"

    for voice_posture, preset in GUIDED_SETUP_VOICE_POSTURES.items():
        if voice_posture == "off":
            continue
        if _mapping_matches_preset(voice_frontmatter, preset["voice_preferences"]):
            return voice_posture

    interaction_mode = str(voice_frontmatter.get("interaction_mode") or "").strip().lower()
    presence_profile = str(voice_frontmatter.get("presence_profile") or "").strip().lower()
    local_transport_mode = str(voice_frontmatter.get("local_transport_mode") or "").strip().lower()

    if interaction_mode == "passive" or presence_profile == "social" or local_transport_mode == "passive":
        return "social"
    if presence_profile == "quiet" or local_transport_mode == "quiet":
        return "quiet"
    return "balanced"


def _derive_relationship_style(
    *, personality_frontmatter: Mapping[str, Any],
) -> str:
    relationship_style = str(personality_frontmatter.get("relationship_style") or "").strip().lower()
    if not relationship_style:
        return ""

    matches = [
        option
        for option, configured_style in GUIDED_SETUP_RELATIONSHIP_STYLES.items()
        if str(configured_style or "").strip().lower() == relationship_style
    ]
    if len(matches) == 1:
        return matches[0]
    return ""


# ─── public dashboard API ────────────────────────────────────────────────


async def get_guided_setup_state(
    *,
    account_user_id: Optional[int],
    subject_user_id: Optional[int],
    identity_platform: str,
    profile: Optional[Dict[str, Any]] = None,
    status_actor_id: str = "guided_setup_state",
    status_reason: str = "guided_setup_state",
) -> GuidedSetupState:
    doc = _load_document()
    if account_user_id is None:
        return GuidedSetupState(
            doc=doc,
            current_step=_COMPLETE_STEP,
            completed_steps=["profile_setup"],
            setup_completed=True,
            profile=dict(profile or {}),
        )

    storage_user_id = int(subject_user_id if subject_user_id is not None else account_user_id)
    storage_platform = str(identity_platform or "owner").strip().lower() or "owner"
    profile_platform = "discord" if storage_platform == "owner" else storage_platform
    resolved_profile = dict(profile or await brain.get_user_profile(storage_user_id, profile_platform) or {})

    progress = await brain.get_onboarding_progress(int(account_user_id)) or {}
    current_step = str(progress.get("current_step") or _ACTIVE_STEP).strip() or _ACTIVE_STEP
    setup_completed = bool(progress.get("profile_setup_completed"))

    bundle = await identity_service.ensure_user_identity_bundle(
        user_id=storage_user_id,
        platform=storage_platform,
        profile=resolved_profile,
        actor_type="system",
        actor_id=status_actor_id,
        reason=status_reason,
    )
    completed_targets = set(_normalize_completed_targets(progress.get("completed_steps")))
    target_provenance = _build_target_provenance(
        doc=doc,
        storage_user_id=storage_user_id,
        storage_platform=storage_platform,
        profile=resolved_profile,
        owner_profile=resolved_profile,
        bundle=bundle,
    )
    completed_targets.update(_completed_targets_from_provenance(target_provenance))

    if _all_required_complete(doc, completed_targets):
        if not setup_completed or current_step != _COMPLETE_STEP:
            await _mark_complete(int(account_user_id), doc=doc, completed_targets=completed_targets)
        setup_completed = True
        current_step = _COMPLETE_STEP

    completed_step_targets = set(completed_targets)
    if setup_completed:
        completed_step_targets.add("profile_setup")

    return GuidedSetupState(
        doc=doc,
        current_step=current_step,
        completed_steps=_stable_completed_step_order(
            doc=doc, completed_targets=completed_step_targets
        ),
        setup_completed=setup_completed,
        profile=resolved_profile,
        bundle=bundle,
        target_provenance=target_provenance,
    )


def build_guided_setup_contract(*, state: GuidedSetupState | None = None) -> Dict[str, Any]:
    doc = _load_document()
    ordered_targets = [
        target
        for target in doc.question_order
        if target in GUIDED_SETUP_TARGET_UI
    ]
    completed_targets = set(getattr(state, "completed_steps", []) or [])
    target_provenance = dict(getattr(state, "target_provenance", {}) or {})

    questions: list[Dict[str, Any]] = []
    for target in ordered_targets:
        ui_meta = dict(GUIDED_SETUP_TARGET_UI.get(target) or {})
        option_meta = dict(GUIDED_SETUP_OPTION_UI.get(target) or {})
        question = {
            "target": target,
            "title": str(ui_meta.get("title") or target.replace("_", " ").title()),
            "prompt": str(ui_meta.get("prompt") or target.replace("_", " ").title()),
            "note": str(doc.target_notes.get(target) or target.replace("_", " ")),
            "inputKind": str(ui_meta.get("input_kind") or "single_choice"),
            "placeholder": str(ui_meta.get("placeholder") or "").strip() or None,
            "required": target in set(doc.required_targets),
            "options": [
                {
                    "value": option,
                    "label": str(option_meta.get(option, {}).get("label") or option.replace("_", " ").title()),
                    "description": str(option_meta.get(option, {}).get("description") or "").strip() or None,
                }
                for option in list(doc.question_options.get(target) or [])
            ],
        }
        if state is not None:
            provenance = dict(target_provenance.get(target) or {})
            question["provenance"] = {
                "completed": target in completed_targets or bool(provenance.get("completed")),
                "summary": provenance.get("summary"),
                "artifacts": list(provenance.get("artifacts") or []),
            }
        questions.append(question)

    return {
        "questionOrder": ordered_targets,
        "questions": questions,
        "maxQuestionsPerTurn": max(1, int(doc.max_questions_per_turn or 1)),
    }


def build_guided_setup_draft(
    *,
    profile: Mapping[str, Any],
    bundle: Any,
) -> Dict[str, Any]:
    preferences = dict(profile.get("preferences") or {})
    user_documents = getattr(bundle, "user_documents", {}) or {}

    personality_frontmatter = dict(
        getattr(user_documents.get("personality_profile"), "frontmatter", {}) or {}
    )
    action_frontmatter = dict(
        getattr(user_documents.get("action_preferences"), "frontmatter", {}) or {}
    )
    voice_frontmatter = dict(
        getattr(user_documents.get("voice_preferences"), "frontmatter", {}) or {}
    )

    checkin_posture = _derive_checkin_posture(preferences)
    voice_posture = _derive_voice_posture(
        preferences=preferences,
        voice_frontmatter=voice_frontmatter,
    )

    return {
        "name": str(profile.get("display_name") or profile.get("username") or "").strip(),
        "companionStyle": _derive_companion_style(
            profile=profile,
            personality_frontmatter=personality_frontmatter,
        ),
        "autonomyPreset": _derive_autonomy_preset(action_frontmatter=action_frontmatter),
        "relationshipStyle": _derive_relationship_style(
            personality_frontmatter=personality_frontmatter,
        ),
        "checkinPosture": checkin_posture,
        "voicePosture": voice_posture,
        "proactiveEnabled": checkin_posture != "off",
        "checkInFreq": _project_dashboard_checkin_frequency(checkin_posture),
        "voiceEnabled": voice_posture != "off",
    }


def derive_dashboard_checkin_posture(
    *,
    proactive_enabled: bool,
    check_in_freq: str,
) -> str:
    if not proactive_enabled:
        return "off"

    normalized = str(check_in_freq or "").strip().lower()
    if normalized == "high":
        return "frequent"
    if normalized == "low":
        return "rare"
    return "normal"


def derive_dashboard_voice_posture(*, voice_enabled: bool) -> str:
    return "balanced" if voice_enabled else "off"


async def persist_guided_setup_completion(
    *,
    account_user_id: int,
    completed_targets: Iterable[str],
) -> None:
    doc = _load_document()
    normalized_targets = set(_normalize_completed_targets(completed_targets))
    await _mark_complete(int(account_user_id), doc=doc, completed_targets=normalized_targets)
