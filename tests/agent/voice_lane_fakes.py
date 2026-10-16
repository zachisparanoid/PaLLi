"""Fakes for the voice-out lane (Batch 8, task 10) — never a real TTS.

The speech facade (``voice.speech.voice_speech_service``) and the bounded
ffmpeg runner (``core.ffmpeg.run_ffmpeg_checked``) are replaced at their own
modules, so everything between them — ``voice.voice_note_out``, the tools,
the doors — is the real code. The same shapes as the Telegram door's own
fakes (tests/bots/test_telegram_voice_out.py). Not collected (no ``test_``
prefix).
"""
from __future__ import annotations

from types import SimpleNamespace

WAV = b"RIFF\x00\x00\x00\x00WAVEfake-pcm"
OGG = b"OggS\x00fake-opus-bytes"


class FakeTTS:
    """The facade's one method the voice-out path uses, exact keywords."""

    def __init__(self, audio: bytes | None = WAV, *, boom: BaseException | None = None,
                 timeline: list | None = None):
        self.audio = audio
        self.boom = boom
        self.timeline = timeline
        self.calls: list[dict] = []

    async def synthesize_speech(self, *, text, encoding="linear16",
                                sample_rate=48000, container=None):
        self.calls.append({"text": text, "encoding": encoding,
                           "sample_rate": sample_rate, "container": container})
        if self.timeline is not None:
            self.timeline.append("tts")
        if self.boom is not None:
            raise self.boom
        return self.audio


class FakeFfmpeg:
    """``run_ffmpeg_checked``'s shape: writes the file it was asked for."""

    def __init__(self, *, produces: bytes | None = OGG, boom: Exception | None = None):
        self.produces = produces
        self.boom = boom
        self.commands: list[list[str]] = []

    async def __call__(self, args, *, timeout_seconds=None):
        argv = [str(a) for a in args]
        self.commands.append(argv)
        if self.boom is not None:
            raise self.boom
        out_path = next((a for a in argv if a.endswith(".ogg")), None)
        if self.produces is not None and out_path:
            with open(out_path, "wb") as handle:
                handle.write(self.produces)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def install(monkeypatch, tts: FakeTTS | None = None, ffmpeg: FakeFfmpeg | None = None):
    """Put the fakes where the lazy imports find them; returns both."""
    import core.ffmpeg as _ffmpeg
    import voice.speech as _speech

    tts = tts or FakeTTS()
    ffmpeg = ffmpeg or FakeFfmpeg()
    monkeypatch.setattr(_speech, "voice_speech_service", tts)
    monkeypatch.setattr(_ffmpeg, "run_ffmpeg_checked", ffmpeg)
    return tts, ffmpeg


def voice_notes_on(monkeypatch) -> None:
    """Turn ``voice.voice_notes`` on for one test (the suite pins it off)."""
    from core import voice_replies

    monkeypatch.setattr(voice_replies, "voice_notes_enabled", lambda: True)


def her_hands_on(monkeypatch) -> None:
    """Turn ``features.her_hands`` on for one test (the suite pins it off):
    voice_note is on her belt only with her hands, and the voice status line
    points at it (final fix wave, ruling FW-16)."""
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: True)


def no_voice_on_this_host(monkeypatch) -> None:
    """Boot found no TTS for this run (``config.effective_tts_provider`` is
    "off" — the field /voice reads)."""
    from core.config import config

    monkeypatch.setattr(config, "effective_tts_provider", "off", raising=False)
