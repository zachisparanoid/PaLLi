from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from core.config import config
from core.memory_bullets import _split_bullet, _stamp_time, item_id_for

from .constants import (
    CORE_DOCS,
    DEFAULT_VOICE_PREFERENCES,
    IDENTITY_DOC_LABELS,
    IDENTITY_DOC_PREAMBLES,
    LEGACY_ACTION_SETTINGS,
    PROMPT_DOCS_ACTION,
    PROMPT_DOCS_TEXT,
    PROMPT_DOCS_VOICE,
)
from .document_policy import document_policy_view
from .models import (
    ActionIdentitySnapshot,
    CompiledActionPolicy,
    CompiledDocumentView,
    CompiledIdentity,
    CompiledIdentityProvenanceEntry,
    IdentityBundle,
    IdentityCompileWarning,
    IdentityDocument,
    TextIdentitySnapshot,
    VoiceIdentitySnapshot,
)

_METADATA_KEYS = {
    "doc_type",
    "scope",
    "owner",
    "ownership_class",
    "mutability",
    "visibility",
    "editable_via",
    "review_requirement",
    "platform",
    "user_id",
    "owner_user_id",
    "version",
    "last_updated_by",
}


def compile_identity(bundle: IdentityBundle) -> CompiledIdentity:
    """Compile a raw IdentityBundle (markdown reader output) into the
    canonical CompiledIdentity carrier.

    E.4 Wave 3 (2026-05-13): formerly returned the Wave-1
    ``CompiledIdentityBundle``; now returns the unified
    :class:`CompiledIdentity` which carries both the snapshot triple +
    provenance + warnings (Wave-1 contract for the web API and the
    ambient/reply identity contexts) and the raw ``core``/``owner`` doc
    dicts (Wave-2 contract for the new ``core/turn/`` spine).
    """
    document_views = {
        doc_key: _compile_document_view(doc_key, doc)
        for doc_key, doc in bundle.all_documents.items()
    }
    provenance = {
        doc_key: CompiledIdentityProvenanceEntry(
            doc_key=doc_key,
            label=view.label,
            path=view.path,
            version=view.version,
            platform=view.platform,
            user_id=view.user_id,
            owner_user_id=view.owner_user_id,
            policy=dict(view.policy),
        )
        for doc_key, view in document_views.items()
    }

    text_snapshot = _build_text_snapshot(document_views)
    voice_snapshot = _build_voice_snapshot(document_views)
    action_snapshot = _build_action_snapshot(document_views)
    action_policy = _build_action_policy(action_snapshot)

    warnings: List[IdentityCompileWarning] = []
    warnings.extend(text_snapshot.warnings)
    warnings.extend(voice_snapshot.warnings)
    warnings.extend(action_snapshot.warnings)
    warnings.extend(action_policy.warnings)

    owner_user_id = bundle.user_id if bundle.platform == "owner" else None
    # E.4 Wave 3: populate BOTH the Wave-1 snapshot fields AND the Wave-2
    # raw-doc dicts on the returned CompiledIdentity so every downstream
    # consumer sees the data it expects without per-call shape adapters.
    return CompiledIdentity(
        user_id=bundle.user_id,
        platform=bundle.platform,
        owner_user_id=owner_user_id,
        text_snapshot=text_snapshot,
        voice_snapshot=voice_snapshot,
        action_snapshot=action_snapshot,
        action_policy=action_policy,
        provenance=provenance,
        warnings=warnings,
        owner_id=bundle.user_id if bundle.platform == "owner" else 0,
        core=dict(bundle.core_documents),
        owner=dict(bundle.user_documents),
    )


def render_prompt_excerpt(doc: IdentityDocument | CompiledDocumentView) -> str:
    """A doc as it reads in a prompt: its frontmatter as ``- key: value``
    bullets (metadata keys left out), then its body.

    The four core docs (CORE_DOCS) render their body alone (Batch 7, her
    voice, task 2, 2026-10-04; audit 4.1). Their frontmatter list
    (``core_form: digital_parrot``, ``reply_style: concise_and_spoken``, ...)
    was a spec sheet read to her on every reply. The keys stay in the files
    and in the compiled view's frontmatter, where code reads the voice ones;
    only the rendering stops. Owner docs keep their bullets."""
    excerpt_lines: List[str] = []
    if doc.doc_key not in CORE_DOCS:
        for key, value in (doc.frontmatter or {}).items():
            if key in _METADATA_KEYS:
                continue
            excerpt_lines.append(f"- {key}: {value}")
    body = (doc.content or "").strip()
    if body:
        excerpt_lines.append("")
        excerpt_lines.append(body)
    return "\n".join(excerpt_lines).strip()


def _compile_document_view(doc_key: str, doc: IdentityDocument) -> CompiledDocumentView:
    frontmatter = dict(doc.frontmatter or {})
    return CompiledDocumentView(
        doc_key=doc_key,
        label=IDENTITY_DOC_LABELS.get(doc_key, doc_key.replace("_", " ").title()),
        path=doc.path,
        version=int(frontmatter.get("version", 0) or 0),
        platform=str(frontmatter.get("platform") or ""),
        user_id=int(frontmatter.get("user_id", 0) or 0),
        owner_user_id=_optional_int(frontmatter.get("owner_user_id")),
        policy=document_policy_view(doc_key),
        frontmatter=_public_frontmatter(frontmatter),
        content=doc.content or "",
        excerpt=render_prompt_excerpt(doc),
    )


def _build_text_snapshot(document_views: Dict[str, CompiledDocumentView]) -> TextIdentitySnapshot:
    warnings: List[IdentityCompileWarning] = []
    docs = _select_documents(document_views, PROMPT_DOCS_TEXT, warnings=warnings)
    traits = {
        doc_key: dict(doc.frontmatter)
        for doc_key, doc in docs.items()
        if doc_key in {"personality_profile", "relationship_state", "memory_summary"}
    }
    return TextIdentitySnapshot(
        prompt_context=_render_prompt_context(docs.values()),
        documents=docs,
        traits=traits,
        warnings=warnings,
    )


def _build_voice_snapshot(document_views: Dict[str, CompiledDocumentView]) -> VoiceIdentitySnapshot:
    warnings: List[IdentityCompileWarning] = []
    docs = _select_documents(document_views, PROMPT_DOCS_VOICE, warnings=warnings)
    voice_doc = docs.get("voice_preferences")
    action_doc = docs.get("action_preferences")
    preferences = dict(DEFAULT_VOICE_PREFERENCES)
    materialized = voice_doc is not None
    if voice_doc:
        preferences.update(voice_doc.frontmatter)
    else:
        warnings.append(
            IdentityCompileWarning(
                code="voice_preferences_defaulted",
                message="Voice preferences document not materialized; using default voice settings.",
                doc_key="voice_preferences",
            )
        )

    action_coupling = {}
    if action_doc:
        action_coupling = {
            "group_voice_behavior": action_doc.frontmatter.get("group_voice_behavior"),
            "autonomy_mode": action_doc.frontmatter.get("autonomy_mode"),
            "initiative_level": action_doc.frontmatter.get("initiative_level"),
            "presence_awareness_mode": action_doc.frontmatter.get("presence_awareness_mode"),
            "arrival_useful_context_policy": action_doc.frontmatter.get("arrival_useful_context_policy"),
            "ambient_voice_output_policy": action_doc.frontmatter.get("ambient_voice_output_policy"),
            "operator_initiative_mode": action_doc.frontmatter.get("operator_initiative_mode"),
            "operator_autonomy_profile": action_doc.frontmatter.get("operator_autonomy_profile"),
            "operator_active_workspace_mode": action_doc.frontmatter.get("operator_active_workspace_mode"),
        }

    return VoiceIdentitySnapshot(
        prompt_context=_render_prompt_context(docs.values()),
        documents=docs,
        preferences=preferences,
        action_coupling=action_coupling,
        materialized=materialized,
        warnings=warnings,
    )


def _build_action_snapshot(document_views: Dict[str, CompiledDocumentView]) -> ActionIdentitySnapshot:
    warnings: List[IdentityCompileWarning] = []
    docs = _select_documents(document_views, PROMPT_DOCS_ACTION, warnings=warnings)
    action_doc = docs.get("action_preferences")
    preferences = dict(LEGACY_ACTION_SETTINGS)
    materialized = action_doc is not None
    if action_doc:
        preferences.update(action_doc.frontmatter)
    else:
        warnings.append(
            IdentityCompileWarning(
                code="action_preferences_defaulted",
                message="Action preferences document not materialized; using legacy-safe action defaults.",
                doc_key="action_preferences",
            )
        )

    return ActionIdentitySnapshot(
        prompt_context=_render_prompt_context(docs.values()),
        documents=docs,
        preferences=preferences,
        relationship=dict(docs.get("relationship_state").frontmatter) if docs.get("relationship_state") else {},
        memory=dict(docs.get("memory_summary").frontmatter) if docs.get("memory_summary") else {},
        routines=dict(docs.get("routines").frontmatter) if docs.get("routines") else {},
        materialized=materialized,
        warnings=warnings,
    )


def _build_action_policy(action_snapshot: ActionIdentitySnapshot) -> CompiledActionPolicy:
    prefs = dict(action_snapshot.preferences)
    config_gates = {
        "autonomous_cli": bool(getattr(config, "AGENTIC_ALLOW_AUTONOMOUS_CLI", True)),
        "privileged_cli": bool(getattr(config, "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", False)),
        "package_installs": bool(getattr(config, "AGENTIC_ALLOW_PACKAGE_INSTALLS", False)),
        "package_install_confirm_required": bool(getattr(config, "AGENTIC_INSTALL_CONFIRM_REQUIRED", True)),
    }
    warnings = list(action_snapshot.warnings)

    if prefs.get("allow_privileged_cli_actions") and not config_gates["privileged_cli"]:
        warnings.append(
            IdentityCompileWarning(
                code="privileged_cli_gated_by_config",
                message="Privileged CLI autonomy is enabled in identity, but blocked by runtime config.",
                doc_key="action_preferences",
                field="allow_privileged_cli_actions",
            )
        )
    if prefs.get("allow_package_install_actions") and not config_gates["package_installs"]:
        warnings.append(
            IdentityCompileWarning(
                code="package_install_gated_by_config",
                message="Package installs are enabled in identity, but blocked by runtime config.",
                doc_key="action_preferences",
                field="allow_package_install_actions",
            )
        )

    environment_mode = str(prefs.get("environment_optimization_mode") or "safe_autonomous").strip().lower()
    background_watch_mode = str(prefs.get("background_watch_mode") or "off").strip().lower()
    external_contact_policy = str(prefs.get("external_contact_policy") or "confirm_first").strip().lower()

    effective_permissions = {
        "bounded_reflection": bool(getattr(config, "ENABLE_BOUNDED_REFLECTION", True)) and bool(prefs.get("allow_bounded_reflection")),
        # 2026-05-12 — Open-Threads Agenda (T17): the legacy
        # ENABLE_COMPANION_AGENDA config knob was deleted (agenda is now
        # unconditional). The user-side preference still gates whether the
        # owner has opted into the companion-style agenda behavior.
        "companion_agenda": bool(prefs.get("allow_companion_agenda", True)),
        "proactive_outreach": bool(prefs.get("allow_proactive_followups")) and background_watch_mode in {"selected_topics", "enabled"},
        "background_watch": background_watch_mode in {"selected_topics", "enabled"},
        "schedule_creation": bool(prefs.get("allow_schedule_creation")),
        "silent_beneficial_actions": bool(prefs.get("allow_silent_beneficial_actions")),
        "silent_low_risk_actions": bool(prefs.get("allow_silent_low_risk_actions")) and environment_mode not in {"observe_only", "suggest_only"},
        "privileged_cli_autonomy": (
            config_gates["autonomous_cli"]
            and config_gates["privileged_cli"]
            and bool(prefs.get("allow_privileged_cli_actions"))
            and environment_mode == "full_trusted"
        ),
        "package_install_autonomy": (
            config_gates["autonomous_cli"]
            and config_gates["package_installs"]
            and bool(prefs.get("allow_package_install_actions"))
            and environment_mode == "full_trusted"
            and not config_gates["package_install_confirm_required"]
        ),
        "research_loops": bool(prefs.get("allow_research_and_alert_loops")),
        "email_triage": bool(prefs.get("allow_email_triage_actions")),
        "external_contact_without_confirmation": external_contact_policy == "known_contacts",
    }

    return CompiledActionPolicy(
        materialized=bool(action_snapshot.materialized),
        autonomy_mode=str(prefs.get("autonomy_mode") or "ask_first"),
        initiative_level=str(prefs.get("initiative_level") or "medium"),
        post_action_reporting_mode=str(prefs.get("post_action_reporting_mode") or "digest"),
        interruption_threshold=str(prefs.get("interruption_threshold") or "urgent_only"),
        standing_orders_policy=str(prefs.get("standing_orders_policy") or "explicit_only"),
        followthrough_intensity=str(prefs.get("followthrough_intensity") or "steady"),
        environment_optimization_mode=environment_mode,
        background_watch_mode=background_watch_mode,
        external_contact_policy=external_contact_policy,
        social_context_sensitivity=str(prefs.get("social_context_sensitivity") or "medium"),
        quiet_hours_mode=str(prefs.get("quiet_hours_mode") or "adaptive"),
        group_voice_behavior=str(prefs.get("group_voice_behavior") or "socially_adaptive"),
        presence_awareness_mode=str(prefs.get("presence_awareness_mode") or "arrival_only"),
        arrival_useful_context_policy=str(prefs.get("arrival_useful_context_policy") or "one_item"),
        ambient_discord_delivery_policy=str(prefs.get("ambient_discord_delivery_policy") or "dm_only"),
        ambient_voice_output_policy=str(prefs.get("ambient_voice_output_policy") or "local_speaker"),
        operator_initiative_mode=str(prefs.get("operator_initiative_mode") or "proactive"),
        operator_autonomy_profile=str(prefs.get("operator_autonomy_profile") or "high_autonomy"),
        operator_active_workspace_mode=str(prefs.get("operator_active_workspace_mode") or "recent_context"),
        allow_bounded_reflection=bool(prefs.get("allow_bounded_reflection")),
        allow_companion_agenda=bool(prefs.get("allow_companion_agenda", True)),
        allow_proactive_followups=bool(prefs.get("allow_proactive_followups")),
        allow_silent_beneficial_actions=bool(prefs.get("allow_silent_beneficial_actions")),
        allow_silent_low_risk_actions=bool(prefs.get("allow_silent_low_risk_actions")),
        allow_privileged_cli_actions=bool(prefs.get("allow_privileged_cli_actions")),
        allow_package_install_actions=bool(prefs.get("allow_package_install_actions")),
        allow_schedule_creation=bool(prefs.get("allow_schedule_creation")),
        allow_email_triage_actions=bool(prefs.get("allow_email_triage_actions")),
        allow_research_and_alert_loops=bool(prefs.get("allow_research_and_alert_loops")),
        require_confirmation_for_destructive_actions=bool(prefs.get("require_confirmation_for_destructive_actions")),
        require_confirmation_for_external_spending=bool(prefs.get("require_confirmation_for_external_spending")),
        effective_permissions=effective_permissions,
        config_gates=config_gates,
        warnings=warnings,
    )


def _select_documents(
    document_views: Dict[str, CompiledDocumentView],
    doc_keys: Iterable[str],
    *,
    warnings: List[IdentityCompileWarning],
) -> Dict[str, CompiledDocumentView]:
    selected: Dict[str, CompiledDocumentView] = {}
    for doc_key in doc_keys:
        doc = document_views.get(doc_key)
        if doc is None:
            warnings.append(
                IdentityCompileWarning(
                    code="missing_document",
                    message=f"Identity document `{doc_key}` is missing from the compiled bundle.",
                    doc_key=doc_key,
                )
            )
            continue
        selected[doc_key] = doc
    return selected


def _render_prompt_context(documents: Iterable[CompiledDocumentView]) -> str:
    sections = []
    for doc in documents:
        if not doc.excerpt:
            continue
        sections.append(f"### {doc.label}\n{doc.excerpt}")
    return "\n\n".join(sections).strip()


def _public_frontmatter(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in (frontmatter or {}).items()
        if key not in _METADATA_KEYS
    }


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def render_text_prompt(bundle: CompiledIdentity, *, exclude: tuple[str, ...] = ()) -> str:
    """Wave 2 reply-turn prompt assembly from a CompiledIdentity bundle.

    Renders the identity context for tier-0 reply generation by
    concatenating excerpts from: core_identity, operating_principles,
    capabilities_and_boundaries (creator-authored stable layer), then
    personality_profile, relationship_state, memory_summary, memory
    (owner-evolving content). Sections are separated by blank lines.

    Per-doc preambles (IDENTITY_DOC_PREAMBLES) are prepended inside the
    section. Currently applied to "memory" — its bullets are
    agent-maintained over time and historically read by small local
    models as standing orders ("mention favorite color in casual
    replies") rather than reference facts. The preamble re-frames the
    section as background reference and blocks off-topic injection.

    ``exclude`` names docs (by their bundle key) to leave out. Batch 2,
    task 3 (2026-09-27): the reply path renders with ``exclude=("memory",)``
    and places the memory doc itself — its CORE via :func:`plan_memory_core`
    since batch 5, task 3 (2026-09-28); :func:`render_memory_doc` renders it
    whole — at the END of the cache-stable prefix, so a memory edit (~7x a
    day) no longer moves the first differing byte to the top of the prompt.
    """
    docs_in_order = [
        ("core_identity", bundle.core.get("core_identity")),
        ("operating_principles", bundle.core.get("operating_principles")),
        ("capabilities_and_boundaries", bundle.core.get("capabilities_and_boundaries")),
        # Her voice page (2026-08-17). The owner decided "apply + unify" on
        # 08-13 — "shouldn't she have the same identity all around?" — and
        # that change edited PROMPT_DOCS_TEXT, which feeds
        # _build_text_snapshot. But THIS renderer is what the live reply
        # path actually calls (context_adapter._render_identity_core ->
        # compiled.text_prompt()), and its list is hardcoded: her
        # self-authored voice page never reached a single reply. Proven
        # 08-17 from the live logs — the page's text appeared exactly once
        # all day, inside the constitutional gate's own prompt.
        ("voice_identity", bundle.core.get("voice_identity")),
        ("personality_profile", bundle.owner.get("personality_profile")),
        ("relationship_state", bundle.owner.get("relationship_state")),
        ("memory_summary", bundle.owner.get("memory_summary")),
        ("memory", bundle.owner.get("memory")),
    ]
    sections = []
    for key, doc in docs_in_order:
        if doc is None or key in exclude:
            continue
        excerpt = render_prompt_excerpt(doc)
        if not excerpt:
            continue
        sections.append(_render_doc_section(doc, excerpt))
    return "\n\n".join(sections).strip()


def _render_doc_section(doc: IdentityDocument, excerpt: str) -> str:
    """One ``### {label}`` section: the doc's preamble (when it has one)
    ahead of its excerpt. Shared by render_text_prompt and render_memory_doc
    so the memory doc carries exactly the same label and preamble wherever
    it rides."""
    label = IDENTITY_DOC_LABELS.get(doc.doc_key, doc.doc_key.replace("_", " ").title())
    preamble = IDENTITY_DOC_PREAMBLES.get(doc.doc_key, "")
    if preamble:
        return f"### {label}\n{preamble.rstrip()}\n\n{excerpt}"
    return f"### {label}\n{excerpt}"


# The memory doc's sections, least-changing first (batch 2, task 3,
# 2026-09-27). The doc — its core, since batch 5, task 3 — is the last band
# of the cache-stable prefix but for the corrections of record, so the
# provider's prefix cache reaches deepest when the sections that change
# least come first: decisions and references change rarely, durable context
# now and then, active threads several times a day (plus the nightly sweep).
# Sections not named here follow in file order. This is rendering-order
# data, so it lives beside the renderer, not in constants.py's label tables.
MEMORY_SECTION_ORDER: tuple[str, ...] = (
    "## Important Decisions",
    "## Useful References",
    "## Durable Context",
    "## Active Threads",
)

# A section starts where a line starts with "## " (not "# ", not "### ").
_MEMORY_SECTION_BOUNDARY = re.compile(r"(?m)^(?=## )")


def _reorder_memory_sections(excerpt: str) -> str:
    """The excerpt's ``## `` sections in MEMORY_SECTION_ORDER. The pre-heading
    part (frontmatter bullets, the title, the intro lines) stays first;
    sections not in the known four follow in file order. Pieces are split
    at heading boundaries, right-stripped and re-joined with one blank
    line — no words added or dropped, only their order."""
    pieces = [p.rstrip() for p in _MEMORY_SECTION_BOUNDARY.split(excerpt)]
    pieces = [p for p in pieces if p]
    head = [p for p in pieces if not p.startswith("## ")]  # only ever the first piece
    ranked = []
    for index, section in enumerate(pieces):
        if not section.startswith("## "):
            continue
        heading = section.split("\n", 1)[0].rstrip()
        rank = (MEMORY_SECTION_ORDER.index(heading) if heading in MEMORY_SECTION_ORDER
                else len(MEMORY_SECTION_ORDER))
        ranked.append((rank, index, section))
    return "\n\n".join(head + [section for _, _, section in sorted(ranked)])


def render_memory_doc(bundle: CompiledIdentity) -> str:
    """The memory doc on its own, as render_text_prompt renders it (same
    ``### Memory`` label, same preamble) with its sections in
    MEMORY_SECTION_ORDER. "" when the bundle has no memory doc or the doc is
    empty. The reply path (core.agent.context_adapter) put this at the end of
    the stable prefix, after render_text_prompt(exclude=("memory",)) and the
    static blocks, until batch 5, task 3 (2026-09-28) put
    :func:`plan_memory_core`'s budgeted core there instead; the whole-doc
    rendering stays for its tests and for anyone who wants the doc entire."""
    doc = bundle.owner.get("memory")
    if doc is None:
        return ""
    excerpt = render_prompt_excerpt(doc)
    if not excerpt:
        return ""
    return _render_doc_section(doc, _reorder_memory_sections(excerpt)).strip()


# The memory CORE (batch 5, task 3, 2026-09-28; ruling R37 in fix round 1).
# The whole doc is ~72k characters live and rode in every reply prompt; the
# core is the part of it that matters today, under memory.core_budget_chars,
# and the rest is looked up when a moment calls for it (recall, task 4).
# What goes in:
#   - Important Decisions, whole, first, never cut — even past the budget;
#   - every other bullet scores max(last_referenced_at, stamp, and — for an
#     UNSTAMPED line — the render day's local midnight): an unstamped line
#     is never older than today (the same "born today" reading the nightly
#     sweep gives it) and a reference can never lower it. Ties fall to the
#     reference count, then the section (Durable Context, Active Threads,
#     Useful References, then the rest), then file order.
#   - the budget the decisions leave is SHARED by section — Durable Context
#     60 %, Active Threads 30 %, Useful References 10 %; a section not named
#     has no share. Pass one: each section fills its own share in rank
#     order and stops at the first bullet that does not fit (R36 — no
#     skipping ahead to a shorter one). Pass two: the leftover goes round
#     in the same section order, the same way. Without the shares the live
#     file's day one was 20 decisions + 94 Durable lines and not one thread
#     or reference, and a week of fresh threads would then have pushed
#     every Durable fact out (the review's measurement).
# What it looks like: the same ``### Memory`` label, preamble and
# pre-heading part as render_memory_doc, the shown sections in
# MEMORY_SECTION_ORDER (a header only when one of its bullets made the
# core; bullets in file order, the exact lines from the file), then the
# footer. The budget counts section headers, the blank lines between
# sections and bullet lines; the label, preamble, pre-heading part and
# footer sit outside it (R7b). A doc with no ``## `` heading at all (R62)
# is ONE unnamed section under the budget — ranked and shared like Durable
# Context, no header rendered, non-bullet lines dropped — never a
# pre-heading part that rides whole outside it. Pure: the same bundle,
# budget, refs and day give the same bytes (the corrections band follows;
# the provider caches the prefix up to the first byte that differs).
_MEMORY_DECISIONS_HEADING = "## Important Decisions"
# Also the order the shares are filled and the leftover handed round.
_MEMORY_RANK_TIE_ORDER: tuple[str, ...] = (
    "## Durable Context",
    "## Active Threads",
    "## Useful References",
)
_MEMORY_SECTION_SHARES: dict[str, float] = {
    "## Durable Context": 0.60,
    "## Active Threads": 0.30,
    "## Useful References": 0.10,
}
# 10-05: no count. "(534 more lines …)" changed with every new memory, and the
# prompt cache broke at this line on every reply and reflection after it; the
# count stays on MemoryCore.omitted.
_MEMORY_CORE_FOOTER = "(Any memory line not shown here is looked up when a moment calls for it.)"
# R62: the unnamed section of a headingless doc ranks and shares as this one.
_MEMORY_UNNAMED_SECTION_AS = "## Durable Context"


@dataclass(frozen=True)
class MemoryCore:
    """What plan_memory_core picked: the rendered core, the ``mcb_`` ids of
    every bullet in it (recall leaves those out of its look-up results), and
    how many bullets it left for look-up."""

    text: str
    ids: frozenset[str]
    omitted: int


@dataclass(frozen=True)
class _MemoryBullet:
    line: str      # the exact line from the file
    item_id: str
    section: int   # index into the doc's sections
    index: int     # position among the doc's bullets (file order)
    score: float
    count: int     # how often it has been referenced (0 when never)


def _local_midnight(now: float) -> float:
    """00:00 local on the day of ``now`` — how an unstamped bullet is dated."""
    day = time.localtime(now)
    return time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))


def _reference_entry(refs: Any, item_id: str) -> Mapping[str, Any] | None:
    """``refs[item_id]`` when ``refs`` is a mapping (a dict or the read-only
    view core.memory_refs.snapshot hands out) and the entry is one too."""
    entry = refs.get(item_id) if isinstance(refs, Mapping) else None
    return entry if isinstance(entry, Mapping) else None


def _reference_time(refs: Any, item_id: str) -> float | None:
    """``refs[item_id]["last_referenced_at"]`` as a float, or None when the
    id has no entry or the entry is not in the expected shape."""
    entry = _reference_entry(refs, item_id)
    value = entry.get("last_referenced_at") if entry is not None else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reference_count(refs: Any, item_id: str) -> int:
    """``refs[item_id]["count"]`` as an int, 0 when absent or not a number."""
    entry = _reference_entry(refs, item_id)
    value = entry.get("count") if entry is not None else None
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _split_memory_excerpt(excerpt: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The excerpt's pre-heading pieces (as _reorder_memory_sections keeps
    them) and its ``## `` sections as ``(heading, body lines)`` in file order."""
    pieces = [p.rstrip() for p in _MEMORY_SECTION_BOUNDARY.split(excerpt)]
    pieces = [p for p in pieces if p]
    head = [p for p in pieces if not p.startswith("## ")]
    sections: list[tuple[str, list[str]]] = []
    for piece in pieces:
        if not piece.startswith("## "):
            continue
        heading, _, body = piece.partition("\n")
        sections.append((heading.rstrip(), body.split("\n") if body else []))
    return head, sections


def plan_memory_core(
    bundle: CompiledIdentity,
    *,
    budget_chars: int,
    refs: Mapping[str, Any],
    now: float | None = None,
) -> MemoryCore:
    """The memory doc as a core under ``budget_chars`` (the comment above
    says how): the text, the ids of the bullets shown, the count left for
    look-up. ``refs`` is ``{item_id: {"last_referenced_at": float, "count":
    int}}`` from core.memory_refs (any mapping); ``now`` (default: the
    clock) only decides which day an unstamped bullet is born on. An absent
    or empty doc gives an empty core, like render_memory_doc's ""."""
    empty = MemoryCore(text="", ids=frozenset(), omitted=0)
    doc = bundle.owner.get("memory")
    if doc is None:
        return empty
    excerpt = render_prompt_excerpt(doc)
    if not excerpt:
        return empty
    head, sections = _split_memory_excerpt(excerpt)
    if not sections:
        # Ruling R62: no ``## `` heading anywhere — one unnamed section under
        # the budget (heading ""), never a pre-heading part outside it.
        sections = [("", [line for piece in head for line in piece.split("\n")])]
        head = []
    headings = [heading for heading, _ in sections]
    midnight = _local_midnight(time.time() if now is None else float(now))

    def rank_heading(section_index: int) -> str:
        return headings[section_index] or _MEMORY_UNNAMED_SECTION_AS

    bullets: list[_MemoryBullet] = []
    for section_index, (_heading, body_lines) in enumerate(sections):
        for line in body_lines:
            if not line.startswith("- "):
                continue
            stamp, _body, _key = _split_bullet(line)
            item_id = item_id_for(line)
            stamped_at = _stamp_time(stamp)
            known = [t for t in (_reference_time(refs, item_id), stamped_at) if t is not None]
            if stamped_at is None:
                known.append(midnight)  # unstamped: born today, whatever its references say
            bullets.append(_MemoryBullet(
                line=line,
                item_id=item_id,
                section=section_index,
                index=len(bullets),
                score=max(known),
                count=_reference_count(refs, item_id),
            ))

    shown: dict[int, list[_MemoryBullet]] = {}
    used = 0

    def cost(bullet: _MemoryBullet) -> int:
        # its line and the newline before it; a section's first line also
        # pays for the header, and for the blank line between sections when
        # another section is already shown (an unnamed section has no header
        # line, so its first bullet has no newline before it either)
        chars = len(bullet.line) + 1
        if bullet.section not in shown:
            heading = headings[bullet.section]
            chars += (len(heading) if heading else -1) + (2 if shown else 0)
        return chars

    def take(bullet: _MemoryBullet) -> None:
        nonlocal used
        used += cost(bullet)
        shown.setdefault(bullet.section, []).append(bullet)

    for bullet in bullets:
        if headings[bullet.section] == _MEMORY_DECISIONS_HEADING:
            take(bullet)
    remaining = max(0, budget_chars - used)

    def tie_rank(section_index: int) -> int:
        heading = rank_heading(section_index)
        return (_MEMORY_RANK_TIE_ORDER.index(heading) if heading in _MEMORY_RANK_TIE_ORDER
                else len(_MEMORY_RANK_TIE_ORDER))

    ranked = sorted(
        (b for b in bullets if headings[b.section] != _MEMORY_DECISIONS_HEADING),
        key=lambda b: (-b.score, -b.count, tie_rank(b.section), b.index),
    )
    fill_order = sorted(
        {b.section for b in ranked}, key=lambda i: (tie_rank(i), i),
    )
    taken: set[int] = set()

    def fill(section_index: int, room: int) -> int:
        """Take the section's untaken bullets in rank order while they fit
        ``room``; stop at the first that does not (R36). Returns what was
        spent."""
        spent = 0
        for bullet in ranked:
            if bullet.section != section_index or bullet.index in taken:
                continue
            chars = cost(bullet)
            if spent + chars > room:
                break
            take(bullet)
            taken.add(bullet.index)
            spent += chars
        return spent

    # pass one: each section's own share of what the decisions left
    for section_index in fill_order:
        share = int(remaining * _MEMORY_SECTION_SHARES.get(rank_heading(section_index), 0.0))
        fill(section_index, share)
    # pass two: the leftover goes round in the same order
    leftover = budget_chars - used
    for section_index in fill_order:
        leftover -= fill(section_index, leftover)

    def render_rank(section_index: int) -> tuple[int, int]:
        heading = rank_heading(section_index)
        return (
            MEMORY_SECTION_ORDER.index(heading) if heading in MEMORY_SECTION_ORDER else len(MEMORY_SECTION_ORDER),
            section_index,
        )

    blocks = [
        "\n".join(([headings[i]] if headings[i] else []) + [b.line for b in sorted(shown[i], key=lambda b: b.index)])
        for i in sorted(shown, key=render_rank)
    ]
    omitted = len(bullets) - sum(len(lines) for lines in shown.values())
    pieces = head + (["\n\n".join(blocks)] if blocks else []) + [_MEMORY_CORE_FOOTER]
    return MemoryCore(
        text=_render_doc_section(doc, "\n\n".join(pieces)).strip(),
        ids=frozenset(b.item_id for lines in shown.values() for b in lines),
        omitted=omitted,
    )


def render_memory_core(
    bundle: CompiledIdentity,
    *,
    budget_chars: int,
    refs: dict,
    now: float | None = None,
) -> str:
    """The memory core's text alone — plan_memory_core for callers that want
    the string (the reply path uses the plan; its ids feed recall's dedup)."""
    return plan_memory_core(bundle, budget_chars=budget_chars, refs=refs, now=now).text


# The preferences doc in the reply (batch 5, task 7, 2026-09-28, ruling R46).
# Reflect has written ``pref_<key>: <value>`` frontmatter rows to the
# ``preferences`` doc since May and no prompt tuple ever rendered them; the
# user_memory cache derived from them was never rebuilt. The block below is
# how they reach her: one line per key, in document order, under a cap.
_PREFERENCES_LABEL = "### Preferences"
_PREFERENCES_PREFIX = "pref_"
_PREFERENCES_FOOTER = "({n} more preferences not shown.)"


def render_preferences_block(bundle: CompiledIdentity, *, budget_chars: int = 2000) -> str:
    """The ``preferences`` doc's ``pref_*`` frontmatter as ``### Preferences``
    + one ``- key: value`` line each (the bare key, as the user_memory cache
    spells it), in document order. Whole lines only: the lines are taken in
    order while the block fits ``budget_chars``, and when any were cut one
    footer line says how many. "" when the doc is absent or carries no
    ``pref_`` key with a value (the template ships none). Live: 43 keys,
    about 4,100 chars, so the default cap shows roughly half — intended.
    Deterministic: nothing here reads the clock."""
    doc = bundle.owner.get("preferences")
    if doc is None:
        return ""
    lines: list[str] = []
    for key, value in (doc.frontmatter or {}).items():
        if not isinstance(key, str) or not key.startswith(_PREFERENCES_PREFIX):
            continue
        bare = key[len(_PREFERENCES_PREFIX):]
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = " ".join(value.split())
        else:
            # Fix round 1 (M3): a JSON-shaped value renders as JSON —
            # {"enabled": false}, true, 3 — the frontmatter's own spelling,
            # never a Python repr.
            try:
                text = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                text = " ".join(str(value).split())
        if not bare or not text:
            continue
        lines.append(f"- {bare}: {text}")
    if not lines:
        return ""
    shown: list[str] = []
    used = len(_PREFERENCES_LABEL)
    for line in lines:
        if used + len(line) + 1 > budget_chars:
            break
        shown.append(line)
        used += len(line) + 1
    pieces = [_PREFERENCES_LABEL, *shown]
    omitted = len(lines) - len(shown)
    if omitted:
        pieces.append(_PREFERENCES_FOOTER.format(n=omitted))
    return "\n".join(pieces)


# Step 31 (2026-08-20, spec §7): render_voice_prompt deleted — never
# called from any live path. The voice register rides the SHARED prompt's
# band (spec §4): voice_identity is in PROMPT_DOCS_TEXT, and the
# spoken-register band tells her a turn is spoken. A second renderer is
# the 08-13/08-17 trap ("her page reached zero replies") reborn.


def render_action_view(bundle: CompiledIdentity) -> dict:
    """Action-policy view for autonomy decisions. Returns a flat dict that
    Tier 1/Tier 2 dispatchers can consult for autonomy gates."""
    view: dict = {}
    if bundle.action_policy is not None:
        view["policy"] = bundle.action_policy.as_preferences()
        view["effective_permissions"] = dict(bundle.action_policy.effective_permissions)
    relationship = bundle.owner.get("relationship_state")
    if relationship is not None:
        view["relationship"] = dict(relationship.frontmatter)
    memory_summary = bundle.owner.get("memory_summary")
    if memory_summary is not None:
        view["memory_summary"] = dict(memory_summary.frontmatter)
    routines = bundle.owner.get("routines")
    if routines is not None:
        view["routines"] = dict(routines.frontmatter)
    return view
