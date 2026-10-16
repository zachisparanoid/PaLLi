"""Discord, step 2 part 7 (the who-is-talking plan): what she reads from a
Discord message says who it's for, what it is, and where it was said.

- An @mention reads as the person's name, never a bare number (10-06: the
  owner @mentioned a friend; she saw <@1467…>, took it for her own ping and
  answered).
- "This @mention is for someone else" is a signal she weighs (A6).
- Who a message is addressed to (a reply, @mentions) rides on its sender
  record, as the app says it.
- A forward says it's a forward and that the app doesn't show who first wrote it.
- Joins, pins and other notices from Discord itself are not messages.
- A thread is named, with the channel it's in.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import discord
import pytest

from bots.discord.intake import build_turn_from_message, is_system_message
from core.agent import cadence

BOT = 99999
OWNER = 1111


def _author(i, display, name=None):
    return NS(id=i, display_name=display, name=name or display.lower(), bot=False)


def _msg(content="", *, mentions=(), author=None, reference=None, snapshots=None,
         channel=None, attachments=()):
    m = MagicMock()
    m.content = content
    m.mentions = list(mentions)
    m.author = author or _author(OWNER, "Robin", "robin_example")
    m.reference = reference
    m.attachments = list(attachments)
    m.message_snapshots = list(snapshots or [])
    m.channel = channel or NS(id=5, name="general", guild=NS(id=1, name="the den"))
    return m


def _turn(message, *, kind="owner"):
    return build_turn_from_message(message, bot_user_id=BOT, owner_id=1, speaker_kind=kind)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: True)


SAM = _author(555, "Sam", "sam_x")
ALEX = _author(666, "Alex", "alex_y")


# ── mentions read as names ────────────────────────────────────────────────────

def test_an_at_mention_reads_as_the_persons_name():
    t = _turn(_msg("hey <@555> and <@!666>, <@99999> look <#123> <@&456> <@777>",
                   mentions=[SAM, ALEX, _author(BOT, "PaLLi")]))
    assert "@Sam" in t.content and "@Alex" in t.content
    assert "<@555>" not in t.content and "<@!666>" not in t.content
    assert "99999" not in t.content                       # her own mention, still stripped
    assert "<#123>" in t.content and "<@&456>" in t.content   # channels and roles stay codes
    assert "<@777>" in t.content                          # not resolved by the app: left as is


def test_a_mentioned_name_never_carries_the_owners_mark():
    from core.agent.speakers import OWNER_MARK

    t = _turn(_msg("ask <@888>", mentions=[_author(888, f"Robin {OWNER_MARK}")]))
    assert OWNER_MARK not in t.content and "@Robin owner" in t.content


# ── "this @mention is for someone else" (A6) ──────────────────────────────────

def test_the_mention_signal_with_the_switch_on(switch_on):
    t = _turn(_msg("<@555> did you see this?", mentions=[SAM]))
    assert t.metadata.extra["mentions_others"] == ["Sam"]
    line = cadence.render_signals(cadence.signals_of(t))
    assert "it @mentions someone else (Sam), not her" in line


def test_a_mention_of_her_and_someone_else(switch_on):
    t = _turn(_msg("<@99999> <@555> both of you", mentions=[_author(BOT, "PaLLi"), SAM]))
    line = cadence.render_signals(cadence.signals_of(t))
    assert "it @mentions her" in line and "it also @mentions Sam" in line


def test_no_mention_no_signal_key(switch_on):
    t = _turn(_msg("plain words"))
    assert "mentions_others" not in t.metadata.extra
    assert "mentions_others" not in cadence.signals_of(t)


def test_switch_off_adds_nothing_to_extra():
    t = _turn(_msg("<@555> hi", mentions=[SAM]))
    assert t.metadata.extra == {}
    assert "@Sam" in t.content                            # the name still reads


def test_signals_merge_keeps_every_name_once():
    merged = cadence.merge_signals([
        {"is_dm": False, "mentions_others": ["Sam"]},
        {"is_dm": False, "mentions_others": ["Alex", "Sam"]},
        {"is_dm": False},
    ])
    assert merged["mentions_others"] == ["Sam", "Alex"]
    assert "mentions_others" not in cadence.merge_signals([{"is_dm": False}])


# ── who it's addressed to ─────────────────────────────────────────────────────

def test_the_sender_record_says_who_it_is_addressed_to(switch_on):
    reply_ref = NS(type=discord.MessageReferenceType.default,
                   resolved=NS(author=ALEX, content="hi", created_at=None), cached_message=None)
    t = _turn(_msg("<@555> yes", mentions=[SAM], reference=reply_ref))
    to = t.metadata.speaker["to"]
    assert {"id": "555", "name": "Sam", "how": "mention"} in to
    assert {"id": "666", "name": "Alex", "how": "reply"} in to


def test_a_reply_to_her_names_her():
    reply_ref = NS(type=discord.MessageReferenceType.default,
                   resolved=NS(author=_author(BOT, "PaLLi"), content="hi", created_at=None),
                   cached_message=None)
    t = _turn(_msg("thanks", reference=reply_ref))
    assert {"id": str(BOT), "name": "PaLLi", "how": "reply"} in t.metadata.speaker["to"]


# ── forwards ──────────────────────────────────────────────────────────────────

FORWARD_LINE = "(forwarded message; the app doesn't show who first wrote it)"


def test_a_forward_says_so_and_shows_its_words():
    ref = NS(type=discord.MessageReferenceType.forward, resolved=None, cached_message=None)
    img = NS(content_type="image/png", filename="a.png", url="u", size=1)
    snap = NS(content="the shelf is oak", attachments=[img])
    t = _turn(_msg("", reference=ref, snapshots=[snap]))
    assert t.content.startswith(FORWARD_LINE)
    assert "the shelf is oak" in t.content and "[forwarded image]" in t.content


def test_a_forward_with_a_comment_keeps_the_comment():
    ref = NS(type=discord.MessageReferenceType.forward, resolved=None, cached_message=None)
    t = _turn(_msg("look at this", reference=ref, snapshots=[NS(content="old words", attachments=[])]))
    assert t.content.startswith("look at this")
    assert FORWARD_LINE in t.content and "old words" in t.content


def test_snapshots_without_a_forward_reference_are_ignored():
    t = _turn(_msg("hello", snapshots=[NS(content="ghost", attachments=[])]))
    assert t.content == "hello"


# ── Discord's own notices ─────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", [discord.MessageType.new_member, discord.MessageType.pins_add,
                                  discord.MessageType.thread_created,
                                  discord.MessageType.thread_starter_message])
def test_discords_own_notices_are_not_messages(kind):
    assert is_system_message(NS(type=kind)) is True


@pytest.mark.parametrize("kind", [discord.MessageType.default, discord.MessageType.reply])
def test_ordinary_messages_and_replies_are(kind):
    assert is_system_message(NS(type=kind)) is False


def test_a_fake_or_missing_type_is_an_ordinary_message():
    assert is_system_message(MagicMock()) is False
    assert is_system_message(NS()) is False


# ── threads ───────────────────────────────────────────────────────────────────

def _thread():
    th = MagicMock(spec=discord.Thread)
    th.id = 77
    th.name = "deploy notes"
    th.parent = NS(id=5, name="general")
    th.guild = NS(id=1, name="the den")
    return th


def test_a_thread_is_named_with_its_channel(switch_on):
    t = _turn(_msg("hi", channel=_thread()))
    assert t.metadata.channel_name == "deploy notes"
    assert t.metadata.extra["thread_in"] == "general"


def test_the_room_card_names_a_thread(switch_on):
    from core.agent import room_card

    t = _turn(_msg("hi", channel=_thread()))
    card = room_card.room_section(t, [])
    assert 'This is a thread, "deploy notes", in #general, in the server "the den".' in card


# ── through the real door ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_join_notice_never_becomes_a_turn(monkeypatch, tmp_path):
    from tests.bots.room_harness import RoomHarness

    room = RoomHarness(monkeypatch, tmp_path)
    notice = room._discord_message("sam", "")
    notice.type = discord.MessageType.new_member
    await room.client.on_message(notice)
    await room.settle()
    assert room.full_runs == 0 and not room.cadence_prompts
    assert await room.room_messages() == []


@pytest.mark.asyncio
async def test_the_owner_mentioning_a_friend_reads_as_a_name_and_a_signal(monkeypatch, tmp_path):
    """10-06, replayed: the owner @mentions a friend in a group. The quick vote
    now reads the friend's name, not a number, and the signal that the
    @mention is for someone else."""
    from tests.bots.room_harness import GUEST_IDS, RoomHarness

    room = RoomHarness(monkeypatch, tmp_path)
    msg = room._discord_message("owner", f"<@{GUEST_IDS['sam']}> did you see the game?")
    msg.mentions = [NS(id=GUEST_IDS["sam"], display_name="Sam", name="sam_example", bot=False)]
    room.plan(model_chooses="stay_quiet", reason="it's for Sam")
    await room.client.on_message(msg)
    await room.client._fire_burst(room.room)
    room.client._coalescer.mark_replied(room.room)
    await room.settle()
    vote = room.cadence_prompts[-1]
    assert "@Sam did you see the game?" in vote and f"<@{GUEST_IDS['sam']}>" not in vote
    assert "it @mentions someone else (Sam), not her" in vote
