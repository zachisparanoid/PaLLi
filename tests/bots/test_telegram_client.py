"""The Telegram door itself (telegram door, task 2).

A faithful port of bots/discord/client.py's orchestration onto a second
transport: owner gate → guest lane or owner lane → burst coalescer → the
steering fork (steer / barge-in / stop) → dispatch → reply. The lanes are
the SAME code (bots/common/steering.py, bots/common/burst.py,
bots/common/guest_gate.py); what differs is the transport underneath, so
these tests are about the wiring, not about re-proving the lanes.

Nothing here imports ``telegram``: the Application is injected, and the
handler objects are built through a small factory a test can replace —
``MessageHandler`` and ``CommandHandler`` cannot be constructed where the
library is absent, and the suite must run there.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

OWNER_TELEGRAM_ID = 777
APP_OWNER_ID = 1
STRANGER_ID = 4242
BOT_ID = 999
BOT_USERNAME = "palli_test_bot"
CHAT = 424242
SESSION = f"telegram:{CHAT}"


# ── fakes ─────────────────────────────────────────────────────────────────
class FakeBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.actions: list[dict] = []
        self.published: list = []
        self.me = FakeUser(BOT_ID, username=BOT_USERNAME, is_bot=True)

    async def get_me(self):
        return self.me

    async def set_my_commands(self, commands):
        # Task 3: the "/" menu Telegram shows. Recorded, not logged, so the
        # lifecycle test's log assertion keeps its exact shape.
        self.published.append(list(commands))

    async def send_message(self, *, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text})

    async def send_chat_action(self, *, chat_id, action, **kw):
        self.actions.append({"chat_id": chat_id, "action": action})


class FakeUpdater:
    def __init__(self, log):
        self._log = log

    async def start_polling(self, **kw):
        self._log.append(("updater.start_polling", kw))

    async def stop(self):
        self._log.append(("updater.stop", {}))


class FakeApplication:
    def __init__(self):
        self.handlers: list = []
        self.error_handlers: list = []
        self.bot = FakeBot()
        self.log: list = []
        self.updater = FakeUpdater(self.log)

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, callback):
        self.error_handlers.append(callback)

    async def initialize(self):
        self.log.append(("initialize", {}))

    async def start(self):
        self.log.append(("start", {}))

    async def stop(self):
        self.log.append(("stop", {}))

    async def shutdown(self):
        self.log.append(("shutdown", {}))


class FakeHandlerFactory:
    """Stands in for telegram.ext's handler constructors."""

    def command(self, name, callback):
        return ("command", name, callback)

    def text(self, callback):
        return ("text", callback)

    def voice(self, callback):
        return ("voice", callback)

    def file(self, callback):
        return ("file", callback)

    def other(self, callback):
        return ("other", callback)


class FakeUser:
    def __init__(self, id, *, username=None, full_name=None, is_bot=False):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, id=CHAT, type="private"):
        self.id = id
        self.type = type
        self.title = None


class FakeMessage:
    def __init__(self, *, text=None, author_id=OWNER_TELEGRAM_ID, chat_id=CHAT,
                 chat_type="private", document=None, photo=(), voice=None):
        self.chat = FakeChat(chat_id, chat_type)
        self.from_user = FakeUser(author_id, username="dana_example", full_name="Dana")
        self.text = text
        self.caption = None
        self.entities = []
        self.caption_entities = []
        self.reply_to_message = None
        self.document = document
        self.photo = list(photo)
        self.voice = voice
        self.audio = None
        self.message_id = 17


class FakeUpdate:
    def __init__(self, message):
        self.message = message
        self.edited_message = None
        self.channel_post = None
        self.effective_message = message


def _client(monkeypatch=None, *, application=None):
    from bots.common.guest_gate import GuestGate
    from bots.telegram.client import PalliTelegramClient

    app = application or FakeApplication()
    client = PalliTelegramClient(
        application=app,
        bot_user_id=BOT_ID,
        bot_username=BOT_USERNAME,
        owner_id=APP_OWNER_ID,
        guest_gate=GuestGate(),
    )
    client._handler_factory = FakeHandlerFactory

    async def _owner_tg_id():
        return OWNER_TELEGRAM_ID

    client._owner_telegram_id = _owner_tg_id
    return client


def _result(text="on it"):
    from core.turn.types import TurnResult

    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


async def _idle_prior():
    return asyncio.get_running_loop().create_task(asyncio.sleep(30))


async def _drain_guests(client):
    """Await the guest turns the door now holds in the BACKGROUND.

    After the final wave's I2 fix ``on_message`` returns while a stranger's
    turn is still running, so a test that wants to see the end of one waits
    for the set the client holds it in.
    """
    tasks = list(getattr(client, "_guest_tasks", ()) or ())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)          # let the done-callbacks discard


# ── handlers ──────────────────────────────────────────────────────────────
def test_the_handlers_register_in_the_order_that_lets_commands_win():
    """Task 3 fills the command seam; it is registered FIRST on purpose —
    python-telegram-bot runs the first matching handler in a group, so a
    command must be offered the update before the catch-all text handler."""
    app = FakeApplication()
    client = _client(application=app)

    client.register_handlers()

    assert [h[0] for h in app.handlers] == ["text", "voice", "file", "other"]
    assert app.handlers[0][1] == client.on_text
    assert app.handlers[1][1] == client.on_voice
    assert app.handlers[2][1] == client.on_file
    # The catch-all is LAST on purpose: the library runs at most one handler
    # per group and takes the first that matches in registration order
    # (Application.add_handler appends; process_update breaks after a match).
    assert app.handlers[3][1] == client.on_other


def test_a_command_in_the_seam_is_registered_before_the_text_handler():
    app = FakeApplication()
    client = _client(application=app)

    async def _start(update, context=None):
        return None

    client.command_specs = [("start", _start)]
    client.register_handlers()

    assert [h[0] for h in app.handlers] == ["command", "text", "voice", "file",
                                            "other"]
    assert app.handlers[0][1] == "start"


# ── the owner's lane ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_his_message_reaches_dispatch_on_this_chats_session():
    from core.agent.runner import _derive_session_key

    client = _client()
    update = FakeUpdate(FakeMessage(text="what's the plan"))

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("here's the plan"))) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(update)
        assert client._burst_fragments[CHAT]
        await client._fire_burst(CHAT)

    turn = disp.await_args.args[0]
    assert turn.surface == "telegram_text"
    assert turn.owner_id == APP_OWNER_ID
    assert turn.content == "what's the plan"
    assert _derive_session_key(turn) == SESSION
    rec.assert_awaited()
    assert send.await_args.args[1] == CHAT
    assert send.await_args.args[2] == "here's the plan"


@pytest.mark.asyncio
async def test_her_own_messages_and_other_bots_are_never_turns():
    client = _client()
    with patch("bots.telegram.client.dispatch_turn", new=AsyncMock()) as disp, \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(FakeUpdate(FakeMessage(text="echo", author_id=BOT_ID)))
        other = FakeUpdate(FakeMessage(text="beep", author_id=BOT_ID + 1))
        other.message.from_user.is_bot = True
        await client.on_message(other)

    disp.assert_not_called()
    rec.assert_not_awaited()
    assert client._burst_fragments == {}


@pytest.mark.asyncio
async def test_a_group_message_that_is_not_for_her_never_buffers():
    client = _client()
    update = FakeUpdate(FakeMessage(text="anyone got a 10mm socket",
                                    chat_id=-1001234567890, chat_type="supergroup"))

    with patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(update)

    rec.assert_not_awaited()
    assert client._burst_fragments == {}


# ── the guest lane ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_stranger_over_the_cap_waits_their_turn_and_gets_no_canned_line(monkeypatch):
    """Batch 8, task 2b (ruling T2b-6; rewritten under the kickoff's exception
    (a) — it was ``test_a_stranger_at_capacity_gets_one_line_and_no_model_call``):
    the in-flight cap is a queue now. Over it, the stranger's turn waits for a
    slot inside its own background task — the door stays free — and nothing
    is said for her; when a slot frees, she reads it like any other."""
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 1, raising=False)
    client = _client()
    gate = client._guest_gate
    await gate.acquire()                         # someone else's turn holds the one slot
    update = FakeUpdate(FakeMessage(text="hello?", author_id=STRANGER_ID))

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("hi!"))) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await asyncio.wait_for(client.on_message(update), timeout=2.0)   # never holds the door
        for _ in range(5):
            await asyncio.sleep(0)
        assert gate.waiting == 1                 # queued, not refused
        disp.assert_not_called()
        send.assert_not_awaited()                # no "at capacity" line

        gate.release()                           # the other turn ends
        await _drain_guests(client)

    disp.assert_awaited_once()
    rec.assert_not_awaited()                     # never his history
    assert send.await_args.args[2] == "hi!"
    assert all(c.args[2] != GuestGate.AT_CAPACITY_LINE for c in send.await_args_list)
    assert client._burst_fragments == {}
    assert gate.in_flight == 0


@pytest.mark.asyncio
async def test_a_stranger_under_the_cap_gets_one_plain_turn(monkeypatch):
    from core.config import config
    from core.owner_gate import GUEST_ORIGIN

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    update = FakeUpdate(FakeMessage(text="who are you?", author_id=STRANGER_ID))

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("I'm PaLLi."))) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(update)
        await _drain_guests(client)

    turn = disp.await_args.args[0]
    assert turn.owner_id == STRANGER_ID
    assert turn.metadata.origin == GUEST_ORIGIN
    rec.assert_not_awaited()
    assert send.await_args.args[2] == "I'm PaLLi."
    assert client._guest_gate.in_flight == 0     # the slot came back


@pytest.mark.asyncio
async def test_a_first_message_names_the_senders_numeric_id(monkeypatch, caplog):
    """Task 6 review round 1, minor: this line is how a person finds the
    number `LOCAL_OWNER_TELEGRAM_USER_ID` wants. It logged the @username
    only — and a Telegram account need not have one, so a stranger with no
    @username had `None` and nothing to look up. SETUP.md section 2b now
    points at this line, so it has to carry the id."""
    import logging

    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    update = FakeUpdate(FakeMessage(text="hello", author_id=STRANGER_ID))

    with caplog.at_level(logging.INFO, logger="bots.telegram.client"), \
         patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("Hello."))), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(update)

    lines = [r.getMessage() for r in caplog.records if "guest message from" in r.getMessage()]
    assert lines, caplog.text
    # STRANGER_ID (4242) is a SUBSTRING of CHAT (424242), so the chat id has
    # to come out before the sender id can be looked for — otherwise this
    # test passes on the line that never carried the sender id at all.
    assert str(STRANGER_ID) in lines[0].replace(str(CHAT), ""), lines[0]


@pytest.mark.asyncio
async def test_a_strangers_turn_never_stalls_the_door(monkeypatch):
    """Final wave, I2. python-telegram-bot processes updates SEQUENTIALLY by
    default — ApplicationBuilder installs
    ``SimpleUpdateProcessor(max_concurrent_updates=1)``
    (_applicationbuilder.py:214) and ``Application.__update_fetcher`` awaits
    each update inline (_application.py:1254-1262). Awaiting a guest's
    20-60 s turn inside the handler therefore held the WHOLE door: his next
    message, in any chat, was not even recorded until the stranger had an
    answer. The turn runs as a held background task now, so the handler
    returns at once — and the slot still comes back when it ends.
    """
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def _slow_dispatch(turn):
        started.set()
        await finish.wait()
        return _result("took me a while")

    update = FakeUpdate(FakeMessage(text="hello?", author_id=STRANGER_ID))

    with patch("bots.telegram.client.dispatch_turn", _slow_dispatch), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        # The door is free while the stranger is being answered.
        await asyncio.wait_for(client.on_message(update), timeout=2.0)
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert client._guest_gate.in_flight == 1     # still running
        send.assert_not_awaited()

        finish.set()
        await _drain_guests(client)

    assert send.await_args.args[2] == "took me a while"
    assert client._guest_gate.in_flight == 0         # the slot came back
    assert client._guest_tasks == set()              # and nothing is held


@pytest.mark.asyncio
async def test_a_backgrounded_guest_turn_that_raises_still_gives_its_slot_back(
        monkeypatch, caplog):
    """The slot is taken by the admission, so every path after it — now
    including one inside a task nobody awaits — must reach the finally, and
    the failure must be SAID: an exception that dies inside a detached task
    is a failure nothing and nobody notices."""
    import logging

    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    update = FakeUpdate(FakeMessage(text="hello?", author_id=STRANGER_ID))

    with caplog.at_level(logging.ERROR, logger="bots.telegram.client"), \
         patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(update)
        await _drain_guests(client)

    assert client._guest_gate.in_flight == 0
    assert client._guest_tasks == set()
    assert "guest turn failed" in caplog.text


@pytest.mark.asyncio
async def test_stop_does_not_leak_a_guest_turn_still_in_flight(monkeypatch):
    """Shutdown fans out to the held guest turns the way the duplex stop's
    backstop does: a short grace to finish, then cancel. A task left behind
    keeps a slot of the guest budget for the life of the process."""
    from bots.telegram import client as tc
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(tc, "GUEST_STOP_GRACE_SECONDS", 0.01, raising=False)
    client = _client()
    never = asyncio.Event()

    async def _never_ends(turn):
        await never.wait()
        return _result()

    update = FakeUpdate(FakeMessage(text="hello?", author_id=STRANGER_ID))

    with patch("bots.telegram.client.dispatch_turn", _never_ends), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        # wait_for, not a bare await: before the fix this call never
        # returns at all — the stall this whole item is about.
        await asyncio.wait_for(client.on_message(update), timeout=2.0)
        held = list(client._guest_tasks)
        assert len(held) == 1
        await asyncio.wait_for(client.stop(), timeout=2.0)

    assert client._stop_event.is_set()
    assert held[0].done()
    assert client._guest_gate.in_flight == 0         # the finally still ran


# ── the steering fork ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_with_the_fork_off_a_second_message_barges_in(monkeypatch):
    from bots.common import steering

    monkeypatch.setattr(steering, "_flag_on", lambda: False)
    client = _client()
    prior = await _idle_prior()
    client._inflight_tasks[CHAT] = prior

    with patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(FakeUpdate(FakeMessage(text="no, the other one")))

    assert prior.cancelled() or prior.cancelling() or prior.done()
    assert client._inflight_tasks.get(CHAT) is None
    prior.cancel()


@pytest.mark.asyncio
async def test_with_the_fork_on_and_real_work_in_flight_the_message_steers(monkeypatch):
    from bots.common import steering as fork
    from core.agent import steering

    steering.clear(SESSION)
    steering.clear_run_state(SESSION)
    monkeypatch.setattr(fork, "_flag_on", lambda: True)
    steering.set_run_phase(SESSION, "generating")
    steering.note_tool_calls(SESSION, 1)
    client = _client()
    prior = await _idle_prior()
    client._inflight_tasks[CHAT] = prior

    with patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(FakeUpdate(FakeMessage(text="also grab the error count")))

    assert not prior.cancelled()
    assert client._inflight_tasks.get(CHAT) is prior
    # Spec §4e: the steer lane skips the arrival transcript append; the run's
    # boundary drain writes it, and _fire_burst repairs if the fork declines.
    assert rec.await_args.kwargs.get("transcript") is False
    assert len(client._burst_unappended.get(CHAT, [])) == 1
    prior.cancel()
    steering.clear_run_state(SESSION)
    steering.clear(SESSION)


@pytest.mark.asyncio
async def test_stop_asks_the_run_to_stop_at_its_next_boundary(monkeypatch):
    """Step 27 §3: with duplex on and real work in flight, 'stop' is a
    cooperative request, not a hard kill — the hard cancel is the backstop."""
    from bots.common import steering as fork
    from core.agent import duplex, steering

    steering.clear(SESSION)
    steering.clear_run_state(SESSION)
    monkeypatch.setattr(fork, "_flag_on", lambda: True)
    monkeypatch.setattr(duplex, "duplex_enabled", lambda: True)
    steering.set_run_phase(SESSION, "generating")
    steering.note_tool_calls(SESSION, 1)
    client = _client()
    prior = await _idle_prior()
    client._inflight_tasks[CHAT] = prior

    with patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(FakeUpdate(FakeMessage(text="stop")))

    assert steering.stop_requested(SESSION) is True
    assert client._inflight_tasks.get(CHAT) is None
    prior.cancel()
    steering.clear_run_state(SESSION)
    steering.clear(SESSION)


@pytest.mark.asyncio
async def test_the_post_run_sweep_fires_after_a_normal_turn(monkeypatch):
    """Spec §4f: whatever the run never answered becomes a real turn."""
    from bots.common import steering as fork

    monkeypatch.setattr(fork, "_flag_on", lambda: True)
    client = _client()
    swept = AsyncMock(return_value=0)
    monkeypatch.setattr(fork, "sweep_after_run", swept)

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result())), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(FakeUpdate(FakeMessage(text="hi")))
        await client._fire_burst(CHAT)

    swept.assert_awaited()
    assert swept.await_args.args[0] == SESSION


# ── typing ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_she_shows_as_typing_while_the_run_is_in_flight():
    app = FakeApplication()
    client = _client(application=app)

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result())), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_message(FakeUpdate(FakeMessage(text="hi")))
        await client._fire_burst(CHAT)

    assert app.bot.actions and app.bot.actions[0] == {"chat_id": CHAT, "action": "typing"}


# ── the manual lifecycle ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_polling_drives_the_lifecycle_by_hand():
    """``Application.run_polling()`` takes over the event loop and installs
    signal handlers — she already HAS a loop, and every other component runs
    on it. The manual sequence is the whole reason this client exists."""
    app = FakeApplication()
    client = _client(application=app)

    task = asyncio.create_task(client.run_polling())
    for _ in range(50):
        await asyncio.sleep(0)
        if any(name == "start" for name, _ in app.log):
            break
    await client.stop()
    await asyncio.wait_for(task, timeout=2)

    assert [name for name, _ in app.log] == [
        "initialize", "updater.start_polling", "start",
        "updater.stop", "stop", "shutdown",
    ]
    # PREMISE REVERSED 2026-09-18, owner's call: messages held while she was
    # restarting are replayed, not thrown away. Losing what he said in the
    # minute she was down is worse than answering it a little late.
    assert dict(app.log[1][1])["drop_pending_updates"] is False
    # get_me is the only way to learn her own id and handle.
    assert client._bot_user_id == BOT_ID
    assert client._bot_username == BOT_USERNAME


# ── the start path ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_blank_token_builds_nothing(monkeypatch):
    from bots.telegram import client as tc
    from core.config import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    registered = AsyncMock()
    monkeypatch.setattr(tc.channel_runtime, "register_adapter", registered)

    await tc.start_telegram_client()

    registered.assert_not_called()
    assert tc.get_active_client() is None


@pytest.mark.asyncio
async def test_a_missing_library_is_a_closed_door_not_a_crash(monkeypatch, caplog):
    """The suite runs where python-telegram-bot is not installed, and so may
    a stranger's first boot. A door that cannot open says so."""
    import sys

    from bots.telegram import client as tc
    from core.config import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional",
                        raising=False)
    monkeypatch.setitem(sys.modules, "telegram", None)
    monkeypatch.setitem(sys.modules, "telegram.ext", None)
    registered = AsyncMock()
    monkeypatch.setattr(tc.channel_runtime, "register_adapter", registered)

    with caplog.at_level("ERROR"):
        await tc.start_telegram_client()

    registered.assert_not_called()
    assert tc.get_active_client() is None
    assert any("not installed" in r.getMessage() for r in caplog.records)


# ── every kind reaches the lane (review round 1, important 2) ─────────────
@pytest.mark.asyncio
async def test_a_video_reaches_dispatch_with_an_honest_line():
    """It matched no handler at all before: no turn, no line, no log."""
    client = _client()
    update = FakeUpdate(FakeMessage(text=None))
    update.message.video = object()

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_other(update)
        await client._fire_burst(CHAT)

    assert "a video arrived" in disp.await_args.args[0].content


# ── the attachment wiring (review round 1, minor 7) ───────────────────────
@pytest.mark.asyncio
async def test_the_files_come_down_before_the_fragment_is_recorded():
    """A ref that reaches the transcript must already point at a local file:
    a Telegram file URL carries her bot token."""
    from bots.telegram import intake as tg_intake

    client = _client()
    order: list[str] = []

    async def _materialize(turn, bot, **kw):
        order.append("materialize")
        return turn

    async def _record(turn, **kw):
        order.append("record")

    update = FakeUpdate(FakeMessage(text="look at this"))
    update.message.photo = [object()]

    with patch.object(tg_intake, "materialize_attachments", _materialize), \
         patch("bots.telegram.client.record_user_fragment", _record):
        await client.on_file(update)

    assert order == ["materialize", "record"]


@pytest.mark.asyncio
async def test_a_guests_file_is_materialized_too(monkeypatch):
    from bots.telegram import intake as tg_intake
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    seen: list = []

    async def _materialize(turn, bot, **kw):
        seen.append(turn)
        return turn

    update = FakeUpdate(FakeMessage(text="have a look", author_id=STRANGER_ID))
    update.message.document = object()

    with patch.object(tg_intake, "materialize_attachments", _materialize), \
         patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result())), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_file(update)
        await _drain_guests(client)

    assert len(seen) == 1
    assert seen[0].owner_id == STRANGER_ID


# ── a raising handler is FELT (review round 1, minor 6) ───────────────────
@pytest.mark.asyncio
async def test_the_error_handler_is_registered_and_feels_the_failure(monkeypatch):
    """python-telegram-bot catches a raising handler and keeps polling —
    right, but nothing noticed. Now something does."""
    from types import SimpleNamespace

    from bots.telegram import client as tc
    from core.config import config

    app = FakeApplication()
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional",
                        raising=False)
    client = _client(application=app)
    felt: list[tuple] = []
    monkeypatch.setattr(tc, "delivery_notice",
                        lambda kind, **kw: felt.append((kind, kw)))

    await client.on_error(None, SimpleNamespace(error=RuntimeError("boom")))

    assert felt == [("turn_failed", {"error_class": "RuntimeError",
                                     "stage": "telegram_handler"})]


@pytest.mark.asyncio
async def test_the_start_path_hands_the_error_handler_over(monkeypatch):
    from bots.telegram import client as tc

    app = FakeApplication()
    registered: list = []

    class _Builder:
        def token(self, _t):
            return self

        def build(self):
            return app

    from core.config import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional",
                        raising=False)
    monkeypatch.setattr(tc.channel_runtime, "register_adapter", AsyncMock())
    monkeypatch.setattr(tc.channel_runtime, "unregister_adapter", AsyncMock())

    async def _refs():
        return {"app_user_id": APP_OWNER_ID, "telegram_user_id": OWNER_TELEGRAM_ID}

    monkeypatch.setattr(tc, "get_local_owner_refs", _refs)

    import sys
    import types as _types

    fake_ext = _types.ModuleType("telegram.ext")
    fake_ext.ApplicationBuilder = lambda: _Builder()
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)

    async def _run_polling(self):
        registered.append(list(app.error_handlers))

    monkeypatch.setattr(tc.PalliTelegramClient, "run_polling", _run_polling)
    monkeypatch.setattr(tc.PalliTelegramClient, "register_handlers",
                        lambda self: None)

    await tc.start_telegram_client()

    assert registered and registered[0], "no error handler was registered"


# ── the command seam, filled (telegram door, task 3) ──────────────────────
@pytest.mark.asyncio
async def test_the_start_path_fills_the_seam_with_the_six_commands(monkeypatch):
    """bots/telegram/commands.py::build_handlers is what goes in the seam,
    and it goes in BEFORE register_handlers — a CommandHandler registered
    after the catch-all would never be offered the update."""
    from bots.telegram import client as tc
    from bots.telegram.commands import COMMAND_SPECS
    from core.config import config

    app = FakeApplication()
    seen: list = []

    class _Builder:
        def token(self, _t):
            return self

        def build(self):
            return app

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional",
                        raising=False)
    monkeypatch.setattr(tc.channel_runtime, "register_adapter", AsyncMock())
    monkeypatch.setattr(tc.channel_runtime, "unregister_adapter", AsyncMock())

    async def _refs():
        return {"app_user_id": APP_OWNER_ID, "telegram_user_id": OWNER_TELEGRAM_ID}

    monkeypatch.setattr(tc, "get_local_owner_refs", _refs)

    import sys
    import types as _types

    fake_ext = _types.ModuleType("telegram.ext")
    fake_ext.ApplicationBuilder = lambda: _Builder()
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)

    async def _run_polling(self):
        seen.append([name for name, _ in self.command_specs])

    monkeypatch.setattr(tc.PalliTelegramClient, "run_polling", _run_polling)
    monkeypatch.setattr(tc.PalliTelegramClient, "register_handlers",
                        lambda self: None)

    await tc.start_telegram_client()

    assert seen == [[name for name, _ in COMMAND_SPECS]]


def test_six_commands_register_ahead_of_every_message_handler():
    from bots.telegram.commands import build_handlers

    app = FakeApplication()
    client = _client(application=app)
    client.command_specs = build_handlers(client)

    client.register_handlers()

    assert [h[0] for h in app.handlers] == (
        ["command"] * 6 + ["text", "voice", "file", "other"])
    assert [h[1] for h in app.handlers[:6]] == [
        "start", "help", "link", "proactive", "insights", "voice"]


@pytest.mark.asyncio
async def test_run_polling_publishes_the_command_menu(monkeypatch):
    from bots.telegram import commands as tcmd

    monkeypatch.setattr(tcmd, "_bot_command_factory",
                        lambda name, description: (name, description))
    app = FakeApplication()
    client = _client(application=app)

    task = asyncio.create_task(client.run_polling())
    for _ in range(50):
        await asyncio.sleep(0)
        if any(name == "start" for name, _ in app.log):
            break
    await client.stop()
    await asyncio.wait_for(task, timeout=2)

    assert app.bot.published == [list(tcmd.COMMAND_SPECS)]


@pytest.mark.asyncio
async def test_a_menu_that_will_not_publish_never_closes_the_door(monkeypatch):
    from bots.telegram import commands as tcmd

    def _boom(name, description):
        raise RuntimeError("no BotCommand here")

    monkeypatch.setattr(tcmd, "_bot_command_factory", _boom)
    app = FakeApplication()
    client = _client(application=app)

    task = asyncio.create_task(client.run_polling())
    for _ in range(50):
        await asyncio.sleep(0)
        if any(name == "start" for name, _ in app.log):
            break
    await client.stop()
    await asyncio.wait_for(task, timeout=2)

    assert [name for name, _ in app.log] == [
        "initialize", "updater.start_polling", "start",
        "updater.stop", "stop", "shutdown",
    ]


# ── /stop and the unknown command reach the owner lane ────────────────────
@pytest.mark.asyncio
async def test_slash_stop_reaches_the_stop_lane_through_the_catch_all(monkeypatch):
    """No CommandHandler is registered for 'stop' and `TEXT & ~COMMAND`
    excludes it — so `/stop` arrives on the filters.ALL handler, which is
    ``on_other``. Proved by driving THAT callback, the one the library would
    call."""
    from bots.common import steering as fork
    from core.agent import duplex, steering

    steering.clear(SESSION)
    steering.clear_run_state(SESSION)
    monkeypatch.setattr(fork, "_flag_on", lambda: True)
    monkeypatch.setattr(duplex, "duplex_enabled", lambda: True)
    steering.set_run_phase(SESSION, "generating")
    steering.note_tool_calls(SESSION, 1)
    client = _client()
    prior = await _idle_prior()
    client._inflight_tasks[CHAT] = prior

    with patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_other(FakeUpdate(FakeMessage(text="/stop")))

    assert steering.stop_requested(SESSION) is True
    assert client._inflight_tasks.get(CHAT) is None
    prior.cancel()
    steering.clear_run_state(SESSION)
    steering.clear(SESSION)


@pytest.mark.asyncio
async def test_an_unknown_command_becomes_an_ordinary_turn():
    """`/frobnicate` matches no CommandHandler. It must not vanish: it
    reaches her as the words he typed, so she can say she doesn't know it."""
    client = _client()
    dispatched = AsyncMock(return_value=_result())

    with patch("bots.telegram.client.dispatch_turn", new=dispatched), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()):
        await client.on_other(FakeUpdate(FakeMessage(text="/frobnicate")))
        await client._fire_burst(CHAT)

    assert dispatched.await_args.args[0].content == "/frobnicate"


# ── polling network blips (2026-09-30) ─────────────────────────────────────

def _polling_record(exc):
    import logging
    import sys
    try:
        raise exc
    except Exception:
        info = sys.exc_info()
    return logging.LogRecord("telegram.ext.Updater", logging.ERROR, __file__, 411,
                             "Exception happened while polling for updates.", (), info)


def test_a_polling_network_drop_becomes_one_warning_line():
    """09-29 21:13 and 09-30 09:27: a long-poll read dropped (httpx.ReadError,
    wrapped as telegram's NetworkError); the library retried and the next poll
    was fine 12 s later, but it logged a full ERROR traceback each time."""
    import logging
    from telegram.error import NetworkError, TimedOut
    from bots.telegram.client import PollingNetworkBlipFilter

    for exc in (NetworkError("httpx.ReadError: "), TimedOut()):
        record = _polling_record(exc)
        assert PollingNetworkBlipFilter().filter(record) is True
        assert record.levelno == logging.WARNING and record.levelname == "WARNING"
        assert record.exc_info is None
        assert "retries on its own" in record.getMessage()


def test_real_telegram_errors_keep_their_error_and_traceback():
    import logging
    from telegram.error import BadRequest, Conflict, InvalidToken
    from bots.telegram.client import PollingNetworkBlipFilter

    for exc in (BadRequest("chat not found"), Conflict("another getUpdates"), InvalidToken("bad")):
        record = _polling_record(exc)
        assert PollingNetworkBlipFilter().filter(record) is True
        assert record.levelno == logging.ERROR and record.exc_info is not None


def test_the_filter_is_installed_once_on_the_updater_logger():
    import logging
    from bots.telegram.client import PollingNetworkBlipFilter, install_polling_blip_filter

    log = logging.getLogger("telegram.ext.Updater")
    before = [f for f in log.filters if isinstance(f, PollingNetworkBlipFilter)]
    try:
        install_polling_blip_filter()
        install_polling_blip_filter()
        assert len([f for f in log.filters if isinstance(f, PollingNetworkBlipFilter)]) == max(1, len(before))
    finally:
        for f in [f for f in log.filters if isinstance(f, PollingNetworkBlipFilter)]:
            if f not in before:
                log.removeFilter(f)


@pytest.mark.asyncio
async def test_a_collaborator_still_takes_the_guest_path(tmp_path, monkeypatch):
    """Batch 8, task 1 (B8-1): a collaborator reads more of the room's work
    (dispatch resolves that from their own id), but their message is still a
    guest turn under THEIR id — never the owner's history or lane."""
    from core import collaborators
    from core.config import config
    from core.owner_gate import GUEST_ORIGIN

    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    collaborators.add_collaborator("telegram", STRANGER_ID, name="Sam")
    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    client = _client()
    update = FakeUpdate(FakeMessage(text="what are you two working on?", author_id=STRANGER_ID))

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("the disk"))) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment", new=AsyncMock()) as rec:
        await client.on_message(update)
        await _drain_guests(client)

    turn = disp.await_args.args[0]
    assert turn.owner_id == STRANGER_ID
    assert turn.metadata.origin == GUEST_ORIGIN
    rec.assert_not_awaited()
    assert client._burst_fragments == {}
