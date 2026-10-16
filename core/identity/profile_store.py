from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from core.activity_logger import activity_logger

logger = logging.getLogger(__name__)


class ProfileReflectionStore:
    """Persistence helper for user profiles, identity version history, and reflection runs."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _rowcount(self, status: str) -> int:
        return self._brain._rowcount(status)

    def _profile_record(self, row: Any) -> Dict[str, Any]:
        return {
            "user_id": row[0],
            "platform": row[1],
            "username": row[2],
            "display_name": row[3],
            "personality_data": self._load_json(row[4], {}),
            "communication_style": self._load_json(row[5], {}),
            "preferences": self._load_json(row[6], {}),
            "habits": self._load_json(row[7], {}),
            "emotional_patterns": self._load_json(row[8], {}),
            "learning_progress": row[9],
            "last_interaction": row[10],
            "total_interactions": row[11],
            "personality_preset": row[12],
            "created_at": row[13],
            "updated_at": row[14],
        }

    def create_default_profile(self, user_id: int, platform: str) -> Dict[str, Any]:
        """Create default user profile structure."""
        return {
            "user_id": user_id,
            "platform": platform,
            "username": None,
            "display_name": None,
            "personality_data": {},
            "communication_style": {},
            "preferences": {},
            "habits": {},
            "emotional_patterns": {},
            "learning_progress": 0.0,
            "last_interaction": self._now(),
            "total_interactions": 0,
            "personality_preset": "default",
            "created_at": self._now(),
            "updated_at": self._now(),
        }

    async def get_user_profile(self, user_id: int, platform: str) -> Dict[str, Any]:
        """Get comprehensive user profile."""
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT user_id, platform, username, display_name,
                       personality_data, communication_style, preferences,
                       habits, emotional_patterns, learning_progress,
                       last_interaction, total_interactions, personality_preset,
                       created_at, updated_at
                FROM user_profiles
                WHERE user_id = $1 AND platform = $2
            """,
                user_id,
                platform,
            )

            if not row:
                default_profile = self.create_default_profile(user_id, platform)
                await self._update_user_profile_internal(
                    conn,
                    user_id,
                    platform,
                    default_profile,
                )
                return default_profile

            return self._profile_record(row)
        finally:
            await conn.close()

    async def update_user_profile(self, user_id: int, platform: str, updates: Dict[str, Any]) -> None:
        """Update user profile with new learning data."""
        conn = await self._brain.get_connection()
        activity_logger.log_database_operation("update_user_profile", f"user {user_id} on {platform}")
        try:
            await self._update_user_profile_internal(conn, user_id, platform, updates)
            logger.debug("Updated profile for user %s on %s", user_id, platform)
        finally:
            await conn.close()

    async def _update_user_profile_internal(
        self,
        conn: Any,
        user_id: int,
        platform: str,
        updates: Dict[str, Any],
    ) -> None:
        """Update a user profile within an existing connection."""
        try:
            row = await conn.fetchrow(
                """
                SELECT user_id, platform, username, display_name,
                       personality_data, communication_style, preferences,
                       habits, emotional_patterns, learning_progress,
                       last_interaction, total_interactions, personality_preset,
                       created_at, updated_at
                FROM user_profiles
                WHERE user_id = $1 AND platform = $2
            """,
                user_id,
                platform,
            )

            if row:
                profile = self._profile_record(row)
            else:
                profile = self.create_default_profile(user_id, platform)
        except Exception as exc:
            logger.error("Error getting profile: %s", exc)
            profile = self.create_default_profile(user_id, platform)

        for key, value in updates.items():
            if key in {
                "personality_data",
                "communication_style",
                "preferences",
                "habits",
                "emotional_patterns",
            }:
                if isinstance(value, dict):
                    profile[key].update(value)
                else:
                    profile[key] = value
            else:
                profile[key] = value

        updated_at = self._now()
        status = await conn.execute(
            """
            UPDATE user_profiles
            SET username = $1,
                display_name = $2,
                personality_data = $3::jsonb,
                communication_style = $4::jsonb,
                preferences = $5::jsonb,
                habits = $6::jsonb,
                emotional_patterns = $7::jsonb,
                learning_progress = $8,
                last_interaction = $9,
                total_interactions = $10,
                personality_preset = $11,
                updated_at = $12
            WHERE user_id = $13 AND platform = $14
            """,
            profile["username"],
            profile["display_name"],
            json.dumps(profile["personality_data"]),
            json.dumps(profile["communication_style"]),
            json.dumps(profile["preferences"]),
            json.dumps(profile["habits"]),
            json.dumps(profile["emotional_patterns"]),
            profile["learning_progress"],
            profile["last_interaction"],
            profile["total_interactions"],
            profile.get("personality_preset"),
            updated_at,
            user_id,
            platform,
        )

        if self._rowcount(status) == 0:
            await conn.execute(
                """
                INSERT INTO user_profiles
                (user_id, platform, username, display_name, personality_data,
                 communication_style, preferences, habits, emotional_patterns,
                 learning_progress, last_interaction, total_interactions,
                 personality_preset, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $12, $13, $14, $15)
                """,
                user_id,
                platform,
                profile["username"],
                profile["display_name"],
                json.dumps(profile["personality_data"]),
                json.dumps(profile["communication_style"]),
                json.dumps(profile["preferences"]),
                json.dumps(profile["habits"]),
                json.dumps(profile["emotional_patterns"]),
                profile["learning_progress"],
                profile["last_interaction"],
                profile["total_interactions"],
                profile.get("personality_preset"),
                profile["created_at"],
                updated_at,
            )

    async def _update_user_interaction_internal(self, conn: Any, user_id: int, platform: str) -> None:
        """Update user interaction statistics within an existing connection."""
        status = await conn.execute(
            """
            UPDATE user_profiles
            SET last_interaction = $1, total_interactions = total_interactions + 1
            WHERE user_id = $2 AND platform = $3
        """,
            self._now(),
            user_id,
            platform,
        )

        if self._rowcount(status) == 0:
            default_profile = self.create_default_profile(user_id, platform)
            default_profile["total_interactions"] = 1
            await self._update_user_profile_internal(
                conn,
                user_id,
                platform,
                default_profile,
            )

    async def update_user_interaction(self, user_id: int, platform: str) -> None:
        """Update user interaction statistics."""
        conn = await self._brain.get_connection()
        try:
            await self._update_user_interaction_internal(conn, user_id, platform)
        finally:
            await conn.close()

    async def create_identity_document_version(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        file_path: str,
        version: int,
        actor_type: str,
        actor_id: str,
        reason: str,
        content: str,
        frontmatter: Dict[str, Any],
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                INSERT INTO identity_document_versions
                (user_id, platform, doc_key, file_path, version, actor_type, actor_id,
                 reason, content, frontmatter, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
                """,
                user_id,
                platform,
                doc_key,
                file_path,
                int(version),
                actor_type,
                actor_id,
                reason,
                content,
                json.dumps(frontmatter or {}),
                self._now(),
            )
        finally:
            await conn.close()

    async def list_identity_document_versions(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            params: List[Any] = [user_id, platform]
            query = (
                "SELECT doc_key, file_path, version, actor_type, actor_id, reason, content, frontmatter, created_at "
                "FROM identity_document_versions WHERE user_id = $1 AND platform = $2"
            )
            if doc_key:
                params.append(doc_key)
                query += f" AND doc_key = ${len(params)}"
            params.append(max(1, int(limit)))
            query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [
                {
                    "doc_key": row[0],
                    "file_path": row[1],
                    "version": row[2],
                    "actor_type": row[3],
                    "actor_id": row[4],
                    "reason": row[5],
                    "content": row[6],
                    "frontmatter": self._load_json(row[7], {}),
                    "created_at": row[8],
                }
                for row in rows
            ]
        finally:
            await conn.close()

    async def first_identity_document_version_at(self, *, user_id: int) -> Optional[float]:
        """MIN(created_at) over this user's identity_document_versions rows,
        any platform or doc — when their identity was first written down,
        which is first contact (Batch 5, task 9, ruling R16). None when the
        user has no version rows."""
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT MIN(created_at) FROM identity_document_versions WHERE user_id = $1",
                int(user_id),
            )
        finally:
            await conn.close()
        if not rows or rows[0][0] is None:
            return None
        return float(rows[0][0])

    async def get_identity_document_version(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        version: int,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT doc_key, file_path, version, actor_type, actor_id, reason, content, frontmatter, created_at
                FROM identity_document_versions
                WHERE user_id = $1 AND platform = $2 AND doc_key = $3 AND version = $4
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                platform,
                doc_key,
                int(version),
            )
            if row is None:
                return None
            return {
                "doc_key": row[0],
                "file_path": row[1],
                "version": row[2],
                "actor_type": row[3],
                "actor_id": row[4],
                "reason": row[5],
                "content": row[6],
                "frontmatter": self._load_json(row[7], {}),
                "created_at": row[8],
            }
        finally:
            await conn.close()

