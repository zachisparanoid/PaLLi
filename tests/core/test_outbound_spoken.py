"""The spoken custody primitive (step 32, spec §3): record on SPEAK, never
on intent. Leg 1 = say_text through the live voice session; a declined or
failed speak raises with ZERO bookkeeping. Then the text primitive's own
legs: DB assistant row (extra carries spoken_aloud), an assistant-only
"[said aloud] …" line in the picked channel's transcript (the freshness
tail and her later replies must know it happened), the deed, and the
sense-D mirror (platform="voice")."""
import json

import pytest

from core.turn import outbound


class _FakeVoiceControl:
    def __init__(self, ok=True, raise_=False):
        self.ok = ok
        self.raise_ = raise_
        self.calls = []

    async def say_text(self, *, runtime_session_key, text):
        if self.raise_:
            raise RuntimeError("voice session gone")
        self.calls.append((runtime_session_key, text))
        return self.ok


@pytest.fixture()
def legs(monkeypatch, tmp_path):
    """Stub every bookkeeping leg + the say seam; return the recorders."""
    state = {}
    control = _FakeVoiceControl()
    monkeypatch.setattr(
        "voice.control.voice_session_control_service", control
    )

    async def _fake_persist(*, turn, response_text):
        state["turn"] = turn
        state["text"] = response_text

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    deeds = []
    monkeypatch.setattr(
        "ai.recent_deeds.record_deed",
        lambda kind, detail, **kw: deeds.append((kind, detail)),
    )
    notices = []
    monkeypatch.setattr(
        "ai.present_moment.notice",
        lambda kind, **fields: notices.append((kind, fields)),
    )
    state["control"] = control
    state["deeds"] = deeds
    state["notices"] = notices
    state["tmp"] = tmp_path
    return state


@pytest.mark.asyncio
async def test_spoken_full_custody(legs):
    ok = await outbound.record_outbound_spoken(
        owner_id=1, channel_id=123, runtime_session_key="voice:sess",
        text="  the ferret verdict, out loud  ", origin="arbiter",
        extra={"sensitivity": "routine"},
    )
    assert ok is True
    assert legs["control"].calls == [("voice:sess", "the ferret verdict, out loud")]

    # DB row: same discord channel row her replies read, marked spoken
    assert legs["text"] == "the ferret verdict, out loud"
    turn = legs["turn"]
    assert turn.metadata.channel_id == 123
    assert turn.metadata.origin == "arbiter"
    assert turn.metadata.extra["spoken_aloud"] is True
    assert turn.metadata.extra["self_initiated"] is True
    assert turn.metadata.extra["sensitivity"] == "routine"

    # transcript: assistant-only, visibly spoken
    lines = (legs["tmp"] / "discord_123.jsonl").read_text().strip().splitlines()
    last = json.loads(lines[-1])
    assert last["role"] == "assistant"
    assert "[said aloud]" in json.dumps(last)

    # deed + sense-D mirror
    assert any("aloud" in detail for _, detail in legs["deeds"])
    assert ("initiative_sent", {"platform": "voice", "chars": 28}) in legs["notices"]


@pytest.mark.asyncio
async def test_declined_speak_records_nothing(legs):
    legs["control"].ok = False
    with pytest.raises(outbound.SpokenSendFailed):
        await outbound.record_outbound_spoken(
            owner_id=1, channel_id=123, runtime_session_key="voice:sess",
            text="hello", origin="arbiter",
        )
    assert "turn" not in legs  # no DB row
    assert legs["deeds"] == []
    assert not (legs["tmp"] / "discord_123.jsonl").exists()


@pytest.mark.asyncio
async def test_raising_speak_records_nothing(legs):
    legs["control"].raise_ = True
    with pytest.raises(RuntimeError):
        await outbound.record_outbound_spoken(
            owner_id=1, channel_id=123, runtime_session_key="voice:sess",
            text="hello", origin="arbiter",
        )
    assert "turn" not in legs
    assert legs["deeds"] == []


@pytest.mark.asyncio
async def test_empty_text_or_missing_key_refuses(legs):
    with pytest.raises(outbound.SpokenSendFailed):
        await outbound.record_outbound_spoken(
            owner_id=1, channel_id=123, runtime_session_key="",
            text="hello", origin="arbiter",
        )
    with pytest.raises(outbound.SpokenSendFailed):
        await outbound.record_outbound_spoken(
            owner_id=1, channel_id=123, runtime_session_key="voice:sess",
            text="   ", origin="arbiter",
        )
    assert legs["control"].calls == []
