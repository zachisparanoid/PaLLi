"""Step 31 (spec §7): the tidy — the dead voice experiments leave the tree.

- voice/local_audio.py guts to playback-only: play_wav_file +
  _coerce_device survive (their one live caller is core/ambient_service);
  the recorder/listener/capture classes and local_audio_available had
  ZERO callers, tests included.
- The deleted-TUI ghost: data/palli.yaml's `local_tui:` block goes; the
  schema's LocalTuiConfig STAYS (key removal is its own migration) and
  its defaults are byte-equal to what the block said, so snapshots hold.
- The never-called seams: VoiceReplyBundle + the dedicated-generation
  branch, render_voice_prompt / CompiledIdentity.voice_prompt (the voice
  register rides the SHARED prompt's band, spec §4 — a second renderer is
  the 08-13/08-17 "her page reached zero replies" trap reborn), and the
  pre-_decide_turn_response responder pair.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_local_audio_is_playback_only():
    import voice.local_audio as local_audio

    assert callable(local_audio.play_wav_file)
    assert callable(local_audio._coerce_device)
    for dead in (
        "LocalPushToTalkRecorder",
        "LocalPassiveListener",
        "LocalAudioCaptureResult",
        "local_audio_available",
    ):
        assert not hasattr(local_audio, dead), dead


def test_ambient_service_survivor_imports_stay_clean():
    from core.ambient_service import _coerce_device, play_wav_file  # noqa: F401


def test_local_tui_yaml_block_is_gone_and_defaults_hold():
    # The SHIPPED config, not his: data/palli.yaml exists in a clone only because
    # tests/conftest.py seeds it, which made this test depend on suite order (the
    # assembled public tree, 2026-09-18). What ships is what this is about anyway.
    _config = _REPO / "data" / "palli.yaml"
    if not _config.is_file():
        _config = _REPO / "data" / "palli.example.yaml"
    raw = _config.read_text(encoding="utf-8")
    assert "local_tui:" not in raw
    from core.settings_service import SettingsService

    voice = SettingsService(yaml_path=str(_config)).load().voice
    # The schema defaults are byte-equal to what the deleted block said.
    assert voice.local_tui.enabled is True
    assert voice.local_tui.default_mode.value == "push_to_talk"


def test_voice_reply_bundle_seam_is_gone():
    import voice.runtime as runtime
    import voice.types as voice_types

    assert not hasattr(voice_types, "VoiceReplyBundle")
    assert not hasattr(runtime, "VoiceReplyBundle")


def test_voice_prompt_renderer_seam_is_gone():
    from core.identity import compiler, models

    assert not hasattr(compiler, "render_voice_prompt")
    assert not hasattr(models.CompiledIdentity, "voice_prompt")


def test_superseded_responders_are_gone():
    from voice.runtime import VoiceConversationSession

    assert not hasattr(VoiceConversationSession, "_should_respond_direct_address")
    assert not hasattr(VoiceConversationSession, "_should_respond_local_passive")
