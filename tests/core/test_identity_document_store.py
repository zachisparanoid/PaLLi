from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.database import brain
from core.identity.document_store import IdentityDocumentStore
from core.identity.frontmatter import parse_frontmatter


def _seed_identity_root(tmp_path: Path) -> Path:
    root = tmp_path / "identity"
    shutil.copytree(Path("identity/core"), root / "core")
    shutil.copytree(Path("identity/templates"), root / "templates")
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "owners").mkdir(parents=True, exist_ok=True)
    return root


def test_document_store_render_user_template_uses_legacy_action_defaults(tmp_path):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))

    markdown = store.render_user_template(
        doc_key="action_preferences",
        user_id=43,
        platform="discord",
        profile={
            "display_name": "Legacy",
            "total_interactions": 12,
            "preferences": {"proactive_consent": {"enabled": False}},
        },
    )
    frontmatter, _ = parse_frontmatter(markdown)

    assert frontmatter["allow_proactive_followups"] is False
    assert frontmatter["allow_bounded_reflection"] is False
    assert frontmatter["background_watch_mode"] == "off"
    assert frontmatter["standing_orders_policy"] == "explicit_only"
    assert frontmatter["initiative_level"] == "medium"


@pytest.mark.asyncio
async def test_document_store_update_raw_document_canonicalizes_policy_metadata_and_appends_changelog(
    tmp_path,
    monkeypatch,
):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))
    recorded = []

    async def fake_record_version(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    doc = await store.update_raw_document(
        user_id=212,
        platform="discord",
        doc_key="action_preferences",
        markdown=(
            "---\n"
            "doc_type: action_preferences\n"
            "owner: system\n"
            "ownership_class: creator_owned_stable\n"
            "mutability: constitutional\n"
            "review_requirement: creator_review_required\n"
            "initiative_level: low\n"
            "---\n\n"
            "# Action Preferences\n"
        ),
        actor_type="user",
        actor_id="212",
        reason="unit_test_raw_policy_update",
    )

    changelog_path = root / "users" / "discord" / "212" / "CHANGELOG.md"

    assert doc.frontmatter["owner"] == "shared"
    assert doc.frontmatter["ownership_class"] == "user_authored_preferences"
    assert doc.frontmatter["mutability"] == "user_editable"
    assert doc.frontmatter["review_requirement"] == "user_directed"
    assert doc.frontmatter["initiative_level"] == "low"
    assert changelog_path.exists()
    assert [entry["doc_key"] for entry in recorded] == ["action_preferences", "changelog"]


def test_document_store_load_document_normalizes_invalid_voice_values(tmp_path):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))
    user_dir = root / "users" / "local" / "99"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / "VOICE_PREFERENCES.md"
    path.write_text(
        "---\n"
        "doc_type: voice_preferences\n"
        "interaction_mode: weird\n"
        "presence_profile: SOCIAL\n"
        "focus_state: nope\n"
        "local_transport_mode: strange\n"
        "---\n\n"
        "# Voice Preferences\n",
        encoding="utf-8",
    )

    doc = store.load_document(
        doc_key="voice_preferences",
        path=str(path),
        platform="local",
        user_id=99,
    )

    assert doc.frontmatter["interaction_mode"] == "direct_address"
    assert doc.frontmatter["presence_profile"] == "social"
    assert doc.frontmatter["focus_state"] == "responsive"
    assert doc.frontmatter["local_transport_mode"] == "push_to_talk"


def test_document_store_load_bundle_prefers_owner_storage_documents(tmp_path):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))

    discord_dir = root / "users" / "discord" / "7"
    discord_dir.mkdir(parents=True, exist_ok=True)
    (discord_dir / "ACTION_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: action_preferences\n"
        "initiative_level: low\n"
        "---\n\n"
        "# Action Preferences\n",
        encoding="utf-8",
    )

    owner_dir = root / "owners" / "7"
    owner_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir / "ACTION_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: action_preferences\n"
        "initiative_level: high\n"
        "---\n\n"
        "# Action Preferences\n",
        encoding="utf-8",
    )

    bundle = store.load_bundle(user_id=7, platform="owner")

    assert "core_identity" in bundle.core_documents
    assert bundle.user_documents["action_preferences"].frontmatter["initiative_level"] == "high"
    assert bundle.user_documents["action_preferences"].frontmatter["platform"] == "owner"
    assert bundle.user_documents["action_preferences"].frontmatter["owner_user_id"] == 7


@pytest.mark.asyncio
async def test_document_store_history_reads_delegate_to_brain(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))

    recorded = {"list": [], "detail": []}

    async def fake_list_identity_document_versions(**kwargs):
        recorded["list"].append(kwargs)
        return [{"doc_key": "memory_summary", "version": 3}]

    async def fake_get_identity_document_version(**kwargs):
        recorded["detail"].append(kwargs)
        return {"doc_key": "memory_summary", "version": 3}

    monkeypatch.setattr(brain, "list_identity_document_versions", fake_list_identity_document_versions)
    monkeypatch.setattr(brain, "get_identity_document_version", fake_get_identity_document_version)

    history = await store.list_history(
        user_id=7,
        platform="owner",
        doc_key="memory_summary",
        limit=4,
    )
    entry = await store.get_history_entry(
        user_id=7,
        platform="owner",
        doc_key="memory_summary",
        version=3,
    )

    assert history == [{"doc_key": "memory_summary", "version": 3}]
    assert entry == {"doc_key": "memory_summary", "version": 3}
    assert recorded["list"] == [
        {
            "user_id": 7,
            "platform": "owner",
            "doc_key": "memory_summary",
            "limit": 4,
        }
    ]
    assert recorded["detail"] == [
        {
            "user_id": 7,
            "platform": "owner",
            "doc_key": "memory_summary",
            "version": 3,
        }
    ]
