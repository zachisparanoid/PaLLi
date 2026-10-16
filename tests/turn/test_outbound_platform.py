"""The custody primitive learns a second word (telegram door, task 1).

``record_outbound`` is the ONE place her organic sends are recorded: the
send, the DB assistant row, the transcript line her next reply reads. Every
leg of it hardcoded "discord". A Telegram send that rode it would be
delivered to Discord, persisted under platform 'discord', and filed in the
wrong transcript.

The default is the whole regression guard: an existing caller that passes
no platform must behave EXACTLY as it does today (tests/core/test_outbound_custody.py
is the untouched pin; these cases re-state it beside the Telegram half).
"""
from __future__ import annotations

import json

import pytest

from core.turn import outbound


class _FakeRuntime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_envelope(self, *, platform, surface_id, envelope):
        self.sent.append((platform, surface_id, envelope.text))


@pytest.fixture
def wired(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)
    persisted: dict = {}

    async def _fake_persist(*, turn, response_text):
        persisted["turn"] = turn
        persisted["text"] = response_text

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    return runtime, persisted, tmp_path


def _roles(path):
    lines = path.read_text().strip().splitlines()
    return [json.loads(line)["role"] for line in lines if line.strip()]


@pytest.mark.asyncio
async def test_the_default_call_is_byte_for_byte_the_discord_send(wired):
    runtime, persisted, tmp_path = wired

    ok = await outbound.record_outbound(
        owner_id=1, channel_id=123, text="hey — the ferret verdict?",
        origin="arbiter", extra={"thread_id": "abc"},
    )

    assert ok is True
    assert runtime.sent == [("discord", "123", "hey — the ferret verdict?")]
    assert persisted["turn"].surface == "discord_text"
    assert persisted["turn"].metadata.channel_id == 123
    assert _roles(tmp_path / "discord_123.jsonl")[-1] == "assistant"


@pytest.mark.asyncio
async def test_a_telegram_send_goes_out_the_telegram_door(wired):
    runtime, persisted, tmp_path = wired

    ok = await outbound.record_outbound(
        owner_id=1, channel_id=424242, text="the kettle's boiled",
        origin="arbiter", platform="telegram",
    )

    assert ok is True
    assert runtime.sent == [("telegram", "424242", "the kettle's boiled")]


@pytest.mark.asyncio
async def test_a_telegram_send_persists_under_its_own_surface(wired):
    runtime, persisted, tmp_path = wired

    await outbound.record_outbound(
        owner_id=1, channel_id=424242, text="the kettle's boiled",
        platform="telegram",
    )

    assert persisted["turn"].surface == "telegram_text"
    assert persisted["turn"].metadata.channel_id == 424242
    assert persisted["turn"].metadata.extra["self_initiated"] is True


@pytest.mark.asyncio
async def test_a_telegram_send_lands_in_the_telegram_transcript(wired):
    """The transcript key must be the one her next Telegram turn reads —
    a line filed under discord:<chat> is a line she never sees again."""
    runtime, persisted, tmp_path = wired

    await outbound.record_outbound(
        owner_id=1, channel_id=424242, text="the kettle's boiled",
        platform="telegram",
    )

    assert (tmp_path / "telegram_424242.jsonl").exists()
    assert not (tmp_path / "discord_424242.jsonl").exists()
    assert _roles(tmp_path / "telegram_424242.jsonl")[-1] == "assistant"


@pytest.mark.asyncio
async def test_a_caller_may_name_the_surface_id_itself(wired):
    """Telegram chat ids are not always the address a door wants to send
    to; the caller may hand the surface_id over explicitly."""
    runtime, persisted, tmp_path = wired

    await outbound.record_outbound(
        owner_id=1, channel_id=424242, text="hi", platform="telegram",
        surface_id="-1001234",
    )

    assert runtime.sent == [("telegram", "-1001234", "hi")]


@pytest.mark.asyncio
async def test_a_mid_task_telegram_act_still_writes_no_transcript(wired):
    """Step 27's rule is a rule about the RUN, not about Discord: the tool
    round-trip is the record on every door."""
    runtime, persisted, tmp_path = wired

    await outbound.record_outbound(
        owner_id=1, channel_id=424242, text="found the part number",
        origin="duplex_chime", extra={"mid_task": True}, platform="telegram",
    )

    assert runtime.sent == [("telegram", "424242", "found the part number")]
    assert persisted["text"] == "found the part number"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_the_spoken_primitive_stays_on_discord(monkeypatch, tmp_path):
    """Speaking is a Discord voice session (step 32). The spoken sibling
    names its platform explicitly so the custody legs can never drift to
    whatever the text default happens to be."""
    persisted: dict = {}

    async def _fake_persist(*, turn, response_text):
        persisted["turn"] = turn

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    class _FakeVoice:
        async def say_text(self, *, runtime_session_key, text):
            return True

    import voice.control as _voice_control

    monkeypatch.setattr(
        _voice_control, "voice_session_control_service", _FakeVoice(),
    )

    ok = await outbound.record_outbound_spoken(
        owner_id=1, channel_id=123, runtime_session_key="vs-1", text="said aloud",
    )

    assert ok is True
    assert persisted["turn"].surface == "discord_text"
    assert (tmp_path / "discord_123.jsonl").exists()


# ── a send that never landed records nothing (final wave, I1) ─────────────
@pytest.mark.asyncio
async def test_a_telegram_send_that_never_landed_records_nothing(monkeypatch, tmp_path):
    """"Leg 1 — the send. Raises to the caller: no record without a send."
    That law held for Discord (its adapter raises when the channel cannot be
    found) and was broken for Telegram: the adapter reported every send
    delivered because bots/telegram/reply.py swallows its failures. A
    reach-out into a chat that had blocked her was persisted, transcribed and
    filed as a deed she had done.

    The real adapter, over a bot that refuses — not a fake of the thing
    under test."""
    from bots.telegram.adapter import TelegramChannelAdapter
    from core.channel_runtime import ChannelRuntimeRegistry

    class _BlockedBot:
        async def send_message(self, *, chat_id, text, **kw):
            raise RuntimeError("Forbidden: bot was blocked by the user")

    registry = ChannelRuntimeRegistry()
    await registry.register_adapter(
        "telegram", TelegramChannelAdapter(_BlockedBot()))
    monkeypatch.setattr("core.channel_runtime.channel_runtime", registry)

    persisted: list = []

    async def _fake_persist(*, turn, response_text):
        persisted.append(turn)

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    with pytest.raises(RuntimeError, match="nothing was delivered"):
        await outbound.record_outbound(
            owner_id=1, channel_id=424242, text="the kettle's boiled",
            origin="arbiter", platform="telegram",
        )

    assert persisted == []                       # no DB assistant row
    assert list(tmp_path.iterdir()) == []        # and no transcript line


@pytest.mark.asyncio
async def test_a_phone_send_is_filed_under_the_phone_not_discord(wired):
    runtime, persisted, tmp_path = wired

    ok = await outbound.record_outbound(
        owner_id=1, channel_id=1, text="thinking of you", platform="mobile",
    )

    assert ok is True
    assert runtime.sent == [("mobile", "1", "thinking of you")]
    assert persisted["turn"].surface == "mobile_text"
    assert _roles(tmp_path / "mobile_1.jsonl")[-1] == "assistant"
