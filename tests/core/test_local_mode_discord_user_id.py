"""Tests for LOCAL_OWNER_DISCORD_USER_ID population in ensure_local_owner.

Phase A task A6 of the 2026-05-08 completion refactor: when the local
owner has a NULL `discord_user_id` AND the operator has set the
`LOCAL_OWNER_DISCORD_USER_ID` env var, ensure_local_owner populates
the column on first call. Existing values are not overwritten.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest


def _make_owner_row(*, discord_user_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": 1,
        "email": "local@palli.local",
        "palli_user_id": 1,
        "role": "admin",
        "has_access": True,
        "email_verified": True,
        "email_verified_at": 0.0,
        "age_confirmed": True,
        "age_confirmed_at": 0.0,
        "tos_accepted_at": 0.0,
        "privacy_accepted_at": 0.0,
        "tos_version": "local",
        "privacy_version": "local",
        "discord_user_id": discord_user_id,
    }


class _FakeBrain:
    """Captures calls to update_app_user so tests can assert payload shape."""

    def __init__(self, owner_row: Dict[str, Any]) -> None:
        self._owner = dict(owner_row)
        self.update_calls: List[Dict[str, Any]] = []

    async def get_app_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return dict(self._owner)

    async def get_app_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        return dict(self._owner)

    async def update_app_user(self, user_id: int, updates: Dict[str, Any]) -> bool:
        self.update_calls.append(dict(updates))
        # Reflect the update so a subsequent get_app_user_by_id returns
        # the merged row.
        self._owner.update(updates)
        return True

    async def get_user_profile(self, palli_user_id: int, platform: str) -> Dict[str, Any]:
        return {"display_name": "Local Operator", "username": "Local Operator", "preferences": {"voice_enabled": True, "proactive_consent": {"enabled": True}}}

    async def update_user_profile(self, palli_user_id: int, platform: str, updates: Dict[str, Any]) -> bool:
        return True


@pytest.mark.asyncio
async def test_ensure_local_owner_populates_discord_user_id_from_config(monkeypatch):
    """When config.LOCAL_OWNER_DISCORD_USER_ID is set and the user's
    discord_user_id is NULL, ensure_local_owner sends an update."""
    from core import local_mode

    fake_brain = _FakeBrain(_make_owner_row(discord_user_id=None))
    monkeypatch.setattr(local_mode, "brain", fake_brain)
    monkeypatch.setattr(
        local_mode.config,
        "LOCAL_OWNER_DISCORD_USER_ID",
        "100000000000000001",
    )

    await local_mode.ensure_local_owner()

    discord_updates = [
        call for call in fake_brain.update_calls
        if "discord_user_id" in call
    ]
    assert len(discord_updates) == 1, f"expected one update with discord_user_id, got {fake_brain.update_calls}"
    assert discord_updates[0]["discord_user_id"] == "100000000000000001"


@pytest.mark.asyncio
async def test_ensure_local_owner_skips_population_when_config_unset(monkeypatch):
    """When LOCAL_OWNER_DISCORD_USER_ID is empty/unset, the update payload
    must not include a discord_user_id key."""
    from core import local_mode

    fake_brain = _FakeBrain(_make_owner_row(discord_user_id=None))
    monkeypatch.setattr(local_mode, "brain", fake_brain)
    monkeypatch.setattr(local_mode.config, "LOCAL_OWNER_DISCORD_USER_ID", "")

    await local_mode.ensure_local_owner()

    for call in fake_brain.update_calls:
        assert "discord_user_id" not in call, (
            f"unexpected discord_user_id update when env var was unset: {call}"
        )


@pytest.mark.asyncio
async def test_ensure_local_owner_does_not_overwrite_existing_discord_user_id(monkeypatch):
    """If the user already has a discord_user_id, ensure_local_owner must
    leave it alone even if the env var is set to a different value."""
    from core import local_mode

    fake_brain = _FakeBrain(_make_owner_row(discord_user_id="111111111111111111"))
    monkeypatch.setattr(local_mode, "brain", fake_brain)
    monkeypatch.setattr(
        local_mode.config,
        "LOCAL_OWNER_DISCORD_USER_ID",
        "100000000000000001",
    )

    await local_mode.ensure_local_owner()

    for call in fake_brain.update_calls:
        assert "discord_user_id" not in call, (
            f"existing discord_user_id was unexpectedly overwritten: {call}"
        )


@pytest.mark.asyncio
async def test_ensure_local_owner_ignores_invalid_discord_user_id(monkeypatch):
    """A non-numeric LOCAL_OWNER_DISCORD_USER_ID is logged-and-ignored
    rather than written to the column."""
    from core import local_mode

    fake_brain = _FakeBrain(_make_owner_row(discord_user_id=None))
    monkeypatch.setattr(local_mode, "brain", fake_brain)
    monkeypatch.setattr(
        local_mode.config,
        "LOCAL_OWNER_DISCORD_USER_ID",
        "not-a-number",
    )

    await local_mode.ensure_local_owner()

    for call in fake_brain.update_calls:
        assert "discord_user_id" not in call, (
            f"invalid discord_user_id should not have been written: {call}"
        )
