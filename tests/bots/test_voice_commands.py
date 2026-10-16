"""Voice slash-commands on the slim client (v1 rewire, 2026-07-11).

The runtime was parked because nothing registered its entry point; these
tests pin the re-homed registration and the two cheapest high-value handler
behaviors, offline (no gateway, no DB writes — preference services mocked).

Task 6 (2026-08-11) added a guild-None guard to all six commands (a DM now
gets a friendly "server only" line instead of crashing). The two handler
tests below now give their fake interaction a real (fake) `guild` object so
they keep exercising in-server behavior instead of unknowingly tripping the
new guard; `test_voice_status_...` also mocks
`resolve_voice_account_context_for_guild` so the now-reachable account
lookup stays offline rather than touching the DB (its result is irrelevant
here — `get_shared_voice_preferences` is already mocked to a fixed value).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bots.discord.voice_commands import register_voice_commands


@pytest.fixture
def caller_is_the_owner(monkeypatch):
    """Batch 8, task 2b (ruling T2b-7): every voice command asks who is
    calling first, and only the owner gets past it. These tests are about the
    owner's own commands, so the caller is the owner (mechanical)."""
    from bots.common.commands import Caller
    import bots.discord.voice_commands as vcmd

    async def resolve_caller(*, platform, platform_user_id):
        return Caller(is_owner=True, user_id=1, platform_user_id=int(platform_user_id),
                      platform=platform)

    monkeypatch.setattr(vcmd, "resolve_caller", resolve_caller)


def _tree() -> discord.app_commands.CommandTree:
    client = discord.Client(intents=discord.Intents.none())
    return discord.app_commands.CommandTree(client)


def _interaction(*, in_voice: bool = False, guild=None):
    """Minimal fake Interaction: deferred response + followup spy."""
    user = SimpleNamespace(
        id=1,
        name="sam",
        voice=SimpleNamespace(channel=SimpleNamespace(id=42, name="den")) if in_voice else None,
    )
    return SimpleNamespace(
        user=user,
        guild=guild,
        channel=SimpleNamespace(id=99),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_register_voice_commands_registers_exactly_the_v1_set():
    tree = _tree()
    register_voice_commands(tree)
    names = {c.name for c in tree.get_commands()}
    assert names == {
        "join_voice", "leave_voice", "voice_mode",
        "voice_presence", "voice_focus", "voice_status",
    }


def test_join_voice_requires_a_voice_channel(caller_is_the_owner):
    """In a server, but not in a voice channel: the pre-existing helpful
    nudge, not the Task-6 guild-None guard (which this fake must now clear
    by supplying a real guild — see module docstring's Task-6 addendum)."""
    tree = _tree()
    register_voice_commands(tree)
    cmd = tree.get_command("join_voice")
    interaction = _interaction(in_voice=False, guild=SimpleNamespace(id=1))
    asyncio.run(cmd.callback(interaction))
    interaction.response.defer.assert_awaited_once()
    sent = interaction.followup.send.await_args.args[0]
    assert "voice channel first" in sent


def test_join_voice_move_stops_old_session_before_disconnect(monkeypatch, caller_is_the_owner):
    """Already connected elsewhere: the OLD listening session is stopped
    BEFORE the old client disconnects (the parked step-33 zombie, fixed
    2026-08-14 — a bare disconnect left the sinks bound to the dead
    connection). The fake target channel has no .connect, so the handler
    then errors out through its own guard — only the teardown ordering is
    under test here."""
    import bots.discord.voice_commands as vcmd

    tree = _tree()
    register_voice_commands(tree)
    cmd = tree.get_command("join_voice")

    order = []

    async def fake_stop(channel_id):
        order.append(("stop", channel_id))
        return True

    async def fake_disconnect():
        order.append(("disconnect", None))

    old_vc = SimpleNamespace(
        channel=SimpleNamespace(id=777, name="old-room"),
        disconnect=fake_disconnect,
    )
    guild = SimpleNamespace(id=1, voice_client=old_vc)
    monkeypatch.setattr(vcmd.config, "ENABLE_VOICE_FEATURES", True)
    monkeypatch.setattr(
        vcmd, "resolve_voice_account_context_for_guild",
        AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(
        vcmd, "require_voice_runtime_ready",
        lambda: SimpleNamespace(ok=True, details={}, messages=[]))
    monkeypatch.setattr(vcmd.voice_processor, "stop_listening", fake_stop)

    interaction = _interaction(in_voice=True, guild=guild)
    asyncio.run(cmd.callback(interaction))
    assert order == [("stop", 777), ("disconnect", None)]


def test_voice_status_works_without_a_live_session(monkeypatch, caller_is_the_owner):
    """In a server, with no live voice_client: the saved-defaults embed
    still renders (must not crash on the absent live session)."""
    import bots.discord.voice_commands as vc
    monkeypatch.setattr(
        vc, "get_shared_voice_preferences",
        AsyncMock(return_value=("local", {
            "interaction_mode": "direct_address",
            "presence_profile": "balanced",
            "focus_state": "responsive",
        })),
    )
    monkeypatch.setattr(
        vc, "resolve_voice_account_context_for_guild",
        AsyncMock(return_value=(None, None, None)),
    )
    tree = _tree()
    register_voice_commands(tree)
    cmd = tree.get_command("voice_status")
    interaction = _interaction(guild=SimpleNamespace(id=1, voice_client=None))
    asyncio.run(cmd.callback(interaction))
    kwargs = interaction.followup.send.await_args.kwargs
    embed = kwargs.get("embed")
    assert embed is not None and "Voice Status" in embed.title


# ── client wiring (Task 2) ──────────────────────────────────────────────

def _client(monkeypatch, *, voice_enabled: bool):
    from core.config import config as cfg
    monkeypatch.setattr(cfg, "ENABLE_VOICE_FEATURES", voice_enabled)
    from bots.discord.client import PalliDiscordClient
    return PalliDiscordClient(owner_id=1, intents=discord.Intents.none())


def test_client_registers_voice_commands_when_flag_on(monkeypatch):
    c = _client(monkeypatch, voice_enabled=True)
    names = {cmd.name for cmd in c.tree.get_commands()}
    assert "join_voice" in names and "leave_voice" in names


def test_client_flag_off_means_no_voice_command_on_the_tree(monkeypatch):
    """Flag-off purity: not one voice command, so the guild sync CLEARS any
    stale ones instead of re-publishing them.

    Re-premised 2026-09-14 (telegram door, task 3). Until then this asserted
    an EMPTY tree, which was the same statement while voice was the only
    thing that ever registered. It is not any more: insights / link_account
    / proactive_consent go on the tree unconditionally, because they are not
    voice commands and a host with no voice runtime still gets them. The
    thing this test is about — the voice flag decides voice, and nothing
    else — is unchanged and asserted exactly.
    """
    c = _client(monkeypatch, voice_enabled=False)
    names = {cmd.name for cmd in c.tree.get_commands()}
    assert names == {"insights", "link_account", "proactive_consent"}
    assert not any(
        name in names
        for name in ("join_voice", "leave_voice", "voice_focus", "voice_mode",
                     "voice_presence", "voice_status")
    )


@pytest.mark.asyncio
async def test_sync_voice_commands_syncs_each_guild_once(monkeypatch):
    c = _client(monkeypatch, voice_enabled=True)
    guild = SimpleNamespace(id=123)
    copy_spy = MagicMock()
    sync_spy = AsyncMock()
    monkeypatch.setattr(c.tree, "copy_global_to", copy_spy)
    monkeypatch.setattr(c.tree, "sync", sync_spy)

    await c._sync_voice_commands([guild])
    await c._sync_voice_commands([guild])  # second call is a no-op

    copy_spy.assert_called_once_with(guild=guild)
    sync_spy.assert_awaited_once_with(guild=guild)


# ── shutdown cleanup (Task 3) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_shutdown_voice_sessions_stops_active_sessions(monkeypatch):
    import voice.always_listening as al
    from bots.discord import client as client_mod
    fake_vp = SimpleNamespace(
        get_session_count=lambda: 2,
        stop_all_sessions=AsyncMock(),
    )
    monkeypatch.setattr(al, "voice_processor", fake_vp)
    await client_mod._shutdown_voice_sessions()
    fake_vp.stop_all_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_voice_sessions_noop_when_idle(monkeypatch):
    import voice.always_listening as al
    from bots.discord import client as client_mod
    fake_vp = SimpleNamespace(
        get_session_count=lambda: 0,
        stop_all_sessions=AsyncMock(),
    )
    monkeypatch.setattr(al, "voice_processor", fake_vp)
    await client_mod._shutdown_voice_sessions()
    fake_vp.stop_all_sessions.assert_not_awaited()
