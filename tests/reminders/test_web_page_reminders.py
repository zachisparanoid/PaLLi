"""Web-page reminders (the owner, 2026-10-10: "web page reminders first"; the
design: palli/7.0.0a/web-page-reminders.md on the NAS).

The owner's rule of 2026-09-28 was "Last chat, else refuse": a reminder made
off a chat went to the Discord or Telegram chat used last, and with no chat at
all the tool refused, because the web page could not receive one. On an
install that only uses the web page (a stranger's first day, the demo) that
meant no reminders at all. The rule is now "last chat, else the web page"
(the owner, 2026-10-10: with a chat app, the phone still gets the
notification; only with none does the web page take it).

The web page has no live connection: its delivery IS the conversation row
log_reminder_delivery writes (platform "dashboard", channel 0, metadata type
"deferred_action"), which the page reads when it opens and every 15 seconds
(GET /api/chat/history). The in-memory dashboard buffer lost these (a
5-minute life, under a key the page never drained), so a web reminder never
touches it. Her record of having said it goes into the web chat's own
transcript, written only between runs, never in the middle of one.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import core.agent.builtin_tools.create_reminder as cr
import core.reminders.binding as binding
import core.reminders.scheduler as scheduler
from core.agent.builtin_tools.create_reminder import CreateReminderTool, _resolve_door
from core.agent.tools import ToolContext
from core.doors import CHAT_DOORS, WEB_DOOR
from core.reminders.binding import ReminderDeliveryRequest, execute_reminder_delivery


def _door(value):
    async def _last_used_door(owner_id):
        return value
    return _last_used_door


def _request(**overrides) -> ReminderDeliveryRequest:
    base = dict(
        reminder_id=71, user_id=1, platform=WEB_DOOR, channel_id=0, delivery_surface_id="0",
        reminder_text="call the dentist", reminder_message="Time to call the dentist.",
        scheduled_time=1783739146.0, kind="reminder", metadata={}, delivery_preference="channel",
        runtime_job_id="", account_user_id=None,
    )
    base.update(overrides)
    return ReminderDeliveryRequest(**base)


@pytest.fixture
def no_spine(monkeypatch):
    monkeypatch.setattr(scheduler, "fire_reminder", AsyncMock(return_value=None))


@pytest.fixture
def record(monkeypatch, tmp_path):
    import core.agent.runner as runner
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    spies = {"store_conversation": AsyncMock(return_value=None), "transcript": AsyncMock(return_value=None)}
    monkeypatch.setattr(binding.brain, "store_conversation", spies["store_conversation"])
    monkeypatch.setattr(runner, "_open_session_with_exchange", spies["transcript"])
    return spies


async def _transcripts_done():
    pending = list(binding._PENDING_TRANSCRIPTS)
    if pending:
        await asyncio.gather(*pending)


# ── where a reminder goes ──────────────────────────────────────────────────────

def test_the_web_page_is_its_own_door_and_not_a_chat_door():
    assert WEB_DOOR == "dashboard"
    assert WEB_DOOR not in CHAT_DOORS


@pytest.mark.asyncio
async def test_with_a_chat_app_a_web_page_reminder_still_goes_to_the_last_chat(monkeypatch):
    monkeypatch.setattr(cr, "last_used_door", _door(("telegram", 5222)))
    assert await _resolve_door(surface="dashboard", channel_id=0, owner_id=1) == ("telegram", 5222)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["dashboard", "reminder_fire", "proactive", "voice_local", ""])
async def test_with_no_chat_app_at_all_the_web_page_takes_it(monkeypatch, surface):
    monkeypatch.setattr(cr, "last_used_door", _door(None))
    assert await _resolve_door(surface=surface, channel_id=0, owner_id=1) == (WEB_DOOR, 0)


@pytest.mark.asyncio
async def test_a_reminder_made_in_a_chat_still_stays_in_that_chat(monkeypatch):
    monkeypatch.setattr(cr, "last_used_door", _door(None))
    assert await _resolve_door(surface="discord_text", channel_id=1483, owner_id=1) == ("discord", 1483)


@pytest.mark.asyncio
async def test_the_tool_files_a_web_page_reminder_instead_of_refusing(monkeypatch):
    from core.reminders import scheduler as scheduler_module

    calls: list[dict] = []

    async def parse_and_schedule_reminder(**kwargs):
        calls.append(kwargs)

        class _Result:
            id = 12
            scheduled_for = 1800000000.0
            message = kwargs["message"]
        return _Result()

    monkeypatch.setattr(scheduler_module.reminder_scheduler, "parse_and_schedule_reminder",
                        parse_and_schedule_reminder)
    monkeypatch.setattr(cr, "last_used_door", _door(None))
    raw = await CreateReminderTool().execute(
        {"message": "call the dentist", "when": "in 2 hours"},
        ToolContext(turn_owner_id=1, is_owner=True, surface="dashboard", channel_id=0),
    )
    assert isinstance(raw, str) and "scheduled" in raw
    assert len(calls) == 1
    assert (calls[0]["platform"], calls[0]["channel_id"], calls[0]["surface_id"]) == (WEB_DOOR, 0, "0")


def test_the_promise_check_can_offer_a_reminder_in_the_web_page(monkeypatch):
    class WebTurn:
        surface, channel_id, turn_owner_id = "dashboard", 0, 1

    monkeypatch.setattr(cr, "last_used_door", _door(None))
    assert asyncio.run(cr.can_deliver_from(WebTurn())) is True


# ── delivery ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_web_page_reminder_is_delivered_by_its_record_never_the_memory_buffer(no_spine, record, monkeypatch):
    from core.channel_runtime import channel_runtime

    send = AsyncMock(side_effect=AssertionError("a web reminder must never use the in-memory buffer"))
    monkeypatch.setattr(channel_runtime, "send_envelope", send)
    result = await execute_reminder_delivery(_request(), bot=None)
    await _transcripts_done()

    assert result.sent is True and result.delivered_text == "Time to call the dentist."
    send.assert_not_called()
    kwargs = record["store_conversation"].await_args.kwargs
    assert (kwargs["platform"], kwargs["channel_id"], kwargs["role"]) == (WEB_DOOR, 0, "assistant")
    assert kwargs["metadata"]["type"] == "deferred_action"


@pytest.mark.asyncio
async def test_a_blank_web_page_reminder_is_not_a_delivery(no_spine, record):
    result = await execute_reminder_delivery(_request(reminder_message="   "), bot=None)
    await _transcripts_done()
    assert result.sent is False
    record["store_conversation"].assert_not_called()


@pytest.mark.asyncio
async def test_her_record_of_it_goes_to_the_web_chat_transcript(no_spine, record):
    await execute_reminder_delivery(_request(), bot=None)
    await _transcripts_done()
    args = record["transcript"].await_args.args
    assert args[0] == "dashboard:1" and args[3] == "Time to call the dentist."


@pytest.mark.asyncio
async def test_the_transcript_waits_for_a_run_in_progress_and_writes_after_it(monkeypatch, tmp_path):
    import core.agent.runner as runner
    from core.agent import steering

    written: list[str] = []

    async def fake_exchange(key, root, user_text, assistant_text, **kw):
        written.append(key)

    monkeypatch.setattr(runner, "_open_session_with_exchange", fake_exchange)
    steering.set_run_phase("dashboard:1", "generating")
    try:
        task = asyncio.create_task(binding._transcribe_when_idle(
            "dashboard:1", tmp_path, "Time to call the dentist.", poll_seconds=0.01, max_wait_seconds=5.0))
        await asyncio.sleep(0.05)
        assert written == []                       # never in the middle of a run
        steering.clear_run_state("dashboard:1")
        await asyncio.wait_for(task, 1.0)
        assert written == ["dashboard:1"]          # right after it
    finally:
        steering.clear_run_state("dashboard:1")


@pytest.mark.asyncio
async def test_a_run_that_never_ends_never_gets_a_row_written_into_it(monkeypatch, tmp_path):
    import core.agent.runner as runner
    from core.agent import steering

    written: list[str] = []

    async def fake_exchange(key, root, user_text, assistant_text, **kw):
        written.append(key)

    monkeypatch.setattr(runner, "_open_session_with_exchange", fake_exchange)
    steering.set_run_phase("dashboard:1", "generating")
    try:
        await binding._transcribe_when_idle(
            "dashboard:1", tmp_path, "Time to call the dentist.", poll_seconds=0.01, max_wait_seconds=0.05)
        assert written == []
    finally:
        steering.clear_run_state("dashboard:1")


@pytest.mark.asyncio
async def test_the_fire_turn_reads_the_web_chat_it_will_post_to(monkeypatch):
    from core.database import brain

    asked: list[dict] = []

    async def history(**kw):
        asked.append(kw)
        return [{"role": "user", "content": "remind me at 9 to call the dentist", "timestamp": 1.0}]

    monkeypatch.setattr(brain, "get_conversation_history", history)
    lines = await scheduler._recent_conversation_lines(1, platform=WEB_DOOR, channel_id=0)
    assert asked and asked[0]["platform"] == WEB_DOOR and asked[0]["channel_id"] == 0
    assert "call the dentist" in lines


@pytest.mark.asyncio
async def test_discord_and_telegram_delivery_is_unchanged(no_spine, record, monkeypatch):
    from core.channel_runtime import channel_runtime

    async def ready(platform):
        return True

    send = AsyncMock(return_value={"sent": True})
    monkeypatch.setattr(channel_runtime, "door_ready", ready)
    monkeypatch.setattr(channel_runtime, "send_envelope", send)
    result = await execute_reminder_delivery(
        _request(platform="telegram", channel_id=5222, delivery_surface_id="5222"), bot=None)
    await _transcripts_done()
    assert result.sent is True
    send.assert_awaited_once()
    assert record["transcript"].await_args.args[0] == "telegram:5222"


# ── Fable's review (2026-10-10) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_web_page_reminder_whose_record_failed_is_not_delivered(no_spine, record):
    """The row IS the web page's delivery: if it could not be written, the
    reminder never reached the page, so it is not sent (the retry and
    dead-letter rules apply), and nothing goes into her transcript."""
    record["store_conversation"].side_effect = RuntimeError("database is locked")
    result = await execute_reminder_delivery(_request(), bot=None)
    await _transcripts_done()
    assert result.sent is False and result.delivered_text == ""
    record["transcript"].assert_not_called()


@pytest.mark.asyncio
async def test_a_chat_reminder_whose_record_failed_is_still_delivered(no_spine, record, monkeypatch):
    """On a chat door the message already reached the chat; the row is only
    bookkeeping, as before."""
    from core.channel_runtime import channel_runtime

    async def ready(platform):
        return True

    monkeypatch.setattr(channel_runtime, "door_ready", ready)
    monkeypatch.setattr(channel_runtime, "send_envelope", AsyncMock(return_value={"sent": True}))
    record["store_conversation"].side_effect = RuntimeError("database is locked")
    result = await execute_reminder_delivery(
        _request(platform="telegram", channel_id=5222, delivery_surface_id="5222"), bot=None)
    await _transcripts_done()
    assert result.sent is True


class _Scheduled:
    def __init__(self):
        self.calls: list[dict] = []

    async def parse_and_schedule_reminder(self, **kwargs):
        self.calls.append(kwargs)

        class _Result:
            id = 12
            scheduled_for = 1800000000.0
            message = kwargs["message"]
        return _Result()


@pytest.mark.asyncio
@pytest.mark.parametrize("door, says_web_page", [(None, True), (("discord", 1483), False)])
async def test_the_tool_says_when_a_reminder_goes_to_the_web_page(monkeypatch, door, says_web_page):
    """With no chat on record she says where it will appear, so a reminder
    never goes quietly to a page nobody opens (a voice-only owner, say)."""
    from core.reminders import scheduler as scheduler_module

    fake = _Scheduled()
    monkeypatch.setattr(scheduler_module.reminder_scheduler, "parse_and_schedule_reminder",
                        fake.parse_and_schedule_reminder)
    monkeypatch.setattr(cr, "last_used_door", _door(door))
    raw = await CreateReminderTool().execute(
        {"message": "call the dentist", "when": "in 2 hours"},
        ToolContext(turn_owner_id=1, is_owner=True, surface="voice_local", channel_id=0),
    )
    assert ("web page" in raw) is says_web_page
