"""Step 31 (spec §7): the OpenAI realtime-bridge experiment is DELETED.

It was unreachable in the live tree (yaml `engine: classic` +
VOICE_OPENAI_REALTIME_ENABLE_DISCORD=false) — 385 lines of parked
experiment. Deleted, not archived: git keeps the idea. Classic is THE
engine; every engine-name input collapses to classic.
"""

from __future__ import annotations

import importlib.util

from typing import get_args


def test_bridge_module_is_gone():
    assert importlib.util.find_spec("voice.openai_realtime_bridge") is None


def test_engine_helpers_collapse_to_classic():
    from voice.engine import (
        VOICE_ENGINE_CLASSIC,
        build_voice_engine,
        normalize_voice_engine,
        resolve_voice_engine,
    )

    assert normalize_voice_engine("openai_realtime_bridge") == VOICE_ENGINE_CLASSIC
    resolution = resolve_voice_engine("openai_realtime_bridge", platform="discord")
    assert resolution.effective == VOICE_ENGINE_CLASSIC
    for platform in ("discord", "local", "web_local"):
        _, engine = build_voice_engine(
            requested_engine="openai_realtime_bridge", platform=platform
        )
        assert engine.name == VOICE_ENGINE_CLASSIC


def test_voice_engine_enum_is_single_valued():
    from core.settings_schema import VoiceEngine

    assert not hasattr(VoiceEngine, "OPENAI_REALTIME_BRIDGE")
    assert {member.value for member in VoiceEngine} == {"classic"}


def test_voice_engine_name_literal_is_classic_only():
    from voice.types import VoiceEngineName

    assert get_args(VoiceEngineName) == ("classic",)


def test_allowed_voice_engines_schema_set():
    from core.config_schema import ALLOWED_VOICE_ENGINES

    assert ALLOWED_VOICE_ENGINES == frozenset({"classic"})


def test_config_carries_no_bridge_attrs():
    from core.config import Config

    leftover = [
        name
        for name in vars(Config)
        if "OPENAI_REALTIME" in name.upper()
    ]
    assert leftover == []
