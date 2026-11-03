from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class RuntimeArtifactStore:
    """Persistence helper for owner-facing runtime artifacts."""

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

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _runtime_artifact_record(self, row: Any) -> Dict[str, Any]:
        storage_path = self._clean_text(row[7])
        exists = bool(storage_path) and Path(storage_path).exists()
        expires_at = float(row[11]) if row[11] is not None else None
        now = self._now()
        expired = expires_at is not None and expires_at > 0 and expires_at <= now
        status = self._clean_text(row[9], default="ready")
        effective_status = status
        if status != "deleted" and not exists:
            effective_status = "missing"
        elif status not in {"deleted", "missing"} and expired:
            effective_status = "expired"
        metadata = self._load_json(row[12], {})
        return {
            "artifact_id": self._clean_text(row[0]),
            "owner_user_id": int(row[1]),
            "source_kind": self._clean_text(row[2]),
            "source_id": self._clean_optional_text(row[3]),
            "source_run_id": self._clean_optional_text(row[4]),
            "source_action_id": self._clean_optional_text(row[5]),
            "surface_id": self._clean_optional_text(row[6]),
            "storage_path": storage_path,
            "artifact_kind": self._clean_text(row[8]),
            "status": status,
            "effective_status": effective_status,
            "content_type": self._clean_optional_text(row[10]),
            "expires_at": expires_at,
            "metadata": metadata,
            "created_at": float(row[13] or 0.0),
            "updated_at": float(row[14] or 0.0),
            "last_delivered_at": float(row[15]) if row[15] is not None else None,
            "delivery_count": int(row[16] or 0),
            "label": self._clean_text(metadata.get("label") or ""),
            "file_exists": exists,
            "expired": expired,
        }

    async def create_runtime_artifact(
        self,
        *,
        artifact_id: str,
        owner_user_id: int,
        source_kind: str,
        artifact_kind: str,
        storage_path: str,
        source_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
        source_action_id: Optional[str] = None,
        surface_id: Optional[str] = None,
        status: str = "ready",
        content_type: Optional[str] = None,
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = float(created_at) if created_at is not None else self._now()
        try:
            await conn.execute(
                """
                INSERT INTO runtime_artifacts
                (
                    artifact_id, owner_user_id, source_kind, source_id, source_run_id, source_action_id,
                    surface_id, storage_path, artifact_kind, status, content_type, expires_at,
                    metadata, created_at, updated_at, last_delivered_at, delivery_count
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $14, NULL, 0)
                """,
                self._clean_text(artifact_id),
                int(owner_user_id),
                self._clean_text(source_kind),
                self._clean_optional_text(source_id),
                self._clean_optional_text(source_run_id),
                self._clean_optional_text(source_action_id),
                self._clean_optional_text(surface_id),
                self._clean_text(storage_path),
                self._clean_text(artifact_kind),
                self._clean_text(status, default="ready"),
                self._clean_optional_text(content_type),
                float(expires_at) if expires_at is not None else None,
                self._dump_json(dict(metadata or {})),
                now,
            )
        finally:
            await conn.close()

    async def get_runtime_artifact(self, *, artifact_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT artifact_id, owner_user_id, source_kind, source_id, source_run_id, source_action_id,
                       surface_id, storage_path, artifact_kind, status, content_type, expires_at,
                       metadata, created_at, updated_at, last_delivered_at, delivery_count
                FROM runtime_artifacts
                WHERE artifact_id = $1
                LIMIT 1
                """,
                self._clean_text(artifact_id),
            )
            return self._runtime_artifact_record(row) if row else None
        finally:
            await conn.close()

    async def list_runtime_artifacts(
        self,
        *,
        owner_user_id: int,
        source_kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT artifact_id, owner_user_id, source_kind, source_id, source_run_id, source_action_id,
                       surface_id, storage_path, artifact_kind, status, content_type, expires_at,
                       metadata, created_at, updated_at, last_delivered_at, delivery_count
                FROM runtime_artifacts
                WHERE owner_user_id = $1
            """
            params: List[Any] = [int(owner_user_id)]
            clean_source_kind = self._clean_text(source_kind)
            if clean_source_kind:
                params.append(clean_source_kind)
                query += f" AND source_kind = ${len(params)}"
            clean_status = self._clean_text(status)
            if clean_status:
                params.append(clean_status)
                query += f" AND status = ${len(params)}"
            params.append(max(int(limit or 24), 1))
            query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._runtime_artifact_record(row) for row in rows]
        finally:
            await conn.close()

    async def mark_runtime_artifact_delivered(
        self,
        *,
        artifact_id: str,
        surface_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        clean_artifact_id = self._clean_text(artifact_id)
        if not clean_artifact_id:
            return
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata, delivery_count
                FROM runtime_artifacts
                WHERE artifact_id = $1
                LIMIT 1
                """,
                clean_artifact_id,
            )
            if not current:
                return
            merged_metadata = self._load_json(current[0], {})
            if metadata:
                merged_metadata.update(dict(metadata))
            await conn.execute(
                """
                UPDATE runtime_artifacts
                SET surface_id = COALESCE($2, surface_id),
                    metadata = $3::jsonb,
                    updated_at = $4,
                    last_delivered_at = $4,
                    delivery_count = COALESCE(delivery_count, 0) + 1
                WHERE artifact_id = $1
                """,
                clean_artifact_id,
                self._clean_optional_text(surface_id),
                self._dump_json(merged_metadata),
                now,
            )
        finally:
            await conn.close()

    async def extend_runtime_artifact_expiry(
        self,
        *,
        artifact_id: str,
        expires_at: Optional[float] = None,
        extend_seconds: Optional[float] = None,
    ) -> None:
        clean_artifact_id = self._clean_text(artifact_id)
        if not clean_artifact_id:
            return
        now = self._now()
        next_expiry = float(expires_at) if expires_at is not None else None
        if next_expiry is None and extend_seconds is not None:
            next_expiry = now + max(float(extend_seconds), 0.0)
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                UPDATE runtime_artifacts
                SET expires_at = $2,
                    updated_at = $3
                WHERE artifact_id = $1
                """,
                clean_artifact_id,
                next_expiry,
                now,
            )
        finally:
            await conn.close()

    async def delete_runtime_artifact(self, *, artifact_id: str) -> None:
        clean_artifact_id = self._clean_text(artifact_id)
        if not clean_artifact_id:
            return
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                UPDATE runtime_artifacts
                SET status = 'deleted',
                    updated_at = $2
                WHERE artifact_id = $1
                """,
                clean_artifact_id,
                now,
            )
        finally:
            await conn.close()
