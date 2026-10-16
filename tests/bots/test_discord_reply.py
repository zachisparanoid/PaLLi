"""Wave 3: tests for bots.discord.reply — TurnResult -> Discord send chunks."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _channel():
    """A discord.Channel-like mock that supports `async with .typing():`
    and `await .send(text)`."""
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    return channel


@pytest.mark.asyncio
async def test_send_reply_sends_short_message_in_one_chunk() -> None:
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    await send_turn_reply(channel, "hello world")
    channel.send.assert_called_once_with("hello world")


@pytest.mark.asyncio
async def test_send_reply_chunks_long_message_at_2000_chars() -> None:
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    text = "a" * 5000
    await send_turn_reply(channel, text)
    # Three chunks: 2000 + 2000 + 1000.
    assert channel.send.call_count == 3
    sent = [call.args[0] for call in channel.send.call_args_list]
    assert sum(len(s) for s in sent) == 5000
    assert all(len(s) <= 2000 for s in sent)


@pytest.mark.asyncio
async def test_send_reply_skips_when_text_is_empty() -> None:
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    await send_turn_reply(channel, "")
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_skips_when_text_is_whitespace_only() -> None:
    """An all-whitespace reply (e.g. fallback that the model generated empty)
    should still be a no-op rather than a blank message in the channel."""
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    await send_turn_reply(channel, "   \n\n   ")
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_swallows_send_errors() -> None:
    """If discord.send raises (transient gateway issue), reply should
    not crash the turn. The error is logged and we move on."""
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    channel.send.side_effect = RuntimeError("gateway hiccup")
    # Should NOT raise.
    await send_turn_reply(channel, "hi")


@pytest.mark.asyncio
async def test_send_reply_uses_typing_indicator() -> None:
    """Each send happens inside an `async with channel.typing():` block."""
    from bots.discord.reply import send_turn_reply

    channel = _channel()
    await send_turn_reply(channel, "hi")
    channel.typing.assert_called_once()


# ── DEFECT_RECORD §15 (2026-09-01): the bounced package ─────────────────────
# A 15.4 MB MP3 hit Discord's 10 MB cap five times (413 Payload Too Large);
# the 6.6 MB file taped to it died too; and no failure ever reached her —
# she kept saying "it's right there". Now: weigh before sending, and every
# send failure or dropped attachment becomes something she feels.

import sys, types


def _fake_discord(monkeypatch):
    mod = types.ModuleType("discord")
    class File:
        def __init__(self, path): self.path = path
    mod.File = File
    monkeypatch.setitem(sys.modules, "discord", mod)
    return mod


def _notices(monkeypatch):
    import ai.present_moment as pm
    monkeypatch.setattr("bots.discord.reply._calculate_typing_delay", lambda t: 0.0)
    fired = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    return fired


@pytest.mark.asyncio
async def test_send_failure_is_felt_as_turn_failed(monkeypatch) -> None:
    from bots.discord.reply import send_turn_reply
    fired = _notices(monkeypatch)
    channel = _channel()
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)   # a truthy mock would swallow the error
    channel.send = AsyncMock(side_effect=RuntimeError("413 Payload Too Large"))
    await send_turn_reply(channel, "here you go")
    assert fired and fired[0]["kind"] == "turn_failed" and fired[0]["stage"] == "discord_send"


@pytest.mark.asyncio
async def test_oversize_attachment_is_dropped_named_in_text_and_felt(monkeypatch, tmp_path) -> None:
    from bots.discord import reply as r
    _fake_discord(monkeypatch)
    fired = _notices(monkeypatch)
    big = tmp_path / "essay.mp3"
    with open(big, "wb") as f:
        f.truncate(r.DISCORD_MAX_ATTACHMENT_BYTES + 1)
    small = tmp_path / "note.txt"; small.write_text("hi")
    channel = _channel()
    await r.send_turn_reply(channel, "both files, as promised", attachments=(str(big), str(small)))
    kw = channel.send.call_args.kwargs
    assert [f.path for f in kw["files"]] == [str(small)]                       # the small one still lands
    assert "essay.mp3" in kw["content"] and "over Discord's" in kw["content"]  # the drop is named to him
    assert any(f["kind"] == "attachment_dropped" for f in fired)


@pytest.mark.asyncio
async def test_missing_attachment_path_is_felt_too(monkeypatch, tmp_path) -> None:
    from bots.discord.reply import send_turn_reply
    _fake_discord(monkeypatch)
    fired = _notices(monkeypatch)
    channel = _channel()
    await send_turn_reply(channel, "attached above", attachments=(str(tmp_path / "gone.png"),))
    assert any(f["kind"] == "attachment_dropped" and f["reason"] == "missing" for f in fired)
    assert "gone.png" in channel.send.call_args.kwargs.get("content", channel.send.call_args.args[0] if channel.send.call_args.args else "")


def test_attachment_dropped_has_a_lived_phrase():
    from ai.present_moment import _humanize_events
    assert "too big" in _humanize_events([{"kind": "attachment_dropped", "reason": "too_big"}])
