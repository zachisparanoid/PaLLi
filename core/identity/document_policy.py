from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal

from .constants import IDENTITY_DOC_LABELS

EditOperation = Literal["raw_markdown", "structured", "silent_maintenance"]


@dataclass(frozen=True, slots=True)
class IdentityPolicyDescriptor:
    key: str
    label: str
    summary: str
    description: str

    def to_view(self) -> Dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "summary": self.summary,
            "description": self.description,
        }


_SCOPE_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "core": IdentityPolicyDescriptor(
        key="core",
        label="Core Layer",
        summary="Shared creator-authored identity foundations.",
        description="This document lives in PaLLi's shared core layer rather than in one owner's personal identity directory.",
    ),
    "user": IdentityPolicyDescriptor(
        key="user",
        label="Owner Layer",
        summary="Owner-specific identity material.",
        description="This document belongs to the current owner's identity workspace and can shape their local PaLLi instance directly.",
    ),
}

_OWNER_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "creator": IdentityPolicyDescriptor(
        key="creator",
        label="Creator Owned",
        summary="Defined by PaLLi's creator.",
        description="This layer is treated as a creator-authored foundation for the product line, not as an owner-customization surface.",
    ),
    "user": IdentityPolicyDescriptor(
        key="user",
        label="Owner Directed",
        summary="Set directly by the owner.",
        description="This layer is meant to reflect the owner's stated preferences rather than quiet system inference.",
    ),
    "shared": IdentityPolicyDescriptor(
        key="shared",
        label="Shared Preference Layer",
        summary="Owner-directed with shared runtime consumption.",
        description="This layer is still owner-directed, but it is consumed broadly across prompts, action policy, and runtime surfaces.",
    ),
    "system": IdentityPolicyDescriptor(
        key="system",
        label="System Maintained",
        summary="Maintained by PaLLi under policy guardrails.",
        description="This layer can evolve through controlled runtime maintenance instead of only through explicit owner editing.",
    ),
}

_OWNERSHIP_CLASS_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "creator_owned_stable": IdentityPolicyDescriptor(
        key="creator_owned_stable",
        label="Creator-Owned Stable",
        summary="Foundational identity that should not drift casually.",
        description="These documents capture PaLLi's stable constitutional or strategic identity layers and generally require creator/admin review to change.",
    ),
    "user_authored_preferences": IdentityPolicyDescriptor(
        key="user_authored_preferences",
        label="User-Authored Preferences",
        summary="Owner-directed settings for how PaLLi should behave.",
        description="These documents are the main preference layers the owner can shape directly through the dashboard, chat-guided setup, or raw markdown editing.",
    ),
    "agent_maintained_evolving": IdentityPolicyDescriptor(
        key="agent_maintained_evolving",
        label="Agent-Maintained Evolving",
        summary="Living summaries that PaLLi may maintain under guardrails.",
        description="These documents stay user-visible, but PaLLi can update them through safe maintenance flows to preserve continuity, memory, and relationship context over time.",
    ),
    "structured_operational_state": IdentityPolicyDescriptor(
        key="structured_operational_state",
        label="Structured Operational State",
        summary="Execution-derived recordkeeping rather than freeform identity shaping.",
        description="These documents behave more like append-only runtime history than a normal preference or personality layer.",
    ),
}

_MUTABILITY_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "constitutional": IdentityPolicyDescriptor(
        key="constitutional",
        label="Constitutional",
        summary="Deep identity foundation that should change rarely.",
        description="Use this for PaLLi's most stable identity commitments; edits should be deliberate and review-heavy.",
    ),
    "strategic": IdentityPolicyDescriptor(
        key="strategic",
        label="Strategic",
        summary="Stable guidance that can evolve deliberately.",
        description="This layer is still stable, but it can be revised carefully when product direction or creator intent materially changes.",
    ),
    "user_editable": IdentityPolicyDescriptor(
        key="user_editable",
        label="User Editable",
        summary="Direct owner customization layer.",
        description="The owner can update this layer through supported dashboard, chat, or raw-markdown paths without requiring silent system maintenance.",
    ),
    "system_evolving": IdentityPolicyDescriptor(
        key="system_evolving",
        label="System Evolving",
        summary="Quietly maintainable continuity layer.",
        description="PaLLi may update this layer through constrained maintenance flows, while still leaving it inspectable and correctable by the owner.",
    ),
    "append_only": IdentityPolicyDescriptor(
        key="append_only",
        label="Append Only",
        summary="Operational trace that grows instead of being rewritten.",
        description="This layer is meant to preserve execution history and provenance rather than act like a normal editable identity document.",
    ),
}

_REVIEW_REQUIREMENT_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "none": IdentityPolicyDescriptor(
        key="none",
        label="No Special Review",
        summary="No extra review gate is recorded.",
        description="The policy layer does not add a dedicated review posture beyond the normal edit permissions for this document.",
    ),
    "creator_review_required": IdentityPolicyDescriptor(
        key="creator_review_required",
        label="Creator Review Required",
        summary="Stable creator-owned layer that should not drift casually.",
        description="This document sits in PaLLi's stable creator-owned layer and should only move through reviewed creator/admin changes.",
    ),
    "user_directed": IdentityPolicyDescriptor(
        key="user_directed",
        label="User Directed",
        summary="Should change because the owner wants it to.",
        description="The owner is the expected source of truth for this layer, even when a structured helper writes the update on their behalf.",
    ),
    "safe_self_maintenance": IdentityPolicyDescriptor(
        key="safe_self_maintenance",
        label="Safe Self-Maintenance",
        summary="PaLLi may maintain this layer quietly under guardrails.",
        description="The runtime can update this document for continuity and memory upkeep, but it should remain user-visible and attributable.",
    ),
    "append_only_system": IdentityPolicyDescriptor(
        key="append_only_system",
        label="System Append Only",
        summary="Only the system should extend this operational record.",
        description="This layer captures operational trace data and should be appended by the runtime rather than manually rewritten.",
    ),
}

_EDIT_PATH_DESCRIPTORS: Dict[str, IdentityPolicyDescriptor] = {
    "dashboard": IdentityPolicyDescriptor(
        key="dashboard",
        label="Dashboard Forms",
        summary="Owner-facing structured controls.",
        description="This document can be shaped through the guided owner dashboard rather than only through raw markdown.",
    ),
    "chat": IdentityPolicyDescriptor(
        key="chat",
        label="Chat/Voice Updates",
        summary="Conversational setup or preference capture.",
        description="This document can be updated through chat/voice-driven preference flows in addition to dashboard forms.",
    ),
    "raw_markdown": IdentityPolicyDescriptor(
        key="raw_markdown",
        label="Raw Markdown Workspace",
        summary="Advanced direct document editing.",
        description="This document is exposed through the advanced raw-markdown editor for direct inspection and authoring.",
    ),
    "admin": IdentityPolicyDescriptor(
        key="admin",
        label="Admin Raw Edit",
        summary="Reserved for creator/admin maintenance.",
        description="This document is only meant to be changed through privileged creator/admin editing paths.",
    ),
    "system": IdentityPolicyDescriptor(
        key="system",
        label="System Managed",
        summary="Maintained by PaLLi's runtime.",
        description="This document is not a normal owner-edit surface; it is maintained by the runtime itself.",
    ),
}


@dataclass(frozen=True, slots=True)
class IdentityDocumentPolicy:
    doc_key: str
    scope: str
    owner: str
    ownership_class: str
    mutability: str
    visibility: str
    editable_via: tuple[str, ...]
    raw_markdown_editors: tuple[str, ...] = ()
    structured_editors: tuple[str, ...] = ()
    silent_maintenance_editors: tuple[str, ...] = ()
    review_requirement: str = "none"

    def allows(self, *, actor_type: str, operation: EditOperation) -> bool:
        editors = self._editors_for(operation)
        return str(actor_type or "").strip().lower() in editors

    def supports(self, operation: EditOperation) -> bool:
        return bool(self._editors_for(operation))

    def to_view(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "owner": self.owner,
            "ownershipClass": self.ownership_class,
            "mutability": self.mutability,
            "visibility": self.visibility,
            "editableVia": list(self.editable_via),
            "scopeInfo": _descriptor_view(_SCOPE_DESCRIPTORS, self.scope),
            "ownerInfo": _descriptor_view(_OWNER_DESCRIPTORS, self.owner),
            "ownershipClassInfo": _descriptor_view(_OWNERSHIP_CLASS_DESCRIPTORS, self.ownership_class),
            "mutabilityInfo": _descriptor_view(_MUTABILITY_DESCRIPTORS, self.mutability),
            "reviewRequirementInfo": _descriptor_view(_REVIEW_REQUIREMENT_DESCRIPTORS, self.review_requirement),
            "editableViaInfo": [_descriptor_view(_EDIT_PATH_DESCRIPTORS, key) for key in self.editable_via],
            "reviewRequirement": self.review_requirement,
            "rawMarkdownEditable": bool(self.raw_markdown_editors),
            "structuredEditable": bool(self.structured_editors),
            "silentMaintenanceAllowed": bool(self.silent_maintenance_editors),
            "dashboardEditable": "dashboard" in self.editable_via,
            "chatEditable": "chat" in self.editable_via,
            "allowedActors": {
                "rawMarkdown": list(self.raw_markdown_editors),
                "structured": list(self.structured_editors),
                "silentMaintenance": list(self.silent_maintenance_editors),
            },
        }

    def _editors_for(self, operation: EditOperation) -> tuple[str, ...]:
        if operation == "raw_markdown":
            return self.raw_markdown_editors
        if operation == "structured":
            return self.structured_editors
        return self.silent_maintenance_editors


IDENTITY_DOCUMENT_POLICIES: Dict[str, IdentityDocumentPolicy] = {
    "core_identity": IdentityDocumentPolicy(
        doc_key="core_identity",
        scope="core",
        owner="creator",
        ownership_class="creator_owned_stable",
        mutability="constitutional",
        visibility="user_visible",
        editable_via=("admin", "raw_markdown"),
        raw_markdown_editors=("admin", "creator"),
        review_requirement="creator_review_required",
    ),
    "operating_principles": IdentityDocumentPolicy(
        doc_key="operating_principles",
        scope="core",
        owner="creator",
        ownership_class="creator_owned_stable",
        mutability="constitutional",
        visibility="user_visible",
        editable_via=("admin", "raw_markdown"),
        raw_markdown_editors=("admin", "creator"),
        review_requirement="creator_review_required",
    ),
    "voice_identity": IdentityDocumentPolicy(
        doc_key="voice_identity",
        # 2026-08-13 — hers now (manifesto §2.8, owner-approved): she authors
        # it from her own record via the narrative organ; first authorship
        # through scripts/voice_identity_first_authorship.py with the owner
        # taste-gate. File resolves palli-first (document_store).
        scope="core",
        owner="palli",
        ownership_class="palli_owned_living",
        mutability="living",
        visibility="user_visible",
        editable_via=("admin", "raw_markdown"),
        raw_markdown_editors=("admin", "creator"),
        review_requirement="none",
    ),
    "capabilities_and_boundaries": IdentityDocumentPolicy(
        doc_key="capabilities_and_boundaries",
        scope="core",
        owner="creator",
        ownership_class="creator_owned_stable",
        mutability="strategic",
        visibility="user_visible",
        editable_via=("admin", "raw_markdown"),
        raw_markdown_editors=("admin", "creator"),
        review_requirement="creator_review_required",
    ),
    "personality_profile": IdentityDocumentPolicy(
        doc_key="personality_profile",
        scope="user",
        owner="user",
        ownership_class="user_authored_preferences",
        mutability="user_editable",
        visibility="user_visible",
        editable_via=("dashboard", "chat", "raw_markdown"),
        raw_markdown_editors=("user", "admin"),
        structured_editors=("user", "admin"),
        review_requirement="user_directed",
    ),
    "action_preferences": IdentityDocumentPolicy(
        doc_key="action_preferences",
        scope="user",
        owner="shared",
        ownership_class="user_authored_preferences",
        mutability="user_editable",
        visibility="user_visible",
        editable_via=("dashboard", "chat", "raw_markdown"),
        raw_markdown_editors=("user", "admin"),
        structured_editors=("user", "admin"),
        review_requirement="user_directed",
    ),
    "voice_preferences": IdentityDocumentPolicy(
        doc_key="voice_preferences",
        scope="user",
        owner="shared",
        ownership_class="user_authored_preferences",
        mutability="user_editable",
        visibility="user_visible",
        editable_via=("dashboard", "chat", "raw_markdown"),
        raw_markdown_editors=("user", "admin"),
        structured_editors=("user", "admin"),
        review_requirement="user_directed",
    ),
    # Phase B / B4 step 4 (2026-05-09) — audit findings IM-CRIT-1,
    # IM-CRIT-2, IM-HIGH-1: a structured destination for user-volunteered
    # preferences and personal facts that reflection captures during
    # conversation. Previously these landed in ``user_memory`` via
    # ``upsert_user_fact``, which polluted decay-ranked recall and
    # competed with the legacy ``analyze_and_store_facts`` pipeline.
    # Decision 3 in the completion design makes identity Markdown
    # documents canonical for preferences; this is the destination.
    #
    # Ownership shape: system-maintained (reflect writes silently) but
    # user-overridable (dashboard / chat preference flows can correct
    # auto-captured values), parallel to ``relationship_state`` and
    # ``memory`` rather than to ``personality_profile``. The structured
    # editors set includes ``user`` so the dashboard's preference editor
    # can land changes; ``silent_maintenance_editors`` includes
    # ``system`` so reflect's hot path works.
    "preferences": IdentityDocumentPolicy(
        doc_key="preferences",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("dashboard", "chat", "raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("user", "system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "relationship_state": IdentityDocumentPolicy(
        doc_key="relationship_state",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "memory": IdentityDocumentPolicy(
        doc_key="memory",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    # Batch 5 (memory that picks, 2026-09-28): the archive takes the same
    # permissions as memory — the nightly sweep writes both through the
    # store, and `palli_ctl memory restore` moves a line back.
    "memory_archive": IdentityDocumentPolicy(
        doc_key="memory_archive",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "memory_summary": IdentityDocumentPolicy(
        doc_key="memory_summary",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "routines": IdentityDocumentPolicy(
        doc_key="routines",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "standing_orders": IdentityDocumentPolicy(
        doc_key="standing_orders",
        scope="user",
        owner="system",
        ownership_class="agent_maintained_evolving",
        mutability="system_evolving",
        visibility="user_visible",
        editable_via=("raw_markdown", "silent_maintenance"),
        raw_markdown_editors=("user", "system", "admin"),
        structured_editors=("system", "admin"),
        silent_maintenance_editors=("system", "admin"),
        review_requirement="safe_self_maintenance",
    ),
    "changelog": IdentityDocumentPolicy(
        doc_key="changelog",
        scope="user",
        owner="system",
        ownership_class="structured_operational_state",
        mutability="append_only",
        visibility="user_visible",
        editable_via=("system",),
        review_requirement="append_only_system",
    ),
}


def get_document_policy(doc_key: str) -> IdentityDocumentPolicy:
    key = str(doc_key or "").strip()
    policy = IDENTITY_DOCUMENT_POLICIES.get(key)
    if policy is None:
        raise KeyError(f"Unknown identity document policy: {doc_key}")
    return policy


def document_policy_view(doc_key: str) -> Dict[str, Any]:
    return get_document_policy(doc_key).to_view()


def identity_governance_view() -> Dict[str, Any]:
    return {
        "documentOrder": list(IDENTITY_DOCUMENT_POLICIES.keys()),
        "documents": {
            doc_key: {
                "docKey": doc_key,
                "label": IDENTITY_DOC_LABELS.get(doc_key, doc_key.replace("_", " ").title()),
                "policy": policy.to_view(),
            }
            for doc_key, policy in IDENTITY_DOCUMENT_POLICIES.items()
        },
        "scopes": _descriptor_map_view(_SCOPE_DESCRIPTORS),
        "owners": _descriptor_map_view(_OWNER_DESCRIPTORS),
        "ownershipClasses": _descriptor_map_view(_OWNERSHIP_CLASS_DESCRIPTORS),
        "mutabilityStates": _descriptor_map_view(_MUTABILITY_DESCRIPTORS),
        "reviewRequirements": _descriptor_map_view(_REVIEW_REQUIREMENT_DESCRIPTORS),
        "editPaths": _descriptor_map_view(_EDIT_PATH_DESCRIPTORS),
    }


def apply_document_policy(
    *,
    doc_key: str,
    frontmatter: Dict[str, Any],
    user_id: int,
    platform: str,
    actor_type: str,
    owner_user_id: int | None = None,
    increment_version: bool = True,
) -> Dict[str, Any]:
    policy = get_document_policy(doc_key)
    normalized = dict(frontmatter or {})
    normalized["doc_type"] = doc_key
    normalized["scope"] = policy.scope
    normalized["owner"] = policy.owner
    normalized["ownership_class"] = policy.ownership_class
    normalized["mutability"] = policy.mutability
    normalized["visibility"] = policy.visibility
    normalized["editable_via"] = list(policy.editable_via)
    normalized["review_requirement"] = policy.review_requirement
    if policy.scope == "core":
        normalized["platform"] = "global"
        normalized["user_id"] = 0
        normalized.pop("owner_user_id", None)
    else:
        normalized["platform"] = platform
        normalized["user_id"] = int(user_id)
        if owner_user_id is not None:
            normalized["owner_user_id"] = int(owner_user_id)
        else:
            normalized.pop("owner_user_id", None)
    normalized["last_updated_by"] = actor_type
    version = _coerce_version(normalized.get("version"))
    normalized["version"] = version + 1 if increment_version else max(version, 1)
    return normalized


def assert_document_edit_allowed(
    *,
    doc_key: str,
    actor_type: str,
    operation: EditOperation,
    allow_admin_override: bool = False,
) -> None:
    actor = str(actor_type or "").strip().lower()
    policy = get_document_policy(doc_key)
    if policy.allows(actor_type=actor, operation=operation):
        return
    if allow_admin_override and actor == "admin":
        return
    raise PermissionError(
        f"Document `{doc_key}` does not allow `{operation}` edits by actor `{actor or 'unknown'}`."
    )


def user_facing_raw_markdown_editable(doc_key: str) -> bool:
    return get_document_policy(doc_key).allows(actor_type="user", operation="raw_markdown")


def supports_silent_maintenance(doc_key: str) -> bool:
    return get_document_policy(doc_key).supports("silent_maintenance")


def _coerce_version(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _descriptor_view(
    mapping: Dict[str, IdentityPolicyDescriptor],
    key: str,
) -> Dict[str, str]:
    descriptor = mapping.get(str(key or "").strip())
    if descriptor is None:
        normalized = str(key or "").strip()
        return {
            "key": normalized,
            "label": normalized.replace("_", " ").title() if normalized else "Unknown",
            "summary": "",
            "description": "",
        }
    return descriptor.to_view()


def _descriptor_map_view(
    mapping: Dict[str, IdentityPolicyDescriptor],
) -> Dict[str, Dict[str, str]]:
    return {key: descriptor.to_view() for key, descriptor in mapping.items()}
