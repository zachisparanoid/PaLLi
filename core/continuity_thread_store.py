from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class ContinuityThreadStore:
    """Persistence helper for continuity thread records."""

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

    def _continuity_thread_record(self, row: Any) -> Dict[str, Any]:
        return {
            "thread_id": str(row[0] or ""),
            "owner_user_id": int(row[1]),
            "thread_kind": str(row[2] or ""),
            "status": str(row[3] or "active"),
            "label": str(row[4] or ""),
            "workspace_root": str(row[5] or ""),
            "workspace_label": str(row[6] or ""),
            "root_agent_session_key": str(row[7] or ""),
            "last_runtime_session_key": str(row[8] or ""),
            "last_workflow_id": str(row[9] or ""),
            "reset_count": int(row[10] or 0),
            "last_activity": float(row[11] or 0.0),
            "metadata": self._load_json(row[12], {}),
            "created_at": float(row[13] or 0.0),
            "updated_at": float(row[14] or 0.0),
            "archived_at": float(row[15]) if row[15] is not None else None,
        }

    async def upsert_continuity_thread(
        self,
        *,
        thread_id: str,
        owner_user_id: int,
        thread_kind: str,
        status: str = "active",
        label: str = "",
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        root_agent_session_key: Optional[str] = None,
        last_runtime_session_key: Optional[str] = None,
        last_workflow_id: Optional[str] = None,
        reset_count: int = 0,
        last_activity: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        clean_thread_id = self._clean_text(thread_id)
        if not clean_thread_id:
            await conn.close()
            return
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, reset_count
                FROM continuity_threads
                WHERE thread_id = $1
                LIMIT 1
                """,
                clean_thread_id,
            )
            merged_metadata = self._load_json(current[0], {}) if current else {}
            if metadata:
                merged_metadata.update(metadata)
            next_reset_count = int(reset_count if reset_count is not None else (current[1] or 0) if current else 0)
            await conn.execute(
                """
                INSERT INTO continuity_threads
                (
                    thread_id, owner_user_id, thread_kind, status, label, workspace_root, workspace_label,
                    root_agent_session_key, last_runtime_session_key, last_workflow_id, reset_count,
                    last_activity, metadata, created_at, updated_at, archived_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $14, $15)
                ON CONFLICT (thread_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    thread_kind = EXCLUDED.thread_kind,
                    status = EXCLUDED.status,
                    label = EXCLUDED.label,
                    workspace_root = COALESCE(EXCLUDED.workspace_root, continuity_threads.workspace_root),
                    workspace_label = COALESCE(EXCLUDED.workspace_label, continuity_threads.workspace_label),
                    root_agent_session_key = COALESCE(EXCLUDED.root_agent_session_key, continuity_threads.root_agent_session_key),
                    last_runtime_session_key = COALESCE(EXCLUDED.last_runtime_session_key, continuity_threads.last_runtime_session_key),
                    last_workflow_id = COALESCE(EXCLUDED.last_workflow_id, continuity_threads.last_workflow_id),
                    reset_count = EXCLUDED.reset_count,
                    last_activity = EXCLUDED.last_activity,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at,
                    archived_at = EXCLUDED.archived_at
                """,
                clean_thread_id,
                int(owner_user_id),
                self._clean_text(thread_kind),
                self._clean_text(status, default="active"),
                self._clean_optional_text(label),
                self._clean_optional_text(workspace_root),
                self._clean_optional_text(workspace_label),
                self._clean_optional_text(root_agent_session_key),
                self._clean_optional_text(last_runtime_session_key),
                self._clean_optional_text(last_workflow_id),
                next_reset_count,
                float(last_activity if last_activity is not None else now),
                self._dump_json(merged_metadata),
                now,
                float(archived_at) if archived_at is not None else None,
            )
        finally:
            await conn.close()

    async def update_continuity_thread(
        self,
        *,
        thread_id: str,
        status: Optional[str] = None,
        label: Optional[str] = None,
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        root_agent_session_key: Optional[str] = None,
        last_runtime_session_key: Optional[str] = None,
        last_workflow_id: Optional[str] = None,
        reset_count: Optional[int] = None,
        last_activity: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        clean_thread_id = self._clean_text(thread_id)
        if not clean_thread_id:
            await conn.close()
            return
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, archived_at, reset_count
                FROM continuity_threads
                WHERE thread_id = $1
                LIMIT 1
                """,
                clean_thread_id,
            )
            if not current:
                return
            merged_metadata = self._load_json(current[0], {})
            if metadata:
                merged_metadata.update(metadata)
            next_archived_at = float(archived_at) if archived_at is not None else (float(current[1]) if current[1] is not None else None)
            next_reset_count = int(reset_count) if reset_count is not None else int(current[2] or 0)
            await conn.execute(
                """
                UPDATE continuity_threads
                SET status = COALESCE($2, status),
                    label = COALESCE($3, label),
                    workspace_root = COALESCE($4, workspace_root),
                    workspace_label = COALESCE($5, workspace_label),
                    root_agent_session_key = COALESCE($6, root_agent_session_key),
                    last_runtime_session_key = COALESCE($7, last_runtime_session_key),
                    last_workflow_id = COALESCE($8, last_workflow_id),
                    reset_count = COALESCE($9, reset_count),
                    last_activity = COALESCE($10, last_activity),
                    metadata = $11::jsonb,
                    updated_at = $12,
                    archived_at = $13
                WHERE thread_id = $1
                """,
                clean_thread_id,
                self._clean_optional_text(status),
                self._clean_optional_text(label),
                self._clean_optional_text(workspace_root),
                self._clean_optional_text(workspace_label),
                self._clean_optional_text(root_agent_session_key),
                self._clean_optional_text(last_runtime_session_key),
                self._clean_optional_text(last_workflow_id),
                next_reset_count,
                float(last_activity) if last_activity is not None else None,
                self._dump_json(merged_metadata),
                now,
                next_archived_at,
            )
        finally:
            await conn.close()

    async def get_continuity_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT thread_id, owner_user_id, thread_kind, status, label, workspace_root, workspace_label,
                       root_agent_session_key, last_runtime_session_key, last_workflow_id, reset_count,
                       last_activity, metadata, created_at, updated_at, archived_at
                FROM continuity_threads
                WHERE thread_id = $1
                LIMIT 1
                """,
                self._clean_text(thread_id),
            )
            return self._continuity_thread_record(row) if row else None
        finally:
            await conn.close()

    async def find_continuity_thread_by_scope(
        self,
        *,
        owner_user_id: int,
        thread_kind: str,
        scope_key: str,
        include_archived: bool = False,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT thread_id, owner_user_id, thread_kind, status, label, workspace_root, workspace_label,
                       root_agent_session_key, last_runtime_session_key, last_workflow_id, reset_count,
                       last_activity, metadata, created_at, updated_at, archived_at
                FROM continuity_threads
                WHERE owner_user_id = $1
                  AND thread_kind = $2
                  AND json_extract(metadata, '$.scope_key') = $3
                  AND ($4::boolean OR archived_at IS NULL)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                int(owner_user_id),
                self._clean_text(thread_kind),
                self._clean_text(scope_key),
                bool(include_archived),
            )
            return self._continuity_thread_record(row) if row else None
        finally:
            await conn.close()

    async def list_continuity_threads(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 24,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT thread_id, owner_user_id, thread_kind, status, label, workspace_root, workspace_label,
                       root_agent_session_key, last_runtime_session_key, last_workflow_id, reset_count,
                       last_activity, metadata, created_at, updated_at, archived_at
                FROM continuity_threads
                WHERE owner_user_id = $1
            """
            params: List[Any] = [int(owner_user_id)]
            if not include_archived:
                query += " AND archived_at IS NULL"
            clean_statuses = self._clean_text_list(statuses)
            if clean_statuses:
                placeholders = self._brain._placeholders(len(clean_statuses), start=len(params) + 1)
                params.extend(clean_statuses)
                query += f" AND status IN ({placeholders})"
            params.append(max(int(limit or 24), 1))
            query += f" ORDER BY last_activity DESC, updated_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._continuity_thread_record(row) for row in rows]
        finally:
            await conn.close()
