from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.database import brain
from core.identity.document_store import IdentityDocumentStore
from core.identity.materialization import IdentityBundleMaterializer


def _seed_identity_root(tmp_path: Path) -> Path:
    root = tmp_path / "identity"
    shutil.copytree(Path("identity/core"), root / "core")
    shutil.copytree(Path("identity/templates"), root / "templates")
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "owners").mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_identity_materializer_owner_profile_falls_back_to_local_profile(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))
    materializer = IdentityBundleMaterializer(document_store=store)

    calls = []

    async def fake_get_user_profile(user_id, platform):
        calls.append((user_id, platform))
        if platform == "local":
            return {"display_name": "Local Owner"}
        return None

    monkeypatch.setattr(brain, "get_user_profile", fake_get_user_profile)

    profile = await materializer.load_profile_for_storage(user_id=17, platform="owner")

    assert profile == {"display_name": "Local Owner"}
    assert calls == [(17, "discord"), (17, "local")]


@pytest.mark.asyncio
async def test_identity_materializer_migrates_missing_owner_docs_even_when_owner_dir_exists(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))
    materializer = IdentityBundleMaterializer(document_store=store)

    owner_dir = root / "owners" / "7"
    owner_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir / "PERSONALITY_PROFILE.md").write_text(
        "---\n"
        "doc_type: personality_profile\n"
        "version: 2\n"
        "display_name: Existing Owner\n"
        "humor: dry\n"
        "---\n\n"
        "# Personality Profile\n",
        encoding="utf-8",
    )

    discord_dir = root / "users" / "discord" / "4242"
    discord_dir.mkdir(parents=True, exist_ok=True)
    (discord_dir / "ACTION_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: action_preferences\n"
        "version: 4\n"
        "initiative_level: low\n"
        "allow_privileged_cli_actions: false\n"
        "---\n\n"
        "# Action Preferences\n",
        encoding="utf-8",
    )

    local_dir = root / "users" / "local" / "7"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "VOICE_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: voice_preferences\n"
        "version: 3\n"
        "interaction_mode: passive\n"
        "presence_profile: social\n"
        "focus_state: responsive\n"
        "local_transport_mode: passive\n"
        "---\n\n"
        "# Voice Preferences\n",
        encoding="utf-8",
    )

    recorded = []

    async def fake_record_version(**kwargs):
        recorded.append(kwargs)

    async def fake_get_app_user_by_id(user_id):
        return {"id": user_id, "palli_user_id": user_id, "discord_user_id": "4242"}

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)
    monkeypatch.setattr(brain, "get_app_user_by_id", fake_get_app_user_by_id)

    await materializer.ensure_materialized_documents(
        user_id=7,
        platform="owner",
        profile={"display_name": "Operator", "preferences": {}},
        actor_type="system",
        actor_id="test",
        reason="partial_owner_materialize",
    )

    action_doc = store.load_document(
        doc_key="action_preferences",
        path=str(owner_dir / "ACTION_PREFERENCES.md"),
        platform="owner",
        user_id=7,
        owner_user_id=7,
    )
    voice_doc = store.load_document(
        doc_key="voice_preferences",
        path=str(owner_dir / "VOICE_PREFERENCES.md"),
        platform="owner",
        user_id=7,
        owner_user_id=7,
    )
    personality_doc = store.load_document(
        doc_key="personality_profile",
        path=str(owner_dir / "PERSONALITY_PROFILE.md"),
        platform="owner",
        user_id=7,
        owner_user_id=7,
    )

    assert action_doc.frontmatter["initiative_level"] == "low"
    assert action_doc.frontmatter["allow_privileged_cli_actions"] is False
    assert voice_doc.frontmatter["interaction_mode"] == "passive"
    assert voice_doc.frontmatter["local_transport_mode"] == "passive"
    assert personality_doc.frontmatter["display_name"] == "Existing Owner"
    assert any(
        entry["doc_key"] == "action_preferences" and entry["reason"] == "owner_identity_migration"
        for entry in recorded
    )
    assert any(
        entry["doc_key"] == "voice_preferences" and entry["reason"] == "owner_identity_migration"
        for entry in recorded
    )
