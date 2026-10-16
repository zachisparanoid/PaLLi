"""Batch 4 task 5 (fix plan 5.10, rulings R9/R9d/R9e): Discord records a reply
as delivered only as far as it was.

The send functions used to swallow every failure and return None, so the
door could never know a reply had died — her record (written before the
send) said it was said. Now both return whether its first part landed (the
Telegram door's meaning), ``last_send_parts()`` says how many of how many
did, the reason rides back through ``last_send_error()``, ``turn_failed`` is
still felt exactly once, and the door marks her record: not delivered, or
only partly delivered.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_REAL_SLEEP = asyncio.sleep


class HTTPException(Exception):
    """The shape discord.HTTPException has, under its own name: an HTTP
    status and Discord's own error code. The message body is his reply's
    context and must never become the reason."""

    def __init__(self, status: int, code: int, text: str = "Missing Permissions"):
        super().__init__(f"{status} {text} (error code: {code})")
        self.status = status
        self.code = code
        self.text = text


def _channel(*, fail_on: int | None = None, exc: Exception | None = None):
    """A discord.Channel-like mock; raises on the ``fail_on``-th send (1-based)
    and on every send after it. __aexit__ returns False so the exception
    propagates out of typing()."""
    channel = MagicMock()
    channel.id = 555
    sent: list[str] = []
    calls = {"n": 0}

    async def send(*args, **kwargs):
        calls["n"] += 1
        if fail_on is not None and calls["n"] >= fail_on:
            raise exc or HTTPException(403, 50013)
        sent.append(kwargs.get("content") or (args[0] if args else ""))

    channel.send = AsyncMock(side_effect=send)
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    channel.sent = sent
    return channel


class _Beat:
    def __init__(self, text: str) -> None:
        self.text = text
        self.delay_after_previous_seconds = 0.0


class _Cadenced:
    def __init__(self, *texts: str) -> None:
        self.beats = [_Beat(t) for t in texts]
        self.attachments = ()


class _AsyncioWithoutSleep:
    """reply.py's own ``asyncio`` name, with sleep taken out and everything
    else passed through. Only that module's reference is replaced; the
    global asyncio.sleep is untouched."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(seconds, *a, **kw):
        await _REAL_SLEEP(0)


@pytest.fixture
def reply(monkeypatch):
    from bots.discord import reply as r

    monkeypatch.setattr(r, "_calculate_typing_delay", lambda t: 0.0)
    monkeypatch.setattr(r, "asyncio", _AsyncioWithoutSleep())
    fired: list[dict] = []
    monkeypatch.setattr(r, "_delivery_notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    monkeypatch.setattr(r, "fired", fired, raising=False)
    return r


def _turn_failed(fired):
    return [n for n in fired if n["kind"] == "turn_failed"]


# ── the send reports what happened ────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_send_that_dies_on_its_first_part_returns_false_names_the_reason_and_is_felt_once(reply):
    channel = _channel(fail_on=1)

    delivered = await reply.send_turn_reply(channel, "the plan is X")

    assert delivered is False                                     # nothing landed
    assert reply.last_send_parts() == (0, 1)
    assert reply.last_send_error() == "HTTPException 50013"       # class + code, never the body
    assert len(_turn_failed(reply.fired)) == 1
    assert _turn_failed(reply.fired)[0]["stage"] == "discord_send"


@pytest.mark.asyncio
async def test_a_landed_send_returns_true_and_clears_the_reason(reply):
    await reply.send_turn_reply(_channel(fail_on=1), "dies")       # leave a stale reason behind
    channel = _channel()

    delivered = await reply.send_turn_reply(channel, "the plan is X")

    assert delivered is True
    assert channel.sent == ["the plan is X"]
    assert reply.last_send_parts() == (1, 1)
    assert reply.last_send_error() == ""


@pytest.mark.asyncio
async def test_a_reply_that_dies_on_a_later_chunk_counts_as_delivered_and_says_how_much(reply):
    channel = _channel(fail_on=2)

    delivered = await reply.send_turn_reply(channel, "a" * 2000 + "b" * 10)

    assert delivered is True                      # R9d: the first part landed (Telegram's meaning)
    assert reply.last_send_parts() == (1, 2)
    assert reply.last_send_error() == "HTTPException 50013"
    assert len(_turn_failed(reply.fired)) == 1


@pytest.mark.asyncio
async def test_an_exception_without_a_code_is_named_by_class_alone(reply):
    delivered = await reply.send_turn_reply(_channel(fail_on=1, exc=RuntimeError("gateway gone")), "hi")

    assert delivered is False
    assert reply.last_send_error() == "RuntimeError"


@pytest.mark.asyncio
async def test_nothing_to_send_is_not_a_delivery_and_not_a_failure(reply):
    assert await reply.send_turn_reply(_channel(), "   ") is False
    assert reply.last_send_parts() == (0, 0)
    assert _turn_failed(reply.fired) == []


@pytest.mark.asyncio
async def test_the_cadenced_path_reports_too(reply):
    later = _channel(fail_on=2)
    assert await reply.send_turn_reply_cadenced(later, _Cadenced("one", "two", "three")) is True
    assert reply.last_send_parts() == (1, 3)
    assert reply.last_send_error() == "HTTPException 50013"
    assert len(_turn_failed(reply.fired)) == 1

    dead = _channel(fail_on=1)
    assert await reply.send_turn_reply_cadenced(dead, _Cadenced("one", "two")) is False
    assert reply.last_send_parts() == (0, 2)

    ok = _channel()
    assert await reply.send_turn_reply_cadenced(ok, _Cadenced("one", "two")) is True
    assert ok.sent == ["one", "two"]
    assert reply.last_send_parts() == (2, 2)
    assert reply.last_send_error() == ""

    single = _channel(fail_on=1)
    assert await reply.send_turn_reply_cadenced(single, _Cadenced("only")) is False
    assert await reply.send_turn_reply_cadenced(_channel(), None) is False
    assert reply.last_send_parts() == (0, 0)


# ── the door marks her record ─────────────────────────────────────────────
def _client(monkeypatch, tmp_path, channel):
    import discord

    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    client.get_channel = lambda cid: channel
    return client


def _plain(text):
    return SimpleNamespace(responded=True, response_text=text, attachments=(), cadenced_reply=None)


@pytest.mark.asyncio
async def test_deliver_result_marks_a_reply_that_never_landed(reply, monkeypatch, tmp_path):
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    client = _client(monkeypatch, tmp_path, _channel(fail_on=1))

    await client._deliver_result(555, _plain("the plan is X"))

    persisted.assert_awaited_once_with(
        owner_id=1, platform="discord", channel_id=555, reason="HTTPException 50013",
        failed_text="the plan is X", landed=0, total=1, sessions_root=client._sessions_root)
    assert len(_turn_failed(reply.fired)) == 1                     # still felt exactly once


@pytest.mark.asyncio
async def test_deliver_result_marks_a_reply_that_only_partly_landed(reply, monkeypatch, tmp_path):
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    client = _client(monkeypatch, tmp_path, _channel(fail_on=2))
    text = "a" * 2000 + "b" * 10

    await client._deliver_result(555, _plain(text))

    persisted.assert_awaited_once_with(
        owner_id=1, platform="discord", channel_id=555, reason="HTTPException 50013",
        failed_text=text, landed=1, total=2, sessions_root=client._sessions_root)


@pytest.mark.asyncio
async def test_deliver_result_cadenced_failure_is_marked_too(reply, monkeypatch, tmp_path):
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    client = _client(monkeypatch, tmp_path, _channel(fail_on=2))

    await client._deliver_result(555, SimpleNamespace(
        responded=True, response_text="one\n\ntwo", attachments=(),
        cadenced_reply=_Cadenced("one", "two")))

    persisted.assert_awaited_once()
    kw = persisted.await_args.kwargs
    assert (kw["failed_text"], kw["landed"], kw["total"]) == ("one\n\ntwo", 1, 2)


@pytest.mark.asyncio
async def test_a_landed_reply_and_a_whitespace_reply_write_no_row(reply, monkeypatch, tmp_path):
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    channel = _channel()
    client = _client(monkeypatch, tmp_path, channel)

    await client._deliver_result(555, _plain("the plan is X"))
    await client._deliver_result(555, SimpleNamespace(
        responded=True, response_text="one\n\ntwo", attachments=(),
        cadenced_reply=_Cadenced("one", "two")))
    await client._deliver_result(555, _plain("   \n "))

    persisted.assert_not_awaited()
    assert channel.sent == ["the plan is X", "one", "two"]           # whitespace never sent


async def _end_to_end(monkeypatch, tmp_path, channel, text):
    """No stand-in for persist_delivery_failure: the real one writes the
    system row (to a fake history store) and flags her session."""
    import core.conversation_history_store as chs
    from core.agent.session import AgentMessage, Session, TextBlock

    rows: list[dict] = []

    class _Store:
        def __init__(self, *a, **k):
            pass

        async def store_conversation(self, **row):
            rows.append(row)
            return len(rows)

    monkeypatch.setattr(chs, "ConversationHistoryStore", _Store)
    s = await Session.open_or_create("discord:555", root=tmp_path)
    await s.append(AgentMessage(role="user", content=[TextBlock(text="what's the plan")]))
    await s.append(AgentMessage(role="assistant", content=[TextBlock(text=text)]))
    client = _client(monkeypatch, tmp_path, channel)

    await client._deliver_result(555, _plain(text))

    fresh = await Session.open_or_create("discord:555", root=tmp_path)
    return rows, [m.delivery for m in fresh.messages]


@pytest.mark.asyncio
async def test_end_to_end_nothing_landed_is_the_full_row_and_the_full_mark(reply, monkeypatch, tmp_path):
    rows, flags = await _end_to_end(monkeypatch, tmp_path, _channel(fail_on=1), "the plan is X")

    assert rows == [{"user_id": 1, "platform": "discord", "channel_id": 555, "thread_id": None,
                     "role": "system", "content": "[not delivered to them: HTTPException 50013]",
                     "username": "system"}]
    assert flags == ["", "none"]


@pytest.mark.asyncio
async def test_end_to_end_a_partial_send_is_the_partial_row_and_the_partial_mark(reply, monkeypatch, tmp_path):
    rows, flags = await _end_to_end(monkeypatch, tmp_path, _channel(fail_on=2), "a" * 2000 + "b" * 10)

    assert [r["content"] for r in rows] == [
        "[only partly delivered to them: HTTPException 50013 after 1 of 2 parts]"]
    assert flags == ["", "partial"]


# ── the guest's reply is theirs, and marked in theirs ────────────────────
def _guest_message(channel):
    msg = MagicMock()
    msg.author = MagicMock(bot=False, id=4242, display_name="Sam")
    msg.author.name = "Sam"
    msg.content = "hi"
    msg.attachments = []
    msg.mentions = []
    msg.channel = channel
    return msg


@pytest.mark.asyncio
async def test_a_guest_reply_that_dies_is_marked_on_the_guests_own_record(reply, monkeypatch, tmp_path):
    """A guest turn is keyed on THEIR id (owner_id = the author) and this
    channel, so the row and the flag land in their record, never his."""
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    monkeypatch.setattr(client_mod, "dispatch_turn", AsyncMock(return_value=SimpleNamespace(
        responded=True, response_text="hello, stranger")))
    channel = _channel(fail_on=1)
    client = _client(monkeypatch, tmp_path, channel)

    await client._handle_guest_message(_guest_message(channel), 555)

    persisted.assert_awaited_once_with(
        owner_id=4242, platform="discord", channel_id=555, reason="HTTPException 50013",
        failed_text="hello, stranger", landed=0, total=1, sessions_root=client._sessions_root)


@pytest.mark.asyncio
async def test_a_whitespace_guest_reply_is_never_a_failure_row(reply, monkeypatch, tmp_path):
    from bots.discord import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    monkeypatch.setattr(client_mod, "dispatch_turn", AsyncMock(return_value=SimpleNamespace(
        responded=True, response_text="  \n ")))
    channel = _channel()
    client = _client(monkeypatch, tmp_path, channel)

    await client._handle_guest_message(_guest_message(channel), 555)

    persisted.assert_not_awaited()
    assert channel.sent == []


@pytest.mark.asyncio
async def test_a_barge_in_cancel_right_after_the_dead_send_does_not_cost_her_the_mark(reply, monkeypatch, tmp_path):
    """His "hello?" after a dead reply cancels the very task that is marking
    it (the in-flight _fire_burst). The mark is shielded and held by a strong
    reference, so it still lands."""
    from bots.discord import client as client_mod

    started, release, done = asyncio.Event(), asyncio.Event(), []

    async def slow_persist(**kw):
        started.set()
        await release.wait()
        done.append(kw["reason"])

    monkeypatch.setattr(client_mod, "persist_delivery_failure", slow_persist)
    client = _client(monkeypatch, tmp_path, _channel(fail_on=1))
    task = asyncio.create_task(client._deliver_result(555, _plain("the plan is X")))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(client_mod._DELIVERY_MARK_TASKS) == 1          # held while it runs on
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)

    assert done == ["HTTPException 50013"]
    assert client_mod._DELIVERY_MARK_TASKS == set()           # and let go once done
