"""
Local speaker playback helpers.

Step 31 (2026-08-20, spec §7): gutted to playback-only. The terminal-TUI
capture stack that lived here — LocalPushToTalkRecorder,
LocalPassiveListener, LocalAudioCaptureResult, local_audio_available, and
a duplicate barge-in block — had zero callers (tests included) since the
TUI's deletion; git keeps it. What survives is what
core/ambient_service.py actually plays through.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import soundfile as sf

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - exercised via availability checks
    sd = None


def _coerce_device(value: str) -> Any | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return text


async def play_wav_file(*, filepath: str, device: Any | None = None) -> None:
    if sd is None:
        raise RuntimeError("sounddevice is not installed")

    def _play() -> None:
        data, sample_rate = sf.read(filepath, dtype="float32", always_2d=False)
        sd.play(data, sample_rate, device=device)
        sd.wait()

    await asyncio.to_thread(_play)
