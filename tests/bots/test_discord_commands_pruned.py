"""What Discord actually offers, after the prune (telegram door, task 3).

``bots/discord/discord_commands.py`` carried 22 SaaS-era slash commands
inside a ``setup_commands(bot: commands.Bot)`` that NOTHING called — the live
client is a plain ``discord.Client`` and registers its tree itself
(bots/discord/client.py). So the file was 1,700 lines of code that could not
run, and three commands the owner's spec wants — ``insights``,
``link_account``, ``proactive_consent`` — were not on Discord at all.

This file pins the shape that replaces it:

  * the dead set is GONE, by name, from the module and from its source;
  * ``resolve_voice_account_context_for_guild`` STAYS (voice_commands.py:30
    imports it and tests/bots/test_discord_local_mode.py pins it);
  * the three shared commands are on the tree ALWAYS — they are not voice,
    so ``ENABLE_VOICE_FEATURES`` must not decide whether they exist;
  * with the flag on, the tree is exactly those three plus the voice six —
    nine, no more;
  * their bodies are glue: the WORDS come from bots/common/commands.py, so
    the same command says the same thing on the other door.

Hermetic: discord.py is installed and a client is constructed exactly as
tests/bots/test_guest_rate_limit.py does; nothing here opens a socket.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Every command name the dead setup_commands defined, minus the three the
# spec keeps here and the six that live in voice_commands.py. None of these
# may come back into this file.
DELETED_COMMAND_NAMES = (
    "link_server",
    "unlink_account",
    "toggle_decisions",
    "toggle_analysis",
    "multi_analysis",
    "toggle_status",
    "plan_info",
    "proactive_stats",
    "toggle_proactive_verbose",
    "toggle_proactive_blocking",
    "my_personality",
    "personality_preset",
    "voice_personality",
)

KEPT_WITH_VOICE = [
    "insights",
    "join_voice",
    "leave_voice",
    "link_account",
    "proactive_consent",
    "voice_focus",
    "voice_mode",
    "voice_presence",
    "voice_status",
]
KEPT_WITHOUT_VOICE = ["insights", "link_account", "proactive_consent"]

MODULE_PATH = Path(__file__).resolve().parents[2] / "bots" / "discord" / "discord_commands.py"


def _client(monkeypatch, *, voice: bool):
    import discord

    from core.config import config

    monkeypatch.setattr(config, "ENABLE_VOICE_FEATURES", voice)
    from bots.discord.client import PalliDiscordClient

    return PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
    )


class _FakeResponse:
    def __init__(self):
        self.deferred: list[dict] = []

    async def defer(self, **kw):
        self.deferred.append(kw)


class _FakeFollowup:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, content=None, **kw):
        self.sent.append((content, kw))


class _FakeUser:
    def __init__(self, id=1234567890123):
        self.id = id
        self.name = "stranger"


class _FakeInteraction:
    def __init__(self, user_id=1234567890123):
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.user = _FakeUser(user_id)


def _command(tree, name):
    for command in tree.get_commands():
        if command.name == name:
            return command
    raise AssertionError(f"{name} is not on the tree")


# ── the tree ─────────────────────────────────────────────────────────────

def test_the_tree_is_the_voice_six_plus_the_three(monkeypatch):
    client = _client(monkeypatch, voice=True)

    assert sorted(c.name for c in client.tree.get_commands()) == KEPT_WITH_VOICE


def test_the_three_are_there_even_with_voice_off(monkeypatch):
    """They are not voice commands. A host with no voice runtime still gets
    to link an account, read her insights and decide about reach-outs."""
    client = _client(monkeypatch, voice=False)

    assert sorted(c.name for c in client.tree.get_commands()) == KEPT_WITHOUT_VOICE


# ── the prune ────────────────────────────────────────────────────────────

def test_the_dead_entry_point_and_its_toggle_store_are_gone():
    from bots.discord import discord_commands

    assert not hasattr(discord_commands, "setup_commands")
    assert not hasattr(discord_commands, "get_user_toggles")
    assert not hasattr(discord_commands, "user_toggles")


def test_the_voice_account_resolver_survives():
    """voice_commands.py:30 imports it; test_discord_local_mode.py pins it."""
    from bots.discord import discord_commands

    assert hasattr(discord_commands, "resolve_voice_account_context_for_guild")
    from bots.discord.voice_commands import (  # noqa: F401
        resolve_voice_account_context_for_guild,
    )


def test_no_deleted_command_name_is_left_in_the_source():
    source = MODULE_PATH.read_bytes().decode("utf-8")
    left = [name for name in DELETED_COMMAND_NAMES if f'name="{name}"' in source]

    assert left == []


def test_the_registration_function_is_the_only_entry_point():
    """Two public callables DEFINED here, and no third.

    Strengthened in review round 1: asserting `callable(...)` proved the
    name existed, not that it was the only way in — which is the whole
    point of a file that used to have a second, dead one.
    """
    from bots.discord import discord_commands

    ours = {
        name
        for name, value in vars(discord_commands).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", "") == discord_commands.__name__
    }

    assert ours == {"resolve_voice_account_context_for_guild",
                    "register_common_commands"}


# ── the glue ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insights_renders_the_shared_text_ephemerally(monkeypatch):
    from bots.discord import discord_commands

    client = _client(monkeypatch, voice=False)
    monkeypatch.setattr(discord_commands, "resolve_caller",
                        AsyncMock(return_value="CALLER"))
    said = AsyncMock(return_value="After 9 conversations, here's what I know.")
    monkeypatch.setattr(discord_commands, "insights_text", said)

    interaction = _FakeInteraction()
    await _command(client.tree, "insights").callback(interaction)

    assert said.await_args.args[0] == "CALLER"
    assert interaction.response.deferred == [{"ephemeral": True}]
    content, kw = interaction.followup.sent[0]
    assert content == "After 9 conversations, here's what I know."
    assert kw["ephemeral"] is True


@pytest.mark.asyncio
async def test_link_account_hands_the_code_to_the_shared_function(monkeypatch):
    from bots.discord import discord_commands

    client = _client(monkeypatch, voice=False)
    linked = AsyncMock(return_value="Linked.")
    monkeypatch.setattr(discord_commands, "link_platform_account", linked)

    interaction = _FakeInteraction(user_id=424242)
    await _command(client.tree, "link_account").callback(interaction, "ab12cd")

    assert linked.await_args.kwargs == {
        "platform": "discord", "platform_user_id": 424242, "code": "ab12cd",
    }
    assert interaction.followup.sent[0][0] == "Linked."


@pytest.mark.asyncio
async def test_proactive_consent_writes_only_on_an_explicit_value(monkeypatch):
    from bots.discord import discord_commands

    client = _client(monkeypatch, voice=False)
    monkeypatch.setattr(discord_commands, "resolve_caller",
                        AsyncMock(return_value="CALLER"))
    setter = AsyncMock(return_value="Reach-outs are off.")
    monkeypatch.setattr(discord_commands, "set_proactive_consent", setter)

    interaction = _FakeInteraction()
    await _command(client.tree, "proactive_consent").callback(interaction, False)

    assert setter.await_args.args[0] == "CALLER"
    assert setter.await_args.args[1] is False
    assert interaction.followup.sent[0][0] == "Reach-outs are off."


@pytest.mark.asyncio
async def test_the_bare_form_reads_the_setting_and_never_writes(monkeypatch):
    """One word, one meaning, at both doors (review round 1, minor 4).

    It used to TOGGLE when omitted — inherited from the dead handler —
    while Telegram's bare /proactive reported. That is how a person turns
    his reach-outs off by asking whether they are on.
    """
    from bots.discord import discord_commands

    client = _client(monkeypatch, voice=False)
    monkeypatch.setattr(discord_commands, "resolve_caller",
                        AsyncMock(return_value="CALLER"))
    setter = AsyncMock(return_value="written")
    status = AsyncMock(return_value="Reach-outs are on.")
    monkeypatch.setattr(discord_commands, "set_proactive_consent", setter)
    monkeypatch.setattr(discord_commands, "proactive_status", status)

    interaction = _FakeInteraction()
    await _command(client.tree, "proactive_consent").callback(interaction)

    setter.assert_not_awaited()
    assert status.await_args.args[0] == "CALLER"
    assert interaction.followup.sent[0][0] == "Reach-outs are on."


@pytest.mark.asyncio
async def test_a_failing_shared_call_says_so_instead_of_vanishing(monkeypatch):
    """discord.py swallows what a command callback raises: an exception with
    no followup is a command that silently did nothing."""
    from bots.discord import discord_commands

    client = _client(monkeypatch, voice=False)
    monkeypatch.setattr(discord_commands, "resolve_caller",
                        AsyncMock(side_effect=RuntimeError("db down")))

    interaction = _FakeInteraction()
    await _command(client.tree, "insights").callback(interaction)

    assert interaction.followup.sent, "the failure was never told"
    assert interaction.followup.sent[0][1]["ephemeral"] is True
    # The same sentence at both doors, from one place (review round 1,
    # minor 7) — it is the same failure.
    from bots.common.commands import TROUBLE_LINE

    assert interaction.followup.sent[0][0] == TROUBLE_LINE


# ── the file itself ──────────────────────────────────────────────────────

def test_the_file_is_still_crlf():
    raw = MODULE_PATH.read_bytes()
    lines = raw.split(b"\n")[:-1] if raw.endswith(b"\n") else raw.split(b"\n")

    assert lines, "the file is empty"
    assert all(line.endswith(b"\r") for line in lines)
