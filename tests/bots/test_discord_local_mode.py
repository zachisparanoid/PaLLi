from __future__ import annotations

import pytest

from bots.discord import discord_commands
from core.local_mode import local_operator_open_discord_enabled


@pytest.mark.asyncio
async def test_voice_context_allows_unmapped_guild_in_local_mode(monkeypatch):
    monkeypatch.setattr("core.local_mode.config.PALLI_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(
        discord_commands.brain,
        "get_account_for_discord_guild",
        lambda guild_id: _async_result(None),
    )

    account_user_id, account_features, block_reason = await discord_commands.resolve_voice_account_context_for_guild(
        guild_id=123,
        channel_id=456,
    )

    assert local_operator_open_discord_enabled() is True
    assert account_user_id is None
    assert account_features is None
    assert block_reason is None


@pytest.mark.asyncio
async def test_voice_context_blocks_unmapped_guild_outside_local_mode(monkeypatch):
    monkeypatch.setattr("core.local_mode.config.PALLI_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setattr(
        discord_commands.brain,
        "get_account_for_discord_guild",
        lambda guild_id: _async_result(None),
    )

    _, _, block_reason = await discord_commands.resolve_voice_account_context_for_guild(
        guild_id=123,
        channel_id=456,
    )

    assert "isn't linked" in str(block_reason)


@pytest.mark.asyncio
async def test_voice_context_bypasses_access_flag_but_keeps_feature_override_in_local_mode(monkeypatch):
    monkeypatch.setattr("core.local_mode.config.PALLI_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(discord_commands.config, "REQUIRE_HAS_ACCESS_FOR_VOICE", True)
    monkeypatch.setattr(
        discord_commands.brain,
        "get_account_for_discord_guild",
        lambda guild_id: _async_result({"account_user_id": 99}),
    )
    monkeypatch.setattr(
        discord_commands.brain,
        "get_app_user_by_id",
        lambda user_id: _async_result({"id": user_id, "has_access": False}),
    )
    monkeypatch.setattr(
        discord_commands.brain,
        "get_account_features",
        lambda user_id: _async_result({"allow_voice": False}),
    )

    account_user_id, account_features, block_reason = await discord_commands.resolve_voice_account_context_for_guild(
        guild_id=123,
        channel_id=456,
    )

    assert account_user_id == 99
    assert account_features == {"allow_voice": False}
    assert "mapped local account" in str(block_reason)


async def _async_result(value):
    return value
