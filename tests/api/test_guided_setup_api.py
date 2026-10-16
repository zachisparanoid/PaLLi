from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from api import web_account


@pytest.mark.asyncio
async def test_get_guided_setup_returns_owner_scoped_draft(monkeypatch):
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    state = SimpleNamespace(
        setup_completed=False,
        current_step="guided_setup",
        completed_steps=["user_name", "companion_style"],
        profile={"display_name": "Sam"},
        bundle=SimpleNamespace(),
        target_provenance={
            "user_name": {
                "completed": True,
                "summary": "Already satisfied by Profile.",
                "artifacts": [
                    {
                        "source": "profile",
                        "artifactKey": "profile",
                        "artifactLabel": "Profile",
                        "fieldPath": "display_name",
                        "fieldLabel": "Display Name",
                        "reason": "Display Name is set to `Sam`.",
                    }
                ],
            }
        },
    )

    async def fake_require_user(request):
        return {"id": 7, "email": "local@palli.local", "discord_user_id": "42"}

    async def fake_resolve_owner():
        return 7

    async def fake_get_profile(user_id, platform):
        assert user_id == 7
        assert platform == "discord"
        return {
            "display_name": "Sam",
            "personality_preset": "professional",
            "preferences": {
                "voice_enabled": False,
                "check_in_frequency": "weekly",
                "proactive_consent": {"enabled": True},
            },
        }

    async def fake_get_state(**kwargs):
        assert kwargs["account_user_id"] == 7
        assert kwargs["subject_user_id"] == 7
        assert kwargs["identity_platform"] == "owner"
        return state

    def fake_build_draft(**kwargs):
        assert kwargs["profile"] == state.profile
        assert kwargs["bundle"] is state.bundle
        return {
            "name": "Sam",
            "companionStyle": "direct",
            "autonomyPreset": "cautious",
            "relationshipStyle": "",
            "checkinPosture": "rare",
            "voicePosture": "off",
            "proactiveEnabled": True,
            "checkInFreq": "low",
            "voiceEnabled": False,
        }

    def fake_build_contract(**kwargs):
        assert kwargs["state"] is state
        return {
            "questionOrder": ["user_name", "companion_style", "autonomy_preset", "checkin_posture", "voice_posture"],
            "questions": [
                {
                    "target": "user_name",
                    "title": "You",
                    "prompt": "What should PaLLi call you most often?",
                    "note": "learn what to call the user in a natural way",
                    "inputKind": "text",
                    "placeholder": "Your name or nickname",
                    "required": True,
                    "options": [],
                    "provenance": state.target_provenance["user_name"],
                }
            ],
            "maxQuestionsPerTurn": 1,
        }

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_account.brain, "get_user_profile", fake_get_profile)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "get_guided_setup_state", fake_get_state)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "build_guided_setup_draft", fake_build_draft)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "build_guided_setup_contract", fake_build_contract)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/account/setup")

    assert response.status_code == 200
    payload = response.json()
    assert payload["setupCompleted"] is False
    assert payload["identityPlatform"] == "owner"
    assert payload["ownerUserId"] == 7
    assert payload["contract"]["questionOrder"] == [
        "user_name",
        "companion_style",
        "autonomy_preset",
        "checkin_posture",
        "voice_posture",
    ]
    assert payload["contract"]["questions"][0]["provenance"]["artifacts"][0]["artifactLabel"] == "Profile"
    assert payload["draft"] == {
        "name": "Sam",
        "companionStyle": "direct",
        "autonomyPreset": "cautious",
        "relationshipStyle": "",
        "checkinPosture": "rare",
        "voicePosture": "off",
        "proactiveEnabled": True,
        "checkInFreq": "low",
        "voiceEnabled": False,
    }


@pytest.mark.asyncio
async def test_complete_guided_setup_updates_profile_identity_and_progress(monkeypatch):
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    profiles = {
        ("discord", 7): {
            "display_name": "Local Operator",
            "username": "",
            "personality_preset": "default",
            "preferences": {
                "voice_enabled": True,
                "check_in_frequency": "normal",
                "proactive_consent": {"enabled": True},
            },
        },
        ("discord", 42): {
            "display_name": "Local Operator",
            "username": "",
            "personality_preset": "default",
            "preferences": {
                "voice_enabled": True,
                "check_in_frequency": "normal",
                "proactive_consent": {"enabled": True},
            },
        },
    }
    structured_updates: list[dict] = []
    persisted_completion: list[dict] = []
    state = SimpleNamespace(
        setup_completed=True,
        current_step="guided_setup_complete",
        completed_steps=[
            "user_name",
            "companion_style",
            "autonomy_preset",
            "checkin_posture",
            "voice_posture",
            "profile_setup",
        ],
        profile={
            "display_name": "Sam",
            "preferences": {
                "voice_enabled": True,
                "check_in_frequency": "low",
                "proactive_consent": {"enabled": False, "source": "guided_setup"},
            },
        },
        bundle=SimpleNamespace(),
        target_provenance={},
    )

    async def fake_require_user(request):
        return {"id": 7, "email": "local@palli.local", "discord_user_id": "42"}

    async def fake_resolve_owner():
        return 7

    async def fake_get_profile(user_id, platform):
        return deepcopy(profiles[(platform, user_id)])

    async def fake_update_profile(user_id, platform, updates):
        merged = deepcopy(profiles.get((platform, user_id), {}))
        merged.update(updates)
        profiles[(platform, user_id)] = merged

    async def fake_update_structured_document(**kwargs):
        structured_updates.append(kwargs)
        return None

    async def fake_persist_completion(**kwargs):
        persisted_completion.append(kwargs)

    async def fake_get_state(**kwargs):
        assert kwargs["account_user_id"] == 7
        assert kwargs["subject_user_id"] == 7
        assert kwargs["identity_platform"] == "owner"
        return state

    def fake_build_draft(**kwargs):
        assert kwargs["profile"] == state.profile
        assert kwargs["bundle"] is state.bundle
        return {
            "name": "Sam",
            "companionStyle": "warm",
            "autonomyPreset": "trusted",
            "relationshipStyle": "coach",
            "checkinPosture": "off",
            "voicePosture": "social",
            "proactiveEnabled": False,
            "checkInFreq": "low",
            "voiceEnabled": True,
        }

    def fake_build_contract(**kwargs):
        assert kwargs["state"] is state
        return {
            "questionOrder": [
                "user_name",
                "companion_style",
                "autonomy_preset",
                "checkin_posture",
                "voice_posture",
                "relationship_style",
            ],
            "questions": [],
            "maxQuestionsPerTurn": 1,
        }

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)
    monkeypatch.setattr(web_account.identity_service, "resolve_local_owner_user_id", fake_resolve_owner)
    monkeypatch.setattr(web_account.brain, "get_user_profile", fake_get_profile)
    monkeypatch.setattr(web_account.brain, "update_user_profile", fake_update_profile)
    monkeypatch.setattr(web_account.identity_service, "update_structured_document", fake_update_structured_document)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "persist_guided_setup_completion", fake_persist_completion)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "get_guided_setup_state", fake_get_state)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "build_guided_setup_draft", fake_build_draft)
    monkeypatch.setattr(web_account.guided_setup_dashboard, "build_guided_setup_contract", fake_build_contract)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/web/account/setup",
            json={
                "name": "Sam",
                "companion_style": "warm",
                "autonomy_preset": "trusted",
                "checkin_posture": "off",
                "voice_posture": "social",
                "relationship_style": "coach",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["setupCompleted"] is True
    assert payload["contract"]["questionOrder"][-1] == "relationship_style"
    assert payload["draft"]["name"] == "Sam"
    assert payload["draft"]["companionStyle"] == "warm"
    assert payload["draft"]["autonomyPreset"] == "trusted"
    assert profiles[("discord", 7)]["display_name"] == "Sam"
    assert profiles[("discord", 42)]["display_name"] == "Sam"
    assert profiles[("discord", 7)]["preferences"]["voice_enabled"] is True
    assert profiles[("discord", 7)]["preferences"]["check_in_frequency"] == "low"
    assert profiles[("discord", 7)]["preferences"]["proactive_consent"]["enabled"] is False

    assert [entry["doc_key"] for entry in structured_updates] == [
        "personality_profile",
        "action_preferences",
        "voice_preferences",
        "personality_profile",
    ]
    assert structured_updates[0]["platform"] == "owner"
    assert structured_updates[1]["updates"]["allow_privileged_cli_actions"] is True
    assert structured_updates[2]["updates"]["presence_profile"] == "social"
    assert structured_updates[3]["updates"]["relationship_style"] == "supportive_accountability_partner"
    assert persisted_completion == [
        {
            "account_user_id": 7,
            "completed_targets": [
                "user_name",
                "companion_style",
                "autonomy_preset",
                "checkin_posture",
                "voice_posture",
                "relationship_style",
            ],
        }
    ]


@pytest.mark.asyncio
async def test_complete_guided_setup_rejects_unknown_style(monkeypatch):
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    async def fake_require_user(request):
        return {"id": 7, "email": "local@palli.local", "discord_user_id": None}

    monkeypatch.setattr(web_account, "_require_user", fake_require_user)
    monkeypatch.setattr(web_account, "require_csrf", lambda request: None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/web/account/setup",
            json={
                "name": "Sam",
                "companion_style": "unknown",
                "autonomy_preset": "balanced",
                "proactive_enabled": True,
                "check_in_freq": "normal",
                "voice_enabled": True,
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown companion style."
