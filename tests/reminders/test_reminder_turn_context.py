"""A reminder turn sees the chat it will be posted to (Batch 4 task 1, 1.7).

DEFECT_RECORD §16 gave the fire turn the recent conversation — but always the
owner's Discord lines, from every channel at once. A reminder set in Telegram fired
reading Discord, and a Discord reminder set in one channel read another's.
The reminder's record already names its door (platform + channel); the fire
path now carries it to ``_recent_conversation_lines``, which reads THAT chat.
A reminder object with no door keeps the §16 query.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.reminders.binding as binding
import core.reminders.scheduler as scheduler
from core.reminders.binding import ReminderDeliveryRequest, deliver_reminder

# The owner's conversation record across three chats (the store's row shape).
_ROWS = [
    {"platform": "discord", "channel_id": 111, "role": "user", "content": "discord general: the budget is done", "timestamp": 1.0},
    {"platform": "discord", "channel_id": 222, "role": "user", "content": "discord other room: unrelated", "timestamp": 2.0},
    {"platform": "telegram", "channel_id": -100555, "role": "user", "content": "telegram chat: already stretched, thanks", "timestamp": 3.0},
    {"platform": "telegram", "channel_id": -100555, "role": "assistant", "content": "nice, go you", "timestamp": 4.0},
]


@pytest.fixture
def history(monkeypatch):
    """A fake ``brain.get_conversation_history`` that filters the rows above
    the way the store does, and records every call's kwargs."""
    from core.database import brain
    calls: list[dict] = []

    async def fake_history(**kw):
        calls.append(kw)
        rows = [r for r in _ROWS
                if (not kw.get("platform") or r["platform"] == kw["platform"])
                and (kw.get("channel_id") is None or r["channel_id"] == kw["channel_id"])]
        return rows[-int(kw.get("limit") or 50):]

    monkeypatch.setattr(brain, "get_conversation_history", fake_history)
    return calls


@pytest.fixture
def fired(monkeypatch):
    """Captures the fire turn instead of dispatching it."""
    seen: dict = {}

    async def fake_dispatch(turn):
        seen["turn"] = turn
        return None
    import core.agent.dispatch as dispatch_mod
    monkeypatch.setattr(dispatch_mod, "dispatch_turn", fake_dispatch)
    return seen


def _fire(reminder) -> None:
    asyncio.run(scheduler.fire_reminder(reminder, owner_id=1))


def test_a_telegram_reminder_reads_that_telegram_chat(history, fired):
    _fire(SimpleNamespace(id=7, what="stretch", platform="telegram", channel_id=-100555))
    assert history == [{"user_id": 1, "platform": "telegram", "channel_id": -100555, "limit": 30}]
    content = fired["turn"].content
    assert "they: telegram chat: already stretched, thanks" in content
    assert "you: nice, go you" in content
    assert "discord" not in content
    assert "beat you to it" in content                     # the §16 wording stays


def test_a_discord_reminder_reads_only_its_own_channel(history, fired):
    _fire(SimpleNamespace(id=8, what="budget", platform="discord", channel_id=111))
    assert history == [{"user_id": 1, "platform": "discord", "channel_id": 111, "limit": 30}]
    content = fired["turn"].content
    assert "they: discord general: the budget is done" in content
    assert "other room" not in content


@pytest.mark.parametrize("reminder", [
    type("R", (), {"id": 9, "what": "call the shop"})(),                       # no door at all
    SimpleNamespace(id=9, what="call the shop", platform="discord", channel_id=0),
    SimpleNamespace(id=9, what="call the shop", platform="telegram", channel_id=None),
    SimpleNamespace(id=9, what="call the shop", platform="local", channel_id=0),
])
def test_a_reminder_without_a_chat_door_keeps_the_section16_query(history, fired, reminder):
    _fire(reminder)
    assert history == [{"user_id": 1, "platform": "discord", "limit": 30}]
    assert "they: discord general: the budget is done" in fired["turn"].content


@pytest.mark.parametrize("channel_id", [0, 5])
def test_a_web_page_reminder_reads_the_web_chat(history, fired, channel_id):
    """2026-10-10 (web-page reminders): a reminder on the web page reads the
    web chat it will appear in, which is always channel 0."""
    _fire(SimpleNamespace(id=9, what="call the shop", platform="dashboard", channel_id=channel_id))
    assert history == [{"user_id": 1, "platform": "dashboard", "channel_id": 0, "limit": 30}]


def test_the_chat_never_becomes_the_fire_turns_own_channel(history, fired):
    """The door feeds the recent-lines block only. On the turn's metadata it
    would tell the "Where I Am" block the fire is IN that chat, and hand the
    chat to create_reminder's ToolContext."""
    _fire(SimpleNamespace(id=7, what="stretch", platform="telegram", channel_id=-100555))
    turn = fired["turn"]
    assert turn.surface == "reminder_fire"
    assert turn.metadata.channel_id is None


def test_the_delivery_path_carries_the_records_door_to_the_fire_turn(
        history, fired, monkeypatch):
    """End to end from the record's delivery request: deliver_reminder hands
    fire_reminder the platform and chat, and the fire turn reads that chat."""
    async def fake_send(**kw):
        return None

    async def door_up(platform):
        return True
    monkeypatch.setattr(binding.channel_runtime, "send_envelope", fake_send)
    # The door is up (final review, Important 3: a chat door that is not is
    # asked before the fire turn and never reaches the send).
    monkeypatch.setattr(binding.channel_runtime, "door_ready", door_up)
    request = ReminderDeliveryRequest(
        reminder_id=7, user_id=1, platform="telegram", channel_id=-100555,
        delivery_surface_id="-100555", reminder_text="stretch",
        reminder_message="Reminder: stretch", scheduled_time=1783739146.0,
        kind="reminder", metadata={}, delivery_preference="channel",
        runtime_job_id="", account_user_id=None,
    )
    result = asyncio.run(deliver_reminder(request, bot=None))
    assert result.sent is True
    assert history == [{"user_id": 1, "platform": "telegram", "channel_id": -100555, "limit": 30}]
    assert "telegram chat: already stretched" in fired["turn"].content


# ── final review, Important 1: the not-delivered note is the system's ─────────
def test_a_not_delivered_note_reads_as_a_system_note_never_as_hers(monkeypatch, fired):
    """A door that could not send her reply writes a ``system`` row after it
    (core/turn/persistence.py persist_delivery_failure). Labelled "you:", the
    fire turn read the note as her own words."""
    from core.database import brain

    rows = [
        {"platform": "discord", "channel_id": 111, "role": "user", "content": "what's the plan?", "timestamp": 1.0},
        {"platform": "discord", "channel_id": 111, "role": "assistant", "content": "the plan is X", "timestamp": 2.0},
        {"platform": "discord", "channel_id": 111, "role": "system",
         "content": "[not delivered to them: HTTPException 50013]", "timestamp": 3.0},
        {"platform": "discord", "channel_id": 111, "role": "system",
         "content": "[only partly delivered to them: HTTPException 50013 after 1 of 2 parts]", "timestamp": 4.0},
    ]

    async def fake_history(**kw):
        return rows

    monkeypatch.setattr(brain, "get_conversation_history", fake_history)
    _fire(SimpleNamespace(id=8, what="budget", platform="discord", channel_id=111))
    content = fired["turn"].content
    assert "- system: [not delivered to them: HTTPException 50013]" in content
    assert "- system: [only partly delivered to them: HTTPException 50013 after 1 of 2 parts]" in content
    assert "you: [not delivered" not in content
    assert "you: [only partly delivered" not in content
    assert "- you: the plan is X" in content          # her own words keep their label
    assert "- they: what's the plan?" in content
