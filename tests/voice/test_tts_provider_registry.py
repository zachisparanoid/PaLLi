"""Step 31 (spec §3): the Cartesia cut — Lemonfox is her provider of record.

Owner's call, 2026-08-20 23:42: "cut cartesia out entirely… remain with
lemonfox throughout." Deepgram stays as STT and as the silent code-level
TTS fallback. These pins hold BOTH registries — voice/speech.py's default
AND the plugin-runtime boot bundle. The bundle REPLACES the registry at
boot (core/plugin_runtime.py installs it via replace_provider_registry),
so a provider missing there is silently absent from the live server even
when the default registry has it — the 2026-07-06 anthropic outage is
what that drift looks like. Without lemonfox in the boot bundle, the
yaml's `voice.tts_provider: lemonfox` would fall back to deepgram and
her chosen voice would never sound.
"""

from __future__ import annotations

EXPECTED_TTS = frozenset({"deepgram", "lemonfox", "pyttsx3"})


def test_default_tts_registry_is_deepgram_lemonfox_pyttsx3():
    from voice.speech import _build_default_tts_provider_registry

    assert set(_build_default_tts_provider_registry()) == set(EXPECTED_TTS)


def test_boot_bundle_tts_registry_matches_default_registry():
    """The live server's TTS registry is the boot bundle, not the default
    dict — the two must never drift (derive, don't hand-maintain)."""
    from core.plugin_runtime import plugin_runtime
    from voice.speech import (
        _build_default_stt_provider_registry,
        _build_default_tts_provider_registry,
    )

    bundle = plugin_runtime._load_builtin_bundle("builtin.voice.providers")
    assert set(bundle.tts_providers) == set(_build_default_tts_provider_registry())
    assert set(bundle.stt_providers) == set(_build_default_stt_provider_registry())


def test_tts_provider_enum_has_no_cartesia_member():
    from core.settings_schema import TTSProvider

    assert not hasattr(TTSProvider, "CARTESIA")
    assert {member.value for member in TTSProvider} == set(EXPECTED_TTS)


def test_allowed_tts_providers_is_deepgram_lemonfox_pyttsx3():
    from core.config_schema import ALLOWED_TTS_PROVIDERS

    assert ALLOWED_TTS_PROVIDERS == EXPECTED_TTS


def test_config_carries_no_cartesia_attrs():
    from core.config import Config

    leftover = [name for name in vars(Config) if "CARTESIA" in name.upper()]
    assert leftover == []
