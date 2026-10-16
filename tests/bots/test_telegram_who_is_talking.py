"""Telegram, step 2 part 8 (the who-is-talking plan): what she reads from a
Telegram group says who it's for, where it came from, and who's there.

- "This @mention is for someone else" (A6), from mention and text_mention
  entities, and who a message is addressed to, on its sender record.
- A forward says where it came from: all four of Telegram's kinds (a person,
  a person who hides their account, a group, a channel).
- An anonymous admin, or a channel posting into its discussion group, is a
  guest named for what it is, never the owner (they arrive "from" Telegram's
  own placeholder bot, and used to be dropped as a bot).
- The privacy setting is read at startup; the room card says so when it's on.
- /start in a group tells the truth with group chat on.
- "Who's in here?" is list_members on Telegram: the admins and the head count.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from bots.telegram import intake
from core.agent import cadence
from tests.bots.test_telegram_client import (
    BOT_ID, BOT_USERNAME, OWNER_TELEGRAM_ID, STRANGER_ID, FakeMessage, FakeUpdate, FakeUser,
)

GROUP = -100123


def _group_msg(text, *, author_id=STRANGER_ID, entities=(), forward_origin=None, sender_chat=None,
               author=None):
    m = FakeMessage(text=text, author_id=author_id, chat_id=GROUP, chat_type="supergroup")
    m.chat.title = "Family"
    m.entities = list(entities)
    if author is not None:
        m.from_user = author
    if forward_origin is not None:
        m.forward_origin = forward_origin
    if sender_chat is not None:
        m.sender_chat = sender_chat
    return m


def _turn(message):
    result = intake.build_turn(FakeUpdate(message), bot_username=BOT_USERNAME, bot_user_id=BOT_ID,
                               owner_ref={"app_user_id": 1, "telegram_user_id": OWNER_TELEGRAM_ID})
    return None if result is None else result.turn


@pytest.fixture(autouse=True)
def switch_on(monkeypatch):
    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: True)


def _mention(text, word, kind="mention", user=None):
    i = text.index(word)
    return NS(type=kind, offset=i, length=len(word), user=user)


# ── mentions ──────────────────────────────────────────────────────────────────

def test_an_at_mention_of_someone_else_is_a_signal():
    text = "@sam_x did you see this?"
    t = _turn(_group_msg(text, entities=[_mention(text, "@sam_x")]))
    assert t.metadata.extra["mentions_others"] == ["@sam_x"]
    assert "it @mentions someone else (@sam_x), not her" in cadence.render_signals(cadence.signals_of(t))
    assert {"id": "", "name": "@sam_x", "how": "mention"} in t.metadata.speaker["to"]


def test_a_text_mention_names_the_person_and_their_account():
    text = "Sam look"
    sam = FakeUser(555, username=None, full_name="Sam")
    t = _turn(_group_msg(text, entities=[_mention(text, "Sam", "text_mention", sam)]))
    assert t.metadata.extra["mentions_others"] == ["Sam"]
    assert {"id": "555", "name": "Sam", "how": "mention"} in t.metadata.speaker["to"]


def test_her_own_mention_is_not_someone_else():
    text = f"@{BOT_USERNAME} hi"
    t = _turn(_group_msg(text, entities=[_mention(text, f"@{BOT_USERNAME}")]))
    assert "mentions_others" not in t.metadata.extra


def test_switch_off_adds_no_mention_signal(monkeypatch):
    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: False)
    text = f"@{BOT_USERNAME} @sam_x hi"
    t = _turn(_group_msg(text, entities=[_mention(text, f"@{BOT_USERNAME}"),
                                         _mention(text, "@sam_x")]))
    assert "mentions_others" not in t.metadata.extra


def test_a_reply_names_who_it_answers():
    m = _group_msg("yes")
    m.reply_to_message = NS(from_user=FakeUser(666, username="alex_y", full_name="Alex"),
                            text="ok?", date=None)
    t = _turn(m)
    assert {"id": "666", "name": "Alex", "how": "reply"} in t.metadata.speaker["to"]


# ── forwards: the four kinds ──────────────────────────────────────────────────

@pytest.mark.parametrize("origin, line", [
    (NS(type="user", sender_user=FakeUser(5, username="sam_x", full_name="Sam")),
     "(forwarded from Sam)"),
    (NS(type="hidden_user", sender_user_name="Sam R"),
     '(forwarded from "Sam R"; Telegram doesn\'t show their account)'),
    (NS(type="chat", sender_chat=NS(id=-5, title="Book club", type="group")),
     '(forwarded from the group "Book club")'),
    (NS(type="channel", chat=NS(id=-6, title="News", type="channel")),
     '(forwarded from the channel "News")'),
])
def test_a_forward_says_where_it_came_from(origin, line):
    t = _turn(_group_msg("the shelf is oak", forward_origin=origin))
    body = t.content.split("]: ", 1)[1]                      # a guest's line keeps its tag
    assert body.startswith(line + "\n") and body.endswith("the shelf is oak")


def test_a_forward_saying_her_name_is_not_her_name_said():
    origin = NS(type="hidden_user", sender_user_name="X")
    t = _turn(_group_msg("palli is great", forward_origin=origin))
    assert t.metadata.extra.get("names_her") is False


# ── anonymous admins and channels ─────────────────────────────────────────────

ANON_BOT = FakeUser(1087968824, username="GroupAnonymousBot", full_name="Group", is_bot=True)


def test_an_anonymous_admin_is_a_guest_named_for_what_it_is():
    m = _group_msg("meeting at 6", author=ANON_BOT, sender_chat=NS(id=GROUP, title="Family", type="supergroup"))
    t = _turn(m)
    assert t is not None and t.metadata.origin == "guest"
    assert t.metadata.speaker["kind"] == "hidden" and t.metadata.speaker["name"] == "an anonymous admin"
    assert t.content == "[an anonymous admin]: meeting at 6"


def test_a_channel_posting_in_its_group_is_named():
    m = _group_msg("new post", author=FakeUser(136817688, username="Channel_Bot", full_name="Channel",
                                               is_bot=True),
                   sender_chat=NS(id=-777, title="News", type="channel"))
    t = _turn(m)
    assert t.metadata.speaker["kind"] == "hidden"
    assert t.metadata.speaker["name"] == 'the channel "News"'


def test_another_bot_stays_invisible():
    assert _turn(_group_msg("beep", author=FakeUser(31337, username="other_bot", full_name="Other",
                                                    is_bot=True))) is None


# The owner's own id behind a hidden sender: tests/bots/test_fable_final_review.py
# (the version here could not fail: Fable's final review, 5).


# ── the privacy setting ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value, said", [(False, True), (True, False), (None, None)])
def test_the_privacy_reading(value, said):
    from bots.telegram.client import privacy_mode_from

    me = NS(id=BOT_ID, username=BOT_USERNAME)
    if value is not None:
        me.can_read_all_group_messages = value
    assert privacy_mode_from(me) is said


def test_the_room_card_names_privacy_with_the_admin_exception():
    from core.agent import room_card

    room_card.reset_for_tests()
    try:
        room_card.set_telegram_privacy(True)
        t = _turn(_group_msg("hi"))
        card = room_card.room_section(t, [])
        assert ("Telegram's privacy setting is on for me, so unless I'm an admin here I only "
                "see messages that mention me, reply to me, or start with a slash command.") in card
    finally:
        room_card.reset_for_tests()


# ── /start in a group ─────────────────────────────────────────────────────────

def test_start_in_a_group_tells_the_truth_both_ways(monkeypatch):
    from bots.telegram.commands import _START_DM, _start_text
    from core.agent import room_card

    client = NS(_bot_username=BOT_USERNAME)
    on = _start_text(client, "supergroup")
    assert "read along" in on and f"@{BOT_USERNAME}" in on and "reply" in on
    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: False)
    off = _start_text(client, "supergroup")
    assert off.startswith("I'm PaLLi. In a group I only answer when you") and "reply" in off
    assert _start_text(client, "private") is _START_DM
    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: True)
    room_card.reset_for_tests()
    room_card.set_telegram_privacy(True)
    try:
        assert _start_text(client, "group") == off              # privacy on: the old words are true
    finally:
        room_card.reset_for_tests()


# ── who's here ────────────────────────────────────────────────────────────────

class _Bot:
    def __init__(self, admins, count, *, fail=False):
        self.admins, self.count, self.fail = admins, count, fail

    async def get_chat_administrators(self, chat_id):
        if self.fail:
            raise RuntimeError("telegram is down")
        return self.admins

    async def get_chat_member_count(self, chat_id):
        return self.count


def _ctx(channel_id=GROUP):
    from core.agent.tools import ToolContext
    return ToolContext(turn_owner_id=1, is_owner=True, surface="telegram_text", channel_id=channel_id)


def _list(bot, monkeypatch, channel_id=GROUP):
    import core.agent.hands_runtime as hr
    from core.agent.builtin_tools.her_hands import build_her_hands_tools

    async def refs():
        return {"app_user_id": 1, "discord_user_id": None, "telegram_user_id": OWNER_TELEGRAM_ID}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", refs)
    hr.register_telegram_bot(bot)
    try:
        tool = {t.name: t for t in build_her_hands_tools()}["list_members"]
        return asyncio.run(tool.execute({}, _ctx(channel_id)))
    finally:
        hr.register_telegram_bot(None)


def test_whos_here_on_telegram_lists_admins_owner_first(monkeypatch):
    admins = [NS(user=FakeUser(5, username="sam_x", full_name="Sam")),
              NS(user=FakeUser(OWNER_TELEGRAM_ID, username="robin_tg", full_name="Robin"))]
    out = _list(_Bot(admins, 12), monkeypatch)
    lines = out.splitlines()
    assert lines[0] == "This group has 12 members. Telegram only lets me list its admins:"
    assert lines[1].startswith("- Robin (@robin_tg") and lines[1].endswith("— the owner")
    assert "Sam (@sam_x" in out
    assert "Who has spoken lately is in my room card." in out


def test_whos_here_says_plainly_when_telegram_isnt_there(monkeypatch):
    out = _list(None, monkeypatch)
    assert out == "Telegram isn't connected right now, so I can't see anyone there."
    assert _list(_Bot([], 0, fail=True), monkeypatch) == (
        "Telegram didn't answer just now, so I can't see who's here.")


def test_whos_here_in_a_telegram_dm(monkeypatch):
    out = _list(_Bot([], 2), monkeypatch, channel_id=OWNER_TELEGRAM_ID)
    assert out == "This is a direct message: just the two of us here."


# ── one name for a guest (Fable's checkpoint review, 8; 2026-10-13) ──────────
# The tag on a guest's line and their record both use full_name, the name the
# group shows; the account name stays the handle. Rows stored before, tagged
# with the username, still read under the shown name.

def test_a_guests_tag_and_record_name_them_as_the_group_shows_them():
    from core.agent.session import AgentMessage, TextBlock
    from core.agent.speakers import label_speakers_for_llm

    t = _turn(_group_msg("hi all", author=FakeUser(STRANGER_ID, username="sam_x", full_name="Sam")))
    assert t.content == "[Sam]: hi all"
    assert (t.metadata.speaker["name"], t.metadata.speaker["handle"]) == ("Sam", "sam_x")
    assert t.metadata.extra["telegram_username"] == "sam_x"      # the handle, as before
    rows = [AgentMessage(role="user", content=[TextBlock(text=text)], speaker=t.metadata.speaker)
            for text in ("[sam_x]: stored before", t.content)]
    assert [m.content[0].text for m in label_speakers_for_llm(rows)] == [
        "[Sam]: stored before", "[Sam]: hi all"]


def test_a_guest_with_only_a_username_is_tagged_with_it():
    t = _turn(_group_msg("hi", author=FakeUser(STRANGER_ID, username="sam_x", full_name=None)))
    assert t.content == "[sam_x]: hi"
