from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

_ACCOUNT_FEATURE_KEYS = (
    "allow_web_search",
    "allow_email",
    "allow_calendar",
    "allow_voice",
    "allow_proactive",
    "allow_personality_learning",
)


class AccountIntegrationStore:
    """Persistence helper for account-linked guild mappings, features, OAuth, and onboarding."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _default_account_features(self) -> Dict[str, bool]:
        return {key: True for key in _ACCOUNT_FEATURE_KEYS}

    def _discord_guild_account_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "guild_id": int(row[1]),
            "account_user_id": int(row[2]),
            "default_channel_id": int(row[3]) if row[3] is not None else None,
            "created_at": row[4],
            "updated_at": row[5],
        }

    def _oauth_connection_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "user_id": int(row[1]),
            "provider": row[2],
            "provider_user_id": row[3],
            "provider_email": row[4],
            "access_token_encrypted": row[5],
            "refresh_token_encrypted": row[6],
            "token_expires_at": row[7],
            "scopes": self._load_json(row[8], []),
            "connection_status": row[9],
            "last_sync_at": row[10],
            "error_message": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def _onboarding_progress_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row[0],
            "user_id": row[1],
            "current_step": row[2],
            "completed_steps": self._load_json(row[3], []),
            "platform_connections_completed": bool(row[4]),
            "oauth_connections_completed": bool(row[5]),
            "profile_setup_completed": bool(row[6]),
            "completed_at": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    async def link_discord_guild_to_account(
        self,
        guild_id: int,
        account_user_id: int,
        default_channel_id: Optional[int] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            clean_guild_id = int(guild_id)
            clean_account_user_id = int(account_user_id)
            clean_default_channel_id = int(default_channel_id) if default_channel_id is not None else None
            mapping_id = await conn.fetchval(
                """
                INSERT INTO discord_guild_accounts (guild_id, account_user_id, default_channel_id, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT(guild_id) DO UPDATE SET
                    account_user_id = excluded.account_user_id,
                    default_channel_id = excluded.default_channel_id,
                    updated_at = excluded.updated_at
                RETURNING id
                """,
                clean_guild_id,
                clean_account_user_id,
                clean_default_channel_id,
                now,
                now,
            )
            await conn.execute(
                """
                INSERT INTO platform_space_connections
                (platform, space_id, space_kind, account_user_id, default_response_mode, label, metadata, created_at, updated_at)
                VALUES ('discord', $1, 'guild', $2, 'mention_or_reply', NULL, $3::jsonb, $4, $5)
                ON CONFLICT(platform, space_id) DO UPDATE SET
                    account_user_id = EXCLUDED.account_user_id,
                    default_response_mode = EXCLUDED.default_response_mode,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                str(clean_guild_id),
                clean_account_user_id,
                self._dump_json({"default_channel_id": clean_default_channel_id}),
                now,
                now,
            )
            return int(mapping_id)
        finally:
            await conn.close()

    async def get_account_for_discord_guild(self, guild_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            clean_guild_id = int(guild_id)
            row = await conn.fetchrow(
                """
                SELECT id, guild_id, account_user_id, default_channel_id,
                       created_at, updated_at
                FROM discord_guild_accounts
                WHERE guild_id = $1
                """,
                clean_guild_id,
            )
            if row:
                return self._discord_guild_account_record(row)

            row = await conn.fetchrow(
                """
                SELECT id, space_id, account_user_id, metadata, created_at, updated_at
                FROM platform_space_connections
                WHERE platform = 'discord' AND space_id = $1
                LIMIT 1
                """,
                str(clean_guild_id),
            )
            if not row:
                return None

            metadata = self._load_json(row[3], {})
            return {
                "id": row[0],
                "guild_id": int(row[1]),
                "account_user_id": int(row[2]),
                "default_channel_id": metadata.get("default_channel_id"),
                "created_at": row[4],
                "updated_at": row[5],
            }
        finally:
            await conn.close()

    async def get_discord_guilds_for_account(self, account_user_id: int) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, guild_id, account_user_id, default_channel_id,
                       created_at, updated_at
                FROM discord_guild_accounts
                WHERE account_user_id = $1
                """,
                int(account_user_id),
            )
            return [self._discord_guild_account_record(row) for row in rows]
        finally:
            await conn.close()

    async def upsert_account_features(
        self,
        account_user_id: int,
        **feature_flags: bool,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            row = await conn.fetchrow(
                """
                SELECT account_user_id, allow_web_search, allow_email, allow_calendar,
                       allow_voice, allow_proactive,
                       allow_personality_learning, created_at, updated_at
                FROM account_features
                WHERE account_user_id = $1
                """,
                int(account_user_id),
            )

            current = self._default_account_features()
            created_at = now
            if row:
                current.update(
                    {
                        "allow_web_search": bool(row[1]),
                        "allow_email": bool(row[2]),
                        "allow_calendar": bool(row[3]),
                        "allow_voice": bool(row[4]),
                        "allow_proactive": bool(row[5]),
                        "allow_personality_learning": bool(row[6]),
                    }
                )
                created_at = row[7]
            current.update({key: bool(value) for key, value in feature_flags.items()})

            await conn.execute(
                """
                INSERT INTO account_features
                (account_user_id, allow_web_search, allow_email, allow_calendar,
                 allow_voice, allow_proactive,
                 allow_personality_learning, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (account_user_id) DO UPDATE SET
                    allow_web_search = EXCLUDED.allow_web_search,
                    allow_email = EXCLUDED.allow_email,
                    allow_calendar = EXCLUDED.allow_calendar,
                    allow_voice = EXCLUDED.allow_voice,
                    allow_proactive = EXCLUDED.allow_proactive,
                    allow_personality_learning = EXCLUDED.allow_personality_learning,
                    updated_at = EXCLUDED.updated_at
                """,
                int(account_user_id),
                current["allow_web_search"],
                current["allow_email"],
                current["allow_calendar"],
                current["allow_voice"],
                current["allow_proactive"],
                current["allow_personality_learning"],
                created_at,
                now,
            )
            return int(account_user_id)
        finally:
            await conn.close()

    async def get_account_features(self, account_user_id: int) -> Dict[str, bool]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT allow_web_search, allow_email, allow_calendar,
                       allow_voice, allow_proactive,
                       allow_personality_learning
                FROM account_features
                WHERE account_user_id = $1
                """,
                int(account_user_id),
            )
            if not row:
                return self._default_account_features()
            return {
                "allow_web_search": bool(row[0]),
                "allow_email": bool(row[1]),
                "allow_calendar": bool(row[2]),
                "allow_voice": bool(row[3]),
                "allow_proactive": bool(row[4]),
                "allow_personality_learning": bool(row[5]),
            }
        finally:
            await conn.close()

    async def store_oauth_connection(
        self,
        user_id: int,
        provider: str,
        provider_user_id: Optional[str] = None,
        provider_email: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        token_expires_at: Optional[float] = None,
        scopes: Optional[List[str]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            scopes_json = self._dump_json(scopes) if scopes else None
            connection_id = await conn.fetchval(
                """
                INSERT INTO oauth_connections
                (user_id, provider, provider_user_id, provider_email,
                 access_token_encrypted, refresh_token_encrypted, token_expires_at,
                 scopes, connection_status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'active', $9, $10)
                ON CONFLICT (user_id, provider) DO UPDATE SET
                    provider_user_id = EXCLUDED.provider_user_id,
                    provider_email = EXCLUDED.provider_email,
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                    token_expires_at = EXCLUDED.token_expires_at,
                    scopes = EXCLUDED.scopes,
                    connection_status = 'active',
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                int(user_id),
                provider,
                provider_user_id,
                provider_email,
                access_token,
                refresh_token,
                token_expires_at,
                scopes_json,
                now,
                now,
            )
            return int(connection_id)
        finally:
            await conn.close()

    async def get_oauth_connections(self, user_id: int) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, user_id, provider, provider_user_id, provider_email,
                       access_token_encrypted, refresh_token_encrypted, token_expires_at,
                       scopes, connection_status, last_sync_at, error_message,
                       created_at, updated_at
                FROM oauth_connections
                WHERE user_id = $1
                """,
                int(user_id),
            )
            return [self._oauth_connection_record(row) for row in rows]
        finally:
            await conn.close()

    async def update_onboarding_progress(
        self,
        user_id: int,
        current_step: str,
        completed_steps: List[str],
    ) -> Dict[str, Any]:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            platform_completed = "platform_connections" in completed_steps
            oauth_completed = "oauth_connections" in completed_steps
            profile_completed = "profile_setup" in completed_steps

            await conn.execute(
                """
                INSERT INTO onboarding_progress
                (user_id, current_step, completed_steps,
                 platform_connections_completed, oauth_connections_completed,
                 profile_setup_completed, created_at, updated_at)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
                ON CONFLICT (user_id) DO UPDATE SET
                    current_step = EXCLUDED.current_step,
                    completed_steps = EXCLUDED.completed_steps,
                    platform_connections_completed = EXCLUDED.platform_connections_completed,
                    oauth_connections_completed = EXCLUDED.oauth_connections_completed,
                    profile_setup_completed = EXCLUDED.profile_setup_completed,
                    updated_at = EXCLUDED.updated_at
                """,
                int(user_id),
                current_step,
                self._dump_json(completed_steps),
                platform_completed,
                oauth_completed,
                profile_completed,
                now,
                now,
            )

            return {
                "user_id": user_id,
                "current_step": current_step,
                "completed_steps": completed_steps,
                "platform_connections_completed": platform_completed,
                "oauth_connections_completed": oauth_completed,
                "profile_setup_completed": profile_completed,
            }
        finally:
            await conn.close()

    async def get_onboarding_progress(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, current_step, completed_steps,
                       platform_connections_completed, oauth_connections_completed,
                       profile_setup_completed, completed_at, created_at, updated_at
                FROM onboarding_progress
                WHERE user_id = $1
                """,
                int(user_id),
            )
            return self._onboarding_progress_record(row) if row else None
        finally:
            await conn.close()
