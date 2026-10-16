"""The moment check reads the run in flight under the chat's own door
(2026-10-14, a note from the presence slice): it built ``discord:<id>`` for
every chat, so a reply PaLLi was writing in the owner's Telegram chat never
held a reach-out to that same chat."""
from __future__ import annotations

import asyncio

import pytest

from ai import arbiter


@pytest.fixture
def gates(monkeypatch):
    seen: list[str] = []

    def _phase(key: str) -> str:
        seen.append(key)
        return "generating" if key == "telegram:4242" else ""

    async def _last_msg(**_kw):
        return None

    monkeypatch.setattr(arbiter, "_run_phase", _phase)
    monkeypatch.setattr(arbiter, "_is_working", lambda key: False)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_companion", lambda: None)
    return seen


def test_a_telegram_reply_in_flight_holds_the_reach_out(gates):
    held, _note = asyncio.run(arbiter._moment_gates(
        owner_user_id=1, platform="telegram", channel_id=4242))
    assert gates[0] == "telegram:4242"
    assert held is not None and held["disposition"] == "held_conversation"


def test_discord_stays_the_default_door(gates):
    held, _note = asyncio.run(arbiter._moment_gates(owner_user_id=1, channel_id=4242))
    assert gates[0] == "discord:4242"
    assert held is None
