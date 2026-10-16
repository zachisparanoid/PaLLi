from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class VoiceSessionStore:
    """Persistence helper for local-first voice session lifecycle truth."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    @staticmethod
    def _clean_text(value: Optional[str]) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    def _voice_session_record(self, row: Any) -> Dict[str, Any]:
        metadata = self._load_json(row[20], {})
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": int(row[0]),
            "channel_id": int(row[1]),
            "user_id": int(row[2]) if row[2] is not None else None,
            "platform": str(row[3] or "discord"),
            "text_channel_id": int(row[4]) if row[4] is not None else None,
            "runtime_session_key": str(row[5] or ""),
            "start_time": float(row[6] or 0.0),
            "last_floor_state": str(row[7] or ""),
            "interaction_mode": str(row[8] or ""),
            "presence_profile": str(row[9] or ""),
            "focus_state": str(row[10] or ""),
            "social_context": str(row[11] or ""),
            "human_participant_count": int(row[12] or 0),
            "bot_participant_count": int(row[13] or 0),
            "owner_present": bool(row[14]),
            "last_decision_reason": str(row[15] or ""),
            "initiative_source": str(row[16] or "") or "none",
            "reply_posture": str(row[17] or "") or "brief_companion",
            "local_transport_mode": str(row[18] or "") or None,
            "last_event_at": float(row[19] or 0.0),
            "metadata": metadata,
        }

    async def start_voice_session(
        self,
        channel_id: int,
        user_id: Optional[int] = None,
        platform: str = "discord",
        text_channel_id: Optional[int] = None,
        runtime_session_key: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        presence_profile: Optional[str] = None,
        focus_state: Optional[str] = None,
        social_context: Optional[str] = None,
        human_participant_count: Optional[int] = None,
        bot_participant_count: Optional[int] = None,
        owner_present: Optional[bool] = None,
        last_decision_reason: Optional[str] = None,
        initiative_source: Optional[str] = None,
        reply_posture: Optional[str] = None,
        local_transport_mode: Optional[str] = None,
        last_floor_state: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            session_id = await conn.fetchval(
                """
                INSERT INTO voice_sessions (
                    channel_id,
                    user_id,
                    platform,
                    text_channel_id,
                    runtime_session_key,
                    start_time,
                    last_floor_state,
                    interaction_mode,
                    presence_profile,
                    focus_state,
                    social_context,
                    human_participant_count,
                    bot_participant_count,
                    owner_present,
                    last_decision_reason,
                    initiative_source,
                    reply_posture,
                    local_transport_mode,
                    last_event_at,
                    metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20::jsonb)
                RETURNING id
                """,
                int(channel_id),
                int(user_id) if user_id is not None else None,
                self._clean_text(str(platform or "discord").lower()) or "discord",
                int(text_channel_id) if text_channel_id is not None else None,
                self._clean_text(runtime_session_key),
                now,
                self._clean_text(last_floor_state),
                self._clean_text(interaction_mode),
                self._clean_text(presence_profile),
                self._clean_text(focus_state),
                self._clean_text(social_context),
                int(human_participant_count) if human_participant_count is not None else None,
                int(bot_participant_count) if bot_participant_count is not None else None,
                bool(owner_present) if owner_present is not None else None,
                self._clean_text(last_decision_reason),
                self._clean_text(initiative_source),
                self._clean_text(reply_posture),
                self._clean_text(local_transport_mode),
                now,
                self._dump_json(metadata) if metadata else None,
            )
            return int(session_id)
        finally:
            await conn.close()

    async def update_voice_session_state(
        self,
        *,
        session_id: int,
        last_floor_state: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        presence_profile: Optional[str] = None,
        focus_state: Optional[str] = None,
        social_context: Optional[str] = None,
        human_participant_count: Optional[int] = None,
        bot_participant_count: Optional[int] = None,
        owner_present: Optional[bool] = None,
        last_decision_reason: Optional[str] = None,
        initiative_source: Optional[str] = None,
        reply_posture: Optional[str] = None,
        local_transport_mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        end_time: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                UPDATE voice_sessions
                SET last_floor_state = COALESCE($2, last_floor_state),
                    interaction_mode = COALESCE($3, interaction_mode),
                    presence_profile = COALESCE($4, presence_profile),
                    focus_state = COALESCE($5, focus_state),
                    social_context = COALESCE($6, social_context),
                    human_participant_count = COALESCE($7, human_participant_count),
                    bot_participant_count = COALESCE($8, bot_participant_count),
                    owner_present = COALESCE($9, owner_present),
                    last_decision_reason = COALESCE($10, last_decision_reason),
                    initiative_source = COALESCE($11, initiative_source),
                    reply_posture = COALESCE($12, reply_posture),
                    local_transport_mode = COALESCE($13, local_transport_mode),
                    metadata = COALESCE($14::jsonb, metadata),
                    last_event_at = $15,
                    end_time = COALESCE($16, end_time)
                WHERE id = $1
                """,
                int(session_id),
                self._clean_text(last_floor_state),
                self._clean_text(interaction_mode),
                self._clean_text(presence_profile),
                self._clean_text(focus_state),
                self._clean_text(social_context),
                int(human_participant_count) if human_participant_count is not None else None,
                int(bot_participant_count) if bot_participant_count is not None else None,
                bool(owner_present) if owner_present is not None else None,
                self._clean_text(last_decision_reason),
                self._clean_text(initiative_source),
                self._clean_text(reply_posture),
                self._clean_text(local_transport_mode),
                self._dump_json(metadata) if metadata else None,
                self._now(),
                float(end_time) if end_time is not None else None,
            )
        finally:
            await conn.close()

    async def end_voice_session_for_channel(self, channel_id: int, *, platform: Optional[str] = None) -> None:
        conn = await self._brain.get_connection()
        try:
            if platform:
                await conn.execute(
                    """
                    UPDATE voice_sessions
                    SET end_time = $1
                    WHERE channel_id = $2 AND platform = $3 AND end_time IS NULL
                    """,
                    self._now(),
                    int(channel_id),
                    self._clean_text(str(platform or "").lower()),
                )
            else:
                await conn.execute(
                    """
                    UPDATE voice_sessions
                    SET end_time = $1
                    WHERE channel_id = $2 AND end_time IS NULL
                    """,
                    self._now(),
                    int(channel_id),
                )
        finally:
            await conn.close()

    async def list_open_voice_sessions(self, *, limit: int = 32) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, channel_id, user_id, platform, text_channel_id, runtime_session_key, start_time,
                       last_floor_state, interaction_mode, presence_profile, focus_state, social_context,
                       human_participant_count, bot_participant_count, owner_present, last_decision_reason,
                       initiative_source, reply_posture, local_transport_mode, last_event_at, metadata
                FROM voice_sessions
                WHERE end_time IS NULL
                ORDER BY start_time DESC
                LIMIT $1
                """,
                max(int(limit or 32), 1),
            )
            return [self._voice_session_record(row) for row in rows]
        finally:
            await conn.close()

    async def close_voice_session(self, *, session_id: int, end_time: Optional[float] = None) -> None:
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                UPDATE voice_sessions
                SET end_time = COALESCE($2, end_time, $3)
                WHERE id = $1
                """,
                int(session_id),
                float(end_time) if end_time is not None else None,
                self._now(),
            )
        finally:
            await conn.close()
