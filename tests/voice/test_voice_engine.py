from __future__ import annotations

# Step 31 (2026-08-20, spec §7): the three bridge tests this file held
# (surface-flag availability, surface-disabled fallback, bridge-when-
# enabled) retired with the deleted openai_realtime_bridge experiment —
# the machinery they exercised (surface_allows / available_voice_engines /
# the bridge resolution arms) is gone. The classic-collapse contract they
# leave behind is pinned in test_bridge_retired.py; what remains here is
# the resolution's classic happy path.

from voice.engine import VOICE_ENGINE_CLASSIC, resolve_voice_engine


def test_resolve_voice_engine_classic_is_effective_everywhere():
    for platform in ("discord", "local", "web_local", None):
        resolution = resolve_voice_engine(VOICE_ENGINE_CLASSIC, platform=platform)
        assert resolution.requested == VOICE_ENGINE_CLASSIC
        assert resolution.effective == VOICE_ENGINE_CLASSIC
        assert resolution.fallback_active is False
