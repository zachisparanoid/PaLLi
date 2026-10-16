from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


class AccountSecurityStore:
    """Persistence helper for website account-security tokens and email verification state."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    @staticmethod
    def _token_record(row: Any, *, token_field: str) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "user_id": int(row[1]),
            token_field: row[2],
            "expires_at": row[3],
            "used": bool(row[4]),
            "created_at": row[5],
        }

    async def _create_token(
        self,
        *,
        table: str,
        token_column: str,
        user_id: int,
        token_hash: str,
        expires_at: float,
        invalidate_existing: bool = False,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            if invalidate_existing:
                try:
                    await conn.execute(
                        f"UPDATE {table} SET used = TRUE WHERE user_id = $1 AND used = FALSE",
                        int(user_id),
                    )
                except Exception:
                    pass

            token_id = await conn.fetchval(
                f"""
                INSERT INTO {table} (user_id, {token_column}, expires_at, used, created_at)
                VALUES ($1, $2, $3, FALSE, $4)
                RETURNING id
                """,
                int(user_id),
                token_hash,
                float(expires_at),
                now,
            )
            return int(token_id)
        finally:
            await conn.close()

    async def _get_token(
        self,
        *,
        table: str,
        token_column: str,
        token_field: str,
        token_hash: str,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT id, user_id, {token_column}, expires_at, used, created_at
                FROM {table}
                WHERE {token_column} = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                token_hash,
            )
            return self._token_record(row, token_field=token_field) if row else None
        finally:
            await conn.close()

    async def _mark_token_used(
        self,
        *,
        table: str,
        token_column: str,
        token_hash: str,
    ) -> bool:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                SET used = TRUE
                WHERE {token_column} = $1 AND used = FALSE
                RETURNING id
                """,
                token_hash,
            )
            return row is not None
        finally:
            await conn.close()

    async def create_password_reset_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._create_token(
            table="password_reset_tokens",
            token_column="token",
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    async def get_password_reset_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._get_token(
            table="password_reset_tokens",
            token_column="token",
            token_field="token",
            token_hash=token_hash,
        )

    async def mark_password_reset_token_used(self, token_hash: str) -> bool:
        return await self._mark_token_used(
            table="password_reset_tokens",
            token_column="token",
            token_hash=token_hash,
        )

    async def create_email_verification_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._create_token(
            table="email_verification_tokens",
            token_column="token_hash",
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            invalidate_existing=True,
        )

    async def get_email_verification_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._get_token(
            table="email_verification_tokens",
            token_column="token_hash",
            token_field="token_hash",
            token_hash=token_hash,
        )

    async def mark_email_verification_token_used(self, token_hash: str) -> bool:
        return await self._mark_token_used(
            table="email_verification_tokens",
            token_column="token_hash",
            token_hash=token_hash,
        )

    async def mark_user_email_verified(self, user_id: int) -> bool:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            row = await conn.fetchrow(
                """
                UPDATE users
                SET email_verified = TRUE,
                    email_verified_at = COALESCE(email_verified_at, $1),
                    updated_at = $1
                WHERE id = $2
                RETURNING id
                """,
                now,
                int(user_id),
            )
            return row is not None
        finally:
            await conn.close()

    async def create_account_deletion_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._create_token(
            table="account_deletion_tokens",
            token_column="token_hash",
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            invalidate_existing=True,
        )

    async def get_account_deletion_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._get_token(
            table="account_deletion_tokens",
            token_column="token_hash",
            token_field="token_hash",
            token_hash=token_hash,
        )

    async def mark_account_deletion_token_used(self, token_hash: str) -> bool:
        return await self._mark_token_used(
            table="account_deletion_tokens",
            token_column="token_hash",
            token_hash=token_hash,
        )
