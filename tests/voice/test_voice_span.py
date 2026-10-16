"""Step 31 (spec §5): the span — one honest timing line per spoken turn.

Log-only v1: monotonic stamps threaded finalize→decide→dispatch→tts→
playback; a missing stage renders `-` (the honesty rule — never
reconstruct a number); the formatter NEVER raises. vad_hold and stt stay
`-` in v1: the buffered transcriber fuses them upstream of the runtime's
sight, and the span begins at finalize. This line is the evidence the
latency-tuning follow-up runs on — the numbers must be trustworthy.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from voice.runtime import (
    VoiceConversationSession,
    _format_span,
    _span_mark,
)


def test_format_span_full_line():
    span = {
        "speech_ended": 0.0,
        "turn_finalized": 2.51,
        "stt_start": 0.0,  # no stt_done — renders "-" even with a start
        "decided": 2.59,
        "dispatch_start": 2.60,
        "dispatch_done": 5.80,
        "tts_start": 5.81,
        "tts_done": 6.52,
        "playback_start": 6.64,
    }
    line = _format_span(span)
    assert line == (
        "voice_span: vad_hold=2.51s stt=- decide=0.08s llm=3.20s "
        "tts=0.71s to_playback=0.12s total=4.13s"
    )


def test_format_span_partial_renders_dashes_and_never_raises():
    assert _format_span({}) == (
        "voice_span: vad_hold=- stt=- decide=- llm=- tts=- "
        "to_playback=- total=-"
    )
    # v1 shape: span begins at finalize; llm measured; tts unfinished.
    line = _format_span(
        {"turn_finalized": 1.0, "dispatch_start": 1.1, "dispatch_done": 4.1,
         "tts_start": 4.2}
    )
    assert "vad_hold=-" in line
    assert "llm=3.00s" in line
    assert "tts=-" in line
    assert "total=3.20s" in line
    # Garbage never raises.
    assert _format_span({"turn_finalized": "junk", "decided": None}).startswith(
        "voice_span:"
    )


def test_format_span_notes_timeout():
    line = _format_span({"turn_finalized": 1.0, "timeout": 2.0})
    assert line.endswith(" timeout")


def test_span_mark_stamps_monotonic_and_tolerates_none():
    span: dict = {}
    _span_mark(span, "turn_finalized")
    _span_mark(span, "decided")
    assert isinstance(span["turn_finalized"], float)
    assert span["decided"] >= span["turn_finalized"]
    _span_mark(None, "anything")  # no-op, never raises


def _make_session() -> VoiceConversationSession:
    class _FakeTransport:
        def __init__(self):
            self.spoken: list[str] = []
            self.texts: list[str] = []

        async def speak_text(self, text: str) -> None:
            self.spoken.append(text)

        async def send_text_message(self, text: str) -> None:
            self.texts.append(text)

        async def interrupt_playback(self) -> None:
            pass

    return VoiceConversationSession(
        channel_id=101,
        text_channel_id=102,
        transport=_FakeTransport(),
        owner_user_id=None,  # keeps _schedule_runtime_update inert
    )


@pytest.mark.asyncio
async def test_deliver_response_emits_exactly_one_span_line(caplog):
    session = _make_session()
    span: dict = {}
    _span_mark(span, "turn_finalized")
    _span_mark(span, "decided")
    _span_mark(span, "dispatch_start")
    _span_mark(span, "dispatch_done")
    session._active_span = span

    with caplog.at_level(logging.INFO, logger="voice.runtime"):
        await session._deliver_response("hello there, this is a spoken reply")

    span_lines = [r.message for r in caplog.records if "voice_span:" in r.message]
    assert len(span_lines) == 1
    line = span_lines[0]
    assert "decide=0." in line and "llm=0." in line
    # The fake transport never reaches the synthesis seam — honesty says
    # dash, not a made-up number.
    assert "to_playback=-" in line
    assert session._active_span is None  # consumed; the next turn starts fresh
    assert session.transport.spoken  # the reply was actually spoken


@pytest.mark.asyncio
async def test_no_span_line_for_silent_delivery(caplog):
    """A turn that produces no speech produces no span line."""
    session = _make_session()
    session._active_span = {"turn_finalized": 1.0}
    with caplog.at_level(logging.INFO, logger="voice.runtime"):
        await session._deliver_response("")
    assert not [r for r in caplog.records if "voice_span:" in r.message]
    assert session._active_span is None


def test_session_playback_note_stamps_active_span():
    session = _make_session()
    session._active_span = {"tts_done": 1.0}
    session._span_note_playback_start()
    assert "playback_start" in session._active_span
    session._active_span = None
    session._span_note_playback_start()  # no active span — never raises


@pytest.mark.asyncio
async def test_transport_notes_playback_start_before_playing(monkeypatch):
    """The transport tells the session the moment audio actually starts —
    getattr-guarded, so sessions without the hook are untouched."""
    from voice.discord_transport import DiscordVoiceTransport

    class _FakePCMVolumeTransformer:
        def __init__(self, source, volume=1.0):
            self.source = source

    class _FakeVoiceClient:
        def __init__(self):
            self._playing = False
            self.played_at_note: bool | None = None

        def is_playing(self):
            current = self._playing
            self._playing = False
            return current

        def play(self, source):
            self._playing = True

    monkeypatch.setattr(
        "voice.discord_transport.discord.FFmpegPCMAudio",
        lambda source, **kwargs: SimpleNamespace(source=source),
    )
    monkeypatch.setattr(
        "voice.discord_transport.discord.PCMVolumeTransformer",
        _FakePCMVolumeTransformer,
    )

    async def _fake_synth(**kwargs):
        return b"RIFFmockwav"

    noted: list[str] = []
    transport = DiscordVoiceTransport(
        voice_client=_FakeVoiceClient(), text_channel_id=1
    )
    transport._session = SimpleNamespace(
        synthesize_transport_audio=_fake_synth,
        _span_note_playback_start=lambda: noted.append("playback"),
    )
    await transport.speak_text("hello there")
    assert noted == ["playback"]

    # A session WITHOUT the hook (protocol-minimal) still plays fine.
    transport._session = SimpleNamespace(synthesize_transport_audio=_fake_synth)
    await transport.speak_text("hello again")


# ── The response deadline (step 31, spec §5) ─────────────────────────────


def _patch_owner_refs(monkeypatch):
    async def _refs():
        return {"app_user_id": 1, "discord_user_id": 42}

    monkeypatch.setattr("voice.runtime.get_local_owner_refs", _refs)


@pytest.mark.asyncio
async def test_response_deadline_times_out_to_silence(monkeypatch, caplog):
    """A hung provider must never wedge the voice loop: timeout ⇒ NO speech,
    no partial utterance — one WARNING naming the deadline, and the span
    line notes timeout."""
    session = _make_session()
    session._active_span = {"turn_finalized": 0.0}
    _patch_owner_refs(monkeypatch)

    async def _hang(turn):
        import asyncio

        await asyncio.sleep(30)

    monkeypatch.setattr("voice.runtime.dispatch_turn", _hang)
    from voice.runtime import config as _cfg

    monkeypatch.setattr(
        _cfg, "VOICE_RESPONSE_DEADLINE_SECONDS", 0.05, raising=False
    )

    with caplog.at_level(logging.INFO, logger="voice.runtime"):
        await session._respond_to_message(
            user_id=42,
            username="sam",
            transcription="hey there",
            voice_agentic_policy="auto",
        )

    assert session.transport.spoken == []  # silence, not a partial
    warnings = [
        r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING and "deadline" in r.message.lower()
    ]
    assert len(warnings) == 1
    span_lines = [r.message for r in caplog.records if "voice_span:" in r.message]
    assert len(span_lines) == 1
    assert "timeout" in span_lines[0]
    assert session._active_span is None


@pytest.mark.asyncio
async def test_response_deadline_fast_path_unaffected(monkeypatch, caplog):
    session = _make_session()
    session._active_span = {"turn_finalized": 0.0}
    _patch_owner_refs(monkeypatch)

    async def _quick(turn):
        return SimpleNamespace(responded=True, response_text="hi there.")

    monkeypatch.setattr("voice.runtime.dispatch_turn", _quick)

    with caplog.at_level(logging.INFO, logger="voice.runtime"):
        await session._respond_to_message(
            user_id=42,
            username="sam",
            transcription="hey",
            voice_agentic_policy="auto",
        )

    assert session.transport.spoken == ["hi there."]
    span_lines = [r.message for r in caplog.records if "voice_span:" in r.message]
    assert len(span_lines) == 1
    assert "timeout" not in span_lines[0]
