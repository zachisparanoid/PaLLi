"""A tool may name the doors it works behind (telegram door, task 1).

Five of her hands reach for a Discord object — a voice channel, a guild
member list, her own Discord presence. Offered on a door where that object
does not exist, they are a promise she cannot keep: the model calls one and
gets an exception instead of an answer.

So a tool may carry a ``surfaces`` tuple. A tool WITHOUT one is offered
everywhere, exactly as every tool is today — that default is the whole
reason this change moves nothing for Discord.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.agent.session import ToolUseBlock
from core.agent.tools import ToolContext, ToolRegistry


class _Anywhere:
    name = "anywhere"
    description = "offered on every door"
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx) -> str:
        return "ran"


class _DiscordOnly:
    name = "discord_only"
    description = "needs a Discord object"
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = False
    surfaces = ("discord",)

    async def execute(self, input: dict[str, Any], ctx) -> str:
        return "ran"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_Anywhere())
    reg.register(_DiscordOnly())
    return reg


def _offered(surface: str) -> set[str]:
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface=surface)
    return {schema["name"] for schema in _registry().schemas_for(ctx)}


# ── the listing ─────────────────────────────────────────────────────────

def test_a_discord_turn_is_offered_everything_it_is_offered_today():
    assert _offered("discord_text") == {"anywhere", "discord_only"}
    assert _offered("discord_voice") == {"anywhere", "discord_only"}


def test_a_blank_surface_filters_nothing():
    """Most ToolContexts in the tree are built without a surface; an unknown
    platform must never silently shrink her belt."""
    assert _offered("") == {"anywhere", "discord_only"}
    assert _offered("proactive") == {"anywhere", "discord_only"}
    assert _offered("reminder_fire") == {"anywhere", "discord_only"}


def test_a_telegram_turn_is_not_offered_the_discord_hand():
    assert _offered("telegram_text") == {"anywhere"}


def test_an_unmarked_tool_is_offered_on_every_door():
    for surface in ("discord_text", "telegram_text", "dashboard", "voice_local", ""):
        assert "anywhere" in _offered(surface), surface


def test_an_empty_surfaces_tuple_filters_nothing():
    """A tool that declares no door in particular is not a tool that works
    on none of them."""
    class _Undecided(_Anywhere):
        name = "undecided"
        surfaces = ()

    reg = ToolRegistry()
    reg.register(_Undecided())
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface="telegram_text")
    assert [s["name"] for s in reg.schemas_for(ctx)] == ["undecided"]


def test_owner_only_still_hides_a_tool_from_a_guest():
    class _OwnerOnly(_Anywhere):
        name = "owner_only"
        owner_only = True

    reg = ToolRegistry()
    reg.register(_OwnerOnly())
    guest = ToolContext(turn_owner_id=9, is_owner=False, surface="discord_text")
    assert reg.schemas_for(guest) == []


# ── the refusal ─────────────────────────────────────────────────────────

def _dispatch(surface: str, name: str):
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface=surface)
    tu = ToolUseBlock(id="t1", name=name, input={})
    return asyncio.run(_registry().dispatch(tu, ctx))


def test_calling_a_discord_hand_from_telegram_is_an_honest_error():
    result = _dispatch("telegram_text", "discord_only")
    assert result.is_error is True
    assert "discord_only" in result.content
    assert "surface" in result.content.lower()


def test_calling_it_from_discord_still_runs():
    result = _dispatch("discord_text", "discord_only")
    assert result.is_error is False
    assert result.content == "ran"


def test_calling_it_with_no_surface_still_runs():
    result = _dispatch("", "discord_only")
    assert result.is_error is False


def test_an_unmarked_tool_runs_on_telegram():
    result = _dispatch("telegram_text", "anywhere")
    assert result.is_error is False


# ── her actual hands ────────────────────────────────────────────────────

_THE_FIVE_HANDS = [
    "JoinVoiceChannelTool",
    "LeaveVoiceTool",
    "SpeakLineTool",
    "ListMembersTool",
    "SetPresenceTool",
]


# Step 2, part 8 (2026-10-08): list_members answers on Telegram too, "who's
# in here?" for a group (its admins and head count, through the Telegram
# door's bot); the other four still reach only for Discord.
_DISCORD_ONLY_HANDS = [c for c in _THE_FIVE_HANDS if c != "ListMembersTool"]


@pytest.mark.parametrize("class_name", _DISCORD_ONLY_HANDS)
def test_the_discord_shaped_hands_name_their_doors(class_name):
    """They reach for a Discord object through the global client, not through
    anything on the ctx — so the list is every platform that offered them
    before the surface gate existed, and Telegram is the only exclusion."""
    import core.agent.builtin_tools.her_hands as hh

    tool = getattr(hh, class_name)
    assert getattr(tool, "surfaces", None) == ("discord", "dashboard", "voice"), class_name


@pytest.mark.parametrize("class_name", _THE_FIVE_HANDS)
@pytest.mark.parametrize("surface", ["discord_text", "discord_voice", "dashboard", "voice_local"])
def test_the_five_hands_are_still_offered_where_they_always_were(class_name, surface):
    """The pin. `dashboard` and `voice_local` map to platforms of their own
    (core/turn/recall.py), and a hand hidden there is a hand she lost."""
    import core.agent.builtin_tools.her_hands as hh

    reg = ToolRegistry()
    reg.register(getattr(hh, class_name)())
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface=surface)
    assert [s["name"] for s in reg.schemas_for(ctx)], f"{class_name} hidden on {surface}"


def test_list_members_names_telegram_too():
    import core.agent.builtin_tools.her_hands as hh

    assert hh.ListMembersTool.surfaces == ("discord", "dashboard", "voice", "telegram")
    reg = ToolRegistry()
    reg.register(hh.ListMembersTool())
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface="telegram_text")
    assert [s["name"] for s in reg.schemas_for(ctx)] == ["list_members"]


@pytest.mark.parametrize("class_name", _DISCORD_ONLY_HANDS)
def test_the_four_hands_are_hidden_and_refused_on_telegram(class_name):
    import core.agent.builtin_tools.her_hands as hh

    tool = getattr(hh, class_name)()
    reg = ToolRegistry()
    reg.register(tool)
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface="telegram_text")

    assert reg.schemas_for(ctx) == []
    result = asyncio.run(reg.dispatch(ToolUseBlock(id="t1", name=tool.name, input={}), ctx))
    assert result.is_error is True
    assert "surface" in result.content.lower()


def test_send_message_is_not_pinned_to_discord():
    """It learns a Telegram destination in task 2; pinning it here would
    have to be undone there."""
    import core.agent.builtin_tools.her_hands as hh

    assert not getattr(hh.SendMessageTool, "surfaces", ())


def test_the_mid_task_acts_are_not_pinned_to_discord():
    import core.agent.builtin_tools.duplex_hands as dh

    assert not getattr(dh.ChimeInTool, "surfaces", ())
    assert not getattr(dh.AskHimTool, "surfaces", ())
