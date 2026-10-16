"""Week 2, task 2: a failed send leaves an honest note and keeps the file.

DEFECT_RECORD §15's other half. The files ride the FIRST outgoing message;
when that send raises, the whole message dies — text and attachment together
— and until now all that happened was a log line and a `turn_failed` notice.
She went on believing the file had landed.

Now: one retry of that chunk WITHOUT the files, carrying the note in her own
outgoing words (the `_with_notes` mechanism from DEFECT §16), so both he and
she can see which file did not go and that it is still on disk. The artifact
is never deleted or moved. If the retry raises too, nothing propagates and
the turn is felt as failed, exactly as before.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _channel():
    """A discord.Channel-like mock: `async with .typing():` + `await .send()`.
    __aexit__ returns False so an exception inside the block propagates —
    a bare AsyncMock is truthy and would swallow it."""
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


def _fake_discord(monkeypatch):
    """The reply module imports `discord` lazily to build File objects."""
    mod = types.ModuleType("discord")

    class File:
        def __init__(self, path):
            self.path = path

    mod.File = File
    monkeypatch.setitem(sys.modules, "discord", mod)
    return mod


def _failing_then_ok(channel):
    """Record every send; raise on the first one only."""
    calls: list[tuple[tuple, dict]] = []

    async def send(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise RuntimeError("413 Payload Too Large")
        return None

    channel.send = AsyncMock(side_effect=send)
    return calls


def _content_of(call) -> str:
    args, kwargs = call
    if "content" in kwargs:
        return kwargs["content"] or ""
    return args[0] if args else ""


class _Beat:
    def __init__(self, text: str, delay: float = 0.0) -> None:
        self.text = text
        self.delay_after_previous_seconds = delay


class _Cadenced:
    def __init__(self, beats, attachments) -> None:
        self.beats = beats
        self.attachments = attachments


@pytest.fixture
def artifact(tmp_path):
    p = tmp_path / "snap.png"
    p.write_bytes(b"\x89PNG")
    return p


@pytest.mark.asyncio
async def test_a_send_that_dies_with_the_file_retries_without_it_and_says_so(
    monkeypatch, artifact,
) -> None:
    from bots.discord import reply as r

    _fake_discord(monkeypatch)
    monkeypatch.setattr(r, "_calculate_typing_delay", lambda t: 0.0)
    fired: list[dict] = []
    monkeypatch.setattr(r, "_delivery_notice",
                        lambda kind, **f: fired.append({"kind": kind, **f}))
    channel = _channel()
    calls = _failing_then_ok(channel)

    await r.send_turn_reply(channel, "here you go", attachments=(str(artifact),))

    assert len(calls) == 2, f"expected one retry without the file; got {calls!r}"
    assert calls[0][1].get("files"), "the first send should have carried the file"
    assert not calls[1][1].get("files"), "the retry must not carry the file again"
    assert (
        f"(not delivered — snap.png: RuntimeError; the file is still at {artifact})"
        in _content_of(calls[1])
    )
    assert artifact.exists(), "a failed send must never cost him the file"
    # §15: a drop she cannot feel is the defect. The retry landing does not
    # mean the file did.
    assert any(n["kind"] == "attachment_dropped" and n.get("reason") == "send_failed"
               for n in fired), f"the drop should be felt; got {fired!r}"


@pytest.mark.asyncio
async def test_when_the_retry_dies_too_the_turn_is_felt_and_the_file_stays(
    monkeypatch, artifact,
) -> None:
    from bots.discord import reply as r

    _fake_discord(monkeypatch)
    monkeypatch.setattr(r, "_calculate_typing_delay", lambda t: 0.0)
    fired: list[dict] = []
    monkeypatch.setattr(r, "_delivery_notice",
                        lambda kind, **f: fired.append({"kind": kind, **f}))
    channel = _channel()
    channel.send = AsyncMock(side_effect=RuntimeError("gateway gone"))

    # Must not raise: a dead channel is not a dead turn.
    await r.send_turn_reply(channel, "here you go", attachments=(str(artifact),))

    assert channel.send.call_count == 2, "one retry, then give up"
    assert any(n["kind"] == "turn_failed" and n.get("stage") == "discord_send"
               for n in fired), f"the turn should be felt as failed; got {fired!r}"
    assert artifact.exists()


@pytest.mark.asyncio
async def test_the_cadenced_path_says_it_too(monkeypatch, artifact) -> None:
    from bots.discord import reply as r

    _fake_discord(monkeypatch)
    monkeypatch.setattr(r, "_calculate_typing_delay", lambda t: 0.0)
    channel = _channel()
    calls = _failing_then_ok(channel)

    reply = _Cadenced(
        beats=[_Beat("here you go"), _Beat("that's the one")],
        attachments=(str(artifact),),
    )
    await r.send_turn_reply_cadenced(channel, reply)

    assert len(calls) == 3, f"first beat, its retry, then the second beat; got {calls!r}"
    assert calls[0][1].get("files"), "the first beat should have carried the file"
    assert not calls[1][1].get("files"), "the retry must not carry the file again"
    assert (
        f"(not delivered — snap.png: RuntimeError; the file is still at {artifact})"
        in _content_of(calls[1])
    )
    assert "that's the one" in _content_of(calls[2]), "the rest of the burst still goes"
    assert artifact.exists()
