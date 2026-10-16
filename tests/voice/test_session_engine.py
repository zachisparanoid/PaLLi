from __future__ import annotations

from typing import Any

import pytest

from voice.engine import VOICE_ENGINE_CLASSIC, VoiceEngineResolution
from voice.session_engine import VoiceSessionEngineController

# Step 31 (2026-08-20, spec §7):
# - test_session_engine_controller_downgrades_to_classic_preserving_requested
#   retired — downgrade_to_classic left with the deleted realtime bridge
#   (classic is the only engine; there is nothing to downgrade FROM).
# - the set_requested_engine reset-task test became the reconfigure test
#   below — every engine name now normalizes to classic, so the requested-
#   engine transition it drove can no longer occur; the lifecycle it really
#   pinned (streams closed -> old engine closed -> fresh engine ensured)
#   rides the reconfigure() path the runtime still calls on settings
#   changes.


class _DummyEngine:
    def __init__(self, name: str):
        self.name = name
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _resolution(requested: str, *, effective: str | None = None) -> VoiceEngineResolution:
    return VoiceEngineResolution(
        requested=requested,
        effective=effective or requested,
        fallback_active=False,
        fallback_reason="",
    )


@pytest.mark.asyncio
async def test_session_engine_controller_reconfigure_recycles_engine_and_streams(monkeypatch):
    close_speaker_calls: list[str] = []
    built_engines: list[_DummyEngine] = []

    monkeypatch.setattr(
        "voice.session_engine.resolve_voice_engine",
        lambda requested_engine, *, platform: _resolution(VOICE_ENGINE_CLASSIC),
    )

    def _fake_build_voice_engine(*, requested_engine: str | None, platform: str | None) -> tuple[VoiceEngineResolution, Any]:
        engine = _DummyEngine("classic")
        built_engines.append(engine)
        return _resolution(VOICE_ENGINE_CLASSIC), engine

    monkeypatch.setattr("voice.session_engine.build_voice_engine", _fake_build_voice_engine)

    controller = VoiceSessionEngineController(
        channel_id=77,
        platform="local",
        requested_engine=VOICE_ENGINE_CLASSIC,
        on_state_changed=lambda: None,
        close_speaker_streams=lambda: _close_speakers(close_speaker_calls),
    )

    first = await controller.ensure_engine()
    assert built_engines == [first]

    await controller.reconfigure(reason="settings_changed")

    assert close_speaker_calls == ["closed"]
    assert first.closed is True
    assert len(built_engines) == 2
    assert built_engines[1].closed is False
    assert controller.effective == VOICE_ENGINE_CLASSIC


async def _close_speakers(calls: list[str]) -> None:
    calls.append("closed")
