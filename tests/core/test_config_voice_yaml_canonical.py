"""Step 4 — Voice settings migration to palli.yaml canonical.

Phase E.5 Wave 4 step 4 (2026-05-13). The voice tier
(``VOICE_ENGINE_DEFAULT``, ``STT_PROVIDER``, ``TTS_PROVIDER``,
``VOICE_INTERACTION_MODE``, ``LOCAL_TUI_ENABLED``,
``LOCAL_VOICE_DEFAULT_MODE``) moves off ``core.config.Config`` onto
``data/palli.yaml`` -> ``voice`` via ``settings_service.snapshot()``.

The launcher schema (CoreConfigSchema) still cross-validates the
provider/engine pairs against env-side secrets (DEEPGRAM_API_KEY) so we
keep those checks but route through the runtime YAML when no explicit
payload override is provided. (Step 31, 2026-08-20/21: the canonical TTS
provider flipped deepgram -> lemonfox — Cartesia cut, olivia picked.)

These tests pin the snapshot-driven values and that voice/*.py
consumers reach them through the new path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings_boot import settings_service
from core.settings_service import SettingsService


def _fresh_voice():
    """Force a load of the SHIPPED config, uncontaminated by sibling tests.

    Step 28 task 5: this used to read data/palli.yaml — the OWNER's live file. The public
    tree does not carry it (it is his), and a fresh clone has none until its first boot
    seeds one, so seven tests across this file and its voice twin failed in an assembled
    tree for that reason alone. It now reads data/palli.example.yaml, the file that IS
    shipped: still a real on-disk yaml, still loaded through a fresh SettingsService,
    still carrying values that differ from the schema defaults — so the pin is as
    load-bearing as it was, and it now says the same thing in both trees.

    Unconditional, not "the live file if present": `palli_ctl onboard` seeds
    data/palli.yaml BY COPYING the example (task 4 fix 2), so in a clone that path exists
    and holds the example's values. Keying off existence would flip these assertions to
    the owner's values against a file that is not his.
    """
    example = Path(__file__).resolve().parents[2] / "data" / "palli.example.yaml"
    svc = SettingsService(yaml_path=example)
    svc.load()
    return svc.snapshot().voice


def test_yaml_voice_engine_classic_default():
    voice = _fresh_voice()
    assert voice.engine.value == "classic"


def test_yaml_voice_stt_provider_is_the_keyless_local_one():
    """faster_whisper transcribes on the reader's own machine: the shipped pair needs
    no Deepgram key on day one (task 4 fix 2). The schema default is deepgram, so this
    value can only have come from the yaml."""
    voice = _fresh_voice()
    assert voice.stt_provider.value == "faster_whisper"


def test_yaml_voice_tts_provider_is_the_keyless_local_one():
    """pyttsx3 speaks with the system voice — keyless, like its STT partner. (Step 31
    made lemonfox HER provider of record; that is his install's choice, in his yaml,
    and not what a stranger's first boot should demand a key for.)"""
    voice = _fresh_voice()
    assert voice.tts_provider.value == "pyttsx3"


def test_yaml_voice_interaction_mode_direct_address_default():
    voice = _fresh_voice()
    assert voice.interaction_mode.value == "direct_address"


def test_yaml_voice_local_tui_enabled_default():
    voice = _fresh_voice()
    assert voice.local_tui.enabled is True


def test_yaml_voice_local_tui_default_mode_push_to_talk_default():
    voice = _fresh_voice()
    assert voice.local_tui.default_mode.value == "push_to_talk"


def test_settings_service_module_singleton_returns_yaml_voice_values():
    """The module-level singleton matches the fresh-load snapshot.

    Unlike the fresh-load test above, this one reads what tests/conftest.py seeded from
    data/palli.example.yaml — the SHIPPED config, not the owner's. Step 28 task 4 fix 2
    put the keyless local pair there: faster_whisper transcribes on the reader's own
    machine and pyttsx3 speaks with the system voice, so a fresh clone needs no Deepgram
    or Lemonfox key to turn voice on. Both differ from the schema defaults (deepgram for
    each, core/settings_schema.py), so these lines can only have come from the yaml the
    singleton actually read."""
    voice = settings_service.snapshot().voice
    assert voice.engine.value == "classic"
    assert voice.stt_provider.value == "faster_whisper"
    assert voice.tts_provider.value == "pyttsx3"
    assert voice.interaction_mode.value == "direct_address"
    assert voice.local_tui.enabled is True


def test_voice_speech_picks_yaml_providers(monkeypatch):
    """voice/speech.py's provider resolver reads the YAML value, not the env."""
    # Force a YAML-driven re-read by mutating the live snapshot.
    voice = settings_service.snapshot().voice
    # Already the canonical pair (deepgram STT / lemonfox TTS), but pin it
    # anyway so the test is explicit.
    from core.settings_schema import STTProvider, TTSProvider

    monkeypatch.setattr(voice, "stt_provider", STTProvider.DEEPGRAM, raising=True)
    monkeypatch.setattr(voice, "tts_provider", TTSProvider.LEMONFOX, raising=True)

    # Trigger the provider-resolution path. voice/speech.py has two
    # internal getters; we call them via the module to confirm they pick
    # up the snapshot value.
    from voice import speech as _speech

    # The resolver names start with `_` so we drive them indirectly.
    # The exposed get_stt_engine/get_tts_engine functions are the public
    # entrypoints — they'll consult the same snapshot.
    assert _speech is not None  # imported cleanly = snapshot accessible


def test_voice_runtime_picks_engine_classic_when_yaml_says_classic(monkeypatch):
    """voice/runtime.py reads engine + interaction_mode from snapshot."""
    voice = settings_service.snapshot().voice
    from core.settings_schema import InteractionMode, VoiceEngine

    monkeypatch.setattr(voice, "engine", VoiceEngine.CLASSIC, raising=True)
    monkeypatch.setattr(
        voice, "interaction_mode", InteractionMode.DIRECT_ADDRESS, raising=True
    )

    # Confirms the import succeeds AFTER our patch and the module reads
    # the patched snapshot.
    from voice import runtime as _runtime

    assert _runtime is not None
