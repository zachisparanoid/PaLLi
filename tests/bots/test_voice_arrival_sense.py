"""Step 31 (spec §6): the arrival sense — her moment feels people entering
and leaving the channel SHE is in. Him = priority wake (he walked into the
room she is sitting in). Structural notices only (is_owner — names are hers
to look up with her own hands); NO speech here — whether she greets is her
judgment through speak_line. Never raises into discord.py's event loop."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import ai.present_moment as pm
from bots.discord.client import PalliDiscordClient

OWNER_DID = 555
BOT_UID = 99999
HER_CHAN = SimpleNamespace(id=42)
OTHER_CHAN = SimpleNamespace(id=43)


def _client(owner_did: int | None = OWNER_DID) -> PalliDiscordClient:
    client = PalliDiscordClient(
        bot_user_id=BOT_UID, owner_id=1, intents=discord.Intents.default()
    )
    client._local_owner_discord_id = AsyncMock(return_value=owner_did)  # type: ignore[method-assign]
    return client


def _member(uid: int, *, bot: bool = False, her_channel=HER_CHAN):
    vc = SimpleNamespace(channel=her_channel) if her_channel is not None else None
    return SimpleNamespace(id=uid, bot=bot, guild=SimpleNamespace(voice_client=vc))


def _drive(monkeypatch, client, member, before_chan, after_chan):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: seen.append((kind, f)))
    asyncio.run(
        client.on_voice_state_update(
            member,
            SimpleNamespace(channel=before_chan),
            SimpleNamespace(channel=after_chan),
        )
    )
    return seen


def test_owner_joining_her_channel_is_a_priority_arrival(monkeypatch):
    seen = _drive(monkeypatch, _client(), _member(OWNER_DID), None, HER_CHAN)
    assert seen == [("owner_joined_voice", {"is_owner": True})]


def test_owner_leaving_her_channel(monkeypatch):
    seen = _drive(monkeypatch, _client(), _member(OWNER_DID), HER_CHAN, None)
    assert seen == [("owner_left_voice", {"is_owner": True})]


def test_non_owner_human_join_and_leave(monkeypatch):
    seen = _drive(monkeypatch, _client(), _member(777), None, HER_CHAN)
    assert seen == [("voice_channel_joined", {"is_owner": False})]
    seen = _drive(monkeypatch, _client(), _member(777), HER_CHAN, OTHER_CHAN)
    assert seen == [("voice_channel_left", {"is_owner": False})]


def test_moving_from_another_channel_into_hers_is_a_join(monkeypatch):
    seen = _drive(monkeypatch, _client(), _member(OWNER_DID), OTHER_CHAN, HER_CHAN)
    assert seen == [("owner_joined_voice", {"is_owner": True})]


def test_bots_are_ignored(monkeypatch):
    assert _drive(monkeypatch, _client(), _member(888, bot=True), None, HER_CHAN) == []


def test_her_own_transitions_are_ignored(monkeypatch):
    # She is a bot account (bot=True), but pin the id check independently
    # too — a stale member cache must never let her feel her own footsteps.
    assert _drive(monkeypatch, _client(), _member(BOT_UID, bot=True), None, HER_CHAN) == []
    assert _drive(monkeypatch, _client(), _member(BOT_UID, bot=False), None, HER_CHAN) == []


def test_join_in_some_other_channel_is_not_felt(monkeypatch):
    assert _drive(monkeypatch, _client(), _member(OWNER_DID), None, OTHER_CHAN) == []


def test_nothing_when_she_is_in_no_channel(monkeypatch):
    member = _member(OWNER_DID, her_channel=None)
    assert _drive(monkeypatch, _client(), member, None, HER_CHAN) == []


def test_mute_deafen_flaps_are_not_arrivals(monkeypatch):
    assert _drive(monkeypatch, _client(), _member(OWNER_DID), HER_CHAN, HER_CHAN) == []


def test_sense_never_raises_into_the_event_loop(monkeypatch):
    def _boom(kind, **f):
        raise RuntimeError("moment engine down")

    monkeypatch.setattr(pm, "notice", _boom)
    client = _client()
    asyncio.run(
        client.on_voice_state_update(
            _member(OWNER_DID),
            SimpleNamespace(channel=None),
            SimpleNamespace(channel=HER_CHAN),
        )
    )  # completing without raising IS the assertion


def test_owner_joined_voice_is_a_priority_kind_and_phrases_exist():
    assert "owner_joined_voice" in pm._PRIORITY_KINDS
    for kind in (
        "owner_joined_voice",
        "owner_left_voice",
        "voice_channel_joined",
        "voice_channel_left",
    ):
        assert kind in pm._EVENT_PHRASES
