"""The proactive lane learns to look across doors (telegram door, task 1).

When she decides to say something of her own, she has to pick WHERE. The
pick has been Discord-only: the most recent Discord channel the owner used.
With a second door open, the honest answer is the most recent conversation
he had ANYWHERE — never a random room (the 2026-05-06 law: no eligible
surface means she does not send, it does not mean she guesses).

The Discord-only wrapper stays, because the arbiter and two existing tests
call it by name and patch it.

Since 2026-10-13 (the owner's rule) the pick is "the newest PRIVATE chat with
him": a Discord DM or a Telegram private chat, never a server channel or a
Telegram group. On 10-12 at 08:10 a note meant for him went to a Discord
server channel his friends could see, because the newest chat he had typed in
was that channel. A chat whose privacy cannot be shown is never picked.
"""
from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The marks core/turn/persistence.py writes on his rows since 10-13: whether
# the chat it came from is a private chat with him.
PRIVATE = '{"is_dm": true}'
GROUP = '{"is_dm": false}'


@pytest.fixture(autouse=True)
def _no_doors_registered(monkeypatch):
    """No door is up unless a test says so: an old row's privacy is asked of
    the door it came from (core.channel_runtime), and a door another test left
    registered must not answer here."""
    from core.channel_runtime import channel_runtime

    monkeypatch.setattr(channel_runtime, "_adapters", {})


def _db(tmp_path, rows):
    """A conversations table of his own words (role user). Each row is
    ``(platform, channel_id, timestamp)`` — one of his private chats, marked
    the way persistence marks it — or ``(platform, channel_id, timestamp,
    metadata)`` with the row's own metadata (None: a row written before the
    mark). The pick reads metadata for the mark and to skip spoken rows."""
    db_path = tmp_path / "conversations.sqlite3"
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER, platform TEXT, channel_id INTEGER,
            timestamp REAL, role TEXT, content TEXT, metadata TEXT
        )
        """
    )
    for row in rows:
        platform, channel_id, ts = row[:3]
        metadata = row[3] if len(row) > 3 else PRIVATE
        con.execute(
            "INSERT INTO conversations "
            "(user_id, platform, channel_id, timestamp, role, content, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, platform, channel_id, ts, "user", "said something", metadata),
        )
    con.commit()
    con.close()
    return db_path


def _cfg(db_path, *, allowed_channels=(), allowed_guilds=()):
    cfg = MagicMock()
    cfg.DATABASE_PATH = str(db_path)
    cfg.PROACTIVE_ALLOWED_CHANNEL_IDS = list(allowed_channels)
    cfg.PROACTIVE_ALLOWED_GUILD_IDS = list(allowed_guilds)
    return cfg


class _Door:
    """A Discord door that can say whether a chat is his DM with her
    (``is_owner_private_chat``, the question core.channel_runtime asks)."""

    def __init__(self, answer):
        self.answer = answer
        self.asked: list[str] = []

    async def send_envelope(self, *, surface_id, envelope):
        return {"sent": True}

    async def is_owner_private_chat(self, surface_id):
        self.asked.append(str(surface_id))
        result = self.answer(str(surface_id))
        if asyncio.iscoroutine(result):
            result = await result
        return result


def _door_says(monkeypatch, answer, platform="discord"):
    from core.channel_runtime import channel_runtime

    door = _Door(answer)
    monkeypatch.setitem(channel_runtime._adapters, platform, door)
    return door


# ── the cross-door pick ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_newer_telegram_chat_wins(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        picked = await proactive._pick_proactive_surface(owner_id=1)
    assert picked == ("telegram", "424242")


@pytest.mark.asyncio
async def test_the_newer_discord_channel_wins(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 2000.0), ("telegram", 424242, 1000.0)])
    with patch("core.config.config", _cfg(db_path)):
        picked = await proactive._pick_proactive_surface(owner_id=1)
    assert picked == ("discord", "111")


@pytest.mark.asyncio
async def test_no_conversation_anywhere_means_none_never_a_guess(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) is None


@pytest.mark.asyncio
async def test_a_door_he_has_never_used_is_not_picked(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("telegram", 424242, 1000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_the_caller_may_narrow_the_doors(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        picked = await proactive._pick_proactive_surface(
            owner_id=1, platforms=("discord",),
        )
    assert picked == ("discord", "111")


@pytest.mark.asyncio
async def test_the_channel_allowlist_still_binds_discord_rows(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("discord", 999, 2000.0)])
    with patch("core.config.config", _cfg(db_path, allowed_channels=[111])):
        picked = await proactive._pick_proactive_surface(owner_id=1)
    assert picked == ("discord", "111")


@pytest.mark.asyncio
async def test_the_channel_allowlist_does_not_bind_the_other_door(tmp_path):
    """PROACTIVE_ALLOWED_CHANNEL_IDS is a list of Discord channel ids; a
    Telegram chat is not in it and was never meant to be judged by it."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path, allowed_channels=[111])):
        picked = await proactive._pick_proactive_surface(owner_id=1)
    assert picked == ("telegram", "424242")



# ── only his own words in a text chat count (fix plan 5.8, round 1) ─────

@pytest.mark.asyncio
async def test_her_rows_and_his_spoken_rows_never_win_and_bad_metadata_is_not_fatal(
        tmp_path, monkeypatch):
    """Her send filed under his id (role assistant) and his voice turns
    (marked in metadata) are skipped. A row whose metadata is not valid JSON
    is still his words in a chat: it carries no mark, so it counts when the
    door shows the chat is private, and it does not break the query for the
    others. The door here calls every chat private, so only the old rules
    keep the other rows out."""
    from core.turn import proactive

    _door_says(monkeypatch, lambda surface_id: True)
    db_path = _db(tmp_path, [])
    con = sqlite3.connect(str(db_path))
    for channel_id, ts, role, metadata in [
        (111, 1000.0, "user", "not json at all"),
        (999, 2000.0, "assistant", None),
        (777, 3000.0, "user", '{"surface": "discord_voice"}'),
        (778, 4000.0, "user", '{"voice_session": true, "voice_message": true}'),
        (779, 5000.0, "system", None),
    ]:
        con.execute(
            "INSERT INTO conversations "
            "(user_id, platform, channel_id, timestamp, role, content, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "discord", channel_id, ts, role, "x", metadata),
        )
    con.commit()
    con.close()
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")
        assert await proactive._pick_proactive_channel_id(owner_id=1) == 111


# ── only a private chat with him (the owner, 10-13) ─────────────────────

@pytest.mark.asyncio
async def test_a_newer_server_channel_never_beats_his_older_dm(tmp_path):
    """The 10-12 08:10 case: he typed in a server channel after his DM, and
    her note to him went to the channel."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, PRIVATE),
                             ("discord", 900, 2000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")


@pytest.mark.asyncio
async def test_a_newer_telegram_group_never_beats_his_older_private_chat(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("telegram", 424242, 1000.0, PRIVATE),
                             ("telegram", -100555, 2000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_the_newest_private_chat_wins_across_the_doors(tmp_path):
    """Today's "newest" rule, kept, over his private chats only."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, PRIVATE),
                             ("telegram", 424242, 2000.0, PRIVATE),
                             ("discord", 900, 3000.0, GROUP),
                             ("telegram", -100555, 4000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_only_group_chats_means_none_and_nothing_is_sent(tmp_path):
    """No private chat is the honest "no door": she does not send, and she
    never falls back to a group."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 900, 1000.0, GROUP),
                             ("telegram", -100555, 2000.0, GROUP),
                             ("telegram", -4242, 3000.0, None)])
    send = AsyncMock(return_value={"sent": True})
    with patch("core.config.config", _cfg(db_path)), patch(
        "core.channel_runtime.channel_runtime.send_envelope", send
    ):
        assert await proactive._pick_proactive_surface(owner_id=1) is None
        assert await proactive._pick_proactive_channel_id(owner_id=1) is None
        await proactive._deliver_proactive(owner_id=1, result=_result())
        await proactive._deliver_proactive_to_discord(owner_id=1, result=_result())
    send.assert_not_called()


@pytest.mark.asyncio
async def test_a_telegram_group_is_never_private_whatever_its_row_says(tmp_path):
    """Telegram numbers a private chat by the person's own (positive) user id
    and a group or supergroup below zero, so a negative chat is a group even
    under a mark that says otherwise."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("telegram", -100555, 2000.0, PRIVATE)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) is None


@pytest.mark.asyncio
async def test_a_chat_any_of_whose_rows_says_group_is_a_group(tmp_path):
    """A chat's rows that disagree prove nothing: it is not shown private."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 900, 1000.0, PRIVATE),
                             ("discord", 900, 2000.0, GROUP),
                             ("discord", 901, 3000.0, '{"is_dm": "yes"}')])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) is None


@pytest.mark.asyncio
async def test_the_allowlist_narrows_but_never_admits_a_group(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, PRIVATE),
                             ("discord", 900, 2000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path, allowed_channels=[900])):
        assert await proactive._pick_proactive_surface(owner_id=1) is None
    with patch("core.config.config", _cfg(db_path, allowed_channels=[111, 900])):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")


@pytest.mark.asyncio
async def test_the_discord_wrapper_returns_his_dm_not_the_newer_server_channel(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, PRIVATE),
                             ("discord", 900, 2000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_channel_id(owner_id=1) == 111


# ── rows written before the mark (10-13) ────────────────────────────────

@pytest.mark.asyncio
async def test_an_old_telegram_row_is_shown_private_by_its_positive_id(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("telegram", 424242, 1000.0, None),
                             ("telegram", -100555, 2000.0, None)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_an_old_discord_row_is_never_picked_when_no_door_can_say(tmp_path):
    """Nothing in an old Discord row tells a DM from a server channel. With
    no Discord door up to ask, it is not shown private, so it is not picked."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, None)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) is None


@pytest.mark.asyncio
async def test_after_a_restart_the_door_finds_his_dm_among_rows_from_before_the_mark(
        tmp_path, monkeypatch):
    """Right after the restart that brings the mark in, every row he wrote is
    unmarked. The Discord door is asked about each unmarked chat, newest
    first; a chat already marked is never asked about."""
    from core.turn import proactive

    door = _door_says(monkeypatch, lambda surface_id: surface_id == "111")
    db_path = _db(tmp_path, [("discord", 111, 1000.0, None),
                             ("discord", 900, 2000.0, None),
                             ("discord", 901, 3000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")
    assert door.asked == ["900", "111"]


@pytest.mark.asyncio
async def test_a_marked_dm_needs_no_door(tmp_path, monkeypatch):
    from core.turn import proactive

    def _never(surface_id):
        raise AssertionError("a marked chat is never asked about")

    _door_says(monkeypatch, _never)
    db_path = _db(tmp_path, [("discord", 111, 1000.0, PRIVATE)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")


@pytest.mark.asyncio
async def test_an_unmarked_chat_older_than_the_first_private_chat_is_never_asked_about(
        tmp_path, monkeypatch):
    """The walk stops at the newest private chat: an older unmarked Discord
    chat never costs a door check."""
    from core.turn import proactive

    def _never(surface_id):
        raise AssertionError("an older chat is never asked about")

    door = _door_says(monkeypatch, _never)
    db_path = _db(tmp_path, [("discord", 800, 1000.0, None),
                             ("discord", 111, 2000.0, PRIVATE)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")
    assert door.asked == []


@pytest.mark.asyncio
async def test_a_chat_marked_both_private_and_not_is_warned_about_once(
        tmp_path, monkeypatch, caplog):
    """Its own rows disagree: it is not picked (a group, fail closed), and
    the disagreement is logged as a WARNING once per chat per process, not
    on every pick."""
    import logging

    from core import doors
    from core.turn import proactive

    monkeypatch.setattr(doors, "_MIXED_MARKS_WARNED", set())
    db_path = _db(tmp_path, [("discord", 900, 1000.0, PRIVATE),
                             ("discord", 900, 2000.0, GROUP),
                             ("discord", 111, 500.0, PRIVATE)])
    with caplog.at_level(logging.WARNING, logger="core.doors"), \
            patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "900" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_a_door_that_fails_hangs_or_hedges_shows_nothing(tmp_path, monkeypatch):
    import core.channel_runtime as runtime_mod
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0, None)])

    def _boom(surface_id):
        raise RuntimeError("discord down")

    async def _hang(surface_id):
        await asyncio.sleep(5)
        return True

    monkeypatch.setattr(runtime_mod, "OWNER_PRIVATE_CHAT_TIMEOUT_S", 0.05)
    for answer in (_boom, _hang, lambda surface_id: "yes", lambda surface_id: None):
        _door_says(monkeypatch, answer)
        with patch("core.config.config", _cfg(db_path)):
            assert await proactive._pick_proactive_surface(owner_id=1) is None


# ── the Discord-only wrapper (the pin) ──────────────────────────────────

@pytest.mark.asyncio
async def test_the_discord_wrapper_still_returns_an_int(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("discord", 222, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_channel_id(owner_id=1) == 222


@pytest.mark.asyncio
async def test_a_newer_telegram_chat_never_changes_the_discord_wrapper(tmp_path):
    """ai/arbiter.py calls the wrapper for a Discord channel id. A Telegram
    row must never come back through it."""
    from core.turn import proactive

    db_path = _db(tmp_path, [("discord", 222, 1000.0), ("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_channel_id(owner_id=1) == 222


@pytest.mark.asyncio
async def test_the_discord_wrapper_returns_none_with_no_discord_history(tmp_path):
    from core.turn import proactive

    db_path = _db(tmp_path, [("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_channel_id(owner_id=1) is None


# ── delivery ────────────────────────────────────────────────────────────

def _result(text="Hey, just thinking about you"):
    result = MagicMock()
    result.responded = True
    result.response_text = text
    result.attachments = ()
    return result


@pytest.mark.asyncio
async def test_delivery_sends_on_the_picked_door(tmp_path):
    from core.turn import proactive

    send = AsyncMock(return_value={"sent": True})
    with patch.object(
        proactive, "_pick_proactive_surface",
        AsyncMock(return_value=("telegram", "424242")),
    ), patch("core.channel_runtime.channel_runtime.send_envelope", send):
        await proactive._deliver_proactive(owner_id=1, result=_result())

    send.assert_awaited_once()
    assert send.await_args.kwargs["platform"] == "telegram"
    assert send.await_args.kwargs["surface_id"] == "424242"


@pytest.mark.asyncio
async def test_delivery_still_sends_on_discord_when_discord_is_picked(tmp_path):
    from core.turn import proactive

    send = AsyncMock(return_value={"sent": True})
    with patch.object(
        proactive, "_pick_proactive_surface",
        AsyncMock(return_value=("discord", "222")),
    ), patch("core.channel_runtime.channel_runtime.send_envelope", send):
        await proactive._deliver_proactive(owner_id=1, result=_result())

    assert send.await_args.kwargs["platform"] == "discord"
    assert send.await_args.kwargs["surface_id"] == "222"


@pytest.mark.asyncio
async def test_a_caller_that_already_picked_is_obeyed(tmp_path):
    from core.turn import proactive

    send = AsyncMock(return_value={"sent": True})
    pick = AsyncMock()
    with patch.object(proactive, "_pick_proactive_surface", pick), patch(
        "core.channel_runtime.channel_runtime.send_envelope", send
    ):
        await proactive._deliver_proactive(
            owner_id=1, result=_result(), platform="telegram", surface_id="777",
        )

    pick.assert_not_awaited()
    assert send.await_args.kwargs["surface_id"] == "777"


@pytest.mark.asyncio
async def test_nothing_is_sent_when_no_door_is_eligible(tmp_path):
    from core.turn import proactive

    send = AsyncMock()
    with patch.object(
        proactive, "_pick_proactive_surface", AsyncMock(return_value=None),
    ), patch("core.channel_runtime.channel_runtime.send_envelope", send):
        await proactive._deliver_proactive(owner_id=1, result=_result())

    send.assert_not_called()


@pytest.mark.asyncio
async def test_an_empty_reply_is_never_sent(tmp_path):
    from core.turn import proactive

    send = AsyncMock()
    with patch.object(
        proactive, "_pick_proactive_surface",
        AsyncMock(return_value=("telegram", "424242")),
    ), patch("core.channel_runtime.channel_runtime.send_envelope", send):
        await proactive._deliver_proactive(owner_id=1, result=_result("   "))

    send.assert_not_called()


@pytest.mark.asyncio
async def test_the_discord_delivery_wrapper_still_asks_the_discord_pick(tmp_path):
    """The pin tests/turn/test_proactive_delivery.py owns: the wrapper reads
    ``_pick_proactive_channel_id`` as a module attribute, so a patch takes."""
    from core.turn import proactive

    send = AsyncMock(return_value={"sent": True})
    with patch.object(
        proactive, "_pick_proactive_channel_id", AsyncMock(return_value=222),
    ), patch("core.channel_runtime.channel_runtime.send_envelope", send):
        await proactive._deliver_proactive_to_discord(owner_id=1, result=_result())

    assert send.await_args.kwargs["platform"] == "discord"
    assert send.await_args.kwargs["surface_id"] == "222"
