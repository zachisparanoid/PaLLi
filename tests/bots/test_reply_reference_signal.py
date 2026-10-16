"""The signals she weighs, never requirements (Batch 8, task 2b — ruling T2b-3).

With ``companion.she_decides`` on, each door's intake notes four things about
a message, and her decision calls (the room's first read and the full turn's
quick vote) see them as ONE context line:

- a DM or a shared room (``is_dm``);
- an @mention of her (``is_mention`` — on Telegram no longer folded together
  with a reply);
- a reply to one of her messages (``is_reply_to_bot``), with that message's
  text and time in ``metadata.extra["reply_to"]`` — Discord reads
  ``reference.resolved``, then ``reference.cached_message``, and with neither
  names the reply without text (no fetch in intake);
- her name, "PaLLi" or "Polly", as a whole word (``extra["names_her"]``).

With the switch off, every intake output is today's, field for field.
Stand-in names only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BOT_ID = 99999
OWNER_APP_ID = 1

# The intakes build their Turn without a received_at; that `datetime.utcnow()`
# is production debt pytest.ini leaves unfiltered on purpose, not this file's.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


@pytest.fixture
def switch_on(monkeypatch):
    from core.agent import quiet_choice

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)


# ── Discord ───────────────────────────────────────────────────────────────

def _discord_msg(content="hi", *, reference=None, mentions=()):
    msg = MagicMock()
    msg.content = content
    msg.attachments = []
    msg.mentions = list(mentions)
    msg.reference = reference
    msg.author = SimpleNamespace(id=4242, name="sam_example", bot=False)
    msg.channel = MagicMock()
    msg.channel.id = 8800002
    msg.channel.guild = SimpleNamespace(id=1, name="the den")
    return msg


def _her_message(text="12 newton metres", *, author_id=BOT_ID):
    return SimpleNamespace(author=SimpleNamespace(id=author_id), content=text,
                           created_at=datetime(2026, 10, 1, 14, 2, tzinfo=timezone.utc))


def _build(msg):
    from bots.discord.intake import build_turn_from_message

    return build_turn_from_message(msg, bot_user_id=BOT_ID, owner_id=OWNER_APP_ID)


def test_a_discord_reply_to_her_carries_her_message_and_its_time(switch_on):
    ref = SimpleNamespace(resolved=_her_message(), cached_message=None, type=None)
    turn = _build(_discord_msg("are you sure?", reference=ref))

    assert turn.metadata.is_reply_to_bot is True
    assert turn.metadata.is_mention is False
    reply = turn.metadata.extra["reply_to"]
    assert reply["to_her"] is True and reply["text"] == "12 newton metres"
    assert reply["at"].startswith("2026-10-01T14:02")


def test_a_discord_reply_falls_back_to_the_cached_message(switch_on):
    ref = SimpleNamespace(resolved=None, cached_message=_her_message("it's Tuesday"), type=None)
    turn = _build(_discord_msg("oh right", reference=ref))

    assert turn.metadata.is_reply_to_bot is True
    assert turn.metadata.extra["reply_to"]["text"] == "it's Tuesday"


def test_a_discord_reply_with_nothing_at_hand_is_named_without_text(switch_on):
    deleted = SimpleNamespace(id=1, channel_id=2)            # a DeletedReferencedMessage
    ref = SimpleNamespace(resolved=deleted, cached_message=None, type=None)
    turn = _build(_discord_msg("what did you say?", reference=ref))

    assert turn.metadata.is_reply_to_bot is False
    assert turn.metadata.extra["reply_to"] == {"to_her": None}


def test_a_discord_reply_to_someone_else_is_named_as_such(switch_on):
    ref = SimpleNamespace(resolved=_her_message("mine", author_id=4343), cached_message=None,
                          type=None)
    turn = _build(_discord_msg("nice", reference=ref))

    assert turn.metadata.is_reply_to_bot is False
    assert turn.metadata.extra["reply_to"] == {"to_her": False}


def test_a_discord_forward_is_not_a_reply(switch_on):
    ref = SimpleNamespace(resolved=_her_message(), cached_message=None,
                          type=SimpleNamespace(name="forward"))
    turn = _build(_discord_msg("look at this", reference=ref))

    assert turn.metadata.is_reply_to_bot is False
    assert "reply_to" not in turn.metadata.extra


@pytest.mark.parametrize("text,named", [
    ("hey polly what do you think", True),
    ("PaLLi, you there?", True),
    ("ask Polly.", True),
    ("pallister street is closed", False),
    ("apollyon", False),
    ("no names here", False),
])
def test_her_name_is_a_whole_word_either_spelling(switch_on, text, named):
    turn = _build(_discord_msg(text))
    assert turn.metadata.extra["names_her"] is named


def test_her_own_mention_token_is_not_her_name(switch_on):
    turn = _build(_discord_msg(f"<@{BOT_ID}> hello", mentions=[SimpleNamespace(id=BOT_ID)]))
    assert turn.metadata.is_mention is True
    assert turn.metadata.extra["names_her"] is False


def test_with_the_switch_off_discord_intake_is_todays():
    ref = SimpleNamespace(resolved=_her_message(), cached_message=None, type=None)
    turn = _build(_discord_msg("polly, are you sure?", reference=ref))

    assert turn.metadata.is_reply_to_bot is False
    assert turn.metadata.extra == {}


# ── Telegram ──────────────────────────────────────────────────────────────

def _tg(text, *, chat_type="supergroup", reply_to=None, entities=()):
    from tests.bots.test_telegram_intake import FakeChat, FakeMessage, FakeUser

    chat = FakeChat(-1009900001 if chat_type != "private" else 424242, chat_type)
    msg = FakeMessage(chat=chat, from_user=FakeUser(4242, username="sam_example"), text=text,
                      reply_to_message=reply_to, entities=list(entities))
    return msg


def _tg_build(message):
    from bots.telegram.intake import build_turn
    from tests.bots.test_telegram_intake import FakeUpdate

    return build_turn(FakeUpdate(message=message), bot_username="palli_test_bot", bot_user_id=BOT_ID,
                      owner_ref={"app_user_id": OWNER_APP_ID, "telegram_user_id": 777})


def _tg_her_message(text="12 newton metres"):
    from tests.bots.test_telegram_intake import FakeChat, FakeMessage, FakeUser

    m = FakeMessage(chat=FakeChat(-1009900001, "supergroup"),
                    from_user=FakeUser(BOT_ID, username="palli_test_bot", is_bot=True), text=text)
    m.date = datetime(2026, 10, 1, 14, 2, tzinfo=timezone.utc)
    return m


def test_a_telegram_reply_to_her_is_its_own_signal_with_her_words(switch_on):
    result = _tg_build(_tg("are you sure", reply_to=_tg_her_message()))

    md = result.turn.metadata
    assert md.is_reply_to_bot is True
    assert md.is_mention is False                    # no longer folded together
    assert md.extra["reply_to"]["text"] == "12 newton metres"
    assert md.extra["reply_to"]["at"].startswith("2026-10-01T14:02")


def test_a_telegram_group_message_not_for_her_still_reaches_her_with_the_switch_on(switch_on):
    result = _tg_build(_tg("anyone got a 10mm socket"))
    assert result is not None
    assert result.turn.metadata.is_mention is False
    assert result.turn.metadata.extra["names_her"] is False


def test_a_telegram_channel_post_type_is_still_not_a_conversation(switch_on):
    assert _tg_build(_tg("broadcast", chat_type="channel")) is None


def test_with_the_switch_off_telegram_keeps_both_locks_and_the_fold():
    assert _tg_build(_tg("anyone got a 10mm socket")) is None
    result = _tg_build(_tg("are you sure", reply_to=_tg_her_message()))
    assert result.turn.metadata.is_mention is True   # today's fold
    assert "reply_to" not in result.turn.metadata.extra
    assert "names_her" not in result.turn.metadata.extra


# ── the one line ─────────────────────────────────────────────────────────

def test_the_signals_line_names_each_signal_once():
    from core.agent.cadence import render_signals

    line = render_signals({"is_dm": False, "is_mention": True, "is_reply_to_her": True,
                           "reply_to": {"to_her": True, "text": "12 newton metres",
                                        "at": "2026-10-01T14:02:00+00:00"},
                           "names_her": True})
    assert "\n" not in line
    assert "never rules" in line
    assert "a shared room" in line and "@mentions her" in line
    assert '"12 newton metres"' in line and "says her name" in line
    assert render_signals({}) == ""
    dm = render_signals({"is_dm": True, "is_mention": False, "is_reply_to_her": False,
                         "reply_to": {"to_her": None}, "names_her": False})
    assert "a direct message" in dm and "can't see" in dm


def test_the_signals_line_avoids_the_plain_words_family():
    from core.agent.cadence import render_signals
    from tests.prompts.test_plain_words import PHRASE_FAMILY

    for s in ({"is_dm": True}, {"is_dm": False, "is_mention": True, "names_her": True,
                                "is_reply_to_her": True,
                                "reply_to": {"to_her": True, "text": "x", "at": ""}},
              {"is_dm": False, "reply_to": {"to_her": False}},
              {"is_dm": False, "reply_to": {"to_her": None}}):
        assert not PHRASE_FAMILY.search(render_signals(s))


@pytest.mark.asyncio
async def test_the_full_turns_quick_vote_carries_the_signals_line(monkeypatch, tmp_path):
    """In a DM (no first read) the runner's own vote sees the line; with the
    switch off it sees nothing new."""
    from tests.agent.she_decides_harness import DmHarness

    for on in (True, False):
        h = DmHarness(monkeypatch, tmp_path / str(on), switch_on=on)
        turn = h.turn("are you sure?")
        md = turn.metadata
        from dataclasses import replace

        turn = replace(turn, metadata=replace(md, is_reply_to_bot=True, extra={
            "reply_to": {"to_her": True, "text": "12 newton metres", "at": ""},
            "names_her": False}))
        h.model_answers("yes")
        await h.dispatch(turn)
        prompt = h.cadence_prompts[-1]
        if on:
            assert "a direct message" in prompt and '"12 newton metres"' in prompt
        else:
            assert "never rules" not in prompt and "12 newton metres" not in prompt
