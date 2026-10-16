"""The door's half of "a private chat with him" (the owner, 2026-10-13).

Her own messages to the owner go to a private chat with him. His rows carry a
mark since 10-13 (core/turn/persistence.py); a Discord row written before it
carries nothing that tells his DM from a server channel. For those, core asks
the Discord door through core.channel_runtime: is this chat the DM Discord
keeps between him and her? The door answers by opening that DM (the way the
owner notice and the reminders' DM leg reach him) and comparing its id. It
never raises, and anything it cannot show is False. No real gateway.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.channel_runtime import ChannelRuntimeRegistry

OWNER_DISCORD_ID = 555
HIS_DM = 5001
SERVER_CHANNEL = 9004


class FakeDiscordUser:
    def __init__(self, *, dm_id=HIS_DM, cached_dm=False, fails=False):
        self.dm_id = dm_id
        self.fails = fails
        self.dm_channel = SimpleNamespace(id=dm_id) if cached_dm else None
        self.opened = 0

    async def create_dm(self):
        if self.fails:
            raise RuntimeError("Cannot open a DM with this user")
        self.opened += 1
        self.dm_channel = SimpleNamespace(id=self.dm_id)
        return self.dm_channel


def _adapter(*, ready=True, owner=OWNER_DISCORD_ID, user=None, cached=True):
    from bots.discord.client import DiscordChannelAdapter

    user = user or FakeDiscordUser()
    client = SimpleNamespace(
        is_ready=lambda: ready,
        _local_owner_discord_id=AsyncMock(return_value=owner),
        get_user=lambda uid: user if (cached and uid == OWNER_DISCORD_ID) else None,
        fetch_user=AsyncMock(return_value=user),
    )
    return DiscordChannelAdapter(client), client, user


# ── the Discord door ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_his_dm_is_his_private_chat_and_a_server_channel_is_not():
    adapter, _, user = _adapter()
    assert await adapter.is_owner_private_chat(str(HIS_DM)) is True
    assert await adapter.is_owner_private_chat(str(SERVER_CHANNEL)) is False
    assert user.opened == 1          # opened once; Discord keeps it after


@pytest.mark.asyncio
async def test_an_owner_it_has_not_cached_is_fetched():
    adapter, client, _ = _adapter(cached=False)
    assert await adapter.is_owner_private_chat(str(HIS_DM)) is True
    client.fetch_user.assert_awaited_once_with(OWNER_DISCORD_ID)


@pytest.mark.asyncio
async def test_a_dm_already_open_is_not_opened_again():
    adapter, _, user = _adapter(user=FakeDiscordUser(cached_dm=True))
    assert await adapter.is_owner_private_chat(str(HIS_DM)) is True
    assert user.opened == 0


@pytest.mark.parametrize("kwargs", [
    {"ready": False},                                  # gateway still connecting
    {"owner": None},                                   # owner unknown
    {"user": FakeDiscordUser(fails=True)},             # Discord refuses the DM
])
@pytest.mark.asyncio
async def test_what_the_door_cannot_show_is_false_never_an_exception(kwargs):
    adapter, _, _ = _adapter(**kwargs)
    assert await adapter.is_owner_private_chat(str(HIS_DM)) is False


@pytest.mark.asyncio
async def test_a_chat_id_that_is_not_a_number_is_false():
    adapter, _, _ = _adapter()
    assert await adapter.is_owner_private_chat("not-a-channel") is False
    assert await adapter.is_owner_private_chat("") is False


# ── core.channel_runtime asks the door, and fails closed ──────────────────

class _Door:
    def __init__(self, answer):
        self.answer = answer

    async def send_envelope(self, *, surface_id, envelope):
        return {"sent": True}

    async def is_owner_private_chat(self, surface_id):
        return await self.answer(surface_id)


@pytest.mark.asyncio
async def test_the_registry_passes_on_only_a_plain_yes():
    async def _yes(surface_id):
        return surface_id == str(HIS_DM)

    registry = ChannelRuntimeRegistry()
    await registry.register_adapter("discord", _Door(_yes))
    assert await registry.shows_owner_private_chat("DISCORD ", str(HIS_DM)) is True
    assert await registry.shows_owner_private_chat("discord", str(SERVER_CHANNEL)) is False


@pytest.mark.asyncio
async def test_no_door_or_a_door_that_cannot_say_is_false():
    class _Plain:
        async def send_envelope(self, *, surface_id, envelope):
            return {"sent": True}

    registry = ChannelRuntimeRegistry()
    assert await registry.shows_owner_private_chat("discord", str(HIS_DM)) is False
    await registry.register_adapter("telegram", _Plain())
    assert await registry.shows_owner_private_chat("telegram", "424242") is False


@pytest.mark.asyncio
async def test_a_door_that_raises_hangs_or_hedges_is_false(monkeypatch):
    import core.channel_runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "OWNER_PRIVATE_CHAT_TIMEOUT_S", 0.05)

    async def _boom(surface_id):
        raise RuntimeError("gateway gone")

    async def _hang(surface_id):
        await asyncio.sleep(5)
        return True

    async def _hedge(surface_id):
        return "yes"

    for answer in (_boom, _hang, _hedge):
        registry = ChannelRuntimeRegistry()
        await registry.register_adapter("discord", _Door(answer))
        assert await registry.shows_owner_private_chat("discord", str(HIS_DM)) is False
