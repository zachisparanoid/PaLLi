from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class PlatformConnectionStore:
    """Persistence helper for platform identities, verified links, and surface mappings."""

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

    def _clean_platform(self, value: Any, *, default: str = "") -> str:
        return self._clean_text(value, default=default).lower()

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _platform_subject_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "platform": str(row[1] or ""),
            "platform_user_id": str(row[2] or ""),
            "display_name": str(row[3] or ""),
            "metadata": self._load_json(row[4], {}),
            "created_at": float(row[5] or 0.0),
            "updated_at": float(row[6] or 0.0),
        }

    def _platform_surface_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "platform": str(row[1] or ""),
            "surface_id": str(row[2] or ""),
            "surface_kind": str(row[3] or "channel"),
            "label": str(row[4] or ""),
            "metadata": self._load_json(row[5], {}),
            "created_at": float(row[6] or 0.0),
            "updated_at": float(row[7] or 0.0),
        }

    def _platform_space_connection_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "platform": str(row[1] or ""),
            "space_id": str(row[2] or ""),
            "space_kind": str(row[3] or "channel"),
            "account_user_id": int(row[4]),
            "default_response_mode": str(row[5] or "mention_or_reply"),
            "label": str(row[6] or ""),
            "metadata": self._load_json(row[7], {}),
            "created_at": float(row[8] or 0.0),
            "updated_at": float(row[9] or 0.0),
        }

    def _platform_connection_record(self, row: Any) -> Dict[str, Any]:
        is_verified = bool(row[6])
        return {
            "id": int(row[0]),
            "user_id": int(row[1]),
            "platform": str(row[2] or ""),
            "platform_user_id": str(row[3] or ""),
            "platform_username": str(row[4] or ""),
            "display_name": str(row[5] or ""),
            "verified": is_verified,
            "is_verified": is_verified,
            "verification_code": row[7],
            "verification_expires_at": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }

    async def store_verification_code(
        self,
        *,
        user_id: int,
        platform: str,
        platform_user_id: str,
        code: str,
        ttl_seconds: int = 600,
    ) -> bool:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            expires_at = now + int(ttl_seconds)
            await conn.execute(
                """
                INSERT INTO platform_connections
                (user_id, platform, platform_user_id, verification_code, verification_expires_at, verified, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, FALSE, $6, $7)
                ON CONFLICT(platform, platform_user_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    verification_code = excluded.verification_code,
                    verification_expires_at = excluded.verification_expires_at,
                    verified = FALSE,
                    updated_at = excluded.updated_at
                """,
                int(user_id),
                self._clean_platform(platform),
                self._clean_text(platform_user_id),
                self._clean_text(code),
                expires_at,
                now,
                now,
            )
            return True
        finally:
            await conn.close()

    async def resolve_platform_subject_id(
        self,
        *,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        clean_platform = self._clean_platform(platform)
        clean_platform_user_id = self._clean_text(platform_user_id)
        try:
            now = self._now()
            existing = await conn.fetchval(
                """
                SELECT id
                FROM platform_subjects
                WHERE platform = $1 AND platform_user_id = $2
                LIMIT 1
                """,
                clean_platform,
                clean_platform_user_id,
            )
            if existing is not None:
                await conn.execute(
                    """
                    UPDATE platform_subjects
                    SET display_name = COALESCE($2, display_name),
                        metadata = COALESCE($3::jsonb, metadata),
                        updated_at = $4
                    WHERE id = $1
                    """,
                    int(existing),
                    self._clean_optional_text(display_name),
                    self._dump_json(metadata or {}) if metadata else None,
                    now,
                )
                return int(existing)
            subject_id = await conn.fetchval(
                """
                INSERT INTO platform_subjects
                (platform, platform_user_id, display_name, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                RETURNING id
                """,
                clean_platform,
                clean_platform_user_id,
                self._clean_optional_text(display_name),
                self._dump_json(metadata or {}) if metadata else None,
                now,
                now,
            )
            return int(subject_id)
        finally:
            await conn.close()

    async def get_platform_subject(
        self,
        *,
        platform: str,
        platform_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, platform, platform_user_id, display_name, metadata, created_at, updated_at
                FROM platform_subjects
                WHERE platform = $1 AND platform_user_id = $2
                LIMIT 1
                """,
                self._clean_platform(platform),
                self._clean_text(platform_user_id),
            )
            return self._platform_subject_record(row) if row else None
        finally:
            await conn.close()

    async def resolve_platform_surface_id(
        self,
        *,
        platform: str,
        surface_id: str,
        surface_kind: str = "channel",
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        clean_platform = self._clean_platform(platform)
        clean_surface_id = self._clean_text(surface_id)
        try:
            now = self._now()
            existing = await conn.fetchval(
                """
                SELECT id
                FROM platform_surfaces
                WHERE platform = $1 AND surface_id = $2
                LIMIT 1
                """,
                clean_platform,
                clean_surface_id,
            )
            if existing is not None:
                await conn.execute(
                    """
                    UPDATE platform_surfaces
                    SET surface_kind = COALESCE($2, surface_kind),
                        label = COALESCE($3, label),
                        metadata = COALESCE($4::jsonb, metadata),
                        updated_at = $5
                    WHERE id = $1
                    """,
                    int(existing),
                    self._clean_platform(surface_kind, default="channel"),
                    self._clean_optional_text(label),
                    self._dump_json(metadata or {}) if metadata else None,
                    now,
                )
                return int(existing)
            resolved = await conn.fetchval(
                """
                INSERT INTO platform_surfaces
                (platform, surface_id, surface_kind, label, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                RETURNING id
                """,
                clean_platform,
                clean_surface_id,
                self._clean_platform(surface_kind, default="channel"),
                self._clean_optional_text(label),
                self._dump_json(metadata or {}) if metadata else None,
                now,
                now,
            )
            return int(resolved)
        finally:
            await conn.close()

    async def get_platform_surface(
        self,
        *,
        platform: str,
        surface_id: str,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, platform, surface_id, surface_kind, label, metadata, created_at, updated_at
                FROM platform_surfaces
                WHERE platform = $1 AND surface_id = $2
                LIMIT 1
                """,
                self._clean_platform(platform),
                self._clean_text(surface_id),
            )
            return self._platform_surface_record(row) if row else None
        finally:
            await conn.close()

    async def verify_platform_connection(
        self,
        *,
        platform: str,
        platform_user_id: str,
        code: str,
    ) -> Optional[int]:
        conn = await self._brain.get_connection()
        clean_platform = self._clean_platform(platform)
        clean_platform_user_id = self._clean_text(platform_user_id)
        try:
            now = self._now()
            row = await conn.fetchrow(
                """
                SELECT user_id, verification_expires_at
                FROM platform_connections
                WHERE platform = $1 AND platform_user_id = $2 AND verification_code = $3
                """,
                clean_platform,
                clean_platform_user_id,
                self._clean_text(code),
            )
            if not row:
                return None
            user_id = int(row[0])
            expires_at = float(row[1]) if row[1] is not None else None
            if expires_at is None or now > expires_at:
                return None
            await conn.execute(
                """
                UPDATE platform_connections
                SET verified = TRUE, verification_code = NULL, verification_expires_at = NULL, updated_at = $1
                WHERE platform = $2 AND platform_user_id = $3
                """,
                now,
                clean_platform,
                clean_platform_user_id,
            )
            if clean_platform == "discord":
                await conn.execute(
                    "UPDATE users SET discord_user_id = $1 WHERE id = $2",
                    clean_platform_user_id,
                    user_id,
                )
            return user_id
        finally:
            await conn.close()

    async def get_verified_connection(self, *, platform: str, platform_user_id: str) -> Optional[int]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT user_id FROM platform_connections
                WHERE platform = $1 AND platform_user_id = $2 AND verified = TRUE
                """,
                self._clean_platform(platform),
                self._clean_text(platform_user_id),
            )
            return int(row[0]) if row else None
        finally:
            await conn.close()

    async def link_verified_platform_connection(
        self,
        *,
        user_id: int,
        platform: str,
        platform_user_id: str,
        platform_username: str = "",
        display_name: str = "",
        connection_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        clean_platform = self._clean_platform(platform)
        clean_platform_user_id = self._clean_text(platform_user_id)
        try:
            now = self._now()
            connection_id = await conn.fetchval(
                """
                INSERT INTO platform_connections
                (user_id, platform, platform_user_id, platform_username, display_name, verified, verification_code,
                 verification_expires_at, connection_metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, TRUE, NULL, NULL, $6::jsonb, $7, $8)
                ON CONFLICT(platform, platform_user_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    platform_username = EXCLUDED.platform_username,
                    display_name = EXCLUDED.display_name,
                    verified = TRUE,
                    verification_code = NULL,
                    verification_expires_at = NULL,
                    connection_metadata = EXCLUDED.connection_metadata,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                int(user_id),
                clean_platform,
                clean_platform_user_id,
                self._clean_optional_text(platform_username),
                self._clean_optional_text(display_name),
                self._dump_json(connection_metadata or {}) if connection_metadata else None,
                now,
                now,
            )
            if clean_platform == "discord":
                await conn.execute(
                    "UPDATE users SET discord_user_id = $1 WHERE id = $2",
                    clean_platform_user_id,
                    int(user_id),
                )
            return int(connection_id)
        finally:
            await conn.close()

    async def upsert_platform_space_connection(
        self,
        *,
        platform: str,
        space_id: str,
        account_user_id: int,
        space_kind: str = "channel",
        default_response_mode: str = "mention_or_reply",
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            mapping_id = await conn.fetchval(
                """
                INSERT INTO platform_space_connections
                (platform, space_id, space_kind, account_user_id, default_response_mode, label, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                ON CONFLICT(platform, space_id) DO UPDATE SET
                    space_kind = EXCLUDED.space_kind,
                    account_user_id = EXCLUDED.account_user_id,
                    default_response_mode = EXCLUDED.default_response_mode,
                    label = EXCLUDED.label,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                self._clean_platform(platform),
                self._clean_text(space_id),
                self._clean_platform(space_kind, default="channel"),
                int(account_user_id),
                self._clean_platform(default_response_mode, default="mention_or_reply"),
                self._clean_optional_text(label),
                self._dump_json(metadata or {}) if metadata else None,
                now,
                now,
            )
            return int(mapping_id)
        finally:
            await conn.close()

    async def get_platform_space_connection(
        self,
        *,
        platform: str,
        space_id: str,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, platform, space_id, space_kind, account_user_id, default_response_mode,
                       label, metadata, created_at, updated_at
                FROM platform_space_connections
                WHERE platform = $1 AND space_id = $2
                LIMIT 1
                """,
                self._clean_platform(platform),
                self._clean_text(space_id),
            )
            return self._platform_space_connection_record(row) if row else None
        finally:
            await conn.close()

    async def list_platform_space_connections(
        self,
        *,
        account_user_id: int,
        platform: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT id, platform, space_id, space_kind, account_user_id, default_response_mode,
                       label, metadata, created_at, updated_at
                FROM platform_space_connections
                WHERE account_user_id = $1
            """
            params: List[Any] = [int(account_user_id)]
            if platform:
                params.append(self._clean_platform(platform))
                query += f" AND platform = ${len(params)}"
            query += " ORDER BY updated_at DESC"
            rows = await conn.fetch(query, *params)
            return [self._platform_space_connection_record(row) for row in rows]
        finally:
            await conn.close()

    async def link_platform(
        self,
        *,
        user_id: int,
        platform: str,
        platform_user_id: str,
        platform_username: Optional[str] = None,
        display_name: Optional[str] = None,
        verification_code: Optional[str] = None,
        verification_expires_at: Optional[float] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            connection_id = await conn.fetchval(
                """
                INSERT INTO platform_connections
                (user_id, platform, platform_user_id, platform_username, display_name,
                 verified, verification_code, verification_expires_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, FALSE, $6, $7, $8, $9)
                RETURNING id
                """,
                int(user_id),
                self._clean_platform(platform),
                self._clean_text(platform_user_id),
                self._clean_optional_text(platform_username),
                self._clean_optional_text(display_name),
                self._clean_optional_text(verification_code),
                float(verification_expires_at) if verification_expires_at is not None else None,
                now,
                now,
            )
            return int(connection_id)
        finally:
            await conn.close()

    async def get_platform_connections(self, *, user_id: int) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, user_id, platform, platform_user_id, platform_username,
                       display_name, verified, verification_code, verification_expires_at,
                       created_at, updated_at
                FROM platform_connections
                WHERE user_id = $1
                """,
                int(user_id),
            )
            return [self._platform_connection_record(row) for row in rows]
        finally:
            await conn.close()
