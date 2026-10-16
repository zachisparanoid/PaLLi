"""Phase C / C5 step 1 — pin first-contact resolution writes.

When ``apply_reflection`` finds ``plan.first_contact_resolutions`` it
calls ``_apply_first_contact_resolutions`` which is supposed to land
each resolved field on the corresponding identity document
(``personality_profile`` for ``user_name`` / ``companion_style``,
``action_preferences`` for ``autonomy_preset``, ``voice_preferences``
for ``voice_posture``).

A latent bug (TODO at reflect.py:620, flagged during the B4 step 4
review) made every one of those writes a silent no-op:
``silently_maintain_document`` returns None when the target doc has no
``silent_maintenance_editors`` policy entries — and those three
``user_authored_preferences`` docs intentionally don't.

C5 step 1 fixes this by routing the resolutions through
``update_structured_document`` with ``actor_type="user"`` (which is
how the legacy ``core/first_contact.py``'s ``_apply_updates`` did the
same write), since the user authored these answers via the
guided-setup chat. ``personality_profile`` /
``action_preferences`` / ``voice_preferences`` already permit
``structured`` edits by ``user``, so the policy gate is satisfied
without expanding the silent-maintenance surface area.

These pinning tests exercise each branch of the resolutions dict to
ensure the writes actually fire.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

import core.turn.reflect as reflect_module
from core.identity.service import IdentityService


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_writes_user_name_to_personality_profile():
    """``user_name`` resolution lands on ``personality_profile`` with
    ``display_name`` set, written as the user (matches the legacy
    ``first_contact._apply_updates`` semantics)."""
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"user_name": "Sam"},
        )

    assert applied is True
    autospec_service.update_structured_document.assert_awaited_once()
    call = autospec_service.update_structured_document.await_args
    assert call.kwargs["doc_key"] == "personality_profile"
    assert call.kwargs["updates"] == {"display_name": "Sam"}
    assert call.kwargs["user_id"] == 42
    assert call.kwargs["platform"] == "owner"
    # Actor must be "user" so the user_authored_preferences policy gate
    # is satisfied (silent_maintenance is not on these docs).
    assert call.kwargs["actor_type"] == "user"
    assert call.kwargs["actor_id"] == "reflect"
    assert "first_contact" in call.kwargs["reason"]


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_writes_companion_style_to_personality_profile():
    """``companion_style`` resolution lands on ``personality_profile``
    with ``tone`` set."""
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"companion_style": "warm_companion"},
        )

    assert applied is True
    autospec_service.update_structured_document.assert_awaited_once()
    call = autospec_service.update_structured_document.await_args
    assert call.kwargs["doc_key"] == "personality_profile"
    assert call.kwargs["updates"] == {"tone": "warm_companion"}
    assert call.kwargs["actor_type"] == "user"


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_writes_autonomy_preset_to_action_preferences():
    """``autonomy_preset`` resolution lands on ``action_preferences``
    with ``autonomy_mode`` set, value drawn from the preset map.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"autonomy_preset": "trusted"},
        )

    assert applied is True
    autospec_service.update_structured_document.assert_awaited_once()
    call = autospec_service.update_structured_document.await_args
    assert call.kwargs["doc_key"] == "action_preferences"
    assert call.kwargs["updates"] == {"autonomy_mode": "trusted_autonomous"}
    assert call.kwargs["actor_type"] == "user"


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_writes_voice_posture_to_voice_preferences():
    """``voice_posture`` resolution lands on ``voice_preferences`` with
    ``interaction_mode`` set."""
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={"voice_posture": "direct_address"},
        )

    assert applied is True
    autospec_service.update_structured_document.assert_awaited_once()
    call = autospec_service.update_structured_document.await_args
    assert call.kwargs["doc_key"] == "voice_preferences"
    assert call.kwargs["updates"] == {"interaction_mode": "direct_address"}
    assert call.kwargs["actor_type"] == "user"


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_handles_multiple_resolutions_in_one_call():
    """All four resolution keys can land in a single call. Verifies the
    multi-write path issues four separate document updates (rather than
    coalescing or losing any)."""
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=99,
            resolutions={
                "user_name": "Alex",
                "companion_style": "warm_companion",
                "autonomy_preset": "balanced",
                "voice_posture": "passive",
            },
        )

    assert applied is True
    assert autospec_service.update_structured_document.await_count == 4

    doc_keys = [
        call.kwargs["doc_key"]
        for call in autospec_service.update_structured_document.await_args_list
    ]
    assert doc_keys.count("personality_profile") == 2
    assert doc_keys.count("action_preferences") == 1
    assert doc_keys.count("voice_preferences") == 1


@pytest.mark.asyncio
async def test_apply_first_contact_resolutions_empty_dict_is_noop():
    """Empty resolutions → no writes fired, returns False."""
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.update_structured_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        applied = await reflect_module._apply_first_contact_resolutions(
            owner_id=42, resolutions={},
        )

    assert applied is False
    autospec_service.update_structured_document.assert_not_awaited()
