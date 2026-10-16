"""Batch 4 task 5 (fix plan 5.10, rulings R9/R9c/R9d/R9e): the Telegram door
records a reply as delivered only as far as it was.

``send_turn_reply`` already returned whether anything landed and the client
ignored it; ``send_turn_reply_cadenced`` returned None. Now both report, with
``last_send_parts()`` saying how many of her words' messages landed. The door
marks her record (not delivered, or only partly delivered), and a reply
whose words did not all land is never then sent as a voice note.
``turn_failed`` is still felt once, inside reply.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

CHAT = 424242
APP_OWNER_ID = 1


class NetworkError(Exception):
    pass


class FakeBot:
    """Raises on the ``fail_on``-th message (1-based) and on every one after."""

    def __init__(self, *, fail_on: int | None = None):
        self.messages: list[str] = []
        self.voices: list[bytes] = []
        self._fail_on = fail_on
        self._n = 0

    async def send_message(self, *, chat_id, text, **kw):
        self._n += 1
        if self._fail_on is not None and self._n >= self._fail_on:
            raise NetworkError("chat unreachable")
        self.messages.append(text)

    async def send_voice(self, *, chat_id, voice, **kw):
        self.voices.append(voice)

    async def send_chat_action(self, *, chat_id, action, **kw):
        pass


class _Beat:
    def __init__(self, text):
        self.text = text
        self.delay_after_previous_seconds = 0.0


class _Cadenced:
    def __init__(self, *texts):
        self.beats = [_Beat(t) for t in texts]
        self.attachments = ()


@pytest.fixture
def reply(monkeypatch):
    from bots.telegram import reply as r

    monkeypatch.setattr(r, "CHUNK_SPACING_SECONDS", 0.0)
    fired: list[dict] = []
    monkeypatch.setattr(r, "_delivery_notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    monkeypatch.setattr(r, "fired", fired, raising=False)
    return r


def _turn_failed(fired):
    return [n for n in fired if n["kind"] == "turn_failed"]


# ── the sends report ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_sends_report_how_many_parts_landed(reply):
    long_text = "a" * 4096 + "b" * 10

    assert await reply.send_turn_reply(FakeBot(fail_on=2), CHAT, long_text) is True
    assert reply.last_send_parts() == (1, 2)
    assert reply.last_send_error() == "NetworkError"

    assert await reply.send_turn_reply(FakeBot(fail_on=1), CHAT, long_text) is False
    assert reply.last_send_parts() == (0, 2)

    assert await reply.send_turn_reply(FakeBot(), CHAT, long_text) is True
    assert reply.last_send_parts() == (2, 2)
    assert reply.last_send_error() == ""


@pytest.mark.asyncio
async def test_the_cadenced_send_reports_too(reply):
    assert await reply.send_turn_reply_cadenced(FakeBot(fail_on=1), CHAT, _Cadenced("one", "two")) is False
    assert reply.last_send_parts() == (0, 2)
    assert reply.last_send_error() == "NetworkError"
    assert len(_turn_failed(reply.fired)) == 1

    assert await reply.send_turn_reply_cadenced(FakeBot(fail_on=2), CHAT, _Cadenced("one", "two", "three")) is True
    assert reply.last_send_parts() == (1, 3)

    ok = FakeBot()
    assert await reply.send_turn_reply_cadenced(ok, CHAT, _Cadenced("one", "two")) is True
    assert ok.messages == ["one", "two"]
    assert reply.last_send_parts() == (2, 2)
    assert reply.last_send_error() == ""

    assert await reply.send_turn_reply_cadenced(FakeBot(fail_on=1), CHAT, _Cadenced("only")) is False
    assert await reply.send_turn_reply_cadenced(FakeBot(), CHAT, None) is False
    assert reply.last_send_parts() == (0, 0)


@pytest.mark.asyncio
async def test_a_cadenced_reply_whose_first_beat_landed_is_delivered_even_if_the_file_fallback_dies(reply, tmp_path):
    """R9g: False means nothing landed. The first beat reached him; then the
    document upload died and so did the "(not delivered — …)" line after it."""
    doc = tmp_path / "plan.pdf"
    doc.write_bytes(b"%PDF-1.4")

    class _Bot(FakeBot):
        async def send_document(self, *, chat_id, document, filename=None, **kw):
            raise NetworkError("upload refused")

    bot = _Bot(fail_on=2)
    cadenced = _Cadenced("one", "two")
    cadenced.attachments = (str(doc),)

    assert await reply.send_turn_reply_cadenced(bot, CHAT, cadenced) is True
    assert bot.messages == ["one"]
    assert reply.last_send_parts() == (1, 2)
    assert reply.last_send_error() == "NetworkError"


# ── the door marks her record ─────────────────────────────────────────────
def _client(tmp_path, bot):
    from bots.common.guest_gate import GuestGate
    from bots.telegram.client import PalliTelegramClient

    app = SimpleNamespace(bot=bot)
    return PalliTelegramClient(application=app, bot_user_id=999, bot_username="palli_test_bot",
                               owner_id=APP_OWNER_ID, guest_gate=GuestGate(),
                               sessions_root=tmp_path)


def _result(text="here's the plan", cadenced=None):
    from core.turn.types import TurnResult

    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0,
                      cadenced_reply=cadenced)


@pytest.mark.asyncio
async def test_deliver_result_marks_a_reply_that_never_landed(reply, monkeypatch, tmp_path):
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    client = _client(tmp_path, FakeBot(fail_on=1))

    await client._deliver_result(CHAT, _result())

    persisted.assert_awaited_once_with(
        owner_id=APP_OWNER_ID, platform="telegram", channel_id=CHAT, reason="NetworkError",
        failed_text="here's the plan", landed=0, total=1, sessions_root=client._sessions_root)
    assert len(_turn_failed(reply.fired)) == 1


@pytest.mark.asyncio
async def test_deliver_result_marks_a_reply_that_only_partly_landed(reply, monkeypatch, tmp_path):
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    client = _client(tmp_path, FakeBot(fail_on=2))

    await client._deliver_result(CHAT, _result("one\n\ntwo\n\nthree",
                                               cadenced=_Cadenced("one", "two", "three")))

    persisted.assert_awaited_once()
    kw = persisted.await_args.kwargs
    assert (kw["failed_text"], kw["landed"], kw["total"]) == ("one\n\ntwo\n\nthree", 1, 3)


@pytest.mark.asyncio
async def test_a_landed_reply_and_a_whitespace_reply_write_no_row(reply, monkeypatch, tmp_path):
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    bot = FakeBot()
    client = _client(tmp_path, bot)

    await client._deliver_result(CHAT, _result())
    await client._deliver_result(CHAT, _result("one\n\ntwo", cadenced=_Cadenced("one", "two")))
    await client._deliver_result(CHAT, _result("  \n "))

    persisted.assert_not_awaited()
    assert bot.messages == ["here's the plan", "one", "two"]


@pytest.mark.parametrize("fail_on", [1, 2])
@pytest.mark.asyncio
async def test_words_that_did_not_all_land_are_never_sent_as_a_voice_note(reply, monkeypatch, tmp_path, fail_on):
    """R9c: her record now says he never got (all of) these words; a voice
    note carrying them would make that a lie."""
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    bot = FakeBot(fail_on=fail_on)
    client = _client(tmp_path, bot)
    voice_for = AsyncMock(return_value=b"OggS-voice")
    monkeypatch.setattr(client, "_voice_note_for", voice_for)

    await client._deliver_result(CHAT, _result("one\n\ntwo", cadenced=_Cadenced("one", "two")),
                                 is_voice=True)

    persisted.assert_awaited_once()                        # the mark is written
    voice_for.assert_not_awaited()                         # no speech is even made
    assert bot.voices == []


@pytest.mark.asyncio
async def test_words_that_landed_still_get_their_voice_note(reply, monkeypatch, tmp_path):
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    bot = FakeBot()
    client = _client(tmp_path, bot)
    monkeypatch.setattr(client, "_voice_note_for", AsyncMock(return_value=b"OggS-voice"))

    await client._deliver_result(CHAT, _result(), is_voice=True)

    persisted.assert_not_awaited()
    assert bot.voices == [b"OggS-voice"]


# ── the guest's reply is theirs ───────────────────────────────────────────
async def _run_guest(monkeypatch, tmp_path, bot, text):
    from bots.telegram import client as client_mod

    persisted = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", persisted)
    monkeypatch.setattr(client_mod._intake, "materialize_attachments",
                        AsyncMock(side_effect=lambda turn, bot: turn))
    monkeypatch.setattr(client_mod, "dispatch_turn", AsyncMock(return_value=_result(text)))
    client = _client(tmp_path, bot)
    guest_turn = SimpleNamespace(owner_id=4242, content="hi", metadata=SimpleNamespace(channel_id=CHAT))
    client._guest_gate.release = lambda author_id: None

    await client._run_guest_turn(guest_turn, chat_id=CHAT, author_id=4242)
    return client, persisted


@pytest.mark.asyncio
async def test_a_guest_reply_that_dies_is_marked_on_the_guests_own_record(reply, monkeypatch, tmp_path):
    client, persisted = await _run_guest(monkeypatch, tmp_path, FakeBot(fail_on=1), "hello, stranger")

    persisted.assert_awaited_once_with(
        owner_id=4242, platform="telegram", channel_id=CHAT, reason="NetworkError",
        failed_text="hello, stranger", landed=0, total=1, sessions_root=client._sessions_root)


@pytest.mark.asyncio
async def test_a_whitespace_guest_reply_is_never_a_failure_row(reply, monkeypatch, tmp_path):
    bot = FakeBot()
    _client_, persisted = await _run_guest(monkeypatch, tmp_path, bot, "  \n ")

    persisted.assert_not_awaited()
    assert bot.messages == []
