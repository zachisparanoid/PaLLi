"""
Shared voice runtime types and protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable


TranscriptCallback = Callable[["VoiceTranscriptEvent"], Awaitable[None]]
VoiceFloorState = Literal[
    "idle",
    "listening",
    "hearing_user",
    "holding_turn",
    "thinking",
    "speaking",
    "repairing",
    "interrupted",
    "error",
]
VoiceSocialContext = Literal[
    "local_private",
    "discord_one_to_one",
    "discord_group",
]
VoiceInitiativeSource = Literal[
    "direct_address",
    "followup_window",
    "passive_context",
    "support_cue",
    "confusion_cue",
    "repair_prompt",
    "none",
]
VoiceReplyPosture = Literal[
    "brief_companion",
    "calm_support",
    "practical_operator",
    "light_social",
]
# Step 31 (spec §7): the realtime bridge is deleted — classic is THE engine.
VoiceEngineName = Literal[
    "classic",
]


@dataclass(slots=True)
class VoiceTranscriptEvent:
    """A normalized transcript event emitted by a live transcriber."""

    event: str
    user_id: int
    username: str
    transcript: str = ""
    turn_index: int = 0
    is_final: bool = False
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class VoiceReplyPlan:
    """How the runtime should deliver a response back to the user."""

    spoken_text: str = ""
    mirror_text: Optional[str] = None
    should_speak: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class VoiceReplyGuidance:
    """Deterministic guidance for the dedicated voice reply generator."""

    reply_posture: VoiceReplyPosture = "brief_companion"
    brevity_target: str = "1-2 short natural sentences"
    spoken_pacing: str = "measured"
    mirror_policy: str = "Only send a text mirror when spoken brevity would hide useful detail."
    question_policy: str = "Avoid questions unless one short follow-up is truly necessary."
    serious_moment: bool = False
    operator_mode: bool = False
    delivery_texture: str = "brief_neutral"
    support_sequence: str = "none"
    anti_patterns: list[str] = field(default_factory=list)
    social_context: VoiceSocialContext = "local_private"
    interaction_mode: str = "direct_address"
    presence_profile: str = "balanced"
    focus_state: str = "responsive"
    group_voice_behavior: str = "socially_adaptive"
    style_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_posture": self.reply_posture,
            "brevity_target": self.brevity_target,
            "spoken_pacing": self.spoken_pacing,
            "mirror_policy": self.mirror_policy,
            "question_policy": self.question_policy,
            "serious_moment": self.serious_moment,
            "operator_mode": self.operator_mode,
            "delivery_texture": self.delivery_texture,
            "support_sequence": self.support_sequence,
            "anti_patterns": list(self.anti_patterns),
            "social_context": self.social_context,
            "interaction_mode": self.interaction_mode,
            "presence_profile": self.presence_profile,
            "focus_state": self.focus_state,
            "group_voice_behavior": self.group_voice_behavior,
            "style_notes": self.style_notes,
        }


# Step 31 (2026-08-20, spec §7): VoiceReplyBundle deleted — nothing ever
# produced one. The voice register rides the SHARED prompt's band (spec
# §4); a dedicated voice generator is the 08-13/08-17 trap ("her page
# reached zero replies") reborn.


@dataclass(slots=True)
class VoiceTurnDecision:
    """Structured decision describing whether and how PaLLi should speak."""

    should_reply: bool
    decision_reason: str
    initiative_source: VoiceInitiativeSource = "none"
    reply_posture: VoiceReplyPosture = "brief_companion"
    confidence: float = 0.0
    should_mirror_text: bool = False
    identity_signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "should_reply": self.should_reply,
            "decision_reason": self.decision_reason,
            "initiative_source": self.initiative_source,
            "reply_posture": self.reply_posture,
            "confidence": self.confidence,
            "should_mirror_text": self.should_mirror_text,
        }
        if self.identity_signals:
            payload["identity_signals"] = dict(self.identity_signals)
        return payload


@dataclass(slots=True)
class VoiceSessionSnapshot:
    """Canonical live voice session snapshot used across runtime surfaces."""

    runtime_session_key: str
    platform: str
    channel_id: int
    text_channel_id: int
    owner_user_id: int | None
    floor_state: VoiceFloorState
    voice_engine: VoiceEngineName
    voice_engine_requested: VoiceEngineName
    voice_engine_fallback_active: bool
    voice_engine_fallback_reason: str
    voice_engine_fallback_started_at: float | None
    voice_engine_last_transition_at: float | None
    voice_engine_last_transition_reason: str
    voice_engine_transition_count: int
    interaction_mode: str
    presence_profile: str
    focus_state: str
    social_context: VoiceSocialContext
    human_participant_count: int
    bot_participant_count: int
    owner_present: bool
    local_transport_mode: str | None
    passive_mode: bool
    active_speaker_streams: int = 0
    pending_speaker_streams: int = 0
    cooling_down_speaker_streams: int = 0
    group_voice_behavior: str = "socially_adaptive"
    last_decision_reason: str = ""
    initiative_source: VoiceInitiativeSource = "none"
    reply_posture: VoiceReplyPosture = "brief_companion"
    last_repair_reason: str = ""
    last_response_interrupted: bool = False
    last_user_excerpt: str = ""
    last_assistant_excerpt: str = ""
    started_at: float = 0.0
    last_event_at: float = 0.0
    source: str = "runtime"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_session_key": self.runtime_session_key,
            "platform": self.platform,
            "channel_id": self.channel_id,
            "text_channel_id": self.text_channel_id,
            "owner_user_id": self.owner_user_id,
            "floor_state": self.floor_state,
            "state": self.floor_state,
            "voice_engine": self.voice_engine,
            "voice_engine_requested": self.voice_engine_requested,
            "voice_engine_fallback_active": self.voice_engine_fallback_active,
            "voice_engine_fallback_reason": self.voice_engine_fallback_reason,
            "voice_engine_fallback_started_at": self.voice_engine_fallback_started_at,
            "voice_engine_last_transition_at": self.voice_engine_last_transition_at,
            "voice_engine_last_transition_reason": self.voice_engine_last_transition_reason,
            "voice_engine_transition_count": self.voice_engine_transition_count,
            "interaction_mode": self.interaction_mode,
            "presence_profile": self.presence_profile,
            "focus_state": self.focus_state,
            "social_context": self.social_context,
            "human_participant_count": self.human_participant_count,
            "bot_participant_count": self.bot_participant_count,
            "owner_present": self.owner_present,
            "local_transport_mode": self.local_transport_mode,
            "passive_mode": self.passive_mode,
            "active_speaker_streams": self.active_speaker_streams,
            "pending_speaker_streams": self.pending_speaker_streams,
            "cooling_down_speaker_streams": self.cooling_down_speaker_streams,
            "group_voice_behavior": self.group_voice_behavior,
            "last_decision_reason": self.last_decision_reason,
            "initiative_source": self.initiative_source,
            "reply_posture": self.reply_posture,
            "last_repair_reason": self.last_repair_reason,
            "last_response_interrupted": self.last_response_interrupted,
            "last_user_excerpt": self.last_user_excerpt,
            "last_assistant_excerpt": self.last_assistant_excerpt,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
            "source": self.source,
        }


@dataclass(slots=True)
class VoiceBootstrapStatus:
    """Voice dependency/bootstrap diagnostics for operator visibility."""

    ok: bool
    messages: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@runtime_checkable
class StreamingTranscriber(Protocol):
    """Platform-neutral live speech-to-text contract."""

    async def start(self) -> None:
        ...

    async def push_audio(self, audio_bytes: bytes) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class VoiceTransport(Protocol):
    """Platform-specific audio transport contract."""

    async def start(self, session: "VoiceConversationSessionProtocol") -> None:
        ...

    async def stop(self) -> None:
        ...

    async def interrupt_playback(self) -> None:
        ...

    async def speak_text(self, text: str) -> None:
        ...

    async def send_text_message(self, text: str) -> None:
        ...


@runtime_checkable
class VoiceConversationSessionProtocol(Protocol):
    """Minimal interface the transport uses to notify the runtime."""

    async def on_audio_frame(self, user_id: int, username: str, audio_bytes: bytes) -> None:
        ...

    async def on_transport_error(self, error: Exception) -> None:
        ...

    async def synthesize_transport_audio(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        ...
