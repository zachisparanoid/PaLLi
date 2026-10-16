"""Every new reminder is filed to a real chat, or to the web page (fix plan 5.8).

The owner's rule (2026-09-28): "Last chat, else refuse." A reminder made in
a Discord or Telegram chat stays in that chat. A reminder made anywhere else
(the dashboard, a Discord voice call, the local mic, a reminder turn, a
proactive turn, an unknown or blank surface) goes to the Discord or Telegram
chat he used most recently. Since 2026-10-10 (web-page reminders, the owner's
answer: "the chat app" when there is one) the "else" is the web page: with no
chat at all the reminder is filed to it (``dashboard``, channel 0) and
delivered as a conversation row the page reads. See
tests/reminders/test_web_page_reminders.py.

Before this a reminder made off a chat was filed as "dashboard" or "voice"
with channel 0: no chat to post to, so it failed three times and was
retired as if it had gone out.

The door lookup is the arbiter's own "door he used last"
(``core/turn/proactive.py::_pick_proactive_surface``), wrapped in
``core/doors.py::last_used_door``. The fakes here are ``async def``,
because the real lookup is async.
"""
from __future__ import annotations

import pytest

import core.agent.builtin_tools.create_reminder as cr
from core.agent.builtin_tools.create_reminder import CreateReminderTool, _resolve_door
from core.agent.tools import ToolContext

def _door(value):
    async def _last_used_door(owner_id):
        return value
    return _last_used_door


# --- _resolve_door (the plan's three, with async fakes) ---------------------


@pytest.mark.asyncio
async def test_a_reminder_from_a_chat_keeps_its_chat():
    assert await _resolve_door(surface="telegram_text", channel_id=5222, owner_id=1) == ("telegram", 5222)


@pytest.mark.asyncio
async def test_a_reminder_from_a_reminder_turn_takes_the_last_used_door(monkeypatch):
    monkeypatch.setattr(cr, "last_used_door", _door(("discord", 1483)))
    assert await _resolve_door(surface="reminder_fire", channel_id=0, owner_id=1) == ("discord", 1483)


@pytest.mark.asyncio
async def test_no_chat_at_all_means_the_web_page(monkeypatch):
    monkeypatch.setattr(cr, "last_used_door", _door(None))
    assert await _resolve_door(surface="reminder_fire", channel_id=0, owner_id=1) == ("dashboard", 0)


# --- the rest of the owner's rule --------------------------------------------


@pytest.mark.asyncio
async def test_a_discord_chat_keeps_its_chat_and_never_asks_the_lookup(monkeypatch):
    async def _must_not_run(owner_id):
        raise AssertionError("a reminder made in a chat stays in that chat")

    monkeypatch.setattr(cr, "last_used_door", _must_not_run)
    assert await _resolve_door(surface="discord_text", channel_id=1483, owner_id=1) == ("discord", 1483)


@pytest.mark.parametrize("surface", [
    "dashboard", "discord_voice", "voice_local", "reminder_fire", "proactive", "", "some_new_surface",
])
@pytest.mark.asyncio
async def test_every_surface_that_is_not_a_chat_takes_the_last_used_door(monkeypatch, surface):
    monkeypatch.setattr(cr, "last_used_door", _door(("telegram", 5222)))
    assert await _resolve_door(surface=surface, channel_id=0, owner_id=1) == ("telegram", 5222)


@pytest.mark.asyncio
async def test_a_chat_surface_without_its_chat_id_takes_the_last_used_door(monkeypatch):
    """A chat surface that did not carry its channel is not a chat to post to."""
    monkeypatch.setattr(cr, "last_used_door", _door(("discord", 1483)))
    assert await _resolve_door(surface="discord_text", channel_id=0, owner_id=1) == ("discord", 1483)


@pytest.mark.asyncio
async def test_a_voice_call_with_a_channel_id_is_still_not_a_chat(monkeypatch):
    """A Discord voice channel is where he talks, not a chat she can post in."""
    monkeypatch.setattr(cr, "last_used_door", _door(("discord", 1483)))
    assert await _resolve_door(surface="discord_voice", channel_id=777, owner_id=1) == ("discord", 1483)


@pytest.mark.asyncio
async def test_the_lookup_is_asked_for_the_turns_owner(monkeypatch):
    asked: list[int] = []

    async def _last_used_door(owner_id):
        asked.append(owner_id)
        return ("discord", 1483)

    monkeypatch.setattr(cr, "last_used_door", _last_used_door)
    await _resolve_door(surface="dashboard", channel_id=0, owner_id=42)
    assert asked == [42]


# --- the tool ------------------------------------------------------------------


class _Scheduler:
    def __init__(self):
        self.calls: list[dict] = []

    async def parse_and_schedule_reminder(self, **kwargs):
        self.calls.append(kwargs)

        class _Result:
            id = 12
            scheduled_for = 1800000000.0
            message = kwargs["message"]
        return _Result()


@pytest.fixture
def scheduler(monkeypatch):
    from core.reminders import scheduler as scheduler_module

    fake = _Scheduler()
    monkeypatch.setattr(
        scheduler_module.reminder_scheduler,
        "parse_and_schedule_reminder",
        fake.parse_and_schedule_reminder,
    )
    return fake


def _ctx(surface: str, channel_id: int = 0) -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id)


@pytest.mark.asyncio
async def test_no_chat_at_all_files_to_the_web_page(monkeypatch, scheduler):
    monkeypatch.setattr(cr, "last_used_door", _door(None))
    raw = await CreateReminderTool().execute(
        {"message": "water the plants", "when": "in 2 hours"}, _ctx("dashboard"),
    )
    assert isinstance(raw, str) and "scheduled" in raw
    call = scheduler.calls[0]
    assert (call["platform"], call["channel_id"], call["surface_id"]) == ("dashboard", 0, "0")


@pytest.mark.asyncio
async def test_a_dashboard_reminder_is_filed_to_the_last_used_chat(monkeypatch, scheduler):
    monkeypatch.setattr(cr, "last_used_door", _door(("discord", 1483)))
    raw = await CreateReminderTool().execute(
        {"message": "water the plants", "when": "in 2 hours"}, _ctx("dashboard"),
    )
    assert isinstance(raw, str) and "scheduled" in raw
    assert len(scheduler.calls) == 1
    call = scheduler.calls[0]
    assert call["platform"] == "discord"
    assert call["channel_id"] == 1483
    assert call["surface_id"] == "1483"
    assert call["user_id"] == 1


@pytest.mark.asyncio
async def test_a_telegram_reminder_is_filed_to_its_own_chat(monkeypatch, scheduler):
    monkeypatch.setattr(cr, "last_used_door", _door(("discord", 1483)))
    await CreateReminderTool().execute(
        {"message": "stretch", "when": "in 1 hour"}, _ctx("telegram_text", -100555),
    )
    call = scheduler.calls[0]
    assert (call["platform"], call["channel_id"], call["surface_id"]) == ("telegram", -100555, "-100555")


# --- core/doors.py -------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_used_door_wraps_the_arbiters_lookup_and_gives_an_int(monkeypatch):
    import core.doors as doors
    import core.turn.proactive as proactive

    seen: dict = {}

    async def _pick(*, owner_id, platforms=("discord", "telegram")):
        seen.update(owner_id=owner_id, platforms=platforms)
        return ("telegram", "424242")

    monkeypatch.setattr(proactive, "_pick_proactive_surface", _pick)
    assert await doors.last_used_door(7) == ("telegram", 424242)
    assert seen == {"owner_id": 7, "platforms": ("discord", "telegram", "mobile")}


@pytest.mark.parametrize("picked", [None, ("discord", "not-a-number"), ("discord", ""), ("discord", "0")])
@pytest.mark.asyncio
async def test_last_used_door_is_none_when_there_is_no_usable_chat(monkeypatch, picked):
    import core.doors as doors
    import core.turn.proactive as proactive

    async def _pick(*, owner_id, platforms=("discord", "telegram")):
        return picked

    monkeypatch.setattr(proactive, "_pick_proactive_surface", _pick)
    assert await doors.last_used_door(1) is None



# --- she can still see what she filed --------------------------------------


@pytest.mark.asyncio
async def test_a_reminder_filed_to_telegram_is_one_she_can_list(monkeypatch):
    """A reminder made on the dashboard now lands under the chat he used last,
    which can be Telegram; list_reminders has to read that door too, or she
    files a reminder she can no longer see."""
    from core.agent.builtin_tools.list_reminders import ListRemindersTool
    from core.database import brain

    async def _list(*, user_id, platform):
        if platform == "telegram":
            return [{"id": 31, "reminder_text": "stretch",
                     "scheduled_time": 1800000000.0, "is_completed": False}]
        return []

    monkeypatch.setattr(brain, "list_user_scheduled_reminders", _list)
    out = await ListRemindersTool().execute({}, _ctx("dashboard"))
    assert "stretch" in str(out)


# --- the real lookup, on a real database (fix round 1, R5a) ------------------
#
# "The chat he used last" was the newest conversations row of ANY role under
# his id, so two things that are not him talking in a text chat could win it:
#   * her own send to a friend (her-hands send_message → record_outbound) is
#     filed as an assistant row under HIS id with the FRIEND's channel — the
#     next dashboard reminder went to the friend's DM;
#   * his voice turn is filed as platform "discord" with the voice channel —
#     a voice-made reminder went to the voice channel.
# These rows are written by the real writers into a real temporary database,
# and read back through the real last_used_door and the arbiter's _pick_surface.
#
# Since 2026-10-13 (the owner's rule) it is "the PRIVATE chat he used last": a
# Discord DM or a Telegram private chat, never a server channel or a Telegram
# group (on 10-12 a note meant for him went to a server channel his friends
# could see). Each row he writes in a chat says whether that chat is private.

HIS_DM = 5001
FRIEND_DM = 6002
VOICE_CHANNEL = 7003
HIS_TELEGRAM = 424242            # a private chat: Telegram numbers it by his user id
SERVER_CHANNEL = 9004
TELEGRAM_GROUP = -100555         # groups and supergroups are numbered below zero


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    import core.database as database
    from core.config import config
    from core.database import PalliBrain

    path = tmp_path / "doors.sqlite3"
    brain = PalliBrain(db_path=str(path))
    monkeypatch.setattr(database, "brain", brain)
    monkeypatch.setattr(config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(config, "PROACTIVE_ALLOWED_CHANNEL_IDS", [], raising=False)
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr("ai.recent_deeds.record_deed", lambda *a, **k: None)
    # No door is up to be asked about an unmarked row: every row here is
    # written through the real (marking) writer, or is not a chat at all.
    from core.channel_runtime import channel_runtime
    monkeypatch.setattr(channel_runtime, "_adapters", {})
    return brain


async def _tick():
    # Rows are ordered by their write time; keep each write strictly later.
    import asyncio
    await asyncio.sleep(0.005)


async def _he_says(surface: str, channel_id: int, text: str = "hey", *,
                   is_dm: bool = True) -> None:
    """His words, through the writer dispatch uses for every turn. ``is_dm``
    is what the door's intake says of the chat (a DM / a private chat, or a
    server channel / a group)."""
    from core.turn.persistence import _persist_user_side_with_content
    from core.turn.types import Turn, TurnMetadata

    turn = Turn(
        surface=surface, owner_id=1, content=text,
        metadata=TurnMetadata(origin="user", channel_id=channel_id, is_dm=is_dm,
                              is_voice=surface in ("discord_voice", "voice_local")),
    )
    await _persist_user_side_with_content(turn, text)
    await _tick()


async def _she_sends_to_a_friend(channel_id: int) -> None:
    """Her-hands send_message to someone else's chat: record_outbound under
    HIS id (ctx.turn_owner_id) with THEIR channel."""
    import core.channel_runtime as channel_runtime
    from core.turn.outbound import record_outbound

    class _Runtime:
        async def send_envelope(self, *, platform, surface_id, envelope):
            return {"sent": True}

    original = channel_runtime.channel_runtime
    channel_runtime.channel_runtime = _Runtime()
    try:
        await record_outbound(owner_id=1, channel_id=channel_id,
                              text="hi, it's PaLLi", origin="her_hands")
    finally:
        channel_runtime.channel_runtime = original
    await _tick()


@pytest.mark.asyncio
async def test_her_send_to_a_friend_never_becomes_his_last_chat(real_db):
    import core.doors as doors

    await _he_says("discord_text", HIS_DM)
    await _she_sends_to_a_friend(FRIEND_DM)

    assert await doors.last_used_door(1) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_his_voice_call_never_becomes_his_last_chat(real_db):
    import core.doors as doors

    await _he_says("discord_text", HIS_DM)
    await _he_says("discord_voice", VOICE_CHANNEL, "remind me to call the shop")

    assert await doors.last_used_door(1) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_a_voice_row_from_the_voice_runtime_never_becomes_his_last_chat(real_db):
    """The streaming voice runtime writes its own row (voice/runtime.py,
    _process_finalized_turn), flagged voice_session in its metadata."""
    import core.doors as doors

    await _he_says("discord_text", HIS_DM)
    await real_db.store_conversation(
        user_id=1, platform="discord", channel_id=VOICE_CHANNEL, thread_id=None,
        role="user", content="remind me to call the shop",
        metadata={"username": "him", "voice_message": True, "voice_session": True},
    )

    assert await doors.last_used_door(1) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_only_telegram_means_telegram(real_db):
    import core.doors as doors

    await _he_says("telegram_text", HIS_TELEGRAM)

    assert await doors.last_used_door(1) == ("telegram", HIS_TELEGRAM)


@pytest.mark.asyncio
async def test_nothing_he_said_in_a_chat_means_none(real_db):
    import core.doors as doors

    await _she_sends_to_a_friend(FRIEND_DM)          # her words only
    await _he_says("discord_voice", VOICE_CHANNEL)   # his, but spoken
    await _he_says("dashboard", 0)                   # his, but not a chat

    assert await doors.last_used_door(1) is None


@pytest.mark.asyncio
async def test_a_dashboard_reminder_goes_to_his_dm_not_the_friends(real_db, scheduler):
    """End to end through the tool: the privacy leak itself."""
    await _he_says("discord_text", HIS_DM)
    await _she_sends_to_a_friend(FRIEND_DM)

    await CreateReminderTool().execute(
        {"message": "water the plants", "when": "in 2 hours"}, _ctx("dashboard"),
    )

    call = scheduler.calls[0]
    assert (call["platform"], call["channel_id"]) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_the_arbiters_reach_outs_no_longer_follow_her_own_send(real_db):
    """The same lookup puts the arbiter's reach-outs on "the door he used
    last"; her send to a friend no longer wins it there either."""
    from ai import arbiter

    await _he_says("telegram_text", HIS_TELEGRAM)
    await _she_sends_to_a_friend(FRIEND_DM)

    assert await arbiter._pick_surface(owner_id=1) == ("telegram", str(HIS_TELEGRAM))


@pytest.mark.asyncio
async def test_each_chat_row_says_if_its_chat_is_private_and_a_voice_turn_is_marked(real_db):
    """The marks the lookup reads: a row from a Discord or Telegram chat says
    whether that chat is a private chat with him (10-13); a row persisted from
    a voice surface carries its surface; any other row is written as before
    (no metadata)."""
    import json

    await _he_says("discord_text", HIS_DM)
    await _he_says("discord_text", SERVER_CHANNEL, is_dm=False)
    await _he_says("telegram_text", HIS_TELEGRAM)
    await _he_says("telegram_text", TELEGRAM_GROUP, is_dm=False)
    await _he_says("discord_voice", VOICE_CHANNEL)
    await _he_says("dashboard", 0)

    conn = await real_db.get_connection()
    try:
        rows = await conn.fetch(
            "SELECT channel_id, metadata FROM conversations ORDER BY timestamp"
        )
    finally:
        await conn.close()

    def _read(stored):
        if stored is None:
            return None
        return json.loads(stored) if isinstance(stored, str) else stored

    metadata = {int(r["channel_id"]): _read(r["metadata"]) for r in rows}
    assert metadata[HIS_DM] == {"is_dm": True}
    assert metadata[SERVER_CHANNEL] == {"is_dm": False}
    assert metadata[HIS_TELEGRAM] == {"is_dm": True}
    assert metadata[TELEGRAM_GROUP] == {"is_dm": False}
    assert metadata[VOICE_CHANNEL] == {"surface": "discord_voice"}
    assert metadata[0] is None


# --- only a private chat with him (the owner, 10-13) -------------------------


@pytest.mark.asyncio
async def test_the_last_door_is_his_dm_not_the_newer_server_channel(real_db):
    import core.doors as doors

    await _he_says("discord_text", HIS_DM)
    await _he_says("discord_text", SERVER_CHANNEL, is_dm=False)

    assert await doors.last_used_door(1) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_the_last_door_is_his_telegram_chat_not_the_newer_group(real_db):
    import core.doors as doors

    await _he_says("telegram_text", HIS_TELEGRAM)
    await _he_says("discord_text", SERVER_CHANNEL, is_dm=False)
    await _he_says("telegram_text", TELEGRAM_GROUP, is_dm=False)

    assert await doors.last_used_door(1) == ("telegram", HIS_TELEGRAM)


@pytest.mark.asyncio
async def test_only_groups_means_no_door_and_a_dashboard_reminder_goes_to_the_web_page(
        real_db, scheduler):
    """No private chat: the lookup says so, and the reminders' rule ("last
    chat, else the web page") files it to the web page, which is his alone.
    Never to a group."""
    import core.doors as doors

    await _he_says("discord_text", SERVER_CHANNEL, is_dm=False)
    await _he_says("telegram_text", TELEGRAM_GROUP, is_dm=False)

    assert await doors.last_used_door(1) is None
    raw = await CreateReminderTool().execute(
        {"message": "water the plants", "when": "in 2 hours"}, _ctx("dashboard"),
    )
    call = scheduler.calls[0]
    assert (call["platform"], call["channel_id"]) == ("dashboard", 0)
    # What she is told is true with group chats on record: there is no
    # PRIVATE chat with him, not "no chat".
    assert "no private Discord, Telegram or phone-app chat with them on record" in raw


@pytest.mark.parametrize("surface,group", [
    ("discord_text", SERVER_CHANNEL), ("telegram_text", TELEGRAM_GROUP),
])
@pytest.mark.asyncio
async def test_a_reminder_made_in_a_group_stays_in_that_group(real_db, scheduler, surface, group):
    """Work started in a group goes back to that group: a reminder made there
    is filed to it, and fires there, whatever his private chat is."""
    await _he_says("discord_text", HIS_DM)
    await _he_says(surface, group, is_dm=False)

    await CreateReminderTool().execute(
        {"message": "order the pizza", "when": "in 1 hour"}, _ctx(surface, group),
    )
    call = scheduler.calls[0]
    assert (call["platform"], call["channel_id"], call["surface_id"]) == (
        surface.split("_")[0], group, str(group))


# --- final review, minor 6: one door list ------------------------------------


def test_every_reader_takes_the_doors_from_core_doors():
    """The chat doors were spelled out in four places (core/doors, the
    scheduler, this tool, the binding's transcript leg). core/doors.CHAT_DOORS
    is the one list now; a door added there reaches every reader."""
    import inspect

    import core.doors as doors
    import core.reminders.binding as binding
    import core.reminders.scheduler as scheduler

    assert scheduler._CHAT_DOORS is doors.CHAT_DOORS
    assert binding.CHAT_DOORS is doors.CHAT_DOORS
    assert cr._CHAT_SURFACES == {f"{door}_text": door for door in doors.CHAT_DOORS}
    assert "CHAT_DOORS" in inspect.getsource(cr)
    transcript_leg = inspect.getsource(binding._transcribe_reminder_to_discord)
    assert "CHAT_DOORS" in transcript_leg
    assert '("discord", "telegram")' not in transcript_leg
