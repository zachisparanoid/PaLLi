"""The Discord client is the writer of the owner-presence sense (2026-08-18):
his status/activity across his own devices is the first REAL presence signal
on this headless host. Only HIS presence is recorded; everyone else in the
guild is ignored; on_ready reconciles from the member cache; the moment is
notified on the present<->offline edges through core.owner_presence's hook."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from bots.discord.client import PalliDiscordClient
from core import owner_presence as op


@pytest.fixture(autouse=True)
def _fresh():
    op.reset_for_tests()
    yield
    op.reset_for_tests()


def _member(uid: int, status: str = "online", activity_name: str | None = None):
    act = SimpleNamespace(name=activity_name) if activity_name else None
    return SimpleNamespace(id=uid, status=SimpleNamespace(value=status),
                           activity=act, activities=[act] if act else [], bot=False)


def _client(owner_did: int = 555) -> PalliDiscordClient:
    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    client._local_owner_discord_id = AsyncMock(return_value=owner_did)  # type: ignore[method-assign]
    return client


def test_presence_update_for_the_owner_is_recorded_and_others_are_ignored():
    client = _client()
    asyncio.run(client.on_presence_update(_member(555, "offline"), _member(555, "online", "Star Citizen")))
    assert op.snapshot()["status"] == "online"
    assert op.snapshot()["activity"] == "Star Citizen"
    asyncio.run(client.on_presence_update(_member(777, "online"), _member(777, "offline")))
    assert op.snapshot()["status"] == "online"      # a stranger's presence changes nothing


def test_presence_update_maps_invisible_to_offline_and_tolerates_missing_activity():
    client = _client()
    asyncio.run(client.on_presence_update(None, _member(555, "invisible")))
    assert op.snapshot()["status"] == "offline"


def test_on_ready_reconciles_from_the_member_cache_and_installs_the_moment_hook(monkeypatch):
    client = _client()
    guild = SimpleNamespace(get_member=lambda uid: _member(555, "idle") if uid == 555 else None)
    monkeypatch.setattr(type(client), "guilds", property(lambda self: [guild]))
    monkeypatch.setattr(type(client), "user", property(lambda self: SimpleNamespace(id=99999, name="palli")))
    monkeypatch.setattr(client, "_sync_voice_commands", AsyncMock())
    seen = []
    import ai.present_moment as pm
    monkeypatch.setattr(pm, "notice", lambda kind, **f: seen.append(kind))
    asyncio.run(client.on_ready())
    assert op.snapshot()["status"] == "idle"
    # the hook is the moment's notice: an offline->online edge later reaches it
    op.record_discord_presence(status="offline", at=1000.0)
    op.record_discord_presence(status="online", at=1000.0 + 3600)
    assert seen[-1] == "owner_online"


def test_unresolved_owner_records_nothing():
    client = _client(owner_did=None)  # type: ignore[arg-type]
    asyncio.run(client.on_presence_update(None, _member(555, "online")))
    assert op.snapshot()["status"] == "unknown"


def test_custom_status_is_not_reported_as_playing_but_a_game_is():
    """A custom status is a typed mood line, not an activity — never render it
    as "playing …". A game/app activity is."""
    client = _client()
    custom = SimpleNamespace(name="Custom Status", type=discord.ActivityType.custom, state="🦜 building")
    game = SimpleNamespace(name="Star Citizen", type=discord.ActivityType.playing)
    m = SimpleNamespace(id=555, status=SimpleNamespace(value="online"), activity=custom,
                        activities=[custom], bot=False)
    asyncio.run(client.on_presence_update(None, m))
    assert op.snapshot()["activity"] == ""
    m2 = SimpleNamespace(id=555, status=SimpleNamespace(value="online"), activity=custom,
                         activities=[custom, game], bot=False)
    asyncio.run(client.on_presence_update(None, m2))
    assert op.snapshot()["activity"] == "Star Citizen"
