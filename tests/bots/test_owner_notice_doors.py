"""The doors' half of core.owner_notice (2026-10-04, the spending check).

Each door registers a sender when it starts: Discord sends to the owner's DMs
through her running client, Telegram to the owner's private chat. Neither
goes through her model or a turn, and neither raises — a door that cannot
send says False and core asks the next one. No real gateway, no real bot.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import owner_notice

OWNER_DISCORD_ID = 555
OWNER_TELEGRAM_ID = 777


@pytest.fixture(autouse=True)
def _no_doors(monkeypatch):
    monkeypatch.setattr(owner_notice, "_senders", {})


class FakeDiscordUser:
    def __init__(self, *, fails: bool = False):
        self.sent: list[str] = []
        self.fails = fails

    async def send(self, text):
        if self.fails:
            raise RuntimeError("Cannot send messages to this user")
        self.sent.append(text)


def _discord_self(*, ready=True, owner=OWNER_DISCORD_ID, user=None, cached=True):
    user = user or FakeDiscordUser()
    return SimpleNamespace(
        is_ready=lambda: ready,
        _local_owner_discord_id=AsyncMock(return_value=owner),
        get_user=lambda uid: user if (cached and uid == OWNER_DISCORD_ID) else None,
        fetch_user=AsyncMock(return_value=user),
    ), user


# ── Discord ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discord_sends_the_notice_to_the_owners_dms():
    from bots.discord.client import PalliDiscordClient

    fake, user = _discord_self()
    assert await PalliDiscordClient.send_owner_notice(fake, "Spending check") is True
    assert user.sent == ["Spending check"]


@pytest.mark.asyncio
async def test_discord_fetches_an_owner_it_has_not_cached():
    from bots.discord.client import PalliDiscordClient

    fake, user = _discord_self(cached=False)
    assert await PalliDiscordClient.send_owner_notice(fake, "hello") is True
    fake.fetch_user.assert_awaited_once_with(OWNER_DISCORD_ID)
    assert user.sent == ["hello"]


@pytest.mark.asyncio
async def test_discord_before_its_gateway_is_up_sends_nothing():
    from bots.discord.client import PalliDiscordClient

    fake, user = _discord_self(ready=False)
    assert await PalliDiscordClient.send_owner_notice(fake, "hello") is False
    assert user.sent == []


@pytest.mark.asyncio
async def test_discord_with_no_known_owner_sends_nothing():
    from bots.discord.client import PalliDiscordClient

    fake, user = _discord_self(owner=None)
    assert await PalliDiscordClient.send_owner_notice(fake, "hello") is False
    assert user.sent == []


@pytest.mark.asyncio
async def test_a_discord_send_that_fails_is_false_not_an_exception():
    from bots.discord.client import PalliDiscordClient

    fake, _ = _discord_self(user=FakeDiscordUser(fails=True))
    assert await PalliDiscordClient.send_owner_notice(fake, "hello") is False


@pytest.mark.asyncio
async def test_the_discord_start_path_registers_the_door_and_takes_it_down(monkeypatch):
    from bots.discord import client as client_mod
    from core.config import config

    monkeypatch.setattr(config, "DISCORD_TOKEN", "test_token_xyz")
    monkeypatch.setattr(client_mod, "_active_client", None)

    async def _refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": OWNER_DISCORD_ID}

    monkeypatch.setattr(client_mod, "get_local_owner_refs", _refs)
    monkeypatch.setattr(client_mod.channel_runtime, "register_adapter", AsyncMock())
    monkeypatch.setattr(client_mod, "_shutdown_voice_sessions", AsyncMock())
    seen: list = []

    async def _start(self, token):
        seen.append(owner_notice._senders.get("discord"))
        seen.append(self.send_owner_notice)

    monkeypatch.setattr(client_mod.PalliDiscordClient, "start", _start)

    await client_mod.start_discord_text_client()

    assert seen and seen[0] is not None and seen[0] == seen[1]
    assert owner_notice.registered_doors() == ()


# ── Telegram ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_sends_the_notice_to_the_owners_private_chat():
    from bots.telegram.client import PalliTelegramClient

    bot = SimpleNamespace(send_message=AsyncMock())
    fake = SimpleNamespace(bot=bot, _owner_telegram_id=AsyncMock(return_value=OWNER_TELEGRAM_ID))

    assert await PalliTelegramClient.send_owner_notice(fake, "Spending check") is True
    bot.send_message.assert_awaited_once_with(chat_id=OWNER_TELEGRAM_ID, text="Spending check")


@pytest.mark.asyncio
async def test_telegram_with_no_known_owner_or_no_bot_sends_nothing():
    from bots.telegram.client import PalliTelegramClient

    bot = SimpleNamespace(send_message=AsyncMock())
    nobody = SimpleNamespace(bot=bot, _owner_telegram_id=AsyncMock(return_value=None))
    assert await PalliTelegramClient.send_owner_notice(nobody, "hello") is False
    no_bot = SimpleNamespace(bot=None, _owner_telegram_id=AsyncMock(return_value=OWNER_TELEGRAM_ID))
    assert await PalliTelegramClient.send_owner_notice(no_bot, "hello") is False
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_telegram_send_that_fails_is_false_not_an_exception():
    from bots.telegram.client import PalliTelegramClient

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("Forbidden: bot was blocked")))
    fake = SimpleNamespace(bot=bot, _owner_telegram_id=AsyncMock(return_value=OWNER_TELEGRAM_ID))
    assert await PalliTelegramClient.send_owner_notice(fake, "hello") is False


@pytest.mark.asyncio
async def test_the_telegram_start_path_registers_the_door_and_takes_it_down(monkeypatch):
    import sys
    import types as _types

    from bots.telegram import client as tc
    from core.config import config
    from tests.bots.test_telegram_client import FakeApplication

    app = FakeApplication()

    class _Builder:
        def token(self, _t):
            return self

        def build(self):
            return app

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional", raising=False)
    monkeypatch.setattr(tc.channel_runtime, "register_adapter", AsyncMock())
    monkeypatch.setattr(tc.channel_runtime, "unregister_adapter", AsyncMock())

    async def _refs():
        return {"app_user_id": 1, "telegram_user_id": OWNER_TELEGRAM_ID}

    monkeypatch.setattr(tc, "get_local_owner_refs", _refs)
    fake_ext = _types.ModuleType("telegram.ext")
    fake_ext.ApplicationBuilder = lambda: _Builder()
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)
    seen: list = []

    async def _run_polling(self):
        seen.append(owner_notice._senders.get("telegram"))
        seen.append(self.send_owner_notice)

    monkeypatch.setattr(tc.PalliTelegramClient, "run_polling", _run_polling)
    monkeypatch.setattr(tc.PalliTelegramClient, "register_handlers", lambda self: None)

    await tc.start_telegram_client()

    assert seen and seen[0] is not None and seen[0] == seen[1]
    assert owner_notice.registered_doors() == ()
