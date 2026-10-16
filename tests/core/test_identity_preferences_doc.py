"""Phase B / B4 step 4 — registration tests for the new ``preferences``
identity document type.

A new structured identity document, parallel to the existing
``personality_profile`` and ``action_preferences``, is the destination
for reflection's user-fact writes (B4 step 4 redirects them out of the
``user_memory`` table). These tests pin the registration surface:

  * ``preferences`` appears in ``USER_DOCS`` keyed at ``PREFERENCES.md``.
  * A document policy entry exists with the expected ownership shape
    (system-maintained, agent-evolving, user-visible).
  * The policy supports ``silent_maintenance`` so reflect (acting as
    ``system``) can write through ``silently_maintain_document``.
  * The policy supports ``structured`` edits by ``user`` and ``admin``
    so dashboard / chat preference flows can override or correct the
    auto-captured values.
  * ``IDENTITY_DOC_LABELS`` carries a human-readable label.
  * The document gets materialized into a fresh user's identity
    workspace via ``ensure_user_identity_bundle``.
  * The template seed lives at ``identity/templates/PREFERENCES.md``
    so materialization can render the empty starting state.
  * Round-trip: a ``silently_maintain_document`` write of a
    ``pref_<key>: <value>`` update lands in the doc's frontmatter and
    survives a re-load.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.config import config
from core.database import brain
from core.identity.constants import (
    IDENTITY_DOC_LABELS,
    SILENT_SELF_MAINTENANCE_DOCS,
    USER_DOCS,
)
from core.identity.document_policy import (
    IDENTITY_DOCUMENT_POLICIES,
    get_document_policy,
    supports_silent_maintenance,
)
from core.identity.service import IdentityService


def _seed_identity_root(tmp_path: Path) -> Path:
    root = tmp_path / "identity"
    shutil.copytree(Path("identity/core"), root / "core")
    shutil.copytree(Path("identity/templates"), root / "templates")
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "owners").mkdir(parents=True, exist_ok=True)
    return root


# ─── registration ───────────────────────────────────────────────────────


def test_preferences_doc_is_in_user_docs_registry():
    """``USER_DOCS["preferences"]`` maps to ``PREFERENCES.md``.

    The constants registry is the source of truth for which docs exist
    in a user's identity workspace; the materializer iterates this map
    when seeding new users.
    """
    assert USER_DOCS.get("preferences") == "PREFERENCES.md"


def test_preferences_doc_has_human_readable_label():
    """``IDENTITY_DOC_LABELS["preferences"]`` carries a non-empty string.

    Used by dashboard governance views and history rendering.
    """
    label = IDENTITY_DOC_LABELS.get("preferences")
    assert isinstance(label, str)
    assert label.strip()


def test_preferences_doc_is_in_silent_maintenance_set():
    """``SILENT_SELF_MAINTENANCE_DOCS`` includes ``preferences``.

    Reflect's ``_apply_user_facts`` calls ``silently_maintain_document``
    which gates on this set; without registration the redirect is a
    no-op.
    """
    assert "preferences" in SILENT_SELF_MAINTENANCE_DOCS


def test_preferences_doc_policy_is_registered_with_agent_maintained_shape():
    """``IDENTITY_DOCUMENT_POLICIES["preferences"]`` exists and matches
    the agent-maintained-evolving shape (system writes, user can
    override; structured + silent_maintenance both supported).
    """
    policy = IDENTITY_DOCUMENT_POLICIES.get("preferences")
    assert policy is not None, (
        "preferences doc policy must be registered in "
        "IDENTITY_DOCUMENT_POLICIES — without it, "
        "identity_service.update_structured_document raises KeyError."
    )
    assert policy.scope == "user"
    assert policy.ownership_class == "agent_maintained_evolving"
    # Owner here is "system" because reflect (an agent path) is the
    # primary writer; the user can still override / structured-edit.
    assert policy.owner == "system"
    assert policy.visibility == "user_visible"

    # Editor sets — system can silently maintain; user/admin can do
    # structured edits via the dashboard or chat-driven preference flows.
    assert "system" in policy.silent_maintenance_editors
    assert "system" in policy.structured_editors
    assert "user" in policy.structured_editors


def test_supports_silent_maintenance_returns_true_for_preferences():
    """The helper used by ``silently_maintain_document`` returns True
    for the new doc — without this, every reflect write becomes a
    silent no-op (returns None).
    """
    assert supports_silent_maintenance("preferences") is True


def test_get_document_policy_returns_preferences_policy():
    """The policy lookup helper resolves the new key without raising."""
    policy = get_document_policy("preferences")
    assert policy.doc_key == "preferences"


# ─── materialization ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_user_identity_bundle_materializes_preferences_doc(
    tmp_path, monkeypatch,
):
    """A fresh user gets a ``PREFERENCES.md`` rendered from the template
    when ``ensure_user_identity_bundle`` runs.

    Pins both (a) the template file exists at
    ``identity/templates/PREFERENCES.md`` and (b) the materializer
    iterates the new entry in ``USER_DOCS``.
    """
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    bundle = await service.ensure_user_identity_bundle(
        user_id=4242,
        platform="discord",
        profile={"display_name": "Tester", "preferences": {}},
    )

    assert "preferences" in bundle.user_documents
    doc_path = root / "users" / "discord" / "4242" / "PREFERENCES.md"
    assert doc_path.exists()


# ─── round-trip silently_maintain_document ─────────────────────────────


@pytest.mark.asyncio
async def test_silently_maintain_document_writes_pref_key_to_preferences_doc(
    tmp_path, monkeypatch,
):
    """A ``silently_maintain_document`` call with a ``pref_<key>``
    update lands in the doc's frontmatter and survives a re-load.

    This is the round-trip integration check: reflect's writer asks the
    identity service to mutate the doc; the resulting compiled bundle
    sees the new value.
    """
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=4243,
        platform="discord",
        profile={"display_name": "Tester2", "preferences": {}},
    )

    result = await service.silently_maintain_document(
        user_id=4243,
        platform="discord",
        doc_key="preferences",
        updates={"pref_favorite_color": "blue"},
        actor_id="reflect",
        reason="unit_test_pref_write",
    )

    # silently_maintain_document returns the IdentityDocument when the
    # write actually fired (gate satisfied + content changed).
    assert result is not None
    assert result.frontmatter["pref_favorite_color"] == "blue"

    # Re-load via the document store — the doc on disk must reflect
    # the update (i.e., we didn't just see an in-memory artifact).
    bundle = service.load_identity_bundle_sync(
        user_id=4243, platform="discord",
    )
    reloaded = bundle.user_documents["preferences"]
    assert reloaded.frontmatter["pref_favorite_color"] == "blue"


@pytest.mark.asyncio
async def test_silently_maintain_document_merges_subsequent_pref_writes(
    tmp_path, monkeypatch,
):
    """Two sequential preference writes accumulate — neither one wipes
    the other. Confirms the merge semantics expected by reflect's
    per-fact loop.
    """
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=4244,
        platform="discord",
        profile={"display_name": "Tester3", "preferences": {}},
    )

    await service.silently_maintain_document(
        user_id=4244,
        platform="discord",
        doc_key="preferences",
        updates={"pref_favorite_color": "blue"},
        actor_id="reflect",
        reason="first_write",
    )

    await service.silently_maintain_document(
        user_id=4244,
        platform="discord",
        doc_key="preferences",
        updates={"pref_preferred_editor": "vim"},
        actor_id="reflect",
        reason="second_write",
    )

    bundle = service.load_identity_bundle_sync(
        user_id=4244, platform="discord",
    )
    fm = bundle.user_documents["preferences"].frontmatter
    assert fm["pref_favorite_color"] == "blue"
    assert fm["pref_preferred_editor"] == "vim"
