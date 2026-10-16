from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class RuntimeStateStore:
    """Persistence helper for runtime sessions, jobs, context, ambient, and operator state."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _clean_text(self, value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        text = self._clean_text(value)
        return text or None

    def _clean_text_list(self, values: Optional[List[Any]]) -> List[str]:
        clean_values: List[str] = []
        for value in values or []:
            text = self._clean_text(value)
            if text:
                clean_values.append(text)
        return clean_values

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _runtime_session_record(self, row: Any) -> Dict[str, Any]:
        return {
            "session_key": str(row[0] or ""),
            "owner_user_id": int(row[1]),
            "thread_id": str(row[2] or ""),
            "thread_kind": str(row[3] or ""),
            "thread_label": str(row[4] or ""),
            "session_type": str(row[5] or ""),
            "platform": str(row[6] or ""),
            "channel_id": int(row[7]) if row[7] is not None else None,
            "surface_id": str(row[8] or row[7] or ""),
            "status": str(row[9] or "ready"),
            "label": str(row[10] or ""),
            "metadata": self._load_json(row[11], {}),
            "last_activity": float(row[12] or 0.0),
            "created_at": float(row[13] or 0.0),
            "updated_at": float(row[14] or 0.0),
            "archived_at": float(row[15]) if row[15] is not None else None,
        }

    def _runtime_job_record(self, row: Any) -> Dict[str, Any]:
        return {
            "job_id": str(row[0] or ""),
            "owner_user_id": int(row[1]),
            "runtime_session_key": str(row[2] or ""),
            "job_type": str(row[3] or ""),
            "subtype": str(row[4] or ""),
            "status": str(row[5] or "queued"),
            "request_text": str(row[6] or ""),
            "scheduled_for": float(row[7]) if row[7] is not None else None,
            "started_at": float(row[8]) if row[8] is not None else None,
            "completed_at": float(row[9]) if row[9] is not None else None,
            "attempt_count": int(row[10] or 0),
            "last_error": str(row[11] or ""),
            "last_transition_reason": str(row[12] or ""),
            "retry_of_job_id": str(row[13] or ""),
            "replacement_job_id": str(row[14] or ""),
            "source_table": str(row[15] or ""),
            "source_id": str(row[16] or ""),
            "metadata": self._load_json(row[17], {}),
            "created_at": float(row[18] or 0.0),
            "updated_at": float(row[19] or 0.0),
        }

    def _context_event_record(self, row: Any) -> Dict[str, Any]:
        return {
            "event_id": str(row[0] or ""),
            "owner_user_id": int(row[1]),
            "source": str(row[2] or ""),
            "event_type": str(row[3] or ""),
            "occurred_at": float(row[4] or 0.0),
            "dedupe_key": str(row[5] or ""),
            "runtime_session_key": str(row[6] or ""),
            "payload": self._load_json(row[7], {}),
            "metadata": self._load_json(row[8], {}),
            "created_at": float(row[9] or 0.0),
        }

    def _home_context_state_record(self, row: Any) -> Dict[str, Any]:
        return {
            "owner_user_id": int(row[0]),
            "owner_presence_state": str(row[1] or "unknown"),
            "machine_state": str(row[2] or "unknown"),
            "privacy_state": str(row[3] or "unknown"),
            "quiet_hours_active": bool(row[4]),
            "last_arrival_at": float(row[5]) if row[5] is not None else None,
            "last_departure_at": float(row[6]) if row[6] is not None else None,
            "last_machine_active_at": float(row[7]) if row[7] is not None else None,
            "last_event_at": float(row[8]) if row[8] is not None else None,
            "source_health": self._load_json(row[9], {}),
            "metadata": self._load_json(row[10], {}),
            "created_at": float(row[11] or 0.0),
            "updated_at": float(row[12] or 0.0),
        }

    def _ambient_action_record(self, row: Any) -> Dict[str, Any]:
        return {
            "action_id": str(row[0] or ""),
            "owner_user_id": int(row[1]),
            "source_event_id": str(row[2] or ""),
            "runtime_job_id": str(row[3] or ""),
            "action_type": str(row[4] or ""),
            "status": str(row[5] or ""),
            "delivery_surface": str(row[6] or ""),
            "decision_reason": str(row[7] or ""),
            "suppression_reason": str(row[8] or ""),
            "content": self._load_json(row[9], {}),
            "metadata": self._load_json(row[10], {}),
            "created_at": float(row[11] or 0.0),
            "updated_at": float(row[12] or 0.0),
            "delivered_at": float(row[13]) if row[13] is not None else None,
            "failed_at": float(row[14]) if row[14] is not None else None,
        }

    def _operator_state_record(self, row: Any) -> Dict[str, Any]:
        return {
            "owner_user_id": int(row[0]),
            "pinned_workspace_root": str(row[1] or ""),
            "pinned_workspace_label": str(row[2] or ""),
            "pinned_remote_name": str(row[3] or ""),
            "current_workspace_root": str(row[4] or ""),
            "current_workspace_label": str(row[5] or ""),
            "current_remote_name": str(row[6] or ""),
            "last_operator_action_id": str(row[7] or ""),
            "last_operator_activity_at": float(row[8]) if row[8] is not None else None,
            "metadata": self._load_json(row[9], {}),
            "created_at": float(row[10] or 0.0),
            "updated_at": float(row[11] or 0.0),
        }

    async def upsert_runtime_session(
        self,
        *,
        session_key: str,
        owner_user_id: int,
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        session_type: str,
        platform: str,
        channel_id: Optional[int],
        surface_id: Optional[str] = None,
        status: str,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        last_activity: Optional[float] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        clean_session_key = self._clean_text(session_key)
        ts = float(last_activity if last_activity is not None else now)
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, thread_id, thread_kind, thread_label
                FROM runtime_sessions
                WHERE session_key = $1
                LIMIT 1
                """,
                clean_session_key,
            )
            merged_metadata = self._load_json(current[0], {}) if current else {}
            if metadata:
                merged_metadata.update(metadata)
            next_thread_id = self._clean_text(thread_id) or (self._clean_text(current[1]) if current else "")
            next_thread_kind = self._clean_text(thread_kind) or (self._clean_text(current[2]) if current else "")
            next_thread_label = self._clean_text(thread_label) or (self._clean_text(current[3]) if current else "")
            await conn.execute(
                """
                INSERT INTO runtime_sessions
                (
                    session_key, owner_user_id, thread_id, thread_kind, thread_label, session_type, platform, channel_id, surface_id,
                    status, label, metadata, last_activity, created_at, updated_at, archived_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, NULLIF($9, ''), $10, $11, $12::jsonb, $13, $14, $14, $15)
                ON CONFLICT (session_key) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    thread_id = EXCLUDED.thread_id,
                    thread_kind = EXCLUDED.thread_kind,
                    thread_label = EXCLUDED.thread_label,
                    session_type = EXCLUDED.session_type,
                    platform = EXCLUDED.platform,
                    channel_id = EXCLUDED.channel_id,
                    surface_id = EXCLUDED.surface_id,
                    status = EXCLUDED.status,
                    label = EXCLUDED.label,
                    metadata = EXCLUDED.metadata,
                    last_activity = EXCLUDED.last_activity,
                    updated_at = EXCLUDED.updated_at,
                    archived_at = EXCLUDED.archived_at
                """,
                clean_session_key,
                int(owner_user_id),
                next_thread_id or None,
                next_thread_kind or None,
                next_thread_label or None,
                self._clean_text(session_type),
                self._clean_text(platform),
                int(channel_id) if channel_id is not None else None,
                self._clean_optional_text(surface_id),
                self._clean_text(status, default="ready"),
                self._clean_optional_text(label),
                self._dump_json(merged_metadata),
                ts,
                now,
                float(archived_at) if archived_at is not None else None,
            )
        finally:
            await conn.close()

    async def update_runtime_session(
        self,
        *,
        session_key: str,
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        status: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        last_activity: Optional[float] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        clean_key = self._clean_text(session_key)
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, archived_at, label, status, last_activity, thread_id, thread_kind, thread_label
                FROM runtime_sessions
                WHERE session_key = $1
                LIMIT 1
                """,
                clean_key,
            )
            merged_metadata = self._load_json(current[0], {}) if current else {}
            if metadata:
                merged_metadata.update(metadata)
            next_archived_at = archived_at
            if next_archived_at is None and current is not None:
                next_archived_at = current[1]
            next_label = label if label is not None else (current[2] if current else None)
            next_status = status if status is not None else (current[3] if current else None)
            next_last_activity = (
                float(last_activity)
                if last_activity is not None
                else float(current[4] or now) if current is not None else now
            )
            next_thread_id = self._clean_text(thread_id) or (self._clean_text(current[5]) if current is not None else "") or None
            next_thread_kind = self._clean_text(thread_kind) or (self._clean_text(current[6]) if current is not None else "") or None
            next_thread_label = self._clean_text(thread_label) or (self._clean_text(current[7]) if current is not None else "") or None
            await conn.execute(
                """
                UPDATE runtime_sessions
                SET thread_id = $2,
                    thread_kind = $3,
                    thread_label = $4,
                    status = COALESCE($5, status),
                    label = $6,
                    metadata = $7::jsonb,
                    last_activity = $8,
                    updated_at = $9,
                    archived_at = $10
                WHERE session_key = $1
                """,
                clean_key,
                next_thread_id,
                next_thread_kind,
                next_thread_label,
                next_status,
                next_label,
                self._dump_json(merged_metadata),
                next_last_activity,
                now,
                float(next_archived_at) if next_archived_at is not None else None,
            )
        finally:
            await conn.close()

    async def get_runtime_session(self, session_key: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT session_key, owner_user_id, thread_id, thread_kind, thread_label,
                       session_type, platform, channel_id, surface_id, status, label, metadata,
                       last_activity, created_at, updated_at, archived_at
                FROM runtime_sessions
                WHERE session_key = $1
                LIMIT 1
                """,
                self._clean_text(session_key),
            )
            return self._runtime_session_record(row) if row else None
        finally:
            await conn.close()

    async def list_runtime_sessions(
        self,
        *,
        owner_user_id: Optional[int] = None,
        statuses: Optional[List[str]] = None,
        session_types: Optional[List[str]] = None,
        limit: int = 24,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT session_key, owner_user_id, thread_id, thread_kind, thread_label,
                       session_type, platform, channel_id, surface_id, status, label, metadata,
                       last_activity, created_at, updated_at, archived_at
                FROM runtime_sessions
                WHERE 1 = 1
            """
            params: List[Any] = []
            if owner_user_id is not None:
                params.append(int(owner_user_id))
                query += f" AND owner_user_id = ${len(params)}"
            if not include_archived:
                query += " AND archived_at IS NULL"
            clean_statuses = self._clean_text_list(statuses)
            if clean_statuses:
                placeholders = self._brain._placeholders(len(clean_statuses), start=len(params) + 1)
                params.extend(clean_statuses)
                query += f" AND status IN ({placeholders})"
            clean_session_types = self._clean_text_list(session_types)
            if clean_session_types:
                placeholders = self._brain._placeholders(len(clean_session_types), start=len(params) + 1)
                params.extend(clean_session_types)
                query += f" AND session_type IN ({placeholders})"
            params.append(max(int(limit or 24), 1))
            query += f" ORDER BY last_activity DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._runtime_session_record(row) for row in rows]
        finally:
            await conn.close()

    async def create_runtime_job(
        self,
        *,
        job_id: str,
        owner_user_id: int,
        runtime_session_key: str,
        job_type: str,
        subtype: str = "",
        status: str,
        request_text: str = "",
        scheduled_for: Optional[float] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                INSERT INTO runtime_jobs
                (
                    job_id, owner_user_id, runtime_session_key, job_type, subtype, status,
                    request_text, scheduled_for, started_at, completed_at, attempt_count, last_error,
                    last_transition_reason, retry_of_job_id, replacement_job_id,
                    source_table, source_id, metadata, created_at, updated_at
                )
                VALUES
                ($1, $2, $3, $4, NULLIF($5, ''), $6, $7, $8, NULL, NULL, 0, '', $9, $10, $11, $12, $13, $14::jsonb, $15, $15)
                ON CONFLICT (job_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    runtime_session_key = EXCLUDED.runtime_session_key,
                    job_type = EXCLUDED.job_type,
                    subtype = EXCLUDED.subtype,
                    status = EXCLUDED.status,
                    request_text = EXCLUDED.request_text,
                    scheduled_for = EXCLUDED.scheduled_for,
                    last_transition_reason = COALESCE(EXCLUDED.last_transition_reason, runtime_jobs.last_transition_reason),
                    retry_of_job_id = COALESCE(EXCLUDED.retry_of_job_id, runtime_jobs.retry_of_job_id),
                    replacement_job_id = COALESCE(EXCLUDED.replacement_job_id, runtime_jobs.replacement_job_id),
                    source_table = EXCLUDED.source_table,
                    source_id = EXCLUDED.source_id,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                self._clean_text(job_id),
                int(owner_user_id),
                self._clean_text(runtime_session_key),
                self._clean_text(job_type),
                self._clean_text(subtype),
                self._clean_text(status, default="queued"),
                str(request_text or ""),
                float(scheduled_for) if scheduled_for is not None else None,
                self._clean_optional_text(last_transition_reason),
                self._clean_optional_text(retry_of_job_id),
                self._clean_optional_text(replacement_job_id),
                self._clean_optional_text(source_table),
                self._clean_optional_text(source_id),
                self._dump_json(metadata or {}),
                now,
            )
        finally:
            await conn.close()

    async def update_runtime_job(
        self,
        *,
        job_id: str,
        status: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
        request_text: Optional[str] = None,
        scheduled_for: Optional[float] = None,
        started_at: Optional[float] = None,
        completed_at: Optional[float] = None,
        attempt_count: Optional[int] = None,
        increment_attempt: bool = False,
        last_error: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        clean_job_id = self._clean_text(job_id)
        try:
            current = await conn.fetchrow(
                """
                SELECT runtime_session_key, request_text, scheduled_for, started_at, completed_at,
                       attempt_count, last_error, last_transition_reason, retry_of_job_id,
                       replacement_job_id, source_table, source_id, metadata
                FROM runtime_jobs
                WHERE job_id = $1
                LIMIT 1
                """,
                clean_job_id,
            )
            if not current:
                return
            merged_metadata = self._load_json(current[12], {})
            if metadata:
                merged_metadata.update(metadata)
            next_attempt_count = int(current[5] or 0)
            if attempt_count is not None:
                next_attempt_count = int(attempt_count)
            elif increment_attempt:
                next_attempt_count += 1
            next_started_at = current[3]
            if started_at is not None:
                next_started_at = float(started_at)
            next_completed_at = current[4]
            if completed_at is not None:
                next_completed_at = float(completed_at)
            elif status and self._clean_text(status).lower() in {"scheduled", "queued", "running", "waiting"}:
                next_completed_at = None
            await conn.execute(
                """
                UPDATE runtime_jobs
                SET status = COALESCE($2, status),
                    runtime_session_key = COALESCE($3, runtime_session_key),
                    request_text = COALESCE($4, request_text),
                    scheduled_for = COALESCE($5, scheduled_for),
                    started_at = $6,
                    completed_at = $7,
                    attempt_count = $8,
                    last_error = COALESCE($9, last_error),
                    last_transition_reason = COALESCE($10, last_transition_reason),
                    retry_of_job_id = COALESCE($11, retry_of_job_id),
                    replacement_job_id = COALESCE($12, replacement_job_id),
                    source_table = COALESCE($13, source_table),
                    source_id = COALESCE($14, source_id),
                    metadata = $15::jsonb,
                    updated_at = $16
                WHERE job_id = $1
                """,
                clean_job_id,
                status,
                runtime_session_key,
                request_text,
                float(scheduled_for) if scheduled_for is not None else None,
                next_started_at,
                next_completed_at,
                next_attempt_count,
                last_error,
                self._clean_optional_text(last_transition_reason),
                self._clean_optional_text(retry_of_job_id),
                self._clean_optional_text(replacement_job_id),
                self._clean_optional_text(source_table),
                self._clean_optional_text(source_id),
                self._dump_json(merged_metadata),
                now,
            )
        finally:
            await conn.close()

    async def get_runtime_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT job_id, owner_user_id, runtime_session_key, job_type, subtype, status,
                       request_text, scheduled_for, started_at, completed_at, attempt_count,
                       last_error, last_transition_reason, retry_of_job_id, replacement_job_id,
                       source_table, source_id, metadata, created_at, updated_at
                FROM runtime_jobs
                WHERE job_id = $1
                LIMIT 1
                """,
                self._clean_text(job_id),
            )
            return self._runtime_job_record(row) if row else None
        finally:
            await conn.close()

    async def get_runtime_job_by_source(self, *, source_table: str, source_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT job_id
                FROM runtime_jobs
                WHERE source_table = $1 AND source_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                self._clean_text(source_table),
                self._clean_text(source_id),
            )
            if not row:
                return None
            return await self.get_runtime_job(self._clean_text(row[0]))
        finally:
            await conn.close()

    async def list_runtime_jobs(
        self,
        *,
        owner_user_id: Optional[int] = None,
        statuses: Optional[List[str]] = None,
        job_types: Optional[List[str]] = None,
        limit: int = 32,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT job_id, owner_user_id, runtime_session_key, job_type, subtype, status,
                       request_text, scheduled_for, started_at, completed_at, attempt_count,
                       last_error, last_transition_reason, retry_of_job_id, replacement_job_id,
                       source_table, source_id, metadata, created_at, updated_at
                FROM runtime_jobs
                WHERE 1=1
            """
            params: List[Any] = []
            if owner_user_id is not None:
                params.append(int(owner_user_id))
                query += f" AND owner_user_id = ${len(params)}"
            clean_statuses = self._clean_text_list(statuses)
            if clean_statuses:
                placeholders = self._brain._placeholders(len(clean_statuses), start=len(params) + 1)
                params.extend(clean_statuses)
                query += f" AND status IN ({placeholders})"
            clean_job_types = self._clean_text_list(job_types)
            if clean_job_types:
                placeholders = self._brain._placeholders(len(clean_job_types), start=len(params) + 1)
                params.extend(clean_job_types)
                query += f" AND job_type IN ({placeholders})"
            params.append(max(int(limit or 32), 1))
            query += f" ORDER BY COALESCE(scheduled_for, updated_at, created_at) DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._runtime_job_record(row) for row in rows]
        finally:
            await conn.close()

    async def create_context_event(
        self,
        *,
        event_id: str,
        owner_user_id: int,
        source: str,
        event_type: str,
        occurred_at: float,
        dedupe_key: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                INSERT INTO context_events
                (
                    event_id, owner_user_id, source, event_type, occurred_at, dedupe_key,
                    runtime_session_key, payload, metadata, created_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)
                ON CONFLICT (event_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    source = EXCLUDED.source,
                    event_type = EXCLUDED.event_type,
                    occurred_at = EXCLUDED.occurred_at,
                    dedupe_key = EXCLUDED.dedupe_key,
                    runtime_session_key = EXCLUDED.runtime_session_key,
                    payload = EXCLUDED.payload,
                    metadata = EXCLUDED.metadata
                """,
                self._clean_text(event_id),
                int(owner_user_id),
                self._clean_text(source),
                self._clean_text(event_type),
                float(occurred_at),
                self._clean_optional_text(dedupe_key),
                self._clean_optional_text(runtime_session_key),
                self._dump_json(payload or {}),
                self._dump_json(metadata or {}),
                now,
            )
        finally:
            await conn.close()

    async def get_recent_context_event_by_dedupe(
        self,
        *,
        owner_user_id: int,
        dedupe_key: str,
        since: float,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT event_id, owner_user_id, source, event_type, occurred_at, dedupe_key,
                       runtime_session_key, payload, metadata, created_at
                FROM context_events
                WHERE owner_user_id = $1
                  AND dedupe_key = $2
                  AND occurred_at >= $3
                ORDER BY occurred_at DESC
                LIMIT 1
                """,
                int(owner_user_id),
                self._clean_text(dedupe_key),
                float(since),
            )
            return self._context_event_record(row) if row else None
        finally:
            await conn.close()

    async def list_context_events(
        self,
        *,
        owner_user_id: int,
        event_types: Optional[List[str]] = None,
        limit: int = 32,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT event_id, owner_user_id, source, event_type, occurred_at, dedupe_key,
                       runtime_session_key, payload, metadata, created_at
                FROM context_events
                WHERE owner_user_id = $1
            """
            params: List[Any] = [int(owner_user_id)]
            clean_types = self._clean_text_list(event_types)
            if clean_types:
                placeholders = self._brain._placeholders(len(clean_types), start=len(params) + 1)
                params.extend(clean_types)
                query += f" AND event_type IN ({placeholders})"
            params.append(max(int(limit or 32), 1))
            query += f" ORDER BY occurred_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._context_event_record(row) for row in rows]
        finally:
            await conn.close()

    async def upsert_home_context_state(
        self,
        *,
        owner_user_id: int,
        owner_presence_state: str,
        machine_state: str,
        privacy_state: str,
        quiet_hours_active: bool,
        last_arrival_at: Optional[float] = None,
        last_departure_at: Optional[float] = None,
        last_machine_active_at: Optional[float] = None,
        last_event_at: Optional[float] = None,
        source_health: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT source_health, metadata, last_arrival_at, last_departure_at, last_machine_active_at, last_event_at
                FROM home_context_state
                WHERE owner_user_id = $1
                LIMIT 1
                """,
                int(owner_user_id),
            )
            merged_source_health = self._load_json(current[0], {}) if current else {}
            merged_metadata = self._load_json(current[1], {}) if current else {}
            if source_health:
                merged_source_health.update(source_health)
            if metadata:
                merged_metadata.update(metadata)
            await conn.execute(
                """
                INSERT INTO home_context_state
                (
                    owner_user_id, owner_presence_state, machine_state, privacy_state,
                    quiet_hours_active, last_arrival_at, last_departure_at, last_machine_active_at,
                    last_event_at, source_health, metadata, created_at, updated_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12, $12)
                ON CONFLICT (owner_user_id) DO UPDATE SET
                    owner_presence_state = EXCLUDED.owner_presence_state,
                    machine_state = EXCLUDED.machine_state,
                    privacy_state = EXCLUDED.privacy_state,
                    quiet_hours_active = EXCLUDED.quiet_hours_active,
                    last_arrival_at = EXCLUDED.last_arrival_at,
                    last_departure_at = EXCLUDED.last_departure_at,
                    last_machine_active_at = EXCLUDED.last_machine_active_at,
                    last_event_at = EXCLUDED.last_event_at,
                    source_health = EXCLUDED.source_health,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                int(owner_user_id),
                self._clean_text(owner_presence_state, default="unknown"),
                self._clean_text(machine_state, default="unknown"),
                self._clean_text(privacy_state, default="unknown"),
                bool(quiet_hours_active),
                float(last_arrival_at) if last_arrival_at is not None else (float(current[2]) if current and current[2] is not None else None),
                float(last_departure_at) if last_departure_at is not None else (float(current[3]) if current and current[3] is not None else None),
                float(last_machine_active_at) if last_machine_active_at is not None else (float(current[4]) if current and current[4] is not None else None),
                float(last_event_at) if last_event_at is not None else (float(current[5]) if current and current[5] is not None else None),
                self._dump_json(merged_source_health),
                self._dump_json(merged_metadata),
                now,
            )
        finally:
            await conn.close()

    async def get_home_context_state(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT owner_user_id, owner_presence_state, machine_state, privacy_state,
                       quiet_hours_active, last_arrival_at, last_departure_at, last_machine_active_at,
                       last_event_at, source_health, metadata, created_at, updated_at
                FROM home_context_state
                WHERE owner_user_id = $1
                LIMIT 1
                """,
                int(owner_user_id),
            )
            return self._home_context_state_record(row) if row else None
        finally:
            await conn.close()

    async def create_ambient_action(
        self,
        *,
        action_id: str,
        owner_user_id: int,
        source_event_id: Optional[str],
        runtime_job_id: Optional[str],
        action_type: str,
        status: str,
        delivery_surface: str,
        decision_reason: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                INSERT INTO ambient_actions
                (
                    action_id, owner_user_id, source_event_id, runtime_job_id, action_type, status,
                    delivery_surface, decision_reason, suppression_reason, content, metadata,
                    created_at, updated_at, delivered_at, failed_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12, $12, NULL, NULL)
                ON CONFLICT (action_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    source_event_id = EXCLUDED.source_event_id,
                    runtime_job_id = EXCLUDED.runtime_job_id,
                    action_type = EXCLUDED.action_type,
                    status = EXCLUDED.status,
                    delivery_surface = EXCLUDED.delivery_surface,
                    decision_reason = EXCLUDED.decision_reason,
                    suppression_reason = EXCLUDED.suppression_reason,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                self._clean_text(action_id),
                int(owner_user_id),
                self._clean_optional_text(source_event_id),
                self._clean_optional_text(runtime_job_id),
                self._clean_text(action_type),
                self._clean_text(status),
                self._clean_text(delivery_surface),
                self._clean_optional_text(decision_reason),
                self._clean_optional_text(suppression_reason),
                self._dump_json(content or {}),
                self._dump_json(metadata or {}),
                now,
            )
        finally:
            await conn.close()

    async def update_ambient_action(
        self,
        *,
        action_id: str,
        status: Optional[str] = None,
        runtime_job_id: Optional[str] = None,
        delivery_surface: Optional[str] = None,
        decision_reason: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        delivered_at: Optional[float] = None,
        failed_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        clean_action_id = self._clean_text(action_id)
        try:
            current = await conn.fetchrow(
                """
                SELECT content, metadata, delivered_at, failed_at
                FROM ambient_actions
                WHERE action_id = $1
                LIMIT 1
                """,
                clean_action_id,
            )
            if not current:
                return
            merged_content = self._load_json(current[0], {})
            merged_metadata = self._load_json(current[1], {})
            if content:
                merged_content.update(content)
            if metadata:
                merged_metadata.update(metadata)
            next_delivered_at = float(delivered_at) if delivered_at is not None else (float(current[2]) if current[2] is not None else None)
            next_failed_at = float(failed_at) if failed_at is not None else (float(current[3]) if current[3] is not None else None)
            await conn.execute(
                """
                UPDATE ambient_actions
                SET status = COALESCE($2, status),
                    runtime_job_id = COALESCE($3, runtime_job_id),
                    delivery_surface = COALESCE($4, delivery_surface),
                    decision_reason = COALESCE($5, decision_reason),
                    suppression_reason = COALESCE($6, suppression_reason),
                    content = $7::jsonb,
                    metadata = $8::jsonb,
                    updated_at = $9,
                    delivered_at = $10,
                    failed_at = $11
                WHERE action_id = $1
                """,
                clean_action_id,
                self._clean_optional_text(status),
                self._clean_optional_text(runtime_job_id),
                self._clean_optional_text(delivery_surface),
                self._clean_optional_text(decision_reason),
                self._clean_optional_text(suppression_reason),
                self._dump_json(merged_content),
                self._dump_json(merged_metadata),
                now,
                next_delivered_at,
                next_failed_at,
            )
        finally:
            await conn.close()

    async def list_ambient_actions(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT action_id, owner_user_id, source_event_id, runtime_job_id, action_type, status,
                       delivery_surface, decision_reason, suppression_reason, content, metadata,
                       created_at, updated_at, delivered_at, failed_at
                FROM ambient_actions
                WHERE owner_user_id = $1
            """
            params: List[Any] = [int(owner_user_id)]
            clean_statuses = self._clean_text_list(statuses)
            if clean_statuses:
                placeholders = self._brain._placeholders(len(clean_statuses), start=len(params) + 1)
                params.extend(clean_statuses)
                query += f" AND status IN ({placeholders})"
            params.append(max(int(limit or 24), 1))
            query += f" ORDER BY updated_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._ambient_action_record(row) for row in rows]
        finally:
            await conn.close()

    async def _write_operator_state(
        self,
        *,
        owner_user_id: int,
        pinned_workspace_root: Optional[str] = None,
        pinned_workspace_label: Optional[str] = None,
        pinned_remote_name: Optional[str] = None,
        current_workspace_root: Optional[str] = None,
        current_workspace_label: Optional[str] = None,
        current_remote_name: Optional[str] = None,
        last_operator_action_id: Optional[str] = None,
        last_operator_activity_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        replace_values: bool,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, created_at
                FROM operator_state
                WHERE owner_user_id = $1
                LIMIT 1
                """,
                int(owner_user_id),
            )
            merged_metadata = self._load_json(current[0], {}) if current else {}
            if metadata:
                merged_metadata.update(metadata)
            created_at = float(current[1] or now) if current else now
            conflict_updates = """
                    pinned_workspace_root = EXCLUDED.pinned_workspace_root,
                    pinned_workspace_label = EXCLUDED.pinned_workspace_label,
                    pinned_remote_name = EXCLUDED.pinned_remote_name,
                    current_workspace_root = EXCLUDED.current_workspace_root,
                    current_workspace_label = EXCLUDED.current_workspace_label,
                    current_remote_name = EXCLUDED.current_remote_name,
                    last_operator_action_id = EXCLUDED.last_operator_action_id,
                    last_operator_activity_at = EXCLUDED.last_operator_activity_at,
            """
            if not replace_values:
                conflict_updates = """
                    pinned_workspace_root = COALESCE(EXCLUDED.pinned_workspace_root, operator_state.pinned_workspace_root),
                    pinned_workspace_label = COALESCE(EXCLUDED.pinned_workspace_label, operator_state.pinned_workspace_label),
                    pinned_remote_name = COALESCE(EXCLUDED.pinned_remote_name, operator_state.pinned_remote_name),
                    current_workspace_root = COALESCE(EXCLUDED.current_workspace_root, operator_state.current_workspace_root),
                    current_workspace_label = COALESCE(EXCLUDED.current_workspace_label, operator_state.current_workspace_label),
                    current_remote_name = COALESCE(EXCLUDED.current_remote_name, operator_state.current_remote_name),
                    last_operator_action_id = COALESCE(EXCLUDED.last_operator_action_id, operator_state.last_operator_action_id),
                    last_operator_activity_at = COALESCE(EXCLUDED.last_operator_activity_at, operator_state.last_operator_activity_at),
                """
            await conn.execute(
                f"""
                INSERT INTO operator_state
                (
                    owner_user_id, pinned_workspace_root, pinned_workspace_label, pinned_remote_name,
                    current_workspace_root, current_workspace_label, current_remote_name,
                    last_operator_action_id, last_operator_activity_at, metadata, created_at, updated_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
                ON CONFLICT (owner_user_id) DO UPDATE SET
                {conflict_updates}
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                int(owner_user_id),
                self._clean_optional_text(pinned_workspace_root),
                self._clean_optional_text(pinned_workspace_label),
                self._clean_optional_text(pinned_remote_name),
                self._clean_optional_text(current_workspace_root),
                self._clean_optional_text(current_workspace_label),
                self._clean_optional_text(current_remote_name),
                self._clean_optional_text(last_operator_action_id),
                float(last_operator_activity_at) if last_operator_activity_at is not None else None,
                self._dump_json(merged_metadata),
                created_at,
                now,
            )
        finally:
            await conn.close()

    async def upsert_operator_state(
        self,
        *,
        owner_user_id: int,
        pinned_workspace_root: Optional[str] = None,
        pinned_workspace_label: Optional[str] = None,
        pinned_remote_name: Optional[str] = None,
        current_workspace_root: Optional[str] = None,
        current_workspace_label: Optional[str] = None,
        current_remote_name: Optional[str] = None,
        last_operator_action_id: Optional[str] = None,
        last_operator_activity_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._write_operator_state(
            owner_user_id=owner_user_id,
            pinned_workspace_root=pinned_workspace_root,
            pinned_workspace_label=pinned_workspace_label,
            pinned_remote_name=pinned_remote_name,
            current_workspace_root=current_workspace_root,
            current_workspace_label=current_workspace_label,
            current_remote_name=current_remote_name,
            last_operator_action_id=last_operator_action_id,
            last_operator_activity_at=last_operator_activity_at,
            metadata=metadata,
            replace_values=False,
        )

    async def replace_operator_state(
        self,
        *,
        owner_user_id: int,
        pinned_workspace_root: Optional[str] = None,
        pinned_workspace_label: Optional[str] = None,
        pinned_remote_name: Optional[str] = None,
        current_workspace_root: Optional[str] = None,
        current_workspace_label: Optional[str] = None,
        current_remote_name: Optional[str] = None,
        last_operator_action_id: Optional[str] = None,
        last_operator_activity_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._write_operator_state(
            owner_user_id=owner_user_id,
            pinned_workspace_root=pinned_workspace_root,
            pinned_workspace_label=pinned_workspace_label,
            pinned_remote_name=pinned_remote_name,
            current_workspace_root=current_workspace_root,
            current_workspace_label=current_workspace_label,
            current_remote_name=current_remote_name,
            last_operator_action_id=last_operator_action_id,
            last_operator_activity_at=last_operator_activity_at,
            metadata=metadata,
            replace_values=True,
        )

    async def get_operator_state(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT owner_user_id, pinned_workspace_root, pinned_workspace_label, pinned_remote_name,
                       current_workspace_root, current_workspace_label, current_remote_name,
                       last_operator_action_id, last_operator_activity_at, metadata, created_at, updated_at
                FROM operator_state
                WHERE owner_user_id = $1
                LIMIT 1
                """,
                int(owner_user_id),
            )
            return self._operator_state_record(row) if row else None
        finally:
            await conn.close()
