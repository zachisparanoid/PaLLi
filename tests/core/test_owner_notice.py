"""Plain notices to the owner — words that are not hers (2026-10-04).

The spending check has to tell the owner something without going through her:
no turn, no model, no "you reached out to them on your own" in her Present
Moment. core may not import bots, so each door registers a sender when it
starts and core.owner_notice asks them in order — Discord first, then
Telegram. Nothing here touches a real door.
"""
from __future__ import annotations

import asyncio

import pytest

from core import owner_notice


@pytest.fixture(autouse=True)
def _no_doors(monkeypatch):
    monkeypatch.setattr(owner_notice, "_senders", {})


class Door:
    def __init__(self, result=True, *, raises: bool = False, hangs: bool = False):
        self.result = result
        self.raises = raises
        self.hangs = hangs
        self.sent: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.sent.append(text)
        if self.hangs:
            await asyncio.sleep(3600)
        if self.raises:
            raise RuntimeError("the door fell over")
        return self.result


@pytest.mark.asyncio
async def test_discord_is_asked_first():
    discord, telegram = Door(), Door()
    owner_notice.register_owner_notice_sender("telegram", telegram)
    owner_notice.register_owner_notice_sender("discord", discord)

    assert await owner_notice.send_owner_notice("hello") == "discord"
    assert discord.sent == ["hello"] and telegram.sent == []


@pytest.mark.asyncio
async def test_telegram_when_discord_cannot():
    discord, telegram = Door(False), Door()
    owner_notice.register_owner_notice_sender("discord", discord)
    owner_notice.register_owner_notice_sender("telegram", telegram)

    assert await owner_notice.send_owner_notice("hello") == "telegram"
    assert telegram.sent == ["hello"]


@pytest.mark.asyncio
async def test_a_door_that_raises_is_passed_over():
    owner_notice.register_owner_notice_sender("discord", Door(raises=True))
    telegram = Door()
    owner_notice.register_owner_notice_sender("telegram", telegram)

    assert await owner_notice.send_owner_notice("hello") == "telegram"


@pytest.mark.asyncio
async def test_a_door_that_hangs_is_given_up_on():
    owner_notice.register_owner_notice_sender("discord", Door(hangs=True))
    telegram = Door()
    owner_notice.register_owner_notice_sender("telegram", telegram)

    assert await owner_notice.send_owner_notice("hello", timeout_s=0.05) == "telegram"


@pytest.mark.asyncio
async def test_nobody_to_tell_is_an_empty_answer_not_an_error():
    assert await owner_notice.send_owner_notice("hello") == ""
    owner_notice.register_owner_notice_sender("discord", Door(False))
    assert await owner_notice.send_owner_notice("hello") == ""


@pytest.mark.asyncio
async def test_blank_text_is_not_sent():
    discord = Door()
    owner_notice.register_owner_notice_sender("discord", discord)
    assert await owner_notice.send_owner_notice("   ") == ""
    assert discord.sent == []


def test_unregistering_an_old_door_leaves_its_replacement_alone():
    """A restarted door registers a new sender; the old one's shutdown must
    not take the new one down with it."""
    old, new = Door(), Door()
    owner_notice.register_owner_notice_sender("discord", old)
    owner_notice.register_owner_notice_sender("discord", new)

    owner_notice.unregister_owner_notice_sender("discord", old)
    assert owner_notice.registered_doors() == ("discord",)

    owner_notice.unregister_owner_notice_sender("discord", new)
    assert owner_notice.registered_doors() == ()
