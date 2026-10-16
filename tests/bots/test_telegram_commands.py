"""The six commands on the Telegram door (telegram door, task 3).

Telegram has a commands MENU — the "/" button beside the text box, published
with ``set_my_commands`` — so the door has to say what it offers rather than
hope he guesses. Six: ``/start /help /link /proactive /insights /voice``.

What is deliberate here:

  * **``/start`` does not interview him.** Telegram sends it automatically
    the first time a chat opens, so it is the first thing she ever says to a
    stranger. It is ONE fixed paragraph, no model call: a deterministic
    greeting cannot hallucinate a promise, and first contact proper is the
    reflect lane's job, exactly as on Discord.
  * **The words come from bots/common/commands.py.** These callbacks resolve
    who is asking and render; nothing about consent, insights or pairing is
    decided twice.
  * **``/voice`` is a per-chat session flag**, in memory beside the steering
    budget — a restart forgets it, which is the spec's choice, not an
    oversight.

Nothing here imports ``telegram``: the bot, the update and the BotCommand
constructor are all fakes, so the suite runs where the library is absent.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

OWNER_TELEGRAM_ID = 777
STRANGER_ID = 4242
CHAT = 424242
SESSION = f"telegram:{CHAT}"


# ── fakes ────────────────────────────────────────────────────────────────
class FakeBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.published: list = []

    async def send_message(self, *, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text})

    async def set_my_commands(self, commands):
        self.published.append(list(commands))


class FakeClient:
    def __init__(self, *, guest_gate=None):
        self.bot = FakeBot()
        # The live client hands its process-wide gate here; a client built
        # without one is simply ungated, which is what most of these tests
        # want (review round 1, minor 2).
        if guest_gate is not None:
            self._guest_gate = guest_gate


class FakeUser:
    def __init__(self, id=OWNER_TELEGRAM_ID):
        self.id = id
        self.username = "dana_example"
        self.full_name = "Dana"
        self.is_bot = False


class FakeChat:
    def __init__(self, id=CHAT, type="private"):
        self.id = id
        self.type = type
        self.title = None


class FakeMessage:
    def __init__(self, text, *, author_id=OWNER_TELEGRAM_ID, chat_type="private"):
        self.text = text
        self.chat = FakeChat(type=chat_type)
        self.from_user = FakeUser(author_id)
        self.message_id = 11


class FakeUpdate:
    def __init__(self, message):
        self.message = message
        self.edited_message = None
        self.channel_post = None
        self.effective_message = message


def _handlers(client):
    from bots.telegram.commands import build_handlers

    return dict(build_handlers(client))


async def _run(client, name, text, **kw):
    handler = _handlers(client)[name]
    await handler(FakeUpdate(FakeMessage(text, **kw)), None)
    return client.bot.messages


@pytest.fixture
def caller_is_owner(monkeypatch):
    from bots.common.commands import Caller
    from bots.telegram import commands

    caller = Caller(is_owner=True, user_id=1, platform_user_id=OWNER_TELEGRAM_ID,
                    platform="telegram")
    resolve = AsyncMock(return_value=caller)
    monkeypatch.setattr(commands, "resolve_caller", resolve)
    return resolve


@pytest.fixture
def caller_is_a_guest(monkeypatch):
    from bots.common.commands import Caller
    from bots.telegram import commands

    caller = Caller(is_owner=False, user_id=STRANGER_ID,
                    platform_user_id=STRANGER_ID, platform="telegram")
    resolve = AsyncMock(return_value=caller)
    monkeypatch.setattr(commands, "resolve_caller", resolve)
    return resolve


# ── the menu ─────────────────────────────────────────────────────────────

def test_the_six_are_the_set_and_the_order():
    from bots.telegram.commands import COMMAND_SPECS

    assert [name for name, _ in COMMAND_SPECS] == [
        "start", "help", "link", "proactive", "insights", "voice",
    ]
    assert all(description.strip() for _, description in COMMAND_SPECS)


def test_build_handlers_returns_one_callback_per_spec():
    from bots.telegram.commands import COMMAND_SPECS, build_handlers

    pairs = build_handlers(FakeClient())

    assert [name for name, _ in pairs] == [name for name, _ in COMMAND_SPECS]
    assert all(callable(callback) for _, callback in pairs)


@pytest.mark.asyncio
async def test_publish_commands_sends_the_six_in_order(monkeypatch):
    from bots.telegram import commands

    monkeypatch.setattr(commands, "_bot_command_factory",
                        lambda name, description: (name, description))
    bot = FakeBot()

    await commands.publish_commands(bot)

    assert bot.published == [list(commands.COMMAND_SPECS)]


@pytest.mark.asyncio
async def test_a_menu_that_will_not_publish_never_closes_the_door(monkeypatch):
    from bots.telegram import commands

    class _Broken(FakeBot):
        async def set_my_commands(self, commands):
            raise RuntimeError("telegram said no")

    monkeypatch.setattr(commands, "_bot_command_factory",
                        lambda name, description: (name, description))

    await commands.publish_commands(_Broken())      # must not raise


# ── /start ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_in_a_dm_is_one_fixed_paragraph(caller_is_owner):
    client = FakeClient()

    sent = await _run(client, "start", "/start")

    assert len(sent) == 1
    text = sent[0]["text"]
    assert sent[0]["chat_id"] == CHAT
    assert "PaLLi" in text
    assert "?" not in text          # no questionnaire — first contact is hers


@pytest.mark.asyncio
async def test_start_in_a_group_says_how_to_reach_her(caller_is_owner):
    client = FakeClient()

    sent = await _run(client, "start", "/start", chat_type="supergroup")

    text = sent[0]["text"]
    assert "@" in text or "reply" in text.lower()
    assert text != ""


@pytest.mark.asyncio
async def test_start_calls_no_model_and_writes_nothing(monkeypatch):
    """Deterministic on purpose: the first thing she ever says to a stranger
    is not a generated sentence, and it records nothing about them.

    Strengthened in review round 1 (minor 6): the first version patched
    ``insights_text``, which is not a model call. This one puts a tripwire
    on the LLM facade's five request methods AND on every write method of
    the database the shared module holds, and drives the real
    ``resolve_caller`` with only the owner-refs lookup faked.
    """
    import ai.llm_client as llm_module
    from bots.common import commands as shared
    from bots.telegram import commands

    touched: list[str] = []

    class _Tripwire:
        """Answers reads with nothing; screams on anything else."""

        def __getattr__(self, name):
            async def _call(*args, **kwargs):
                touched.append(f"brain.{name}")
                return {}

            return _call

    monkeypatch.setattr(shared, "brain", _Tripwire())

    async def _refs():
        return {"app_user_id": 1, "palli_user_id": 1,
                "telegram_user_id": OWNER_TELEGRAM_ID}

    monkeypatch.setattr(shared, "get_local_owner_refs", _refs)

    for name in ("chat_completion", "chat_completion_stream",
                 "vision_completion"):
        async def _model(*args, _n=name, **kwargs):
            touched.append(f"llm.{_n}")
            return None

        monkeypatch.setattr(llm_module.llm, name, _model)

    client = FakeClient()
    sent = await _run(client, "start", "/start")

    assert touched == []
    assert sent[0]["text"] == commands._START_DM


# ── /help ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_help_lists_every_command_one_line_each(caller_is_owner):
    from bots.telegram.commands import COMMAND_SPECS

    client = FakeClient()

    sent = await _run(client, "help", "/help")

    text = sent[0]["text"]
    for name, description in COMMAND_SPECS:
        assert f"/{name}" in text
        assert description in text
    assert len([line for line in text.splitlines() if line.startswith("/")]) == 6


# ── /link ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_without_a_code_shows_the_usage(monkeypatch, caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    linker = AsyncMock(return_value="never")
    monkeypatch.setattr(commands, "link_platform_account", linker)

    sent = await _run(client, "link", "/link")

    linker.assert_not_awaited()
    assert "/link" in sent[0]["text"]


@pytest.mark.asyncio
async def test_link_hands_the_code_to_the_shared_function(monkeypatch,
                                                          caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    linker = AsyncMock(return_value="Your Telegram is linked.")
    monkeypatch.setattr(commands, "link_platform_account", linker)

    sent = await _run(client, "link", "/link TG12AB")

    assert linker.await_args.kwargs == {
        "platform": "telegram", "platform_user_id": OWNER_TELEGRAM_ID,
        "code": "TG12AB",
    }
    assert sent[0]["text"] == "Your Telegram is linked."


@pytest.mark.asyncio
async def test_link_reads_the_asking_users_own_id(monkeypatch):
    """The id that gets linked is the id of whoever typed the command, all
    the way from the message author — not a constant, and not the owner's."""
    from bots.common.commands import Caller
    from bots.telegram import commands

    async def _echo(*, platform, platform_user_id):
        return Caller(is_owner=False, user_id=platform_user_id,
                      platform_user_id=platform_user_id, platform=platform)

    monkeypatch.setattr(commands, "resolve_caller", _echo)
    client = FakeClient()
    linker = AsyncMock(return_value="ok")
    monkeypatch.setattr(commands, "link_platform_account", linker)

    await _run(client, "link", "/link ABC123", author_id=STRANGER_ID)

    assert linker.await_args.kwargs["platform_user_id"] == STRANGER_ID


# ── /proactive ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proactive_on_and_off_reach_the_setter(monkeypatch, caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    setter = AsyncMock(return_value="Reach-outs are on.")
    monkeypatch.setattr(commands, "set_proactive_consent", setter)

    await _run(client, "proactive", "/proactive on")
    assert setter.await_args.args[1] is True

    await _run(client, "proactive", "/proactive off")
    assert setter.await_args.args[1] is False


@pytest.mark.asyncio
async def test_proactive_with_no_word_reports_without_writing(monkeypatch,
                                                              caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    setter = AsyncMock(return_value="written")
    status = AsyncMock(return_value="Reach-outs are on.")
    monkeypatch.setattr(commands, "set_proactive_consent", setter)
    monkeypatch.setattr(commands, "proactive_status", status)

    sent = await _run(client, "proactive", "/proactive")

    setter.assert_not_awaited()
    status.assert_awaited()
    assert sent[0]["text"] == "Reach-outs are on."


@pytest.mark.asyncio
async def test_proactive_status_is_the_same_read(monkeypatch, caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    status = AsyncMock(return_value="Reach-outs are off.")
    monkeypatch.setattr(commands, "proactive_status", status)

    sent = await _run(client, "proactive", "/proactive status")

    status.assert_awaited()
    assert sent[0]["text"] == "Reach-outs are off."


@pytest.mark.asyncio
async def test_a_word_she_does_not_know_asks_rather_than_guesses(monkeypatch,
                                                                 caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    setter = AsyncMock(return_value="written")
    monkeypatch.setattr(commands, "set_proactive_consent", setter)

    sent = await _run(client, "proactive", "/proactive maybe")

    setter.assert_not_awaited()
    assert "on" in sent[0]["text"] and "off" in sent[0]["text"]


# ── /insights ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insights_renders_the_shared_text_in_a_dm(monkeypatch, caller_is_owner):
    from bots.telegram import commands

    client = FakeClient()
    monkeypatch.setattr(commands, "insights_text",
                        AsyncMock(return_value="After 41 conversations…"))

    sent = await _run(client, "insights", "/insights")

    assert sent[0]["text"] == "After 41 conversations…"
    assert commands.resolve_caller.await_args.kwargs == {
        "platform": "telegram", "platform_user_id": OWNER_TELEGRAM_ID,
    }


@pytest.mark.asyncio
async def test_insights_in_a_group_refuses_and_never_reads(monkeypatch,
                                                           caller_is_owner):
    """Telegram has no ephemeral message (review round 1, important 1). In a
    supergroup this would print somebody's traits, remembered facts and
    interests to the whole room — so it is not fetched at all."""
    from bots.telegram import commands

    client = FakeClient()
    read = AsyncMock(return_value="After 41 conversations…")
    monkeypatch.setattr(commands, "insights_text", read)

    sent = await _run(client, "insights", "/insights", chat_type="supergroup")

    read.assert_not_awaited()
    assert sent[0]["text"] == "I'll only say that in a DM — message me directly."


@pytest.mark.asyncio
async def test_a_guest_insights_in_a_group_is_refused_too(monkeypatch,
                                                          caller_is_a_guest):
    """It is their own data, but a room is still a room."""
    from bots.telegram import commands

    client = FakeClient()
    read = AsyncMock(return_value="what she knows about the stranger")
    monkeypatch.setattr(commands, "insights_text", read)

    sent = await _run(client, "insights", "/insights", author_id=STRANGER_ID,
                      chat_type="group")

    read.assert_not_awaited()
    assert "DM" in sent[0]["text"]


# ── /voice ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_on_sets_the_flag_for_this_chat(caller_is_owner):
    from bots.common import steering

    steering.set_session_flag(SESSION, "voice_replies", False)
    client = FakeClient()

    await _run(client, "voice", "/voice on")

    assert steering.get_session_flag(SESSION, "voice_replies") is True


@pytest.mark.asyncio
async def test_voice_off_clears_it_again(caller_is_owner):
    from bots.common import steering

    client = FakeClient()
    await _run(client, "voice", "/voice on")

    await _run(client, "voice", "/voice off")

    assert steering.get_session_flag(SESSION, "voice_replies") is False


@pytest.mark.asyncio
async def test_voice_with_no_word_reports_the_flag(caller_is_owner):
    from bots.common import steering

    steering.set_session_flag(SESSION, "voice_replies", True)
    client = FakeClient()

    sent = await _run(client, "voice", "/voice")

    assert "on" in sent[0]["text"].lower()
    assert steering.get_session_flag(SESSION, "voice_replies") is True


@pytest.mark.asyncio
async def test_voice_says_when_there_is_no_voice_on_this_host(caller_is_owner,
                                                              monkeypatch):
    """Task 5 review round 1, important 2: with the TTS lane off, "/voice on"
    answering a plain "on" promises something this host cannot do. The switch
    still flips — it works the moment a key is added — but the line says so."""
    from bots.common import steering
    from core.config import config

    monkeypatch.setattr(config, "effective_tts_provider", "off", raising=False)
    client = FakeClient()

    sent = await _run(client, "voice", "/voice on")

    assert "on" in sent[0]["text"].lower()
    assert "no voice is configured on this host" in sent[0]["text"]
    assert steering.get_session_flag(SESSION, "voice_replies") is True


@pytest.mark.asyncio
async def test_voice_says_nothing_extra_where_a_voice_is_configured(
        caller_is_owner, monkeypatch):
    """Task 6 review round 1, important 1: the line now names the CONDITION.
    `_voice_note_for` (bots/telegram/client.py) returns None unless the
    message she is answering was itself a voice note, so "voice replies are
    on" on its own sent a stranger off to type a sentence and wait for audio
    that was never coming."""
    from core.config import config

    monkeypatch.setattr(config, "effective_tts_provider", "lemonfox",
                        raising=False)
    client = FakeClient()

    sent = await _run(client, "voice", "/voice on")

    assert sent[0]["text"] == (
        "Voice replies are on in this chat: send me a voice note and my "
        "answer comes back as one too. It resets when I restart.")


@pytest.mark.asyncio
async def test_the_flag_is_per_chat(caller_is_owner):
    from bots.common import steering

    client = FakeClient()
    await _run(client, "voice", "/voice on")

    assert steering.get_session_flag("telegram:999999", "voice_replies") is False
    assert steering.get_session_flag(SESSION, "voice_replies") is True


@pytest.mark.asyncio
async def test_voice_is_the_owners_switch(caller_is_a_guest):
    """Once task 5 reads this flag, a guest could otherwise make her answer
    a whole room out loud (review round 1, minor 3)."""
    from bots.common import steering

    steering.set_session_flag(SESSION, "voice_replies", False)
    client = FakeClient()

    sent = await _run(client, "voice", "/voice on", author_id=STRANGER_ID)

    assert steering.get_session_flag(SESSION, "voice_replies") is False
    assert sent[0]["text"] == (
        "Voice replies are a setting for the person I live with.")


@pytest.mark.asyncio
async def test_the_guest_line_is_the_same_shape_for_every_owner_only_knob(
        caller_is_a_guest):
    """One boundary, told one way. The real ``proactive_status`` answers a
    guest before it touches the database, so nothing is patched here."""
    from bots.common.commands import owner_only_line

    reach = await _run(FakeClient(), "proactive", "/proactive",
                       author_id=STRANGER_ID)
    voice = await _run(FakeClient(), "voice", "/voice on",
                       author_id=STRANGER_ID)

    assert reach[0]["text"] == owner_only_line("Reach-outs")
    assert voice[0]["text"] == owner_only_line("Voice replies")


# ── a stranger's commands spend the guest budget ─────────────────────────

@pytest.mark.asyncio
async def test_a_guest_over_the_cap_gets_the_line_and_nothing_runs(
        monkeypatch, caller_is_a_guest):
    """A stranger's command is a database round-trip like their turn, so it
    spends the same budget — one cap across every door, because the money is
    one account (review round 1, minor 2)."""
    from bots.common.guest_gate import GuestGate
    from bots.telegram import commands

    gate = GuestGate()
    monkeypatch.setattr(gate, "admit", lambda author_id: False)
    read = AsyncMock(return_value="what she knows")
    monkeypatch.setattr(commands, "insights_text", read)
    client = FakeClient(guest_gate=gate)

    sent = await _run(client, "insights", "/insights", author_id=STRANGER_ID)

    read.assert_not_awaited()
    assert sent == [{"chat_id": CHAT, "text": GuestGate.AT_CAPACITY_LINE}]


@pytest.mark.asyncio
async def test_a_guest_over_the_budget_still_gets_start_and_help(
        monkeypatch, caller_is_a_guest):
    """``/start`` and ``/help`` are fixed text — no read, no model call — so
    they cost nothing to serve, and Telegram sends ``/start`` by itself the
    moment a chat opens. "I'm at my limit for guests right now" is the wrong
    first thing a stranger ever hears (review round 1, addendum).
    """
    from bots.common.guest_gate import GuestGate
    from bots.telegram.commands import COMMAND_SPECS, _START_DM

    gate = GuestGate()
    monkeypatch.setattr(gate, "admit", lambda author_id: False)
    client = FakeClient(guest_gate=gate)

    start = await _run(client, "start", "/start", author_id=STRANGER_ID)
    assert start[0]["text"] == _START_DM

    helped = await _run(FakeClient(guest_gate=gate), "help", "/help",
                        author_id=STRANGER_ID)
    for name, description in COMMAND_SPECS:
        assert f"/{name} \u2014 {description}" in helped[0]["text"]

    # …and the one that reads is still refused, by the same gate, in the
    # same breath.
    refused = await _run(FakeClient(guest_gate=gate), "insights", "/insights",
                         author_id=STRANGER_ID)
    assert refused[0]["text"] == GuestGate.AT_CAPACITY_LINE


def test_only_the_commands_that_cost_something_are_budgeted():
    from bots.telegram.commands import BUDGETED_COMMANDS, COMMAND_SPECS

    assert BUDGETED_COMMANDS == {"link", "proactive", "insights", "voice"}
    assert BUDGETED_COMMANDS <= {name for name, _ in COMMAND_SPECS}


@pytest.mark.asyncio
async def test_an_admitted_guest_command_gives_its_slot_back(monkeypatch,
                                                             caller_is_a_guest):
    from bots.common.guest_gate import GuestGate
    from bots.telegram import commands

    gate = GuestGate()
    monkeypatch.setattr(commands, "link_platform_account",
                        AsyncMock(return_value="linked"))
    client = FakeClient(guest_gate=gate)

    await _run(client, "link", "/link ABC123", author_id=STRANGER_ID)

    assert gate.in_flight == 0


@pytest.mark.asyncio
async def test_a_command_that_raises_still_gives_the_slot_back(monkeypatch,
                                                               caller_is_a_guest):
    """A slot taken and not given back is taken for the life of the process;
    at the default cap of two, two failures shut the guest door for good."""
    from bots.common.guest_gate import GuestGate
    from bots.telegram import commands

    gate = GuestGate()
    monkeypatch.setattr(commands, "link_platform_account",
                        AsyncMock(side_effect=RuntimeError("db down")))
    client = FakeClient(guest_gate=gate)

    sent = await _run(client, "link", "/link ABC123", author_id=STRANGER_ID)

    assert gate.in_flight == 0
    assert sent[0]["text"].strip()


@pytest.mark.asyncio
async def test_his_own_commands_are_never_budgeted(monkeypatch, caller_is_owner):
    from bots.common.guest_gate import GuestGate
    from bots.telegram import commands

    gate = GuestGate()
    monkeypatch.setattr(gate, "admit", lambda author_id: (_ for _ in ()).throw(
        AssertionError("the owner was sent to the guest gate")))
    monkeypatch.setattr(commands, "insights_text",
                        AsyncMock(return_value="After 41 conversations…"))
    client = FakeClient(guest_gate=gate)

    sent = await _run(client, "insights", "/insights")

    assert sent[0]["text"] == "After 41 conversations…"
    assert gate.in_flight == 0


# ── failure ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_command_that_raises_still_answers_him(monkeypatch,
                                                       caller_is_owner):
    """python-telegram-bot swallows a raising handler. A command that says
    nothing is DEFECT §19's shape: a thing he asked for that never happened."""
    from bots.telegram import commands

    from bots.common.commands import TROUBLE_LINE

    client = FakeClient()
    monkeypatch.setattr(commands, "insights_text",
                        AsyncMock(side_effect=RuntimeError("db down")))

    sent = await _run(client, "insights", "/insights")

    # The same sentence Discord says, from one place (review round 1,
    # minor 7) — it is the same failure.
    assert sent == [{"chat_id": CHAT, "text": TROUBLE_LINE}]


@pytest.mark.asyncio
async def test_an_update_with_no_message_is_simply_ignored(caller_is_owner):
    from bots.telegram.commands import build_handlers

    client = FakeClient()
    handler = dict(build_handlers(client))["help"]

    class _Empty:
        message = None
        edited_message = None
        channel_post = None
        effective_message = None

    await handler(_Empty(), None)

    assert client.bot.messages == []


# ── the boundary ─────────────────────────────────────────────────────────

def test_the_module_does_not_import_the_library_at_module_level():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "bots" / "telegram" / "commands.py").read_text("utf-8")
    top = source.split("def ", 1)[0]

    assert "import telegram" not in top
    assert "from telegram" not in top
