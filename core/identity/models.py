from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class IdentityDocument:
    doc_key: str
    path: str
    frontmatter: Dict[str, Any]
    content: str
    policy: Dict[str, Any] = field(default_factory=dict)
    editable: bool = False


@dataclass(slots=True)
class IdentityBundle:
    user_id: int
    platform: str
    core_documents: Dict[str, IdentityDocument] = field(default_factory=dict)
    user_documents: Dict[str, IdentityDocument] = field(default_factory=dict)

    @property
    def all_documents(self) -> Dict[str, IdentityDocument]:
        merged = {}
        merged.update(self.core_documents)
        merged.update(self.user_documents)
        return merged


@dataclass(slots=True)
class IdentityVersionRecord:
    doc_key: str
    version: int
    actor_type: str
    actor_id: str
    reason: str
    content: str
    frontmatter: Dict[str, Any]
    created_at: float


@dataclass(slots=True)
class IdentityCompileWarning:
    code: str
    message: str
    doc_key: str | None = None
    field: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "docKey": self.doc_key,
            "field": self.field,
        }


@dataclass(slots=True)
class CompiledDocumentView:
    doc_key: str
    label: str
    path: str
    version: int
    platform: str
    user_id: int
    owner_user_id: int | None
    policy: Dict[str, Any] = field(default_factory=dict)
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    content: str = ""
    excerpt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "docKey": self.doc_key,
            "label": self.label,
            "path": self.path,
            "version": self.version,
            "platform": self.platform,
            "userId": self.user_id,
            "ownerUserId": self.owner_user_id,
            "policy": dict(self.policy),
            "frontmatter": dict(self.frontmatter),
            "content": self.content,
            "excerpt": self.excerpt,
        }


@dataclass(slots=True)
class CompiledIdentityProvenanceEntry:
    doc_key: str
    label: str
    path: str
    version: int
    platform: str
    user_id: int
    owner_user_id: int | None
    policy: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "docKey": self.doc_key,
            "label": self.label,
            "path": self.path,
            "version": self.version,
            "platform": self.platform,
            "userId": self.user_id,
            "ownerUserId": self.owner_user_id,
            "policy": dict(self.policy),
        }


@dataclass(slots=True)
class TextIdentitySnapshot:
    prompt_context: str = ""
    documents: Dict[str, CompiledDocumentView] = field(default_factory=dict)
    traits: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    warnings: List[IdentityCompileWarning] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promptContext": self.prompt_context,
            "documents": {key: doc.to_dict() for key, doc in self.documents.items()},
            "traits": {key: dict(value) for key, value in self.traits.items()},
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(slots=True)
class VoiceIdentitySnapshot:
    prompt_context: str = ""
    documents: Dict[str, CompiledDocumentView] = field(default_factory=dict)
    preferences: Dict[str, Any] = field(default_factory=dict)
    action_coupling: Dict[str, Any] = field(default_factory=dict)
    materialized: bool = True
    warnings: List[IdentityCompileWarning] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promptContext": self.prompt_context,
            "documents": {key: doc.to_dict() for key, doc in self.documents.items()},
            "preferences": dict(self.preferences),
            "actionCoupling": dict(self.action_coupling),
            "materialized": self.materialized,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(slots=True)
class ActionIdentitySnapshot:
    prompt_context: str = ""
    documents: Dict[str, CompiledDocumentView] = field(default_factory=dict)
    preferences: Dict[str, Any] = field(default_factory=dict)
    relationship: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    routines: Dict[str, Any] = field(default_factory=dict)
    materialized: bool = True
    warnings: List[IdentityCompileWarning] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promptContext": self.prompt_context,
            "documents": {key: doc.to_dict() for key, doc in self.documents.items()},
            "preferences": dict(self.preferences),
            "relationship": dict(self.relationship),
            "memory": dict(self.memory),
            "routines": dict(self.routines),
            "materialized": self.materialized,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(slots=True)
class CompiledActionPolicy:
    materialized: bool
    autonomy_mode: str
    initiative_level: str
    post_action_reporting_mode: str
    interruption_threshold: str
    standing_orders_policy: str
    followthrough_intensity: str
    environment_optimization_mode: str
    background_watch_mode: str
    external_contact_policy: str
    social_context_sensitivity: str
    quiet_hours_mode: str
    group_voice_behavior: str
    presence_awareness_mode: str
    arrival_useful_context_policy: str
    ambient_discord_delivery_policy: str
    ambient_voice_output_policy: str
    operator_initiative_mode: str
    operator_autonomy_profile: str
    operator_active_workspace_mode: str
    allow_bounded_reflection: bool
    allow_proactive_followups: bool
    allow_silent_beneficial_actions: bool
    allow_silent_low_risk_actions: bool
    allow_privileged_cli_actions: bool
    allow_package_install_actions: bool
    allow_schedule_creation: bool
    allow_email_triage_actions: bool
    allow_research_and_alert_loops: bool
    require_confirmation_for_destructive_actions: bool
    require_confirmation_for_external_spending: bool
    allow_companion_agenda: bool = True
    effective_permissions: Dict[str, bool] = field(default_factory=dict)
    config_gates: Dict[str, bool] = field(default_factory=dict)
    warnings: List[IdentityCompileWarning] = field(default_factory=list)

    def as_preferences(self) -> Dict[str, Any]:
        return {
            "_materialized": self.materialized,
            "autonomy_mode": self.autonomy_mode,
            "initiative_level": self.initiative_level,
            "post_action_reporting_mode": self.post_action_reporting_mode,
            "interruption_threshold": self.interruption_threshold,
            "standing_orders_policy": self.standing_orders_policy,
            "followthrough_intensity": self.followthrough_intensity,
            "environment_optimization_mode": self.environment_optimization_mode,
            "background_watch_mode": self.background_watch_mode,
            "external_contact_policy": self.external_contact_policy,
            "social_context_sensitivity": self.social_context_sensitivity,
            "quiet_hours_mode": self.quiet_hours_mode,
            "group_voice_behavior": self.group_voice_behavior,
            "presence_awareness_mode": self.presence_awareness_mode,
            "arrival_useful_context_policy": self.arrival_useful_context_policy,
            "ambient_discord_delivery_policy": self.ambient_discord_delivery_policy,
            "ambient_voice_output_policy": self.ambient_voice_output_policy,
            "operator_initiative_mode": self.operator_initiative_mode,
            "operator_autonomy_profile": self.operator_autonomy_profile,
            "operator_active_workspace_mode": self.operator_active_workspace_mode,
            "allow_bounded_reflection": self.allow_bounded_reflection,
            "allow_companion_agenda": self.allow_companion_agenda,
            "allow_proactive_followups": self.allow_proactive_followups,
            "allow_silent_beneficial_actions": self.allow_silent_beneficial_actions,
            "allow_silent_low_risk_actions": self.allow_silent_low_risk_actions,
            "allow_privileged_cli_actions": self.allow_privileged_cli_actions,
            "allow_package_install_actions": self.allow_package_install_actions,
            "allow_schedule_creation": self.allow_schedule_creation,
            "allow_email_triage_actions": self.allow_email_triage_actions,
            "allow_research_and_alert_loops": self.allow_research_and_alert_loops,
            "require_confirmation_for_destructive_actions": self.require_confirmation_for_destructive_actions,
            "require_confirmation_for_external_spending": self.require_confirmation_for_external_spending,
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.as_preferences()
        payload["effectivePermissions"] = dict(self.effective_permissions)
        payload["configGates"] = dict(self.config_gates)
        payload["warnings"] = [warning.to_dict() for warning in self.warnings]
        return payload


@dataclass(slots=True)
class CompiledIdentity:
    """E.4 Wave 3 (2026-05-13): the single canonical compiled-identity type.

    Wave 1 introduced ``CompiledIdentityBundle`` (a heavyweight container
    with text/voice/action snapshots, action_policy, provenance, warnings,
    and a JSON-friendly ``to_dict()``). Wave 2 introduced this lightweight
    ``CompiledIdentity`` (raw ``core``/``owner`` doc dicts plus ``text_prompt``/
    ``action_view`` rendering helpers, with file-mtime caching; the
    ``voice_prompt`` helper was deleted in step 31 — the voice register
    rides the shared prompt) for the new ``core/turn/`` spine.

    E.4 Wave 3 unifies them: this dataclass now carries BOTH shapes' fields.
    The Wave-2 raw doc dicts (``core``, ``owner``) and renderer methods stay,
    so ``core/turn/`` consumers (``recall``, ``respond_tier0``, ``cadence``,
    ``decide``, ``reflect``, ``proactive``) keep working unchanged. The
    Wave-1 snapshots and ``to_dict()`` JSON shape stay too, so the web API,
    the ambient generator, voice/preferences, and reply_identity_context
    keep working unchanged. The legacy name ``CompiledIdentityBundle`` is
    gone — this is the only compiled-identity type.

    Cache key: ``compile_for_owner`` populates ``cache_key`` so repeated
    calls with no underlying file change return the same instance. Other
    producers (``compile_identity``, ``compile_identity_sync``) leave it
    at the default ``(0, 0.0)`` because they're tied to a request, not a
    long-lived owner-bound cache.
    """

    # Wave-1 fields (heavy bundle shape) — kept for the web API JSON
    # contract, the snapshot getters, and the ambient/reply identity
    # contexts that duck-type ``.text_snapshot``/``.voice_snapshot``/
    # ``.action_snapshot``/``.action_policy``.
    user_id: int = 0
    platform: str = ""
    owner_user_id: int | None = None
    text_snapshot: TextIdentitySnapshot = field(default_factory=TextIdentitySnapshot)
    voice_snapshot: VoiceIdentitySnapshot = field(default_factory=VoiceIdentitySnapshot)
    action_snapshot: ActionIdentitySnapshot = field(default_factory=ActionIdentitySnapshot)
    action_policy: Optional["CompiledActionPolicy"] = None
    provenance: Dict[str, CompiledIdentityProvenanceEntry] = field(default_factory=dict)
    warnings: List[IdentityCompileWarning] = field(default_factory=list)

    # Wave-2 fields (lightweight raw-docs shape) — kept for the new
    # ``core/turn/`` spine that reads ``compiled_identity.core/owner`` and
    # calls the renderer methods. ``owner_id`` mirrors ``user_id`` when
    # the bundle was compiled for an owner; ``cache_key`` is populated
    # only by ``compile_for_owner``.
    owner_id: int = 0
    core: Dict[str, IdentityDocument] = field(default_factory=dict)
    owner: Dict[str, IdentityDocument] = field(default_factory=dict)
    cache_key: tuple[int, float] = (0, 0.0)  # (owner_id, max_mtime_of_files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "userId": self.user_id,
            "platform": self.platform,
            "ownerUserId": self.owner_user_id,
            "snapshots": {
                "text": self.text_snapshot.to_dict(),
                "voice": self.voice_snapshot.to_dict(),
                "action": self.action_snapshot.to_dict(),
            },
            "policy": self.action_policy.to_dict() if self.action_policy else {},
            "provenance": {key: value.to_dict() for key, value in self.provenance.items()},
            "warnings": [warning.to_dict() for warning in self.warnings],
        }

    def text_prompt(self, *, exclude: tuple[str, ...] = ()) -> str:
        from core.identity.compiler import render_text_prompt
        return render_text_prompt(self, exclude=exclude)

    # Step 31 (spec §7): voice_prompt() deleted with render_voice_prompt —
    # the voice register rides the shared prompt (spec §4), never a
    # second renderer.

    def action_view(self) -> dict:
        from core.identity.compiler import render_action_view
        return render_action_view(self)
