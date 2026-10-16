from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.config import config
from core.database import brain
from core.identity.constants import IDENTITY_DOC_LABELS
from core.identity.service import IdentityService


def _seed_identity_root(tmp_path: Path) -> Path:
    root = tmp_path / "identity"
    shutil.copytree(Path("identity/core"), root / "core")
    shutil.copytree(Path("identity/templates"), root / "templates")
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "owners").mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_identity_service_materializes_user_documents(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    recorded = []

    async def fake_record_version(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    bundle = await service.ensure_user_identity_bundle(
        user_id=42,
        platform="discord",
        profile={
            "display_name": "Robin",
            "preferences": {
                "preferred_name": "Robin",
                "interests": ["automation", "voice", "research"],
                "humor_style": "dry",
                "response_length": "short",
                "voice_enabled": True,
                "proactive_consent": {"enabled": True},
            },
        },
        actor_type="system",
        actor_id="test",
        reason="unit_test",
    )

    assert "core_identity" in bundle.core_documents
    assert "personality_profile" in bundle.user_documents
    assert "voice_preferences" in bundle.user_documents
    assert bundle.user_documents["personality_profile"].frontmatter["display_name"] == "Robin"
    assert bundle.user_documents["action_preferences"].frontmatter["allow_privileged_cli_actions"] is True
    assert bundle.user_documents["action_preferences"].frontmatter["initiative_level"] == "high"
    assert bundle.user_documents["action_preferences"].frontmatter["allow_bounded_reflection"] is False
    assert bundle.user_documents["action_preferences"].frontmatter["background_watch_mode"] == "enabled"
    assert bundle.user_documents["voice_preferences"].frontmatter["presence_profile"] == "balanced"
    assert recorded
    assert (root / "users" / "discord" / "42" / "PERSONALITY_PROFILE.md").exists()


@pytest.mark.asyncio
async def test_identity_service_compose_prompt_context_includes_identity_sections(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    context = await service.compose_prompt_context(
        user_id=99,
        platform="discord",
        modality="voice",
        task_type="response",
        profile={"display_name": "Pilot", "preferences": {}},
    )

    assert "Core Identity" in context
    assert "Voice Identity" in context
    assert "Voice Preferences" in context
    assert "Personality Profile" in context
    assert "Action Preferences" in context


@pytest.mark.asyncio
async def test_identity_service_compile_identity_returns_snapshots_and_policy(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))
    monkeypatch.setattr(config, "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", True)
    monkeypatch.setattr(config, "AGENTIC_ALLOW_PACKAGE_INSTALLS", True)
    monkeypatch.setattr(config, "AGENTIC_INSTALL_CONFIRM_REQUIRED", False)

    async def fake_record_version(**kwargs):
        return None

    async def fake_get_app_user_by_id(user_id):
        return {"id": user_id, "palli_user_id": user_id, "discord_user_id": None}

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)
    monkeypatch.setattr(brain, "get_app_user_by_id", fake_get_app_user_by_id)

    service = IdentityService()
    compiled = await service.compile_identity(
        user_id=101,
        platform="owner",
        profile={"display_name": "Pilot", "preferences": {"preferred_name": "Pilot"}},
    )

    assert compiled.platform == "owner"
    assert compiled.owner_user_id == 101
    assert compiled.text_snapshot.prompt_context
    assert compiled.voice_snapshot.preferences["interaction_mode"] == "direct_address"
    assert compiled.action_snapshot.preferences["autonomy_mode"] in {"trusted_autonomous", "ask_first", "balanced"}
    assert compiled.action_policy is not None
    assert "proactive_outreach" in compiled.action_policy.effective_permissions
    assert compiled.action_policy.allow_bounded_reflection is False
    assert compiled.action_policy.effective_permissions["bounded_reflection"] is False
    assert compiled.action_policy.config_gates["autonomous_cli"] is True
    assert compiled.action_policy.config_gates["privileged_cli"] is True
    assert compiled.action_policy.config_gates["package_installs"] is True
    assert compiled.action_policy.config_gates["package_install_confirm_required"] is False
    assert compiled.text_snapshot.documents["core_identity"].policy["ownershipClass"] == "creator_owned_stable"
    assert compiled.text_snapshot.documents["personality_profile"].policy["ownershipClass"] == "user_authored_preferences"
    assert compiled.provenance["memory_summary"].doc_key == "memory_summary"
    assert compiled.provenance["memory_summary"].label == IDENTITY_DOC_LABELS["memory_summary"]
    assert compiled.provenance["memory_summary"].policy["reviewRequirement"] == "safe_self_maintenance"
    compiled_payload = compiled.to_dict()
    assert compiled_payload["snapshots"]["text"]["documents"]["core_identity"]["label"] == IDENTITY_DOC_LABELS["core_identity"]
    assert compiled_payload["snapshots"]["voice"]["documents"]["voice_preferences"]["policy"]["ownershipClass"] == "user_authored_preferences"
    assert compiled_payload["snapshots"]["action"]["documents"]["action_preferences"]["excerpt"]
    assert compiled_payload["policy"]["configGates"]["autonomous_cli"] is True
    assert compiled_payload["policy"]["configGates"]["package_install_confirm_required"] is False
    assert compiled_payload["provenance"]["memory_summary"]["docKey"] == "memory_summary"
    assert compiled_payload["provenance"]["memory_summary"]["label"] == IDENTITY_DOC_LABELS["memory_summary"]


@pytest.mark.asyncio
async def test_identity_service_compiled_output_reflects_structured_updates(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=202,
        platform="discord",
        profile={"display_name": "Builder", "preferences": {}},
    )

    before = await service.compile_identity(user_id=202, platform="discord")
    assert before.action_policy is not None
    assert before.action_policy.allow_schedule_creation is True
    assert before.action_policy.quiet_hours_mode == "adaptive"

    await service.update_structured_document(
        user_id=202,
        platform="discord",
        doc_key="action_preferences",
        updates={
            "allow_schedule_creation": False,
            "quiet_hours_mode": "off",
        },
        actor_type="user",
        actor_id="202",
        reason="unit_test_update",
    )

    after = await service.compile_identity(user_id=202, platform="discord")
    assert after.action_policy is not None
    assert after.action_policy.allow_schedule_creation is False
    assert after.action_policy.quiet_hours_mode == "off"


@pytest.mark.asyncio
async def test_identity_service_raw_updates_canonicalize_document_policy_metadata(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=212,
        platform="discord",
        profile={"display_name": "Pilot", "preferences": {}},
    )

    doc = await service.update_raw_document(
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

    assert doc.frontmatter["owner"] == "shared"
    assert doc.frontmatter["ownership_class"] == "user_authored_preferences"
    assert doc.frontmatter["mutability"] == "user_editable"
    assert doc.frontmatter["review_requirement"] == "user_directed"
    assert doc.frontmatter["initiative_level"] == "low"


@pytest.mark.asyncio
async def test_identity_service_history_detail_uses_storage_target(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    recorded: list[dict[str, object]] = []

    async def fake_get_identity_document_version(**kwargs):
        recorded.append(kwargs)
        return {"doc_key": "memory_summary", "version": 2}

    monkeypatch.setattr(brain, "get_identity_document_version", fake_get_identity_document_version)

    service = IdentityService()
    entry = await service.get_history_entry(
        user_id=303,
        platform="discord",
        doc_key="memory_summary",
        version=2,
        identity_owner_id=909,
    )

    assert entry == {"doc_key": "memory_summary", "version": 2}
    assert recorded == [
        {
            "user_id": 909,
            "platform": "owner",
            "doc_key": "memory_summary",
            "version": 2,
        }
    ]


@pytest.mark.asyncio
async def test_identity_service_history_list_uses_storage_target(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    recorded: list[dict[str, object]] = []

    async def fake_list_identity_document_versions(**kwargs):
        recorded.append(kwargs)
        return [{"doc_key": "memory_summary", "version": 4}]

    monkeypatch.setattr(brain, "list_identity_document_versions", fake_list_identity_document_versions)

    service = IdentityService()
    history = await service.list_history(
        user_id=303,
        platform="discord",
        doc_key="memory_summary",
        limit=4,
        identity_owner_id=909,
    )

    assert history == [{"doc_key": "memory_summary", "version": 4}]
    assert recorded == [
        {
            "user_id": 909,
            "platform": "owner",
            "doc_key": "memory_summary",
            "limit": 4,
        }
    ]


@pytest.mark.asyncio
async def test_identity_service_blocks_user_structured_update_on_agent_maintained_doc(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=88,
        platform="discord",
        profile={"display_name": "Guarded", "preferences": {}},
    )

    with pytest.raises(PermissionError, match="memory_summary"):
        await service.update_structured_document(
            user_id=88,
            platform="discord",
            doc_key="memory_summary",
            updates={"memory_summary_text": "User should not structured-edit this doc."},
            actor_type="user",
            actor_id="88",
            reason="unit_test_blocked_structured_edit",
        )


@pytest.mark.asyncio
async def test_identity_service_materializes_existing_user_with_legacy_safe_action_defaults(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    bundle = await service.ensure_user_identity_bundle(
        user_id=43,
        platform="discord",
        profile={
            "display_name": "Legacy",
            "total_interactions": 12,
            "preferences": {"proactive_consent": {"enabled": False}},
        },
        actor_type="system",
        actor_id="test",
        reason="legacy_seed",
    )

    frontmatter = bundle.user_documents["action_preferences"].frontmatter
    assert frontmatter["allow_proactive_followups"] is False
    assert frontmatter["allow_bounded_reflection"] is False
    assert frontmatter["background_watch_mode"] == "off"
    assert frontmatter["standing_orders_policy"] == "explicit_only"
    assert frontmatter["initiative_level"] == "medium"


@pytest.mark.asyncio
async def test_identity_service_materializes_owner_documents(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    recorded = []

    async def fake_record_version(**kwargs):
        recorded.append(kwargs)

    async def fake_get_app_user_by_id(user_id):
        return {"id": user_id, "palli_user_id": user_id, "discord_user_id": None}

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)
    monkeypatch.setattr(brain, "get_app_user_by_id", fake_get_app_user_by_id)

    service = IdentityService()
    bundle = await service.ensure_user_identity_bundle(
        user_id=7,
        platform="owner",
        profile={
            "display_name": "Operator",
            "preferences": {"preferred_name": "Operator"},
        },
        actor_type="system",
        actor_id="test",
        reason="unit_test_owner",
    )

    assert bundle.platform == "owner"
    assert bundle.user_id == 7
    assert bundle.user_documents["personality_profile"].frontmatter["platform"] == "owner"
    assert bundle.user_documents["personality_profile"].frontmatter["owner_user_id"] == 7
    assert (root / "owners" / "7" / "PERSONALITY_PROFILE.md").exists()
    assert recorded


@pytest.mark.asyncio
async def test_identity_service_owner_migration_prefers_discord_for_action_and_local_for_voice(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

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

    service = IdentityService()
    bundle = await service.ensure_user_identity_bundle(
        user_id=7,
        platform="owner",
        profile={"display_name": "Operator", "preferences": {}},
        actor_type="system",
        actor_id="test",
        reason="owner_materialize",
    )

    assert bundle.user_documents["action_preferences"].frontmatter["initiative_level"] == "low"
    assert bundle.user_documents["action_preferences"].frontmatter["allow_privileged_cli_actions"] is False
    assert bundle.user_documents["voice_preferences"].frontmatter["interaction_mode"] == "passive"
    assert bundle.user_documents["voice_preferences"].frontmatter["local_transport_mode"] == "passive"
    assert bundle.user_documents["action_preferences"].frontmatter["platform"] == "owner"
    assert bundle.user_documents["voice_preferences"].frontmatter["platform"] == "owner"
    assert any(entry["reason"] == "owner_identity_migration" for entry in recorded)


def test_get_action_preferences_sync_normalizes_quoted_boolean_strings(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    user_dir = root / "users" / "discord" / "99"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "ACTION_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: action_preferences\n"
        "allow_privileged_cli_actions: \"false\"\n"
        "---\n\n"
        "# Action Preferences\n",
        encoding="utf-8",
    )

    service = IdentityService()
    prefs = service.get_action_preferences_sync(user_id=99, platform="discord")

    assert prefs["allow_privileged_cli_actions"] is False
    assert prefs["standing_orders_policy"] == "explicit_only"


def test_get_voice_preferences_sync_normalizes_invalid_values(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    user_dir = root / "users" / "local" / "99"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "VOICE_PREFERENCES.md").write_text(
        "---\n"
        "doc_type: voice_preferences\n"
        "voice_engine: weird\n"
        "interaction_mode: weird\n"
        "presence_profile: SOCIAL\n"
        "focus_state: nope\n"
        "local_transport_mode: strange\n"
        "---\n\n"
        "# Voice Preferences\n",
        encoding="utf-8",
    )

    service = IdentityService()
    prefs = service.get_voice_preferences_sync(user_id=99, platform="local")

    assert prefs["voice_engine"] == "classic"
    assert prefs["interaction_mode"] == "direct_address"
    assert prefs["presence_profile"] == "social"
    assert prefs["focus_state"] == "responsive"
    assert prefs["local_transport_mode"] == "push_to_talk"


@pytest.mark.asyncio
async def test_silent_maintenance_does_not_update_action_preferences(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    async def fake_record_version(**kwargs):
        return None

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    await service.ensure_user_identity_bundle(
        user_id=55,
        platform="discord",
        profile={"display_name": "Guarded", "preferences": {}},
    )

    result = await service.silently_maintain_document(
        user_id=55,
        platform="discord",
        doc_key="action_preferences",
        updates={"allow_privileged_cli_actions": False},
        actor_id="test",
        reason="should_not_apply",
    )

    assert result is None


@pytest.mark.asyncio
async def test_identity_service_admin_raw_update_can_target_core_docs(tmp_path, monkeypatch):
    root = _seed_identity_root(tmp_path)
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(root))

    recorded = []

    async def fake_record_version(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(brain, "create_identity_document_version", fake_record_version)

    service = IdentityService()
    doc = await service.update_raw_document(
        user_id=7,
        platform="owner",
        doc_key="core_identity",
        markdown=(
            "---\n"
            "doc_type: core_identity\n"
            "core_form: digital_parrot\n"
            "---\n\n"
            "# Core Identity\n\nUpdated core identity body.\n"
        ),
        actor_type="admin",
        actor_id="admin-user",
        reason="unit_test_core_identity_edit",
    )

    assert doc.path.endswith("identity\\core\\CORE_IDENTITY.md") or doc.path.endswith("identity/core/CORE_IDENTITY.md")
    assert doc.frontmatter["platform"] == "global"
    assert doc.frontmatter["user_id"] == 0
    assert doc.frontmatter["ownership_class"] == "creator_owned_stable"
    assert recorded[-1]["platform"] == "global"
    assert recorded[-1]["user_id"] == 0
