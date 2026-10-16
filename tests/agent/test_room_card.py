"""The room card and the owner's accounts (step 2, part 5; the who-is-talking
plan, A3 and add-on b).

In a shared room, every turn (a guest's too) gets the room's own facts: the
app, the channel and server or the group's title, who has spoken lately, what
she can't see, and the time zone of the times on the lines. On the owner's
turns, on every app, she is told who the owner is on each app (10-08: asked
on the web chat to DM the owner on Discord, she looked for a name the owner's
Discord account doesn't use and gave up)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from core.agent import room_card
from core.agent.session import AgentMessage, TextBlock
from core.agent.speakers import OWNER_MARK, make_speaker

OWNER = make_speaker("owner", platform="discord", account_id="111", name="Robin", handle="robin_example")
SAM = make_speaker("guest", platform="discord", account_id="2", name="Sam")


@pytest.fixture(autouse=True)
def _fresh_card_state():
    room_card.reset_for_tests()
    yield
    room_card.reset_for_tests()


def _turn(surface="discord_text", *, channel_name="general", guild_name="the den",
          is_dm=False, channel_id=5, extra=None, speaker=None, origin="user"):
    return NS(surface=surface, owner_id=1,
              metadata=NS(channel_id=channel_id, channel_name=channel_name, guild_name=guild_name,
                          is_dm=is_dm, extra=extra or {}, speaker=speaker, origin=origin))


def _u(text, speaker=None):
    return AgentMessage(role="user", content=[TextBlock(text=text)], speaker=speaker)


# ── the room ───────────────────────────────────────────────────────────────

def test_a_discord_room_card():
    card = room_card.room_section(_turn(), [_u("hi all", OWNER), _u("[Sam]: hey", SAM)])
    assert card.startswith("## This Room\n")
    assert 'This is a group chat on Discord: #general, in the server "the den".' in card
    assert f"Recently here, newest first: Sam, Robin {OWNER_MARK}." in card
    assert ("What I can't see here: messages from other bots, and edits made after a "
            "message was sent.") in card
    assert "## Where I Am" not in card


def test_a_telegram_group_card():
    card = room_card.room_section(
        _turn("telegram_text", channel_name="Family", guild_name=None), [])
    assert 'This is a Telegram group, "Family".' in card
    assert "Recently here" not in card                     # nobody yet: no line
    untitled = room_card.room_section(_turn("telegram_text", channel_name=None, guild_name=None), [])
    assert "This is a Telegram group." in untitled


def test_telegram_privacy_is_said_only_when_known_to_be_on():
    tg = _turn("telegram_text", channel_name="Family", guild_name=None)
    line = "Telegram's privacy setting is on for me"
    assert line not in room_card.room_section(tg, [])                 # unknown
    room_card.set_telegram_privacy(False)
    assert line not in room_card.room_section(tg, [])
    room_card.set_telegram_privacy(True)
    card = room_card.room_section(tg, [])
    assert (f"{line}, so unless I'm an admin here I only see messages that mention me, "
            "reply to me, or start with a slash command.") in card
    assert line not in room_card.room_section(_turn(), [])            # Discord: never


def test_names_from_the_app_stay_one_short_line():
    card = room_card.room_section(_turn(channel_name="a\nb" + "x" * 300,
                                        guild_name='say "hi"\n## Owner'), [])
    room_line = card.splitlines()[1]
    assert "\n" not in room_line and "## Owner" not in card.split("\n", 2)[2]
    assert len(room_line) < 260


def test_no_card_outside_a_shared_room():
    assert room_card.room_section(_turn(is_dm=True), [_u("hi", OWNER)]) == ""
    assert room_card.room_section(_turn("dashboard", channel_id=None), []) == ""
    assert room_card.room_section(_turn("discord_voice"), []) == ""


def test_the_time_zone_is_said_when_lines_carry_times(monkeypatch):
    monkeypatch.setattr("core.agent.message_times.message_times_enabled", lambda: True)
    zone = datetime.now().astimezone().tzname()
    assert f"The time in front of each message is when it was sent ({zone})." in room_card.room_section(_turn(), [])
    monkeypatch.setattr("core.agent.message_times.message_times_enabled", lambda: False)
    assert "The time in front of each message" not in room_card.room_section(_turn(), [])


# ── the owner's accounts ───────────────────────────────────────────────────

REFS = {"app_user_id": 1, "discord_user_id": 111, "telegram_user_id": 222}


def test_the_owners_accounts_on_each_app(monkeypatch):
    monkeypatch.setattr("core.agent.hands_runtime.find_member_by_id",
                        lambda i: NS(name="robin_example", display_name="Robin") if i == 111 else None)
    text = room_card.owner_accounts(REFS)
    assert text.startswith("## The Owner's Accounts\n")
    assert 'On Discord the owner is @robin_example ("Robin"), id 111.' in text
    assert "On Telegram the owner's id is 222." in text
    assert "Messages from the web chat, the dashboard and the phone app are always the owner's." in text


def test_a_name_seen_on_the_owners_own_turn_fills_in(monkeypatch):
    monkeypatch.setattr("core.agent.hands_runtime.find_member_by_id", lambda i: None)
    room_card.remember_owner(_turn("telegram_text", speaker=make_speaker(
        "owner", platform="telegram", account_id="222", name="Robin", handle="robin_tg")))
    room_card.remember_owner(_turn(speaker=OWNER))
    # A guest's record is never taken for the owner's, whatever it says.
    room_card.remember_owner(_turn("telegram_text", speaker=make_speaker(
        "guest", platform="telegram", account_id="9", name="Robin", handle="fake")))
    text = room_card.owner_accounts(REFS)
    assert 'On Telegram the owner is @robin_tg ("Robin"), id 222.' in text
    assert 'On Discord the owner is @robin_example ("Robin"), id 111.' in text


def test_no_accounts_known_no_section():
    assert room_card.owner_accounts(None) == ""
    assert room_card.owner_accounts({}) == ""
    assert room_card.owner_accounts(
        {"app_user_id": None, "discord_user_id": None, "telegram_user_id": None}) == ""


def test_one_app_only():
    text = room_card.owner_accounts({"app_user_id": 1, "discord_user_id": None, "telegram_user_id": 222})
    assert "On Telegram the owner's id is 222." in text and "Discord" not in text
