"""Two small fixes from the friend-test rehearsal (2026-10-09, the owner's OK
"yes, do the two small fixes now").

1. send_message to the owner's own account by its handle, id or shown name is
   a message to the owner, not to a third party (the rehearsal: "DM me on
   Discord" from Telegram went to the owner's own handle, arrived, and was filed as someone
   else's).
2. _mentions_bot's fallback reads Telegram's UTF-16 entity offsets correctly:
   an emoji before her @handle no longer hides the mention (the library's own
   parse, used live, was always right).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

import core.agent.hands_runtime as hr

OWNER_DISCORD = 12345678901234567        # snowflake-shaped (17 digits), as a bare id must be


@pytest.fixture
def world(monkeypatch):
    owner = NS(id=OWNER_DISCORD, display_name="Robin", name="robin_example", bot=False, status="")
    sam = NS(id=2, display_name="Sam", name="sam_x", bot=False, status="")

    async def dm_owner():
        return NS(id=9001)

    async def dm_sam():
        return NS(id=9002)
    owner.create_dm, sam.create_dm = dm_owner, dm_sam
    guild = NS(name="home", text_channels=[], voice_channels=[], members=[owner, sam], system_channel=None)
    hr.register_client(NS(guilds=[guild]))

    async def refs():
        return {"app_user_id": 1, "discord_user_id": OWNER_DISCORD, "telegram_user_id": 2222}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", refs)

    import core.agent.builtin_tools.her_hands as hh
    sent: list = []

    async def outbound(**kw):
        sent.append(kw)
        return True
    monkeypatch.setattr(hh, "_record_outbound", outbound)
    yield sent
    hr.register_client(None)


def _send(to: str) -> str:
    from core.agent.builtin_tools.her_hands import build_her_hands_tools
    from core.agent.tools import ToolContext

    tool = {t.name: t for t in build_her_hands_tools()}["send_message"]
    return asyncio.run(tool.execute({"to": to, "text": "buy milk"},
                                    ToolContext(turn_owner_id=1, is_owner=True, surface="telegram_text",
                                                channel_id=-100123)))


@pytest.mark.parametrize("to", ["@robin_example", "robin_example", "Robin", "@Robin", str(OWNER_DISCORD), f"<@{OWNER_DISCORD}>"])
def test_the_owners_own_account_by_any_name_is_the_owner(world, to):
    out = _send(to)
    assert world[-1]["channel_id"] == 9001
    assert world[-1]["extra"] == {}                       # not a third party
    assert out.startswith("Sent to the owner on Discord (DM)")


def test_someone_else_is_still_a_third_party(world):
    out = _send("@sam_x")
    assert world[-1]["channel_id"] == 9002
    assert world[-1]["extra"] == {"third_party": True, "recipient": "Sam"}
    assert out.startswith("Sent to Sam (DM)")


# ── 2. the fallback mention check and UTF-16 ─────────────────────────────────

def test_an_emoji_before_her_handle_is_still_her_mention():
    from bots.telegram.intake import _mentions_bot

    text = "\U0001f389\U0001f389 @palli_test_bot congrats!"
    offset = len(text[:text.index("@")].encode("utf-16-le")) // 2
    message = NS(text=text, caption=None, caption_entities=[],
                 entities=[NS(type="mention", offset=offset, length=len("@palli_test_bot"))])
    assert _mentions_bot(message, "palli_test_bot") is True


def test_the_fallback_still_tells_her_from_someone_else():
    from bots.telegram.intake import _mentions_bot

    text = "\U0001f389 @sam_x look"
    offset = len(text[:text.index("@")].encode("utf-16-le")) // 2
    message = NS(text=text, caption=None, caption_entities=[],
                 entities=[NS(type="mention", offset=offset, length=6)])
    assert _mentions_bot(message, "palli_test_bot") is False
