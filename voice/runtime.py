"""
Platform-neutral voice runtime and session orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, cast

from voice.voice_interjection_analyzer import voice_interjection_analyzer
from core.activity_logger import activity_logger
from core.agent.dispatch import dispatch_turn
from core.config import config
from core.database import brain
from core.local_mode import get_local_owner_refs
from core.runtime_control import runtime_control
from core.turn.ingest import normalize_turn
from core.voice_text_cleaner import clean_text_for_voice
from voice.engine import (
    VOICE_ENGINE_CLASSIC,
    VoiceEngine,
)
from voice.manager_lifecycle import (
    VoiceRuntimeSessionStartRequest,
    start_runtime_session,
    stop_runtime_session_by_key,
)
from voice.manager_registry import (
    ManagedVoiceSession,
    VoiceRuntimeSessionRegistry,
    abort_registered_voice_session,
    apply_registered_voice_runtime_overrides,
    apply_registered_voice_session_overrides,
    get_registered_voice_runtime_snapshot,
    list_registered_voice_runtime_snapshots,
    register_runtime_manager,
    stop_registered_voice_session,
)
from voice.debug_capture import VoiceSessionDebugCaptureRecorder
from voice.session_engine import VoiceSessionEngineController
from voice.presence_policy import VoicePresencePolicy
from voice.speech_io import SpeakerTurnStream, VoiceSessionSpeechIO
from voice.types import (
    VoiceFloorState,
    VoiceReplyPlan,
    VoiceSessionSnapshot,
    VoiceSocialContext,
    VoiceTranscriptEvent,
    VoiceTransport,
    VoiceTurnDecision,
)

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


def _voice_turn_identity(*, user_id: int, refs: dict) -> tuple[int, str]:
    """(owner_id, origin) for a spoken utterance (item 7a, 2026-08-19):
    his Discord id → (app owner, "user"); anyone else → (their id, "guest").

    Week 2 task 3 (gap §5.1.5): an UNRESOLVED owner discord id is a guest
    too. It used to return the owner turn, so with no owner id configured
    every voice in the channel spoke with his tools and his memory. A
    speaker who cannot be proved to be him gets a plain guest turn — and a
    speaker id we cannot even read is still a guest, carried under the app
    owner id because there is no other id to carry it under.
    """
    from core.owner_gate import GUEST_ORIGIN, classify_author, is_owner_verdict

    app_owner = int(refs.get("app_user_id") or 0)
    verdict = classify_author(user_id, refs.get("discord_user_id"))
    if not is_owner_verdict(verdict):
        try:
            return int(user_id), GUEST_ORIGIN
        except (TypeError, ValueError):
            return app_owner, GUEST_ORIGIN
    return app_owner, "user"


async def _emit_voice_runtime_section_update(owner_user_id: int) -> None:
    # Legacy dashboard section-update emit removed in W5.4.
    return


@dataclass
class BargeInCandidate:
    accumulated_ms: float
    last_frame_at_monotonic: float


@dataclass
class PendingVoiceTurn:
    user_id: int
    username: str
    transcript: str
    created_at_monotonic: float
    updated_at_monotonic: float
    speech_metrics: dict[str, Any]
    user_toggles: dict[str, bool] | None


@dataclass(slots=True)
class VoiceParticipantSnapshot:
    social_context: VoiceSocialContext
    human_participant_count: int
    bot_participant_count: int
    owner_present: bool


# The span (step 31, spec §5): one honest INFO line per spoken turn — the
# evidence the latency-tuning follow-up runs on. Log-only v1; monotonic
# stamps; a stage the probes can't reach renders "-" (never reconstruct a
# number — vad_hold and stt stay "-" in v1: the buffered transcriber fuses
# them upstream of the runtime's sight, so the span begins at finalize).
_SPAN_STAGES = (
    ("vad_hold", "speech_ended", "turn_finalized"),
    ("stt", "stt_start", "stt_done"),
    ("decide", "turn_finalized", "decided"),
    ("llm", "dispatch_start", "dispatch_done"),
    ("tts", "tts_start", "tts_done"),
    ("to_playback", "tts_done", "playback_start"),
)


def _span_mark(span: Optional[Dict[str, float]], stage: str) -> None:
    """Stamp a monotonic time onto the span. Tolerates None; never raises."""
    if span is None:
        return
    try:
        span[stage] = time.monotonic()
    except Exception:
        pass


def _format_span(span: Dict[str, float]) -> str:
    """Render the one-line span. Missing stages render `-`; never raises."""
    try:
        parts = []
        for label, start_key, end_key in _SPAN_STAGES:
            start, end = span.get(start_key), span.get(end_key)
            if (
                isinstance(start, (int, float))
                and isinstance(end, (int, float))
                and end >= start
            ):
                parts.append(f"{label}={end - start:.2f}s")
            else:
                parts.append(f"{label}=-")
        first = span.get("turn_finalized")
        stamps = [
            value
            for key, value in span.items()
            if key != "timeout" and isinstance(value, (int, float))
        ]
        if isinstance(first, (int, float)) and stamps and max(stamps) >= first:
            parts.append(f"total={max(stamps) - first:.2f}s")
        else:
            parts.append("total=-")
        if "timeout" in span:
            parts.append("timeout")
        return "voice_span: " + " ".join(parts)
    except Exception:
        return "voice_span: unavailable"


def normalize_voice_floor_state(state: str) -> VoiceFloorState:
    normalized = str(state or "").strip().lower()
    mapping = {
        "user_speaking": "hearing_user",
        "user_turn_hold": "holding_turn",
    }
    normalized = mapping.get(normalized, normalized)
    if normalized in {
        "idle",
        "listening",
        "hearing_user",
        "holding_turn",
        "thinking",
        "speaking",
        "repairing",
        "interrupted",
        "error",
    }:
        return normalized  # type: ignore[return-value]
    return "idle"


# ----------------------------------------------------------------------
# Snapshot construction helpers (inlined from voice/session_runtime_state.py)
# ----------------------------------------------------------------------


def build_voice_turn_decision_snapshot(session: "VoiceConversationSession") -> dict[str, Any] | None:
    decision_reason = str(session.last_decision_reason or "").strip()
    initiative_source = str(session.initiative_source or "none").strip() or "none"
    reply_posture = str(session.reply_posture or "brief_companion").strip() or "brief_companion"
    should_reply = session.last_turn_should_reply
    confidence = session.last_turn_decision_confidence
    should_mirror_text = session.last_turn_should_mirror_text
    identity_signals = dict(session.last_turn_identity_signals or {})
    if should_reply is None and not decision_reason and not identity_signals:
        return None

    payload: dict[str, Any] = {
        "decision_reason": decision_reason,
        "initiative_source": initiative_source,
        "reply_posture": reply_posture,
    }
    if should_reply is not None:
        payload["should_reply"] = bool(should_reply)
    if confidence is not None:
        payload["confidence"] = float(confidence)
    if should_mirror_text is not None:
        payload["should_mirror_text"] = bool(should_mirror_text)
    if identity_signals:
        payload["identity_signals"] = identity_signals
    return payload


def capture_live_participant_snapshot(session: "VoiceConversationSession") -> VoiceParticipantSnapshot:
    if session.platform == "local":
        return VoiceParticipantSnapshot(
            social_context="local_private",
            human_participant_count=1,
            bot_participant_count=1,
            owner_present=True,
        )

    voice_client = getattr(session.transport, "voice_client", None)
    channel = getattr(voice_client, "channel", None)
    members = list(getattr(channel, "members", []) or [])
    human_count = sum(1 for member in members if not bool(getattr(member, "bot", False)))
    bot_count = sum(1 for member in members if bool(getattr(member, "bot", False)))
    owner_present = any(
        int(getattr(member, "id", 0) or 0) == int(session.initiator_user_id or 0)
        for member in members
        if not bool(getattr(member, "bot", False))
    )
    social_context = cast(VoiceSocialContext, "discord_one_to_one" if human_count <= 1 else "discord_group")
    return VoiceParticipantSnapshot(
        social_context=social_context,
        human_participant_count=human_count,
        bot_participant_count=bot_count,
        owner_present=owner_present,
    )


def refresh_session_social_context(session: "VoiceConversationSession") -> VoiceParticipantSnapshot:
    participant_snapshot = capture_live_participant_snapshot(session)
    session.social_context = participant_snapshot.social_context
    session.human_participant_count = participant_snapshot.human_participant_count
    session.bot_participant_count = participant_snapshot.bot_participant_count
    session.owner_present = participant_snapshot.owner_present
    return participant_snapshot


def build_voice_session_snapshot(
    session: "VoiceConversationSession",
    *,
    normalize_floor_state: Callable[[str], VoiceFloorState],
) -> dict[str, Any]:
    participant_snapshot = refresh_session_social_context(session)
    controller = session._voice_engine_controller
    speech_io = session._speech_io
    snapshot = VoiceSessionSnapshot(
        runtime_session_key=session.runtime_session_key,
        platform=session.platform,
        channel_id=session.channel_id,
        text_channel_id=session.text_channel_id,
        owner_user_id=session.owner_user_id,
        floor_state=normalize_floor_state(session.state),
        voice_engine=cast(Any, session.voice_engine),
        voice_engine_requested=cast(Any, session.voice_engine_requested),
        voice_engine_fallback_active=session.voice_engine_fallback_active,
        voice_engine_fallback_reason=session.voice_engine_fallback_reason,
        voice_engine_fallback_started_at=controller.fallback_started_at,
        voice_engine_last_transition_at=controller.last_transition_at,
        voice_engine_last_transition_reason=controller.last_transition_reason,
        voice_engine_transition_count=controller.transition_count,
        interaction_mode=session.interaction_mode,
        presence_profile=session.presence_profile,
        focus_state=session.focus_state,
        social_context=participant_snapshot.social_context,
        human_participant_count=participant_snapshot.human_participant_count,
        bot_participant_count=participant_snapshot.bot_participant_count,
        owner_present=participant_snapshot.owner_present,
        local_transport_mode=session.local_transport_mode,
        passive_mode=session.passive_mode_enabled,
        active_speaker_streams=speech_io.active_stream_count(),
        pending_speaker_streams=speech_io.pending_stream_count(),
        cooling_down_speaker_streams=speech_io.cooldown_count(),
        group_voice_behavior=session.group_voice_behavior,
        last_decision_reason=session.last_decision_reason,
        initiative_source=cast(Any, session.initiative_source),
        reply_posture=cast(Any, session.reply_posture),
        last_repair_reason=session.last_repair_reason,
        last_response_interrupted=session.last_response_interrupted,
        last_user_excerpt=session.last_user_excerpt,
        last_assistant_excerpt=session.last_assistant_excerpt,
        started_at=session.started_at,
        last_event_at=session.last_event_at,
        source="runtime",
    ).to_dict()
    turn_decision = build_voice_turn_decision_snapshot(session)
    if turn_decision:
        snapshot["turn_decision"] = turn_decision
    return snapshot


def build_runtime_session_touch_kwargs(
    snapshot: Mapping[str, Any],
    *,
    runtime_session_key: str,
) -> dict[str, Any]:
    return {
        "session_key": runtime_session_key,
        "status": get_runtime_session_status(snapshot),
        "metadata": {
            "origin": "voice_runtime",
            "voice": dict(snapshot),
        },
        "last_activity": float(snapshot.get("last_event_at") or time.time()),
    }


def build_voice_session_state_kwargs(
    snapshot: Mapping[str, Any],
    *,
    last_floor_state: str | None = None,
    end_time: float | None = None,
) -> dict[str, Any]:
    snapshot_dict = dict(snapshot)
    payload = {
        "last_floor_state": str(last_floor_state or snapshot_dict.get("floor_state") or "idle"),
        "interaction_mode": str(snapshot_dict.get("interaction_mode") or "").strip() or None,
        "presence_profile": str(snapshot_dict.get("presence_profile") or "").strip() or None,
        "focus_state": str(snapshot_dict.get("focus_state") or "").strip() or None,
        "social_context": str(snapshot_dict.get("social_context") or "").strip() or None,
        "human_participant_count": int(snapshot_dict.get("human_participant_count") or 0),
        "bot_participant_count": int(snapshot_dict.get("bot_participant_count") or 0),
        "owner_present": bool(snapshot_dict.get("owner_present")),
        "last_decision_reason": str(snapshot_dict.get("last_decision_reason") or "").strip() or None,
        "initiative_source": str(snapshot_dict.get("initiative_source") or "").strip() or None,
        "reply_posture": str(snapshot_dict.get("reply_posture") or "").strip() or None,
        "local_transport_mode": str(snapshot_dict.get("local_transport_mode") or "").strip() or None,
        "metadata": snapshot_dict,
    }
    if end_time is not None:
        payload["end_time"] = float(end_time)
    return payload


def build_voice_session_start_kwargs(
    session: "VoiceConversationSession",
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "channel_id": session.channel_id,
        "user_id": session.owner_user_id,
        "platform": session.platform,
        "text_channel_id": session.text_channel_id,
        "runtime_session_key": session.runtime_session_key or None,
        **build_voice_session_state_kwargs(snapshot),
    }


def get_runtime_session_status(snapshot: Mapping[str, Any]) -> str:
    floor_state = str(snapshot.get("floor_state") or "idle")
    if floor_state == "error":
        return "degraded"
    if floor_state == "idle":
        return "ready"
    return "active"


class VoiceConversationSession:
    """Owns voice turn-taking, streaming transcription, and response delivery for one channel."""

    def __init__(
        self,
        *,
        channel_id: int,
        text_channel_id: int,
        transport: VoiceTransport,
        owner_user_id: int | None = None,
        initiator_user_id: int | None = None,
        account_user_id: int | None = None,
        account_features: Dict[str, bool] | None = None,
        platform: str = "discord",
        is_dm: bool = False,
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.channel_id = channel_id
        self.text_channel_id = text_channel_id
        self.transport = transport
        self.owner_user_id = owner_user_id
        self.initiator_user_id = initiator_user_id
        self.account_user_id = account_user_id
        self.account_features = account_features or {}
        self.platform = str(platform or "discord")
        self.is_dm = bool(is_dm)
        self.event_callback = event_callback
        self.runtime_session_key = ""
        self.voice_session_id: int | None = None
        self.started_at = time.time()
        self.last_event_at = self.started_at

        self.state = "idle"
        self.is_active = True
        self.listening_personality = "observer"
        self.presence_profile = "balanced"
        # E.5 Wave 4 step 4 (2026-05-13): VOICE_INTERACTION_MODE moved
        # off core.config.Config onto data/palli.yaml -> voice.interaction_mode.
        try:
            import importlib as _importlib
            _settings = _importlib.import_module("core.settings_boot").settings_service
            self.interaction_mode = str(_settings.snapshot().voice.interaction_mode.value or "direct_address").strip().lower()
        except Exception:
            self.interaction_mode = "direct_address"
        self.local_transport_mode = "push_to_talk" if self.platform == "local" else None
        self.passive_mode_enabled = False
        self.passive_context_buffer: list[dict] = []
        self.last_interjection_check = 0.0
        self._last_passive_turn_at = 0.0
        self.focus_state = "responsive"
        self.repair_needed = "none"
        self.last_repair_reason = ""
        self.last_user_excerpt = ""
        self.last_assistant_excerpt = ""
        self.last_response_interrupted = False
        self.group_voice_behavior = "socially_adaptive"
        self.social_context: VoiceSocialContext = "local_private" if self.platform == "local" else "discord_group"
        self.human_participant_count = 1 if self.platform == "local" else 0
        self.bot_participant_count = 1
        self.owner_present = self.platform == "local"
        self.last_decision_reason = ""
        self.initiative_source = "none"
        self.reply_posture = "brief_companion"
        self.last_turn_should_reply: bool | None = None
        self.last_turn_decision_confidence: float | None = None
        self.last_turn_should_mirror_text: bool | None = None
        self.last_turn_identity_signals: Dict[str, Any] = {}
        self.reply_identity_delivery: Dict[str, Any] = {}
        self._last_unsolicited_reply_at = 0.0
        self._last_unsolicited_blocked_at = 0.0
        self._needs_interruption_repair_opener = False
        self._pending_turn: Optional[PendingVoiceTurn] = None
        self._pending_turn_task: Optional[asyncio.Task] = None
        # The span (step 31, spec §5): the current turn's timing stamps —
        # created at finalize, consumed (logged) at delivery. One live
        # response at a time (_launch_response_task interrupts the last).
        self._active_span: Optional[Dict[str, float]] = None
        self._recent_assistant_spoken_text = ""
        self._recent_assistant_spoken_at = 0.0

        # Audio-flow state (inlined from VoiceSessionAudioCoordinator)
        self._barge_in_candidates: dict[int, BargeInCandidate] = {}
        self._frame_trace_counts: dict[int, int] = {}

        # Transport-output state (inlined from VoiceSessionTransportOutputCoordinator)
        self._transport_output_task_lock = asyncio.Lock()
        self._transport_output_playback_task: asyncio.Task[None] | None = None

        # Runtime-update state (inlined from VoiceSessionRuntimeCoordinator)
        self._runtime_update_requested = False
        self._runtime_update_task: asyncio.Task[None] | None = None
        self._runtime_updates_stopping = False

        # Response-flow state (inlined from VoiceSessionResponseCoordinator)
        self._response_task: asyncio.Task[None] | None = None
        self._response_task_lock = asyncio.Lock()

        # Interjection analyzer (used by inlined presence-flow methods)
        self._interjection_analyzer = voice_interjection_analyzer

        self._debug_capture_recorder = VoiceSessionDebugCaptureRecorder(channel_id=self.channel_id)
        self._interjection_check_task: Optional[asyncio.Task] = None
        self.last_addressed_user_id: Optional[int] = None
        self.last_addressed_at = 0.0

        self._speech_io = VoiceSessionSpeechIO(
            channel_id=self.channel_id,
            ensure_voice_engine=lambda: self._ensure_voice_engine(),
            on_transcript_event=lambda event: self.on_transcript_event(event),
            on_stream_state_changed=self._schedule_runtime_update,
            trace=self._trace,
        )
        self.speaker_streams = self._speech_io.speaker_streams
        self._speaker_stream_tasks = self._speech_io.pending_stream_tasks
        self._speaker_cooldowns = self._speech_io.speaker_cooldowns
        # E.5 Wave 4 step 4 (2026-05-13): VOICE_ENGINE_DEFAULT moved off
        # core.config.Config onto data/palli.yaml -> voice.engine.
        try:
            import importlib as _importlib
            _settings = _importlib.import_module("core.settings_boot").settings_service
            _requested_engine = str(_settings.snapshot().voice.engine.value or VOICE_ENGINE_CLASSIC)
        except Exception:
            _requested_engine = VOICE_ENGINE_CLASSIC
        self._voice_engine_controller = VoiceSessionEngineController(
            channel_id=self.channel_id,
            platform=self.platform,
            requested_engine=_requested_engine,
            on_state_changed=self._mark_voice_engine_state_changed,
            close_speaker_streams=self._close_all_speaker_streams,
        )
        self._refresh_passive_mode_enabled()
        self._refresh_social_context()

    def _mark_voice_engine_state_changed(self) -> None:
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    @property
    def voice_engine_requested(self) -> str:
        return self._voice_engine_controller.requested

    @voice_engine_requested.setter
    def voice_engine_requested(self, value: str) -> None:
        self._voice_engine_controller.override_state(requested=value)

    @property
    def voice_engine(self) -> str:
        return self._voice_engine_controller.effective

    @voice_engine.setter
    def voice_engine(self, value: str) -> None:
        self._voice_engine_controller.override_state(effective=value)

    @property
    def voice_engine_fallback_active(self) -> bool:
        return self._voice_engine_controller.fallback_active

    @voice_engine_fallback_active.setter
    def voice_engine_fallback_active(self, value: bool) -> None:
        self._voice_engine_controller.override_state(fallback_active=value)

    @property
    def voice_engine_fallback_reason(self) -> str:
        return self._voice_engine_controller.fallback_reason

    @voice_engine_fallback_reason.setter
    def voice_engine_fallback_reason(self, value: str) -> None:
        self._voice_engine_controller.override_state(fallback_reason=value)

    # ------------------------------------------------------------------
    # Lifecycle / state-event helpers (inlined from VoiceSessionLifecycleCoordinator)
    # ------------------------------------------------------------------

    async def _emit_event(self, payload: Dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        await self.event_callback(payload)

    async def _set_state(self, state: str) -> None:
        self.state = state
        self.last_event_at = time.time()
        floor_state = normalize_voice_floor_state(state)
        await self._emit_event(
            {
                "type": "state",
                "state": floor_state,
                "floor_state": floor_state,
                "channel_id": self.channel_id,
                "runtime_session_key": self.runtime_session_key,
                "platform": self.platform,
            }
        )
        self._schedule_runtime_update()

    def _refresh_passive_mode_enabled(self) -> None:
        passive_policy_enabled = str(
            getattr(config, "VOICE_PASSIVE_POLICY", "experimental") or "experimental"
        ).strip().lower() != "off"
        passive_feature_enabled = bool(getattr(config, "PASSIVE_LISTENING_MODE", False)) and passive_policy_enabled
        if self.platform == "local":
            self.passive_mode_enabled = (
                passive_feature_enabled and self.local_transport_mode == "passive"
            )
        else:
            self.passive_mode_enabled = (
                passive_feature_enabled and self.interaction_mode == "passive"
            )

    def set_interaction_mode(self, interaction_mode: str) -> None:
        normalized = str(interaction_mode or "").strip().lower()
        if normalized not in {"direct_address", "passive"}:
            normalized = "direct_address"
        self.interaction_mode = normalized
        if normalized != "passive":
            self.passive_context_buffer.clear()
            self._cancel_interjection_task()
        self._refresh_passive_mode_enabled()
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_presence_profile(self, presence_profile: str) -> None:
        normalized = str(presence_profile or "").strip().lower()
        if normalized not in {"quiet", "balanced", "social"}:
            normalized = "balanced"
        self.presence_profile = normalized
        mapping = {
            "quiet": "ghost",
            "balanced": "observer",
            "social": "participant",
        }
        self.listening_personality = mapping.get(normalized, "observer")
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_local_transport_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"push_to_talk", "passive", "quiet"}:
            normalized = "push_to_talk"
        self.local_transport_mode = normalized
        self._refresh_passive_mode_enabled()
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def apply_voice_preferences(self, preferences: Dict[str, Any]) -> None:
        if not isinstance(preferences, dict):
            return
        if "_reply_identity_delivery" in preferences:
            reply_identity_delivery = preferences.get("_reply_identity_delivery")
            self.reply_identity_delivery = (
                dict(reply_identity_delivery)
                if isinstance(reply_identity_delivery, dict)
                else {}
            )
        self.set_interaction_mode(
            str(preferences.get("interaction_mode") or self.interaction_mode)
        )
        self.set_presence_profile(
            str(preferences.get("presence_profile") or self.presence_profile)
        )
        self.set_focus_state(str(preferences.get("focus_state") or self.focus_state))
        if "group_voice_behavior" in preferences:
            self.set_group_voice_behavior(
                str(preferences.get("group_voice_behavior") or self.group_voice_behavior)
            )
        if self.platform == "local":
            self.set_local_transport_mode(
                str(
                    preferences.get("local_transport_mode")
                    or self.local_transport_mode
                    or "push_to_talk"
                )
            )
        if "voice_engine" in preferences:
            self.set_voice_engine(
                str(preferences.get("voice_engine") or self.voice_engine_requested)
            )
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_voice_engine(self, voice_engine: str) -> None:
        self._voice_engine_controller.set_requested_engine(voice_engine)

    async def _ensure_voice_engine(self) -> VoiceEngine:
        return await self._voice_engine_controller.ensure_engine()

    async def _close_voice_engine(self) -> None:
        await self._voice_engine_controller.close_engine()

    async def _close_all_speaker_streams(self) -> None:
        await self._speech_io.close_all_speaker_streams()
        self._clear_barge_in_candidates()

    async def _reconfigure_voice_engine(self, *, reason: str) -> None:
        await self._voice_engine_controller.reconfigure(reason=reason)

    async def synthesize_transport_audio(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        audio = await self._speech_io.synthesize_transport_audio(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )
        # Span (step 31): the transport calls this mid-speak_text; synthesis
        # ends here — the wav handoff to playback follows in the transport.
        _span_mark(self._active_span, "tts_done")
        return audio

    def get_status_snapshot(self) -> Dict[str, Any]:
        return build_voice_session_snapshot(
            self,
            normalize_floor_state=normalize_voice_floor_state,
        )

    async def start(self) -> None:
        await self._set_state("listening")
        await self._ensure_voice_engine()
        await self.transport.start(self)
        essential_logger.info(
            "VoiceConversationSession started for channel %s (mode=%s, passive=%s, presence=%s, focus=%s)",
            self.channel_id,
            self.interaction_mode,
            self.passive_mode_enabled,
            self.presence_profile,
            self.focus_state,
        )

    async def stop(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self._cancel_pending_turn_task()
        self._cancel_interjection_task()
        self._voice_engine_controller.cancel_pending_reconfigure()
        await self._interrupt_response(reason="session_stop")
        await self._flush_idle_debug_captures(force=True, reason="session_stop")
        await self._close_all_speaker_streams()
        await self._close_voice_engine()
        self._debug_capture_recorder.clear()
        self.passive_context_buffer.clear()
        await self._set_state("idle")
        await self.transport.stop()

    def set_listening_personality(self, personality: str) -> None:
        mapping = {
            "ghost": "quiet",
            "observer": "balanced",
            "participant": "social",
        }
        self.listening_personality = personality
        self.presence_profile = mapping.get(
            str(personality or "").strip().lower(),
            self.presence_profile,
        )
        essential_logger.info("Listening personality changed to %s", personality)
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_local_voice_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        self.set_local_transport_mode(normalized or "push_to_talk")
        if normalized != "passive":
            self._cancel_interjection_task()
            self.passive_context_buffer.clear()
        essential_logger.info(
            "Local voice mode changed to %s (interaction_mode=%s, passive=%s)",
            normalized or "push_to_talk",
            self.interaction_mode,
            self.passive_mode_enabled,
        )
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_group_voice_behavior(self, group_voice_behavior: str) -> None:
        normalized = str(group_voice_behavior or "").strip().lower()
        if normalized not in {"reserved", "socially_adaptive", "companion_forward"}:
            normalized = "socially_adaptive"
        self.group_voice_behavior = normalized
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def set_focus_state(self, focus_state: str) -> None:
        normalized = str(focus_state or "").strip().lower()
        if normalized not in {"responsive", "quiet"}:
            normalized = "responsive"
        self.focus_state = normalized
        if normalized == "quiet":
            self.passive_context_buffer.clear()
            self._cancel_interjection_task()
        if self.interaction_mode == "passive":
            essential_logger.info(
                "Local voice focus set to %s for channel %s",
                self.focus_state,
                self.channel_id,
            )
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    def _refresh_social_context(self) -> None:
        refresh_session_social_context(self)

    def _followup_window_match(self, user_id: int) -> bool:
        followup_window = float(getattr(config, "VOICE_DIRECT_ADDRESS_FOLLOWUP_WINDOW_SECONDS", 20.0))
        if self.last_addressed_user_id != user_id:
            return False

        reference_at = float(self.last_addressed_at or 0.0)
        if self._recent_assistant_spoken_at > 0:
            # Start the follow-up window from the latest point where PaLLi either
            # committed to the addressed user or actually finished forming a spoken reply.
            reference_at = max(reference_at, float(self._recent_assistant_spoken_at))
        if reference_at <= 0:
            return False
        return (time.monotonic() - reference_at) <= followup_window

    def _seconds_since_recent_assistant_turn(self) -> float | None:
        if self._recent_assistant_spoken_at <= 0:
            return None
        return max(0.0, time.monotonic() - self._recent_assistant_spoken_at)

    def _seconds_since_recent_unsolicited_turn(self) -> float | None:
        recent = max(self._last_unsolicited_reply_at, self._last_unsolicited_blocked_at)
        if recent <= 0:
            return None
        return max(0.0, time.monotonic() - recent)

    @staticmethod
    def _is_unsolicited_decision(decision: VoiceTurnDecision) -> bool:
        return decision.initiative_source not in {"none", "direct_address", "followup_window", "repair_prompt"}

    def _remember_turn_decision(self, decision: VoiceTurnDecision) -> None:
        self.last_decision_reason = str(decision.decision_reason or "")
        self.initiative_source = str(decision.initiative_source or "none")
        self.reply_posture = str(decision.reply_posture or "brief_companion")
        self.last_turn_should_reply = bool(decision.should_reply)
        self.last_turn_decision_confidence = float(decision.confidence)
        self.last_turn_should_mirror_text = bool(decision.should_mirror_text)
        self.last_turn_identity_signals = dict(decision.identity_signals or {})
        if self._is_unsolicited_decision(decision):
            if decision.should_reply:
                self._last_unsolicited_reply_at = time.monotonic()
            else:
                self._last_unsolicited_blocked_at = time.monotonic()
        self.last_event_at = time.time()
        self._schedule_runtime_update()

    # ------------------------------------------------------------------
    # Runtime-update scheduling + persistence (inlined from VoiceSessionRuntimeCoordinator)
    # ------------------------------------------------------------------

    def _schedule_runtime_update(self) -> None:
        if self._runtime_updates_stopping or self.owner_user_id is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._runtime_update_requested = True
        task = self._runtime_update_task
        if task is not None and not task.done():
            return
        self._runtime_update_task = loop.create_task(
            self._drain_runtime_updates(),
            name=f"voice-runtime-update-{self.channel_id}",
        )

    async def _flush_runtime_updates(self) -> None:
        task = self._runtime_update_task
        if task is None or task is asyncio.current_task():
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _begin_runtime_updates_stop(self) -> None:
        self._runtime_updates_stopping = True
        self._runtime_update_requested = False
        task = self._runtime_update_task
        if task is not None and not task.done():
            task.cancel()

    async def _record_session_started(self) -> int:
        await self._flush_runtime_updates()
        snapshot = self.get_status_snapshot()
        session_id = int(
            await brain.start_voice_session(
                **build_voice_session_start_kwargs(self, snapshot)
            )
        )
        self.voice_session_id = session_id
        if self.owner_user_id is not None:
            await self._emit_runtime_update()
        return session_id

    async def _record_session_stopped(self) -> None:
        await self._flush_runtime_updates()
        snapshot = self.get_status_snapshot()
        if self.voice_session_id is not None:
            await brain.update_voice_session_state(
                session_id=int(self.voice_session_id),
                **build_voice_session_state_kwargs(
                    snapshot,
                    last_floor_state="idle",
                    end_time=time.time(),
                ),
            )
        else:
            await brain.end_voice_session_for_channel(
                self.channel_id,
                platform=self.platform,
            )
        if self.runtime_session_key:
            await runtime_control.touch_session(
                **build_runtime_session_touch_kwargs(
                    snapshot,
                    runtime_session_key=self.runtime_session_key,
                )
            )
        await self._emit_voice_section_update_if_owned()

    async def _drain_runtime_updates(self) -> None:
        try:
            while self._runtime_update_requested and not self._runtime_updates_stopping:
                self._runtime_update_requested = False
                await self._emit_runtime_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "Failed to synchronize voice runtime update for channel %s",
                self.channel_id,
                exc_info=True,
            )
        finally:
            if self._runtime_update_task is asyncio.current_task():
                self._runtime_update_task = None
            if self._runtime_update_requested and not self._runtime_updates_stopping:
                self._schedule_runtime_update()

    async def _emit_runtime_update(self) -> None:
        snapshot = self.get_status_snapshot()
        if self.runtime_session_key:
            await runtime_control.touch_session(
                **build_runtime_session_touch_kwargs(
                    snapshot,
                    runtime_session_key=self.runtime_session_key,
                )
            )
        if self.voice_session_id is not None:
            await brain.update_voice_session_state(
                session_id=int(self.voice_session_id),
                **build_voice_session_state_kwargs(snapshot),
            )
        await self._emit_voice_section_update_if_owned()

    async def _emit_voice_section_update_if_owned(self) -> None:
        if self.owner_user_id is None:
            return
        await _emit_voice_runtime_section_update(int(self.owner_user_id))

    # ------------------------------------------------------------------
    # Transport-output / playback coordination (inlined from VoiceSessionTransportOutputCoordinator)
    # ------------------------------------------------------------------

    def _span_note_playback_start(self) -> None:
        """Span (step 31): called by the transport at the moment audio
        actually starts playing. getattr-guarded on the transport side, so
        a transport that never calls it just leaves the stage `-`."""
        _span_mark(self._active_span, "playback_start")

    def _transport_output_has_active_playback(self) -> bool:
        task = self._transport_output_playback_task
        return task is not None and not task.done()

    async def _transport_output_interrupt(self) -> None:
        async with self._transport_output_task_lock:
            playback_task = self._transport_output_playback_task
            self._transport_output_playback_task = None

        await self.transport.interrupt_playback()

        if playback_task and not playback_task.done():
            playback_task.cancel()
            try:
                await playback_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Voice playback task failed during interrupt", exc_info=True)

    async def _transport_output_deliver(self, *, response_text: str, plan: VoiceReplyPlan) -> None:
        await self._emit_event({"type": "assistant_message", "text": response_text})

        if plan.mirror_text:
            await self.transport.send_text_message(plan.mirror_text)

        if not plan.should_speak or not plan.spoken_text:
            return

        async def _speak() -> None:
            await self._set_state("speaking")
            _span_mark(self._active_span, "tts_start")
            try:
                await self.transport.speak_text(plan.spoken_text)
            except Exception as exc:
                activity_logger.log_error("voice_runtime", f"Voice playback failed: {exc}")
                if not plan.mirror_text:
                    await self.transport.send_text_message(response_text)
            await self._set_state("listening")

        task = asyncio.create_task(_speak(), name=f"voice-speak-{self.channel_id}")
        async with self._transport_output_task_lock:
            self._transport_output_playback_task = task
        try:
            await task
        finally:
            async with self._transport_output_task_lock:
                if self._transport_output_playback_task is task:
                    self._transport_output_playback_task = None

    # ------------------------------------------------------------------
    # Presence / passive-interjection flow (inlined from VoiceSessionPresenceCoordinator)
    # ------------------------------------------------------------------

    def _cancel_interjection_task(self) -> None:
        task = self._interjection_check_task
        self._interjection_check_task = None
        if task and not task.done():
            task.cancel()

    def _cancel_pending_turn_task(self) -> None:
        task = self._pending_turn_task
        self._pending_turn_task = None
        if task and not task.done():
            task.cancel()

    def _should_allow_passive_interjection(self) -> bool:
        self._refresh_social_context()
        if not self.passive_mode_enabled or self.interaction_mode != "passive":
            return False
        if self.focus_state != "responsive":
            return False
        if self.social_context == "discord_group":
            return False
        if not self.passive_context_buffer:
            return False
        return True

    def _passive_entry_meets_speech_threshold(self, entry: dict) -> bool:
        if not self.passive_mode_enabled:
            return False
        if self.platform != "local":
            return True

        speech_samples = entry.get("speech_samples", 0)
        if isinstance(speech_samples, (int, float)) and speech_samples <= 0:
            return False

        speech_duration = float(entry.get("speech_duration_seconds") or 0.0)
        if speech_duration > 0.0 and speech_duration < float(
            getattr(config, "LOCAL_PASSIVE_MIN_SPEECH_DURATION_SECONDS", 0.35)
        ):
            return False

        speech_rms = float(entry.get("speech_rms", 0.0) or 0.0)
        if speech_rms <= 0.0:
            return False

        min_speech_rms = float(getattr(config, "LOCAL_PASSIVE_ACTIVITY_THRESHOLD", 220))
        if speech_rms < min_speech_rms:
            return False

        speech_peak = float(entry.get("speech_peak", 0.0) or 0.0)
        if speech_peak < float(min_speech_rms):
            return False

        return True

    async def _schedule_interjection_check(self, reason: str = "passive_turn") -> None:
        if not self._should_allow_passive_interjection():
            self._cancel_pending_interjection_timer()
            return

        self._cancel_pending_interjection_timer()
        self._interjection_check_task = asyncio.create_task(
            self._run_interjection_check(reason=reason),
            name=f"voice-interjection-check-{self.channel_id}",
        )

    def _cancel_pending_interjection_timer(self) -> None:
        self._cancel_interjection_task()

    async def _run_interjection_check(self, reason: str) -> None:
        essential_logger.debug(
            "Scheduling passive interjection check for channel %s (reason=%s)",
            self.channel_id,
            reason,
        )
        delay = max(0.0, float(getattr(config, "PASSIVE_MAX_SILENCE_BEFORE_CHECK_SECONDS", 30.0) or 30.0))
        if delay > 0:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

        if not self._should_allow_passive_interjection():
            return

        since_last_turn = time.monotonic() - self._last_passive_turn_at
        required_silence = float(getattr(config, "PASSIVE_MAX_SILENCE_BEFORE_CHECK_SECONDS", 30.0) or 30.0)
        if since_last_turn < required_silence:
            return

        if not self._interjection_check_task:
            return
        await self._check_interjection_opportunity()

    # ------------------------------------------------------------------
    # Audio-frame intake + barge-in gating (inlined from VoiceSessionAudioCoordinator)
    # ------------------------------------------------------------------

    def _clear_barge_in_candidates(self) -> None:
        self._barge_in_candidates.clear()

    def _prune_inactive_speakers(self, active_user_ids: set[int]) -> None:
        for user_id in list(self._barge_in_candidates.keys()):
            if user_id not in active_user_ids:
                self._barge_in_candidates.pop(user_id, None)

    async def on_audio_frame(self, user_id: int, username: str, audio_bytes: bytes) -> None:
        if not self.is_active or not audio_bytes:
            return

        await self._speech_io.cleanup_inactive_streams()
        self._prune_inactive_speakers(set(self.speaker_streams.keys()))
        await self._record_debug_audio(user_id, username, audio_bytes)
        self._trace_audio_frame(user_id=user_id, username=username, audio_bytes=audio_bytes)
        prior_state = self.state
        self.state = "user_speaking"
        self.last_event_at = time.time()

        if self._should_trigger_barge_in(user_id=user_id, audio_bytes=audio_bytes, prior_state=prior_state):
            await self._interrupt_response(reason="barge_in")

        try:
            await self._speech_io.push_audio_frame(
                user_id=user_id,
                username=username,
                audio_bytes=audio_bytes,
            )
        except Exception as exc:
            essential_logger.warning(
                "Voice audio stream failed for %s in channel %s: %s",
                username,
                self.channel_id,
                exc,
            )
            self._trace(
                "audio-stream-failed",
                user_id=user_id,
                username=username,
                error=str(exc),
            )
            await self.on_transport_error(exc)

    def _estimate_frame_duration_ms(self, audio_bytes: bytes) -> float:
        sample_rate = max(int(getattr(config, "VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 48000) or 48000), 1)
        channels = max(int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHANNELS", 2) or 2), 1)
        bytes_per_second = sample_rate * channels * 2
        if bytes_per_second <= 0:
            return 0.0
        return (len(audio_bytes) / float(bytes_per_second)) * 1000.0

    @staticmethod
    def _calculate_linear16_rms(audio_bytes: bytes) -> float:
        usable = len(audio_bytes) - (len(audio_bytes) % 2)
        if usable <= 0:
            return 0.0

        total_sq = 0.0
        count = 0
        for (sample,) in struct.iter_unpack("<h", audio_bytes[:usable]):
            total_sq += float(sample * sample)
            count += 1
        if count <= 0:
            return 0.0
        return (total_sq / float(count)) ** 0.5

    def _should_trigger_barge_in(self, *, user_id: int, audio_bytes: bytes, prior_state: str) -> bool:
        if not bool(getattr(config, "VOICE_BARGE_IN_ENABLED", True)):
            return False
        if not self._has_active_voice_response(prior_state):
            self._clear_barge_in_candidates()
            return False

        min_rms = float(getattr(config, "VOICE_BARGE_IN_MIN_RMS", 900.0) or 900.0)
        min_duration_ms = float(getattr(config, "VOICE_BARGE_IN_MIN_DURATION_MS", 220.0) or 220.0)
        max_gap_ms = float(getattr(config, "VOICE_BARGE_IN_MAX_GAP_MS", 160.0) or 160.0)

        if min_rms <= 0.0 or min_duration_ms <= 0.0:
            return True

        frame_duration_ms = self._estimate_frame_duration_ms(audio_bytes)
        if frame_duration_ms <= 0.0:
            return False

        rms = self._calculate_linear16_rms(audio_bytes)
        now = time.monotonic()

        stale_after_ms = max_gap_ms * 2.0
        for candidate_user_id, candidate in list(self._barge_in_candidates.items()):
            if ((now - candidate.last_frame_at_monotonic) * 1000.0) > stale_after_ms:
                self._barge_in_candidates.pop(candidate_user_id, None)

        if rms < min_rms:
            candidate = self._barge_in_candidates.get(user_id)
            if candidate and ((now - candidate.last_frame_at_monotonic) * 1000.0) > max_gap_ms:
                self._barge_in_candidates.pop(user_id, None)
            return False

        candidate = self._barge_in_candidates.get(user_id)
        if candidate is None or ((now - candidate.last_frame_at_monotonic) * 1000.0) > max_gap_ms:
            candidate = BargeInCandidate(
                accumulated_ms=frame_duration_ms,
                last_frame_at_monotonic=now,
            )
        else:
            candidate.accumulated_ms += frame_duration_ms
            candidate.last_frame_at_monotonic = now

        self._barge_in_candidates[user_id] = candidate
        if candidate.accumulated_ms < min_duration_ms:
            return False

        self._clear_barge_in_candidates()
        essential_logger.info(
            "Barge-in gate passed for user %s in channel %s (rms=%.1f, duration=%.0fms, threshold=%.1f/%.0fms)",
            user_id,
            self.channel_id,
            rms,
            candidate.accumulated_ms,
            min_rms,
            min_duration_ms,
        )
        return True

    def _trace_audio_frame(self, *, user_id: int, username: str, audio_bytes: bytes) -> None:
        if not bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
            return
        count = self._frame_trace_counts.get(user_id, 0) + 1
        self._frame_trace_counts[user_id] = count
        every = max(1, int(getattr(config, "VOICE_TRACE_FRAME_LOG_EVERY", 40) or 40))
        if count > 3 and (count % every) != 0:
            return
        self._trace(
            "audio-frame",
            user_id=user_id,
            username=username,
            frame=count,
            bytes=len(audio_bytes),
            rms=round(self._calculate_linear16_rms(audio_bytes), 1),
            state=self.state,
            has_stream=user_id in self.speaker_streams,
            has_pending_stream=user_id in self._speaker_stream_tasks,
        )

    async def _record_debug_audio(self, user_id: int, username: str, audio_bytes: bytes) -> None:
        await self._debug_capture_recorder.record_audio(
            user_id=user_id,
            username=username,
            audio_bytes=audio_bytes,
        )

    async def _flush_idle_debug_captures(self, *, force: bool, reason: str) -> None:
        await self._debug_capture_recorder.flush_idle(
            force=force,
            reason=reason,
        )

    async def on_transport_error(self, error: Exception) -> None:
        activity_logger.log_error("voice_runtime", f"Transport error: {error}")
        await self._set_state("error")

    async def _ensure_speaker_stream(self, user_id: int, username: str) -> SpeakerTurnStream:
        return await self._speech_io.ensure_speaker_stream(user_id, username)

    async def _cleanup_inactive_streams(self) -> None:
        await self._speech_io.cleanup_inactive_streams()
        self._prune_inactive_speakers(set(self.speaker_streams.keys()))

    async def on_transcript_event(self, event: VoiceTranscriptEvent) -> None:
        if not self.is_active:
            return

        if event.event == "speech_started":
            if self._pending_turn is not None:
                await self._set_state("user_turn_hold")
            else:
                await self._set_state("user_speaking")
            essential_logger.info(
                "Speech started for %s in voice channel %s",
                event.username,
                self.channel_id,
            )
            self._trace(
                "speech-started",
                user_id=event.user_id,
                username=event.username,
                turn_index=event.turn_index,
            )
            return

        if event.event == "error":
            activity_logger.log_error(
                "voice_runtime",
                f"Live transcription error for {event.username}: {event.metadata.get('error')}",
            )
            essential_logger.warning(
                "Live transcription error for %s in voice channel %s: %s",
                event.username,
                self.channel_id,
                event.metadata.get("error"),
            )
            self._trace(
                "transcription-error",
                user_id=event.user_id,
                username=event.username,
                turn_index=event.turn_index,
                error=event.metadata.get("error"),
                reason=event.metadata.get("reason"),
            )
            return

        if event.event in {"update", "eager_end_of_turn", "turn_resumed"}:
            transcript = str(event.transcript or "").strip()
            if transcript:
                essential_logger.info(
                    "Transcript %s for %s in voice channel %s: %s",
                    event.event,
                    event.username,
                    self.channel_id,
                    transcript[:160],
                )
            return

        if event.event == "end_of_turn":
            transcript = str(event.transcript or "").strip()
            if transcript:
                essential_logger.info(
                    "End of turn for %s in voice channel %s: %s",
                    event.username,
                    self.channel_id,
                    transcript[:200],
                )
                await self._handle_completed_turn(event.user_id, event.username, transcript)
            elif self._pending_turn is None:
                self._trace(
                    "empty-end-of-turn",
                    user_id=event.user_id,
                    username=event.username,
                    turn_index=event.turn_index,
                    reason=event.metadata.get("reason"),
                    duration_seconds=event.metadata.get("duration_seconds"),
                    buffer_bytes=event.metadata.get("buffer_bytes"),
                )
                await self._set_state("listening")

    async def submit_local_utterance(
        self,
        *,
        user_id: int,
        username: str,
        audio_bytes: bytes,
        sample_rate: int,
        channels: int,
        speech_rms: float | None = None,
        speech_peak: float | None = None,
        speech_samples: int | None = None,
        speech_duration_seconds: float | None = None,
        user_toggles: Optional[Dict[str, bool]] = None,
    ) -> None:
        if not self.is_active or not audio_bytes:
            return

        await self._set_state("user_speaking")
        try:
            transcript = await self._speech_io.transcribe_local_utterance(
                audio_bytes,
                sample_rate=sample_rate,
                channels=channels,
            )
        except Exception as exc:
            activity_logger.log_error("voice_runtime", f"Local utterance transcription failed: {exc}")
            await self._emit_event({"type": "transcription_error", "error": str(exc)})
            await self._set_state("listening")
            return

        if not transcript:
            await self._emit_event({"type": "transcription_error", "error": "No speech detected."})
            await self._set_state("listening")
            return

        await self._handle_completed_turn(
            user_id,
            username,
            transcript,
            speech_metrics={
                "speech_rms": speech_rms,
                "speech_peak": speech_peak,
                "speech_samples": speech_samples,
                "speech_duration_seconds": speech_duration_seconds,
            },
            user_toggles=user_toggles,
        )
        if self._pending_turn_task and not self._pending_turn_task.done():
            await self._set_state("user_turn_hold")
        elif not self._response_task or self._response_task.done():
            await self._set_state("listening")


    # ------------------------------------------------------------------
    # Turn-flow: pending-turn coalescing, finalization, repair (inlined from VoiceSessionTurnCoordinator)
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_transcripts(first: str, second: str) -> str:
        parts = [str(first or "").strip(), str(second or "").strip()]
        return " ".join(part for part in parts if part).strip()

    def _continuation_grace_seconds(self) -> float:
        return max(float(getattr(config, "VOICE_CONTINUATION_GRACE_MS", 650) or 650) / 1000.0, 0.0)

    @staticmethod
    def _merge_speech_metrics(
        existing: dict[str, Any],
        new_metrics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(existing or {})
        if not new_metrics:
            return merged
        for key, value in new_metrics.items():
            if value is None:
                continue
            if key == "speech_duration_seconds":
                merged[key] = float(merged.get(key) or 0.0) + float(value or 0.0)
            elif key == "speech_samples":
                merged[key] = int(merged.get(key) or 0) + int(value or 0)
            else:
                merged[key] = max(float(merged.get(key) or 0.0), float(value or 0.0))
        return merged

    @staticmethod
    def _repair_text_for_reason(reason: str) -> str:
        if reason == "self_echo_contamination":
            return "I think some of my own audio bled into that. Go ahead one more time."
        if reason == "partial_hearing":
            return "I caught part of that, but not enough cleanly. Can you say it again?"
        if reason == "clipped_turn":
            return "I think that got clipped at the end. Go ahead and finish the thought."
        if reason == "playback_interrupted":
            return "I stopped because you jumped in. Go ahead."
        return "I think I missed part of that. Go ahead one more time."

    def _schedule_pending_turn_finalize(self) -> None:
        self._cancel_pending_turn_task()

        async def _run() -> None:
            delay_seconds = self._continuation_grace_seconds()
            if delay_seconds > 0:
                try:
                    await asyncio.sleep(delay_seconds)
                except asyncio.CancelledError:
                    return
            await self._finalize_pending_turn()

        self._pending_turn_task = asyncio.create_task(
            _run(),
            name=f"voice-pending-turn-{self.channel_id}",
        )

    async def _handle_completed_turn(
        self,
        user_id: int,
        username: str,
        transcript: str,
        speech_metrics: Dict | None = None,
        user_toggles: Optional[Dict[str, bool]] = None,
    ) -> None:
        cleaned = str(transcript or "").strip()
        if not cleaned:
            return

        now = time.monotonic()
        pending = self._pending_turn
        if pending is not None and pending.user_id == user_id:
            if (now - pending.updated_at_monotonic) <= self._continuation_grace_seconds():
                pending.transcript = self._merge_transcripts(pending.transcript, cleaned)
                pending.updated_at_monotonic = now
                pending.speech_metrics = self._merge_speech_metrics(
                    pending.speech_metrics,
                    speech_metrics,
                )
                if user_toggles is not None:
                    pending.user_toggles = user_toggles
                self._schedule_pending_turn_finalize()
                await self._set_state("user_turn_hold")
                return

        if pending is not None:
            self._cancel_pending_turn_task()
            self._pending_turn = None
            await self._process_finalized_turn(
                user_id=pending.user_id,
                username=pending.username,
                transcript=pending.transcript,
                speech_metrics=pending.speech_metrics,
                user_toggles=pending.user_toggles,
            )

        self._pending_turn = PendingVoiceTurn(
            user_id=user_id,
            username=username,
            transcript=cleaned,
            created_at_monotonic=now,
            updated_at_monotonic=now,
            speech_metrics=dict(speech_metrics or {}),
            user_toggles=user_toggles,
        )
        self._schedule_pending_turn_finalize()
        await self._set_state("user_turn_hold")

    async def _finalize_pending_turn(self) -> None:
        pending = self._pending_turn
        self._pending_turn = None
        self._pending_turn_task = None
        if pending is None:
            return
        await self._process_finalized_turn(
            user_id=pending.user_id,
            username=pending.username,
            transcript=pending.transcript,
            speech_metrics=pending.speech_metrics,
            user_toggles=pending.user_toggles,
        )

    async def _process_finalized_turn(
        self,
        *,
        user_id: int,
        username: str,
        transcript: str,
        speech_metrics: Dict | None = None,
        user_toggles: Optional[Dict[str, bool]] = None,
    ) -> None:
        span: Dict[str, float] = {}
        _span_mark(span, "turn_finalized")
        self._active_span = span
        self.last_user_excerpt = transcript[:200]
        repair_reason = self._detect_repair_reason(
            transcript=transcript,
            speech_metrics=speech_metrics,
        )
        if repair_reason:
            await self._emit_repair(
                user_id=user_id,
                username=username,
                transcript=transcript,
                reason=repair_reason,
            )
            await self._set_state("listening")
            return

        activity_logger.log_message_received(user_id, self.platform, transcript, is_voice=True)
        await brain.store_conversation(
            user_id=user_id,
            platform=self.platform,
            channel_id=self.channel_id,
            thread_id=None,
            role="user",
            content=transcript,
            metadata={
                "username": username,
                "voice_message": True,
                "voice_session": True,
                "voice_runtime_mode": "streaming",
                "voice_interaction_mode": self.interaction_mode,
                "passive_mode": self.passive_mode_enabled,
            },
        )
        await self._emit_event({"type": "user_message", "text": transcript, "username": username})

        decision = await self._decide_turn_response(
            user_id=user_id,
            transcript=transcript,
        )
        _span_mark(self._active_span, "decided")
        self._remember_turn_decision(decision)
        if not decision.should_reply:
            if self.passive_mode_enabled and self.interaction_mode == "passive":
                self._add_to_passive_buffer(
                    user_id,
                    username,
                    transcript,
                    speech_metrics=speech_metrics,
                )
                self._last_passive_turn_at = time.monotonic()
                await self._schedule_interjection_check(reason="passive_turn")
            essential_logger.info(
                "🎤 Ignoring spoken turn in %s: %s",
                self.social_context,
                transcript[:80],
            )
            self._trace(
                "turn-ignored",
                user_id=user_id,
                username=username,
                mode=self.interaction_mode,
                reason=decision.decision_reason,
                transcript_preview=transcript[:120],
            )
            await self._set_state("listening")
            return

        self.last_addressed_user_id = user_id
        self.last_addressed_at = time.monotonic()
        self._trace(
            "turn-accepted",
            user_id=user_id,
            username=username,
            reason=decision.decision_reason,
            source=decision.initiative_source,
            transcript_preview=transcript[:120],
        )
        await self._launch_response_task(
            user_id,
            username,
            transcript,
            turn_decision=decision,
            user_toggles=user_toggles,
        )

    def _detect_repair_reason(
        self,
        *,
        transcript: str,
        speech_metrics: Dict | None = None,
    ) -> str | None:
        cleaned = str(transcript or "").strip()
        if not cleaned:
            return "partial_hearing"

        if self._looks_like_self_echo(cleaned):
            return "self_echo_contamination"

        word_count = len(cleaned.split())
        duration_seconds = float((speech_metrics or {}).get("speech_duration_seconds") or 0.0)
        if duration_seconds >= 1.8 and word_count <= 3:
            return "partial_hearing"
        if self._pending_turn is None and word_count <= 2 and cleaned[-1:] not in {"?", "!", "."}:
            return "clipped_turn"
        return None

    def _looks_like_self_echo(self, transcript: str) -> bool:
        recent = str(self._recent_assistant_spoken_text or "").strip()
        if not recent:
            return False
        if (time.monotonic() - self._recent_assistant_spoken_at) > 4.0:
            return False

        recent_words = [word for word in re.findall(r"[a-z0-9']+", recent.lower()) if len(word) > 2]
        transcript_words = {word for word in re.findall(r"[a-z0-9']+", transcript.lower()) if len(word) > 2}
        if len(recent_words) < 3 or len(transcript_words) < 3:
            return False
        overlap = sum(1 for word in recent_words[:12] if word in transcript_words)
        return overlap >= 3

    async def _emit_repair(
        self,
        *,
        user_id: int,
        username: str,
        transcript: str,
        reason: str,
    ) -> None:
        self.repair_needed = reason
        self.last_repair_reason = reason
        self._remember_turn_decision(
            VoiceTurnDecision(
                should_reply=True,
                decision_reason="repair_needed",
                initiative_source="repair_prompt",
                reply_posture="calm_support",
                confidence=1.0,
                should_mirror_text=False,
            )
        )
        await self._set_state("repairing")

        repair_text = self._repair_text_for_reason(reason)
        self.last_assistant_excerpt = repair_text[:200]
        await self._deliver_response(repair_text)
        essential_logger.info(
            "🎤 Voice repair for %s in channel %s (%s): %s",
            username,
            self.channel_id,
            reason,
            transcript[:120],
        )

    # ------------------------------------------------------------------
    # Presence-flow: turn decisions and direct-address checks (inlined from VoiceSessionPresenceCoordinator)
    # ------------------------------------------------------------------

    async def _decide_turn_response(self, *, user_id: int, transcript: str) -> VoiceTurnDecision:
        explicit_addressed = False
        if self.platform != "local":
            explicit_addressed = await self._interjection_analyzer.check_direct_address(transcript)
        return VoicePresencePolicy.decide_turn(
            transcript=transcript,
            social_context=self.social_context,
            interaction_mode=self.interaction_mode,
            presence_profile=self.presence_profile,
            focus_state=self.focus_state,
            group_voice_behavior=self.group_voice_behavior,
            identity_delivery_plan=self.reply_identity_delivery,
            explicit_addressed=explicit_addressed,
            within_followup_window=self._followup_window_match(user_id),
            recent_unsolicited_seconds=self._seconds_since_recent_unsolicited_turn(),
            recent_assistant_seconds=self._seconds_since_recent_assistant_turn(),
        )

    # Step 31 (spec §7): _should_respond_direct_address and
    # _should_respond_local_passive deleted — zero callers; their logic
    # (direct-address check, follow-up window, local classify) was
    # absorbed into _decide_turn_response -> VoicePresencePolicy.decide_turn.

    def _add_to_passive_buffer(
        self,
        user_id: int,
        username: str,
        text: str,
        speech_metrics: Dict | None = None,
    ) -> None:
        metrics = {
            "speech_rms": 0.0,
            "speech_peak": 0.0,
            "speech_samples": 0,
            "speech_duration_seconds": 0.0,
        }
        if speech_metrics:
            metrics["speech_rms"] = float(speech_metrics.get("speech_rms") or 0.0)
            metrics["speech_peak"] = float(speech_metrics.get("speech_peak") or 0.0)
            metrics["speech_samples"] = int(speech_metrics.get("speech_samples") or 0)
            metrics["speech_duration_seconds"] = float(speech_metrics.get("speech_duration_seconds") or 0.0)

        self.passive_context_buffer.append(
            {
                "user_id": user_id,
                "username": username,
                "text": text,
                "timestamp": time.time(),
                **metrics,
            }
        )
        max_size = int(getattr(config, "PASSIVE_MAX_CONTEXT_BUFFER_SIZE", 10) or 10)
        if len(self.passive_context_buffer) > max_size:
            self.passive_context_buffer = self.passive_context_buffer[-max_size:]

    async def _check_interjection_opportunity(self) -> None:
        if not self._interjection_check_task:
            return

        now = time.time()
        min_interval = float(getattr(config, "PASSIVE_CHECK_INTERVAL_SECONDS", 15.0))
        if (now - self.last_interjection_check) < min_interval:
            return

        if not self._should_allow_passive_interjection():
            return

        silence_window = float(getattr(config, "PASSIVE_MAX_SILENCE_BEFORE_CHECK_SECONDS", 30.0) or 30.0)
        if (time.monotonic() - self._last_passive_turn_at) < max(silence_window, 0.0):
            return

        utterance_count = len(self.passive_context_buffer)
        if utterance_count < int(getattr(config, "PASSIVE_UTTERANCE_CHECK_THRESHOLD", 5) or 5):
            return

        if not self._passive_entry_meets_speech_threshold(self.passive_context_buffer[-1]):
            return

        self._interjection_check_task = None
        self.last_interjection_check = now
        history = await brain.get_conversation_history(
            platform=self.platform,
            channel_id=self.channel_id,
            limit=config.CONTEXT_WINDOW_SIZE,
            include_metadata=True,
        )
        decision = await self._interjection_analyzer.should_interject(
            passive_context=self.passive_context_buffer,
            channel_id=self.channel_id,
            conversation_history=history,
            min_confidence=float(getattr(config, "PASSIVE_MIN_CONFIDENCE_TO_SPEAK", 0.75)),
            personality=self.listening_personality,
        )
        last_entry = self.passive_context_buffer[-1]
        context_summary = "\n".join(
            f"{entry['username']}: {entry['text']}"
            for entry in self.passive_context_buffer[-3:]
        )
        turn_decision = VoicePresencePolicy.decide_passive_interjection(
            transcript=context_summary,
            social_context=self.social_context,
            presence_profile=self.presence_profile,
            focus_state=self.focus_state,
            group_voice_behavior=self.group_voice_behavior,
            identity_delivery_plan=self.reply_identity_delivery,
            analyzer_decision=decision,
            recent_unsolicited_seconds=self._seconds_since_recent_unsolicited_turn(),
            recent_assistant_seconds=self._seconds_since_recent_assistant_turn(),
        )
        self._remember_turn_decision(turn_decision)
        if not decision.get("should_interject", False) or not turn_decision.should_reply:
            return

        self.passive_context_buffer.clear()
        self.last_addressed_user_id = last_entry["user_id"]
        self.last_addressed_at = time.monotonic()
        await self._launch_response_task(
            last_entry["user_id"],
            last_entry["username"],
            context_summary,
            turn_decision=turn_decision,
            interjection_metadata=decision,
            voice_agentic_policy="pipeline_only",
        )

    # ------------------------------------------------------------------
    # Response-flow: planning, delivery, interruption (inlined from VoiceSessionResponseCoordinator)
    # ------------------------------------------------------------------

    def _has_active_voice_response(self, prior_state: str) -> bool:
        task = self._response_task
        return (
            (task is not None and not task.done())
            or self._transport_output_has_active_playback()
            or prior_state in {"speaking", "thinking"}
        )

    def _plan_voice_reply(
        self,
        response_text: str,
        *,
        turn_decision: VoiceTurnDecision | None = None,
    ) -> VoiceReplyPlan:
        cleaned = clean_text_for_voice(response_text)
        compact = " ".join(cleaned.split())
        posture = (
            turn_decision.reply_posture
            if turn_decision is not None
            else str(self.reply_posture or "brief_companion")
        )
        max_chars = int(getattr(config, "VOICE_SHORT_REPLY_MAX_CHARS", 220) or 220)
        if posture == "calm_support":
            max_chars = min(max_chars, 160)
        elif posture == "practical_operator":
            max_chars = min(max_chars, 140)
        elif posture == "light_social":
            max_chars = min(max_chars, 180)
        mirror_enabled = bool(getattr(config, "VOICE_MIRROR_LONG_RESPONSES_TO_TEXT", True))
        mirror_preferred = bool(turn_decision.should_mirror_text) if turn_decision is not None else posture in {
            "calm_support",
            "practical_operator",
        }

        if len(compact) <= max_chars and "\n" not in cleaned and not mirror_preferred:
            spoken = compact
            if self._needs_interruption_repair_opener and spoken:
                spoken = f"Okay. {spoken}"
            return VoiceReplyPlan(
                spoken_text=spoken,
                should_speak=True,
                metadata={"reply_posture": posture},
            )

        sentence = compact.split(". ", 1)[0].strip()
        spoken = sentence or compact[:max_chars].strip()
        if len(spoken) > max_chars:
            spoken = spoken[: max_chars - 1].rstrip() + "…"
        if self._needs_interruption_repair_opener and spoken:
            spoken = f"Okay. {spoken}"

        if mirror_enabled:
            if getattr(config, "VOICE_MIRROR_NOTICE_IN_SPEECH", True):
                spoken = f"{spoken} I put the details in text."
            return VoiceReplyPlan(
                spoken_text=spoken,
                mirror_text=response_text,
                should_speak=True,
                metadata={"reply_posture": posture},
            )

        return VoiceReplyPlan(
            spoken_text=spoken,
            should_speak=True,
            metadata={"reply_posture": posture},
        )

    async def _deliver_response(
        self,
        response_payload: str,
        *,
        turn_decision: VoiceTurnDecision | None = None,
    ) -> None:
        # Step 31 (spec §7): the dedicated-generation branch
        # (VoiceReplyBundle) is deleted — nothing ever produced one. The
        # voice register rides the SHARED prompt's band (spec §4); a second
        # renderer is the 08-13/08-17 trap reborn.
        response_text = str(response_payload or "")
        plan = self._plan_voice_reply(response_text, turn_decision=turn_decision)

        self.last_assistant_excerpt = response_text[:200]
        self._recent_assistant_spoken_text = plan.spoken_text or response_text
        self._recent_assistant_spoken_at = time.monotonic()
        self.repair_needed = "none"
        try:
            await self._transport_output_deliver(
                response_text=response_text,
                plan=plan,
            )
        finally:
            self._needs_interruption_repair_opener = False
            self.last_response_interrupted = False
            # Span (step 31, spec §5): one INFO line per SPOKEN turn, after
            # delivery. Consumed here — the next turn starts fresh.
            span = self._active_span
            self._active_span = None
            if span is not None and plan.should_speak and plan.spoken_text:
                logger.info(_format_span(span))

    async def _respond_to_message(
        self,
        *,
        user_id: int,
        username: str,
        transcription: str,
        voice_agentic_policy: str,
        turn_decision: VoiceTurnDecision | None = None,
        interjection_metadata: Dict | None = None,
        user_toggles_override: Optional[Dict[str, bool]] = None,
    ) -> None:
        async def voice_response_callback(response_payload: Any) -> None:
            await self._deliver_response(response_payload, turn_decision=turn_decision)

        platform = self.platform
        refs = await get_local_owner_refs()
        resolved_owner = refs.get("app_user_id")
        if resolved_owner is None:
            logger.error(
                "voice spine: local owner not resolved; skipping turn "
                "platform=%s user_id=%s", platform, user_id,
            )
            return
        # The owner gate (item 7a, 2026-08-19): a speaker who is not HIM is
        # a guest — their own id, origin "guest" (dispatch: no tools, no
        # reflection; the context adapter renders none of his bands), words
        # tagged with their name. His own utterances are unchanged.
        turn_owner, turn_origin = _voice_turn_identity(user_id=user_id, refs=refs)
        if turn_origin == "guest":
            from core.owner_gate import guest_content

            transcription = guest_content(username, transcription)
            logger.info("voice spine: guest speaker %s (%s)", username, user_id)
        if platform == "discord":
            surface = "discord_voice"
        elif platform == "local":
            surface = "voice_local"
        else:
            logger.error(
                "voice spine: unsupported platform %s; skipping turn", platform,
            )
            return
        turn = normalize_turn({
            "surface": surface,
            "owner_id": turn_owner,
            "content": transcription,
            "metadata": {
                "is_voice": True,
                "is_dm": self.is_dm,
                "channel_id": self.channel_id,
                "origin": turn_origin,
                # Step 2 (2026-10-08): who spoke, from the account; the
                # turn builder normalizes it.
                "speaker": {"kind": "owner" if turn_origin != "guest" else "guest",
                            "platform": "voice", "id": str(user_id or ""),
                            "name": str(username or "")},
            },
        })
        _span_mark(self._active_span, "dispatch_start")
        try:
            result = await asyncio.wait_for(
                dispatch_turn(turn),
                timeout=float(getattr(config, "VOICE_RESPONSE_DEADLINE_SECONDS", 60) or 60),
            )
        except asyncio.TimeoutError:
            # Spec §5: a hung provider must never wedge the voice loop or
            # leave her mid-stage forever. Fail to SILENCE — no partial
            # utterance — and say so in the log + span.
            logger.warning(
                "voice spine: response deadline (%ss) hit; staying silent",
                getattr(config, "VOICE_RESPONSE_DEADLINE_SECONDS", 60),
            )
            span = self._active_span
            self._active_span = None
            _span_mark(span, "timeout")
            if span is not None:
                logger.info(_format_span(span))
            return
        _span_mark(self._active_span, "dispatch_done")
        if result.responded and result.response_text:
            await voice_response_callback(result.response_text)

    async def _launch_response_task(
        self,
        user_id: int,
        username: str,
        transcript: str,
        voice_agentic_policy: str = "auto",
        turn_decision: VoiceTurnDecision | None = None,
        interjection_metadata: Dict | None = None,
        user_toggles: Optional[Dict[str, bool]] = None,
    ) -> None:
        await self._interrupt_response(reason="replace_pending_response")

        async def _run() -> None:
            await self._set_state("thinking")
            await self._respond_to_message(
                user_id=user_id,
                username=username,
                transcription=transcript,
                voice_agentic_policy=voice_agentic_policy,
                turn_decision=turn_decision,
                interjection_metadata=interjection_metadata,
                user_toggles_override=user_toggles,
            )
            await self._set_state("listening")

        self._response_task = asyncio.create_task(
            _run(),
            name=f"voice-response-{self.channel_id}-{user_id}",
        )

    async def _interrupt_response(self, *, reason: str) -> None:
        async with self._response_task_lock:
            response_task = self._response_task
            self._response_task = None

        await self._transport_output_interrupt()

        if response_task is not None and not response_task.done():
            response_task.cancel()
            try:
                await response_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Voice task failed during interrupt", exc_info=True)

        if reason:
            if reason in {"barge_in", "local_barge_in", "replace_pending_response"}:
                self.last_response_interrupted = True
                if reason in {"barge_in", "local_barge_in"}:
                    self.repair_needed = "playback_interrupted"
                    self.last_repair_reason = "playback_interrupted"
                    self._needs_interruption_repair_opener = True
            essential_logger.info(
                "🎤 Interrupted voice response for channel %s (%s)",
                self.channel_id,
                reason,
            )
        self._clear_barge_in_candidates()
        if reason in {"barge_in", "local_barge_in", "manual_abort"}:
            await self._set_state("interrupted")
        await self._set_state("listening")

    async def abort_response(self, *, reason: str = "manual_abort") -> None:
        await self._interrupt_response(reason=reason)

    async def on_local_barge_in_request(self) -> bool:
        if not self._has_active_voice_response(self.state):
            return False
        await self._interrupt_response(reason="local_barge_in")
        return True

    def _trace(self, event: str, **fields: Any) -> None:
        if not bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
            return
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        logger.info("voice_trace channel=%s event=%s %s", self.channel_id, event, details.strip())


class VoiceRuntimeManager:
    """Tracks active voice runtime sessions and provides a stable facade for adapters."""

    def __init__(self) -> None:
        self.registry = VoiceRuntimeSessionRegistry()
        register_runtime_manager(self)

    @property
    def active_sessions(self) -> Dict[str, ManagedVoiceSession]:
        return self.registry.active_sessions

    def get_session(self, runtime_session_key: str) -> VoiceConversationSession | None:
        return self.registry.get_session(runtime_session_key)

    def get_session_by_channel(self, channel_id: int, *, platform: str | None = None) -> VoiceConversationSession | None:
        return self.registry.get_session_by_channel(channel_id, platform=platform)

    def _apply_updates_to_session(self, session: ManagedVoiceSession, updates: Dict[str, Any]) -> Dict[str, Any]:
        if "voice_engine" in updates:
            session.set_voice_engine(str(updates.get("voice_engine") or session.voice_engine_requested))
        if "interaction_mode" in updates:
            session.set_interaction_mode(str(updates.get("interaction_mode") or session.interaction_mode))
        if "presence_profile" in updates:
            session.set_presence_profile(str(updates.get("presence_profile") or session.presence_profile))
        if "focus_state" in updates:
            session.set_focus_state(str(updates.get("focus_state") or session.focus_state))
        if session.platform == "local" and "local_transport_mode" in updates:
            session.set_local_voice_mode(
                str(updates.get("local_transport_mode") or session.local_transport_mode or "push_to_talk")
            )
        if "group_voice_behavior" in updates or "_reply_identity_delivery" in updates:
            session.apply_voice_preferences(updates)
        return session.get_status_snapshot()

    async def start_session(
        self,
        *,
        channel_id: int,
        text_channel_id: int,
        transport: VoiceTransport,
        initiator_user_id: int | None = None,
        account_user_id: int | None = None,
        account_features: Dict[str, bool] | None = None,
        platform: str = "discord",
        is_dm: bool = False,
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> bool:
        return await start_runtime_session(
            registry=self.registry,
            request=VoiceRuntimeSessionStartRequest(
                channel_id=channel_id,
                text_channel_id=text_channel_id,
                transport=transport,
                initiator_user_id=initiator_user_id,
                account_user_id=account_user_id,
                account_features=account_features,
                platform=platform,
                is_dm=is_dm,
                event_callback=event_callback,
            ),
            session_factory=lambda **kwargs: VoiceConversationSession(**kwargs),
            stop_session_by_key=self.stop_session_by_key,
            ensure_surface_session=runtime_control.ensure_surface_session,
            logger=logger,
        )

    async def stop_session(self, channel_id: int, *, platform: str | None = None) -> bool:
        session = self.get_session_by_channel(channel_id, platform=platform)
        if session is None:
            return False
        return await self.stop_session_by_key(session.runtime_session_key, disconnect_transport=False)

    async def stop_session_by_key(self, runtime_session_key: str, *, disconnect_transport: bool = False) -> bool:
        return await stop_runtime_session_by_key(
            registry=self.registry,
            runtime_session_key=runtime_session_key,
            disconnect_transport=disconnect_transport,
            logger=logger,
        )

    async def stop_all_sessions(self) -> None:
        for session_key in list(self.active_sessions.keys()):
            await self.stop_session_by_key(session_key)

    def set_listening_personality(self, channel_id: int, personality: str) -> bool:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return False
        session.set_listening_personality(personality)
        return True

    def set_interaction_mode(self, channel_id: int, interaction_mode: str) -> bool:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return False
        session.set_interaction_mode(interaction_mode)
        return True

    def set_presence_profile(self, channel_id: int, presence_profile: str) -> bool:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return False
        session.set_presence_profile(presence_profile)
        return True

    def set_focus_state(self, channel_id: int, focus_state: str) -> bool:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return False
        session.set_focus_state(focus_state)
        return True

    def apply_voice_preferences(self, channel_id: int, preferences: Dict[str, Any]) -> bool:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return False
        session.apply_voice_preferences(preferences)
        return True

    def get_session_snapshot(self, channel_id: int) -> Dict[str, Any] | None:
        session = self.get_session_by_channel(channel_id)
        if session is None:
            return None
        return session.get_status_snapshot()

    def get_session_snapshot_by_key(self, runtime_session_key: str) -> Dict[str, Any] | None:
        session = self.get_session(runtime_session_key)
        if session is None:
            return None
        return session.get_status_snapshot()

    def apply_session_updates(self, runtime_session_key: str, updates: Dict[str, Any]) -> Dict[str, Any] | None:
        session = self.get_session(runtime_session_key)
        if session is None:
            return None
        return self._apply_updates_to_session(session, updates)

    async def abort_session(self, runtime_session_key: str, *, reason: str = "runtime_control_abort") -> Dict[str, Any] | None:
        session = self.get_session(runtime_session_key)
        if session is None:
            return None
        await session.abort_response(reason=reason)
        return session.get_status_snapshot()

    def list_session_snapshots(self) -> list[Dict[str, Any]]:
        return self.registry.list_session_snapshots()

    def is_session_active(self, channel_id: int) -> bool:
        return self.registry.is_session_active(channel_id)

    def get_session_count(self) -> int:
        return self.registry.get_session_count()
