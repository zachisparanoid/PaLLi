"""Her reply goes out the Telegram door (telegram door, task 2).

Same three jobs bots/discord/reply.py does, against a different wall:
4,096 characters instead of 2,000, 50 MB instead of 10, and a rate limiter
that says exactly how long to wait instead of a blanket 429.

``telegram.error.RetryAfter`` cannot be imported here — the library is not
a test dependency — so the retry is proved with an exception whose CLASS IS
NAMED RetryAfter, which is exactly how the code recognises it.
"""
from __future__ import annotations

import asyncio

import pytest


class FakeBot:
    def __init__(self, *, fail_first_with=None):
        self.messages: list[dict] = []
        self.documents: list[dict] = []
        self.actions: list[dict] = []
        self.calls: list[str] = []
        self._fail_first_with = fail_first_with

    async def send_message(self, *, chat_id, text, **kw):
        if self._fail_first_with is not None:
            exc, self._fail_first_with = self._fail_first_with, None
            raise exc
        self.messages.append({"chat_id": chat_id, "text": text, **kw})
        self.calls.append("message")

    async def send_document(self, *, chat_id, document, filename=None, **kw):
        self.documents.append({"chat_id": chat_id, "document": document,
                               "filename": filename})
        self.calls.append("document")

    async def send_chat_action(self, *, chat_id, action, **kw):
        self.actions.append({"chat_id": chat_id, "action": action})


class RetryAfter(Exception):
    """The shape telegram.error.RetryAfter has, under its own name."""

    def __init__(self, retry_after):
        super().__init__(f"flood control: retry in {retry_after}")
        self.retry_after = retry_after


@pytest.fixture
def no_spacing(monkeypatch):
    from bots.telegram import reply

    monkeypatch.setattr(reply, "CHUNK_SPACING_SECONDS", 0.0)
    return reply


@pytest.fixture
def slept(monkeypatch):
    """Record every sleep instead of taking it."""
    from bots.telegram import reply

    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(seconds, *a, **kw):
        waits.append(float(seconds))
        return await real_sleep(0)

    monkeypatch.setattr(reply.asyncio, "sleep", _sleep)
    return waits


@pytest.mark.asyncio
async def test_a_long_reply_is_three_sends_in_order(no_spacing, slept):
    reply = no_spacing
    bot = FakeBot()
    text = "a" * 4096 + "b" * 4096 + "c" * 808        # 9,000 characters

    await reply.send_turn_reply(bot, 424242, text)

    assert len(bot.messages) == 3
    assert [m["text"][0] for m in bot.messages] == ["a", "b", "c"]
    assert [len(m["text"]) for m in bot.messages] == [4096, 4096, 808]
    assert all(m["chat_id"] == 424242 for m in bot.messages)
    # One spacing pause BETWEEN chunks, never before the first.
    assert slept == [0.0, 0.0]


@pytest.mark.asyncio
async def test_nothing_to_say_and_nothing_to_send_is_a_no_op(no_spacing):
    bot = FakeBot()
    await no_spacing.send_turn_reply(bot, 424242, "   ")
    assert bot.calls == []


@pytest.mark.asyncio
async def test_an_attachment_rides_after_the_first_chunk(no_spacing, tmp_path):
    reply = no_spacing
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 tiny")
    bot = FakeBot()

    await reply.send_turn_reply(bot, 424242, "here it is", attachments=(str(path),))

    assert bot.calls == ["message", "document"]
    assert bot.documents[0]["filename"] == "report.pdf"
    assert bot.documents[0]["chat_id"] == 424242
    assert "not delivered" not in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_a_file_over_the_cap_is_named_not_sent(no_spacing, tmp_path, monkeypatch):
    """DEFECT §15/§16's shape: she must not go on believing it landed. Name
    the file, name the wall, say where it still is."""
    reply = no_spacing
    monkeypatch.setattr(reply, "TELEGRAM_MAX_ATTACHMENT_BYTES", 16)
    path = tmp_path / "dump.iso"
    path.write_bytes(b"x" * 64)
    bot = FakeBot()

    await reply.send_turn_reply(bot, 424242, "the dump you asked for",
                                attachments=(str(path),))

    assert bot.documents == []
    body = bot.messages[0]["text"]
    assert "not delivered" in body
    assert "dump.iso" in body
    assert "50 MB limit" in body or "limit" in body
    assert str(path) in body


@pytest.mark.asyncio
async def test_a_missing_file_is_named_too(no_spacing, tmp_path):
    reply = no_spacing
    bot = FakeBot()

    await reply.send_turn_reply(bot, 424242, "here", attachments=(str(tmp_path / "gone.txt"),))

    assert bot.documents == []
    assert "gone.txt" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_retry_after_waits_exactly_once_and_then_sends(no_spacing, slept):
    """Telegram says how long to wait. Honour it once — a blind retry loop
    against a flood limiter is how a bot earns a longer one."""
    reply = no_spacing
    bot = FakeBot(fail_first_with=RetryAfter(3))

    await reply.send_turn_reply(bot, 424242, "hello")

    assert slept == [3.0]
    assert [m["text"] for m in bot.messages] == ["hello"]


@pytest.mark.asyncio
async def test_a_plain_failure_is_felt_and_never_raised(no_spacing, monkeypatch):
    reply = no_spacing
    felt: list[tuple] = []
    monkeypatch.setattr(reply, "_delivery_notice",
                        lambda kind, **kw: felt.append((kind, kw)))
    bot = FakeBot(fail_first_with=RuntimeError("chat not found"))

    await reply.send_turn_reply(bot, 424242, "hello")      # must not raise

    assert bot.messages == []
    assert felt and felt[0][0] == "turn_failed"
    assert felt[0][1]["error_class"] == "RuntimeError"


@pytest.mark.asyncio
async def test_a_second_retry_after_is_not_retried_again(no_spacing, slept, monkeypatch):
    """Once, never a loop. The second refusal is felt, not slept on again."""
    reply = no_spacing
    felt: list[tuple] = []
    monkeypatch.setattr(reply, "_delivery_notice",
                        lambda kind, **kw: felt.append((kind, kw)))
    tries = {"n": 0}

    class _Bot(FakeBot):
        async def send_message(self, *, chat_id, text, **kw):
            tries["n"] += 1
            raise RetryAfter(1)

    await reply.send_turn_reply(_Bot(), 424242, "hello")

    assert tries["n"] == 2
    assert slept == [1.0]
    assert felt and felt[0][0] == "turn_failed"
    assert felt[0][1]["error_class"] == "RetryAfter"


@pytest.mark.asyncio
async def test_a_cadenced_reply_sends_one_message_per_beat(no_spacing, slept):
    from core.turn.types import Beat, CadencedReply

    reply = no_spacing
    bot = FakeBot()
    cadenced = CadencedReply(
        beats=(Beat(text="wait"), Beat(text="got it", delay_after_previous_seconds=2.0)),
        total_text="wait\ngot it")

    await reply.send_turn_reply_cadenced(bot, 424242, cadenced)

    assert [m["text"] for m in bot.messages] == ["wait", "got it"]
    assert 2.0 in slept                      # the beat's own pacing was honoured


@pytest.mark.asyncio
async def test_a_single_beat_cadenced_reply_is_the_plain_path(no_spacing):
    from core.turn.types import CadencedReply

    bot = FakeBot()
    await no_spacing.send_turn_reply_cadenced(
        bot, 424242, CadencedReply.from_single("just the one"))

    assert [m["text"] for m in bot.messages] == ["just the one"]


@pytest.mark.asyncio
async def test_typing_shows_immediately_and_keeps_showing(monkeypatch):
    """Telegram's typing action expires after ~5 seconds, so a long run needs
    it re-sent. It stops the moment the run does."""
    from bots.telegram import reply

    monkeypatch.setattr(reply, "TYPING_INTERVAL_SECONDS", 0.01)
    bot = FakeBot()

    async with reply.typing_while(bot, 424242):
        assert bot.actions and bot.actions[0]["action"] == "typing"
        await asyncio.sleep(0.05)
        during = len(bot.actions)

    assert during >= 2
    await asyncio.sleep(0.05)
    assert len(bot.actions) == during          # cancelled with the run


def test_a_no_op_send_does_not_keep_an_earlier_sends_reason(no_spacing):
    """Final review, minor 10: the reason was reset only AFTER the
    nothing-to-send early return, so a stale reason from an earlier send in
    the same task survived a no-op send."""
    reply = no_spacing

    async def _both():
        failing = FakeBot(fail_first_with=RuntimeError("Forbidden"))
        assert await reply.send_turn_reply(failing, 42, "hello") is False
        assert reply.last_send_error() == "RuntimeError"
        assert await reply.send_turn_reply(FakeBot(), 42, "   ") is False
        return reply.last_send_error(), reply.last_send_parts()

    error, parts = asyncio.run(_both())
    assert error == ""
    assert parts == (0, 0)
