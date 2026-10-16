from __future__ import annotations

from fastapi import FastAPI
import httpx
import pytest

from api import web_identity
from core.identity.models import IdentityBundle, IdentityDocument


@pytest.mark.asyncio
async def test_get_identity_bundle_returns_documents(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_get_profile(user_id, platform):
        return {"display_name": "Robin", "preferences": {}}

    async def fake_ensure_bundle(**kwargs):
        return IdentityBundle(
            user_id=7,
            platform="owner",
            core_documents={
                "core_identity": IdentityDocument(
                    doc_key="core_identity",
                    path="identity/core/CORE_IDENTITY.md",
                    frontmatter={"doc_type": "core_identity"},
                    content="core body",
                    editable=False,
                )
            },
            user_documents={
                "personality_profile": IdentityDocument(
                    doc_key="personality_profile",
                    path="identity/owners/7/PERSONALITY_PROFILE.md",
                    frontmatter={"doc_type": "personality_profile", "platform": "owner", "tone": "warm_companion"},
                    content="personality body",
                    editable=True,
                )
            },
        )

    async def fake_history(**kwargs):
        return [
            {
                "doc_key": "personality_profile",
                "version": 1,
                "created_at": 1.0,
                "content": "---\ndoc_type: personality_profile\n---\nbody\n",
                "frontmatter": {"doc_type": "personality_profile"},
            }
        ]

    async def fake_resolve_owner(**kwargs):
        return 7

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_identity.brain, "get_user_profile", fake_get_profile)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "ensure_user_identity_bundle", fake_ensure_bundle)
    monkeypatch.setattr(web_identity.identity_service, "list_history", fake_history)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/identity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["userId"] == 7
    assert payload["platform"] == "owner"
    assert payload["ownerUserId"] == 7
    assert "core_identity" in payload["coreDocuments"]
    assert "personality_profile" in payload["userDocuments"]
    assert payload["coreDocuments"]["core_identity"]["policy"]["ownershipClass"] == "creator_owned_stable"
    assert payload["coreDocuments"]["core_identity"]["policy"]["ownershipClassInfo"]["label"] == "Creator-Owned Stable"
    assert payload["userDocuments"]["personality_profile"]["policy"]["dashboardEditable"] is True
    assert payload["governance"]["ownershipClasses"]["agent_maintained_evolving"]["label"] == "Agent-Maintained Evolving"
    assert payload["governance"]["mutabilityStates"]["system_evolving"]["summary"] == "Quietly maintainable continuity layer."
    assert payload["governance"]["reviewRequirements"]["safe_self_maintenance"]["label"] == "Safe Self-Maintenance"
    assert payload["governance"]["documentOrder"][0] == "core_identity"
    assert payload["governance"]["documents"]["personality_profile"]["policy"]["editableViaInfo"][0]["label"] == "Dashboard Forms"
    assert payload["history"][0]["document"]["label"] == "Personality Profile"
    assert payload["history"][0]["document"]["policy"]["reviewRequirement"] == "user_directed"
    assert "content" not in payload["history"][0]
    assert "frontmatter" not in payload["history"][0]


@pytest.mark.asyncio
async def test_get_compiled_identity_bundle_returns_compiled_state(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_get_profile(user_id, platform):
        return {"display_name": "Robin", "preferences": {}}

    class FakeCompiledBundle:
        def to_dict(self):
            return {
                "userId": 7,
                "platform": "owner",
                "ownerUserId": 7,
                "snapshots": {
                    "text": {
                        "promptContext": "text",
                        "documents": {
                            "core_identity": {
                                "docKey": "core_identity",
                                "label": "Core Identity",
                                "path": "identity/core/CORE_IDENTITY.md",
                                "version": 1,
                                "platform": "owner",
                                "userId": 7,
                                "ownerUserId": 7,
                                "policy": {
                                    "ownershipClass": "creator_owned_stable",
                                    "reviewRequirement": "creator_review",
                                },
                                "frontmatter": {},
                                "content": "core body",
                                "excerpt": "core excerpt",
                            }
                        },
                        "traits": {},
                        "warnings": [],
                    },
                    "voice": {
                        "promptContext": "voice",
                        "documents": {
                            "voice_preferences": {
                                "docKey": "voice_preferences",
                                "label": "Voice Preferences",
                                "path": "identity/owners/7/VOICE_PREFERENCES.md",
                                "version": 3,
                                "platform": "owner",
                                "userId": 7,
                                "ownerUserId": 7,
                                "policy": {
                                    "ownershipClass": "user_authored_preferences",
                                    "reviewRequirement": "user_directed",
                                },
                                "frontmatter": {"interaction_mode": "direct_address"},
                                "content": "voice body",
                                "excerpt": "voice excerpt",
                            }
                        },
                        "preferences": {"interaction_mode": "direct_address"},
                        "actionCoupling": {},
                        "materialized": True,
                        "warnings": [],
                    },
                    "action": {
                        "promptContext": "action",
                        "documents": {
                            "action_preferences": {
                                "docKey": "action_preferences",
                                "label": "Action Preferences",
                                "path": "identity/owners/7/ACTION_PREFERENCES.md",
                                "version": 2,
                                "platform": "owner",
                                "userId": 7,
                                "ownerUserId": 7,
                                "policy": {
                                    "ownershipClass": "user_authored_preferences",
                                    "reviewRequirement": "user_directed",
                                },
                                "frontmatter": {"allow_schedule_creation": True},
                                "content": "action body",
                                "excerpt": "action excerpt",
                            }
                        },
                        "preferences": {"allow_schedule_creation": True},
                        "relationship": {},
                        "memory": {},
                        "routines": {},
                        "materialized": True,
                        "warnings": [],
                    },
                },
                "policy": {
                    "allow_bounded_reflection": False,
                    "allow_schedule_creation": True,
                    "effectivePermissions": {"schedule_creation": True, "bounded_reflection": False},
                    "configGates": {
                        "autonomous_cli": True,
                        "privileged_cli": False,
                        "package_installs": True,
                        "package_install_confirm_required": True,
                    },
                    "warnings": [],
                },
                "provenance": {
                    "action_preferences": {
                        "docKey": "action_preferences",
                        "label": "Action Preferences",
                        "path": "identity/owners/7/ACTION_PREFERENCES.md",
                        "version": 2,
                        "platform": "owner",
                        "userId": 7,
                        "ownerUserId": 7,
                        "policy": {
                            "ownershipClass": "user_authored_preferences",
                            "reviewRequirement": "user_directed",
                        },
                    }
                },
                "warnings": [],
            }

    async def fake_resolve_owner(**kwargs):
        return 7

    async def fake_compile_identity(**kwargs):
        return FakeCompiledBundle()

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity.brain, "get_user_profile", fake_get_profile)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "compile_identity", fake_compile_identity)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/identity/compiled")

    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "owner"
    assert payload["snapshots"]["voice"]["preferences"]["interaction_mode"] == "direct_address"
    assert payload["snapshots"]["text"]["documents"]["core_identity"]["label"] == "Core Identity"
    assert payload["snapshots"]["text"]["documents"]["core_identity"]["excerpt"] == "core excerpt"
    assert payload["snapshots"]["action"]["documents"]["action_preferences"]["policy"]["ownershipClass"] == "user_authored_preferences"
    assert payload["policy"]["effectivePermissions"]["schedule_creation"] is True
    assert payload["policy"]["effectivePermissions"]["bounded_reflection"] is False
    assert payload["policy"]["configGates"]["autonomous_cli"] is True
    assert payload["policy"]["configGates"]["privileged_cli"] is False
    assert payload["provenance"]["action_preferences"]["docKey"] == "action_preferences"
    assert payload["provenance"]["action_preferences"]["label"] == "Action Preferences"
    assert payload["provenance"]["action_preferences"]["policy"]["ownershipClass"] == "user_authored_preferences"


@pytest.mark.asyncio
async def test_get_identity_history_filters_by_doc_key(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    recorded: dict[str, object] = {}

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(**kwargs):
        return 7

    async def fake_history(**kwargs):
        recorded.update(kwargs)
        return [
            {
                "doc_key": "personality_profile",
                "version": 3,
                "created_at": 123.0,
                "content": "---\ndoc_type: personality_profile\n---\nbody\n",
                "frontmatter": {"doc_type": "personality_profile"},
            }
        ]

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "list_history", fake_history)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/web/account/identity/history",
            params={"doc_key": "personality_profile", "limit": 12},
        )

    assert response.status_code == 200
    row = response.json()["history"][0]
    assert row["doc_key"] == "personality_profile"
    assert row["document"]["docKey"] == "personality_profile"
    assert row["document"]["label"] == "Personality Profile"
    assert row["document"]["policy"]["ownershipClass"] == "user_authored_preferences"
    assert "content" not in row
    assert "frontmatter" not in row
    assert recorded == {
        "user_id": 7,
        "platform": "owner",
        "doc_key": "personality_profile",
        "limit": 12,
    }


@pytest.mark.asyncio
async def test_get_identity_history_version_returns_detail(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    recorded: dict[str, object] = {}

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(**kwargs):
        return 7

    async def fake_get_history_entry(**kwargs):
        recorded.update(kwargs)
        return {
            "doc_key": "personality_profile",
            "file_path": "identity/owners/7/PERSONALITY_PROFILE.md",
            "version": 3,
            "actor_type": "user",
            "actor_id": "7",
            "reason": "dashboard_raw_edit",
            "content": (
                "---\n"
                "doc_type: personality_profile\n"
                "version: 3\n"
                "tone: warm_companion\n"
                "---\n\n"
                "# Personality Profile\n"
                "Be warm.\n"
            ),
            "frontmatter": {
                "doc_type": "personality_profile",
                "version": 3,
                "tone": "warm_companion",
            },
            "created_at": 321.0,
        }

    async def fake_ensure_bundle(**kwargs):
        return IdentityBundle(
            user_id=7,
            platform="owner",
            user_documents={
                "personality_profile": IdentityDocument(
                    doc_key="personality_profile",
                    path="identity/owners/7/PERSONALITY_PROFILE.md",
                    frontmatter={
                        "doc_type": "personality_profile",
                        "version": 5,
                        "tone": "grounded_direct",
                    },
                    content="# Personality Profile\nBe warmer now.\n",
                    editable=True,
                )
            },
        )

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "get_history_entry", fake_get_history_entry)
    monkeypatch.setattr(web_identity.identity_service, "ensure_user_identity_bundle", fake_ensure_bundle)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/identity/history/personality_profile/3")

    assert response.status_code == 200
    payload = response.json()
    assert payload["doc_key"] == "personality_profile"
    assert payload["version"] == 3
    assert payload["file_path"] == "identity/owners/7/PERSONALITY_PROFILE.md"
    assert payload["frontmatter"]["tone"] == "warm_companion"
    assert payload["content"] == "# Personality Profile\nBe warm.\n"
    assert payload["markdown"].startswith("---\ndoc_type: personality_profile")
    assert payload["document"]["label"] == "Personality Profile"
    assert payload["document"]["path"] == "identity/owners/7/PERSONALITY_PROFILE.md"
    assert payload["document"]["policy"]["mutability"] == "user_editable"
    assert payload["document"]["policy"]["reviewRequirementInfo"]["label"] == "User Directed"
    assert payload["comparison"]["target"] == "current_document"
    assert payload["comparison"]["current_label"] == "Personality Profile"
    assert payload["comparison"]["current_path"] == "identity/owners/7/PERSONALITY_PROFILE.md"
    assert payload["comparison"]["current_version"] == 5
    assert payload["comparison"]["currentDocument"]["docKey"] == "personality_profile"
    assert payload["comparison"]["currentDocument"]["editable"] is True
    assert payload["comparison"]["currentDocument"]["policy"]["ownershipClassInfo"]["label"] == "User-Authored Preferences"
    assert payload["comparison"]["summary"]["history_line_count"] == 8
    assert payload["comparison"]["summary"]["current_line_count"] == 8
    assert payload["comparison"]["summary"]["added_lines"] == 3
    assert payload["comparison"]["summary"]["removed_lines"] == 3
    assert payload["comparison"]["summary"]["changed_blocks"] == 1
    assert payload["comparison"]["summary"]["has_changes"] is True
    assert payload["comparison"]["hunks"][0]["lines"][0]["kind"] == "context"
    assert payload["comparison"]["hunks"][0]["lines"][2]["kind"] == "removed"
    assert recorded == {
        "user_id": 7,
        "platform": "owner",
        "doc_key": "personality_profile",
        "version": 3,
    }


@pytest.mark.asyncio
async def test_get_identity_history_version_returns_404_when_missing(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(**kwargs):
        return 7

    async def fake_get_history_entry(**kwargs):
        return None

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "get_history_entry", fake_get_history_entry)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/identity/history/personality_profile/9")

    assert response.status_code == 404
    assert response.json()["detail"] == "Identity document version not found"


@pytest.mark.asyncio
async def test_get_identity_history_rejects_unknown_doc_key(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(**kwargs):
        return 7

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/web/account/identity/history",
            params={"doc_key": "not_a_real_doc"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown identity document"


@pytest.mark.asyncio
async def test_update_action_preferences_writes_structured_doc(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_update(**kwargs):
        return IdentityDocument(
            doc_key="action_preferences",
            path="identity/owners/7/ACTION_PREFERENCES.md",
            frontmatter={
                "doc_type": "action_preferences",
                "platform": "owner",
                "allow_privileged_cli_actions": True,
                "post_action_reporting_mode": "digest",
            },
            content="action body",
            editable=True,
        )

    async def fake_resolve_owner(**kwargs):
        return 7

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "update_structured_document", fake_update)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/web/account/identity/action-preferences",
            json={"allow_privileged_cli_actions": True, "post_action_reporting_mode": "digest"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["docKey"] == "action_preferences"
    assert payload["frontmatter"]["allow_privileged_cli_actions"] is True
    assert payload["frontmatter"]["post_action_reporting_mode"] == "digest"


@pytest.mark.asyncio
async def test_update_action_preferences_accepts_bounded_reflection_toggle(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_update(**kwargs):
        return IdentityDocument(
            doc_key="action_preferences",
            path="identity/owners/7/ACTION_PREFERENCES.md",
            frontmatter={
                "doc_type": "action_preferences",
                "platform": "owner",
                "allow_bounded_reflection": True,
            },
            content="action body",
            editable=True,
        )

    async def fake_resolve_owner(**kwargs):
        return 7

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_identity.identity_service, "update_structured_document", fake_update)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/web/account/identity/action-preferences",
            json={"allow_bounded_reflection": True},
        )

    assert response.status_code == 200
    assert response.json()["frontmatter"]["allow_bounded_reflection"] is True


@pytest.mark.asyncio
async def test_update_identity_document_requires_identity_architecture_enabled(monkeypatch):
    app = FastAPI()
    app.include_router(web_identity.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

    async def fake_resolve_owner(**kwargs):
        return 7

    monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
    monkeypatch.setattr(web_identity, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_identity.config, "ENABLE_IDENTITY_ARCHITECTURE", False)
    monkeypatch.setattr(web_identity.config, "IDENTITY_ENABLE_DASHBOARD_EDITING", True)
    monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/web/account/identity/document/action_preferences",
            json={"markdown": "---\ndoc_type: action_preferences\n---\n"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_history_list_still_works_after_the_nightly_prune(monkeypatch, tmp_path):
    """Batch 8, task 3: the prune removes old copies of her identity
    documents from a real (temp) database; the dashboard's history list
    still answers, newest first, with the first copy ever and the newest
    ten; a kept version still opens; a pruned one is a 404."""
    from core.database import PalliBrain
    from core.identity import document_store
    from core.identity.history_prune import prune_history
    from core.identity.models import IdentityBundle

    day = 86400.0
    db = PalliBrain(db_path=str(tmp_path / "identity-history.sqlite3"))
    await db.initialize()
    try:
        conn = await db.get_connection()
        try:
            async with conn.transaction():
                for d in range(1, 61):          # one save a day for sixty days
                    await conn.execute(
                        "INSERT INTO identity_document_versions (user_id, platform, doc_key, file_path, "
                        "version, actor_type, actor_id, reason, content, frontmatter, created_at) "
                        "VALUES (7, 'owner', 'personality_profile', 'identity/owners/7/PERSONALITY_PROFILE.md', "
                        "$1, 'system', 'test', $2, $3, '{\"doc_type\": \"personality_profile\"}', $4)",
                        d, f"save {d}", f"---\ndoc_type: personality_profile\n---\nbody {d}\n", d * day,
                    )
        finally:
            await conn.close()
        result = await prune_history(db, keep_last=10, daily_days=30, now=61 * day)
        assert result.removed > 0

        app = FastAPI()
        app.include_router(web_identity.router, prefix="/api/web")

        async def fake_require_user(request):
            return {"id": 7, "discord_user_id": "42", "email": "user@example.com"}

        async def fake_resolve_owner(**kwargs):
            return 7

        async def fake_ensure_bundle(**kwargs):
            return IdentityBundle(user_id=7, platform="owner")

        monkeypatch.setattr(web_identity, "_require_user", fake_require_user)
        monkeypatch.setattr(web_identity.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
        monkeypatch.setattr(web_identity.identity_service, "ensure_user_identity_bundle", fake_ensure_bundle)
        monkeypatch.setattr(document_store, "brain", db)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            listed = await client.get(
                "/api/web/account/identity/history",
                params={"doc_key": "personality_profile", "limit": 100},
            )
            kept_detail = await client.get("/api/web/account/identity/history/personality_profile/1")
            pruned_detail = await client.get("/api/web/account/identity/history/personality_profile/2")

        assert listed.status_code == 200
        versions = [row["version"] for row in listed.json()["history"]]
        assert len(versions) == result.kept
        assert versions[:10] == list(range(60, 50, -1))      # the newest ten, newest first
        assert versions[-1] == 1                              # the first copy ever
        assert 2 not in versions
        assert kept_detail.status_code == 200
        assert kept_detail.json()["markdown"].endswith("body 1\n")
        assert pruned_detail.status_code == 404
    finally:
        await db.close()
