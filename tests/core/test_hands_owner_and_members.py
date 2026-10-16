"""Her Discord hands, step 2 part 7 (the who-is-talking plan; add-on b).

10-08, on the web chat, the owner asked her to send a DM on Discord. She tried
send_message to a name the owner's Discord account doesn't use, list_members
answered "client not ready, or no match", and she told the owner, wrongly,
that Discord wasn't answering. Now:

- "the owner" is a destination: straight to the owner's own account, on
  Discord or Telegram;
- "@Name" always means a person, never a channel;
- list_members answers for this channel when it can, marks the owner and puts
  the owner first, and says plainly which of two things happened: Discord
  isn't connected, or nobody here matches.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

import core.agent.hands_runtime as hr

OWNER_DISCORD = 1111
OWNER_TELEGRAM = 2222


def _member(i, display, name, *, bot=False):
    return NS(id=i, display_name=display, name=name, bot=bot, status="")


def _world():
    owner = _member(OWNER_DISCORD, "Robin", "robin_example")
    sam = _member(2, "Sam", "sam_x")
    eddie = _member(3, "Eddie", "eddie4821")
    general = NS(name="general", id=201, members=[sam, owner])
    sam_channel = NS(name="sam", id=203, members=[sam])
    guild = NS(name="home", text_channels=[general, sam_channel], voice_channels=[],
               members=[sam, eddie, owner], system_channel=general)
    client = NS(guilds=[guild], get_channel=lambda cid: {201: general, 203: sam_channel}.get(cid))
    return client, owner, sam


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    hr.register_client(None)

    async def refs():
        return {"app_user_id": 1, "discord_user_id": OWNER_DISCORD, "telegram_user_id": OWNER_TELEGRAM}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", refs)
    yield
    hr.register_client(None)


def _ctx(surface="dashboard", channel_id=0):
    from core.agent.tools import ToolContext
    return ToolContext(turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id)


def _tool(name):
    from core.agent.builtin_tools.her_hands import build_her_hands_tools
    return {t.name: t for t in build_her_hands_tools()}[name]


def _capture(monkeypatch):
    import core.agent.builtin_tools.her_hands as hh
    sent: dict = {}

    async def fake_outbound(**kw):
        sent.update(kw)
        return True
    monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
    return sent


# ── "the owner" ───────────────────────────────────────────────────────────────

def test_the_owner_on_discord_from_the_web_chat(monkeypatch):
    client, owner, _ = _world()
    owner.create_dm = lambda: _done(NS(id=9001))
    hr.register_client(client)
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute({"to": "the owner", "text": "test"}, _ctx()))
    assert sent["channel_id"] == 9001
    assert "the owner" in out and "Discord" in out


def test_the_owner_on_telegram(monkeypatch):
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute(
        {"to": "the owner on telegram", "text": "test"}, _ctx()))
    assert sent["platform"] == "telegram" and sent["channel_id"] == OWNER_TELEGRAM
    assert "Telegram" in out


def test_the_owner_with_discord_down_goes_to_telegram_when_known(monkeypatch):
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute({"to": "owner", "text": "test"}, _ctx()))
    assert sent["platform"] == "telegram"
    assert "Telegram" in out


def test_the_owner_on_discord_with_discord_down_says_so(monkeypatch):
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute(
        {"to": "the owner on discord", "text": "test"}, _ctx()))
    assert not sent
    assert out == "Discord isn't connected right now, so nothing was sent."


def test_the_owner_from_the_owners_own_telegram_chat_is_this_conversation(monkeypatch):
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute(
        {"to": "the owner on telegram", "text": "x"},
        _ctx(surface="telegram_text", channel_id=OWNER_TELEGRAM)))
    assert not sent and "this conversation" in out


# ── "@Name" is a person ───────────────────────────────────────────────────────

def test_at_name_means_the_person_not_the_channel(monkeypatch):
    client, _, sam = _world()
    sam.create_dm = lambda: _done(NS(id=9002))
    hr.register_client(client)
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute({"to": "@sam", "text": "hi"}, _ctx()))
    assert sent["channel_id"] == 9002 and "(DM)" in out


def test_a_name_with_discord_down_says_discord_is_down(monkeypatch):
    sent = _capture(monkeypatch)
    out = asyncio.run(_tool("send_message").execute({"to": "Sam", "text": "hi"}, _ctx()))
    assert not sent and out == "Discord isn't connected right now, so nothing was sent."


# ── list_members ──────────────────────────────────────────────────────────────

def test_list_members_with_discord_down_says_so():
    out = asyncio.run(_tool("list_members").execute({}, _ctx()))
    assert out == "Discord isn't connected right now, so I can't see anyone there."


def test_list_members_nobody_matches_says_so():
    client, _, _ = _world()
    hr.register_client(client)
    out = asyncio.run(_tool("list_members").execute({"query": "zz"}, _ctx()))
    assert out == "Nobody I can see on Discord matches 'zz'."


def test_list_members_marks_the_owner_and_puts_the_owner_first():
    client, _, _ = _world()
    hr.register_client(client)
    out = asyncio.run(_tool("list_members").execute({}, _ctx()))
    lines = out.splitlines()
    assert lines[1].startswith("- Robin (@robin_example") and lines[1].endswith("— the owner")
    assert sum("the owner" in line for line in lines) == 1


def test_list_members_in_a_channel_lists_who_can_see_it():
    client, _, _ = _world()
    hr.register_client(client)
    out = asyncio.run(_tool("list_members").execute({}, _ctx(surface="discord_text", channel_id=201)))
    assert out.startswith("People who can see this channel:")
    assert "Eddie" not in out and "Sam" in out and "Robin" in out


def test_one_person_in_two_servers_is_listed_once():
    client, owner, _ = _world()
    second = NS(name="other", text_channels=[], voice_channels=[], members=[
        _member(OWNER_DISCORD, "Robin", "robin_example")], system_channel=None)
    client.guilds.append(second)
    hr.register_client(client)
    out = asyncio.run(_tool("list_members").execute({}, _ctx()))
    assert out.count("@robin_example") == 1


async def _done(value):
    return value
