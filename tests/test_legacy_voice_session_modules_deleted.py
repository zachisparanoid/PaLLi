"""After C2.2c, the 10 legacy voice/session_* modules must not be importable.

C2.2c (Phase C, 2026-05-12) consolidated the voice session-cluster:
- 8 Protocol-back-view coordinator modules merged into voice/runtime.py
- 2 helper modules renamed (session_speech_io -> speech_io,
  session_debug_capture -> debug_capture).

Total deletion: ~2,323 LOC across 10 source files plus their unit tests.
The behavior moved onto VoiceConversationSession in voice/runtime.py.
The one survivor: voice/session_engine.py (cohesive STT/TTS engine
lifecycle owner, intentionally kept at this name).
"""
import pytest


@pytest.mark.parametrize(
    "module",
    [
        "voice.session_audio_flow",
        "voice.session_lifecycle",
        "voice.session_runtime_state",
        "voice.session_runtime_updates",
        "voice.session_transport_output",
        "voice.session_turn_flow",
        "voice.session_response_flow",
        "voice.session_presence_flow",
        "voice.session_speech_io",
        "voice.session_debug_capture",
    ],
)
def test_legacy_voice_session_module_deleted(module):
    with pytest.raises(ImportError):
        __import__(module)


def test_canonical_voice_runtime_imports_cleanly():
    """The consolidated voice/runtime.py must own VoiceConversationSession,
    VoiceRuntimeManager, normalize_voice_floor_state, BargeInCandidate,
    PendingVoiceTurn, and the snapshot construction helpers."""
    from voice.runtime import (  # noqa: F401
        BargeInCandidate,
        PendingVoiceTurn,
        VoiceConversationSession,
        VoiceParticipantSnapshot,
        VoiceRuntimeManager,
        build_runtime_session_touch_kwargs,
        build_voice_session_snapshot,
        build_voice_session_start_kwargs,
        build_voice_session_state_kwargs,
        build_voice_turn_decision_snapshot,
        capture_live_participant_snapshot,
        get_runtime_session_status,
        normalize_voice_floor_state,
        refresh_session_social_context,
    )


def test_renamed_helpers_import_at_canonical_locations():
    """The two surviving voice/session_* helpers were renamed (not deleted).
    Their canonical post-C2.2c paths must import cleanly."""
    from voice.debug_capture import VoiceSessionDebugCaptureRecorder  # noqa: F401
    from voice.speech_io import SpeakerTurnStream, VoiceSessionSpeechIO  # noqa: F401


def test_session_engine_module_survived():
    """voice/session_engine.py is the only voice/session_*.py survivor; it owns
    the cohesive STT/TTS engine lifecycle and is intentionally kept at this name."""
    from voice.session_engine import VoiceSessionEngineController  # noqa: F401
