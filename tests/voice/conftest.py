"""Shared pins for the voice suite.

Step 28 task 4 fix 2: the SHIPPED config (data/palli.example.yaml, which tests/conftest.py
seeds the settings service from) moved the voice providers to the keyless local pair —
faster_whisper for STT, pyttsx3 for TTS — so a fresh clone needs no Deepgram or Lemonfox
key. Three tests that exercise the DEEPGRAM path resolved the provider from that ambient
snapshot instead of pinning it, and started failing with "faster-whisper is not installed"
— a true statement about a package this repo deliberately does not require, and nothing at
all about the code under test. The fixture below is the pin they were missing.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def deepgram_stt(monkeypatch):
    """Pin the live snapshot's STT provider to deepgram for one test.

    Not autouse: a test that means to exercise whatever the shipped config selects should
    keep doing that. This is for the ones whose subject IS the Deepgram path.
    """
    from core.settings_boot import settings_service
    from core.settings_schema import STTProvider

    voice = settings_service.snapshot().voice
    monkeypatch.setattr(voice, "stt_provider", STTProvider.DEEPGRAM, raising=True)
    return voice
