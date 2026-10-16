from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

_APP_USER_UPDATE_FIELDS = {
    "email",
    "password_hash",
    "google_sub",
    "role",
    "palli_user_id",
    "has_access",
    "stripe_customer_id",
    "stripe_checkout_session_id",
    "stripe_payment_intent_id",
    "discord_user_id",
    "telegram_username",
    "telegram_user_id",
    "beta_joined_at",
    "phone_number",
    "mfa_secret",
    "mfa_enabled",
    "email_verified",
    "email_verified_at",
    "age_confirmed",
    "age_confirmed_at",
    "tos_accepted_at",
    "privacy_accepted_at",
    "tos_version",
    "privacy_version",
    "consent_ip",
    "consent_user_agent",
}


class AppUserStore:
    """Persistence helper for app-user auth/account records and lookups."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    @staticmethod
    def _app_user_record(row: Any) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "id": int(row[0]),
            "email": row[1],
            "password_hash": row[2],
            "google_sub": row[3],
            "role": row[4],
            "palli_user_id": row[5],
            "has_access": bool(row[6]),
            "stripe_customer_id": row[7],
            "stripe_checkout_session_id": row[8],
            "stripe_payment_intent_id": row[9],
            "discord_user_id": row[10],
            "telegram_username": row[11],
            "beta_joined_at": row[12],
            "email_verified": bool(row[13]),
            "email_verified_at": row[14],
            "age_confirmed": bool(row[15]),
            "age_confirmed_at": row[16],
            "tos_accepted_at": row[17],
            "privacy_accepted_at": row[18],
            "tos_version": row[19],
            "privacy_version": row[20],
            "consent_ip": row[21],
            "consent_user_agent": row[22],
            "phone_number": row[23],
            "mfa_secret": row[24],
            "mfa_enabled": bool(row[25]),
            "created_at": row[26],
            "updated_at": row[27],
        }
        # Telegram door (2026-09-14, task 1). This mapper is POSITIONAL, so
        # the new column is appended LAST in the SELECT above and read last
        # here — no existing index moves. Guarded because a positional mapper
        # handed a row narrower than its select list must degrade to "no
        # Telegram id" rather than IndexError on a column that is optional
        # by construction (NULL until the owner links a Telegram account).
        if len(row) > 28:
            record["telegram_user_id"] = row[28]
        return record

    async def create_app_user(
        self,
        email: str,
        password_hash: Optional[str] = None,
        google_sub: Optional[str] = None,
        role: str = "user",
        palli_user_id: Optional[int] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            user_id = await conn.fetchval(
                """
                INSERT INTO users (email, password_hash, google_sub, role, palli_user_id, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                email,
                password_hash,
                google_sub,
                role,
                palli_user_id,
                now,
                now,
            )
            try:
                await conn.execute(
                    """
                    INSERT INTO user_security (user_id, updated_at)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    int(user_id),
                    now,
                )
            except Exception:
                pass
            return int(user_id)
        finally:
            await conn.close()

    async def get_app_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, google_sub, role, palli_user_id,
                       has_access, stripe_customer_id, stripe_checkout_session_id,
                       stripe_payment_intent_id, discord_user_id, telegram_username,
                       beta_joined_at,
                       email_verified, email_verified_at,
                       age_confirmed, age_confirmed_at,
                       tos_accepted_at, privacy_accepted_at,
                       tos_version, privacy_version,
                       consent_ip, consent_user_agent,
                       phone_number, mfa_secret, mfa_enabled,
                       created_at, updated_at,
                       telegram_user_id
                FROM users
                WHERE id = $1
                """,
                int(user_id),
            )
            return self._app_user_record(row) if row else None
        finally:
            await conn.close()

    async def _get_app_user_by_lookup(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(query, *args)
            user_id = int(row[0]) if row else None
        finally:
            await conn.close()
        if user_id is None:
            return None
        return await self.get_app_user_by_id(user_id)

    async def get_app_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return await self._get_app_user_by_lookup(
            """
            SELECT id FROM users
            WHERE lower(email) = lower($1)
            """,
            email,
        )

    async def get_app_user_by_google_sub(self, google_sub: str) -> Optional[Dict[str, Any]]:
        return await self._get_app_user_by_lookup(
            """
            SELECT id FROM users
            WHERE google_sub = $1
            """,
            google_sub,
        )

    async def get_app_user_by_discord_id(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_app_user_by_lookup(
            """
            SELECT id FROM users
            WHERE discord_user_id = $1
            """,
            discord_user_id,
        )

    async def get_app_user_by_telegram_id(self, telegram_user_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_app_user_by_lookup(
            """
            SELECT id FROM users
            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
        )


    async def update_app_user(self, user_id: int, updates: Dict[str, Any]) -> bool:
        if not updates:
            return True

        set_parts: List[str] = []
        params: List[Any] = []
        for key, value in updates.items():
            if key not in _APP_USER_UPDATE_FIELDS:
                continue
            params.append(value)
            set_parts.append(f"{key} = ${len(params)}")

        if not set_parts:
            return True

        params.append(self._now())
        updated_at_idx = len(params)
        params.append(int(user_id))
        user_id_idx = len(params)

        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                f"""
                UPDATE users
                SET {", ".join(set_parts)}, updated_at = ${updated_at_idx}
                WHERE id = ${user_id_idx}
                """,
                *params,
            )
            return True
        finally:
            await conn.close()

    # Phase B / B4 step 2 (2026-05-09): redirect for the reflect
    # session-close hook. Audit finding IM-HIGH-2: previously stored as a
    # ``last_session_closed_at`` row in ``user_memory`` (a generic
    # decay-ranked store), polluting the recall budget. B4 step 1 added
    # the dedicated column ``users.last_session_closed_at``; this method
    # is the writer side of that move. ``update_app_user`` deliberately
    # excludes ``last_session_closed_at`` from its allowlist — this is a
    # mechanical bookkeeping field, not part of the auth/account record.
    async def set_user_last_session_closed_at(
        self,
        *,
        owner_user_id: int,
        timestamp: float,
    ) -> None:
        """Set ``users.last_session_closed_at`` for the given owner.

        Coerces inputs defensively (str/numeric → int/float) so callers
        can pass loosely-typed values without surprises. Stamps
        ``updated_at`` with the now-injection so the row reflects the
        write event in standard fashion.
        """
        owner_id_int = int(owner_user_id)
        ts_float = float(timestamp)
        now = self._now()

        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                UPDATE users
                SET last_session_closed_at = $1,
                    updated_at = $2
                WHERE id = $3
                """,
                ts_float,
                now,
                owner_id_int,
            )
        finally:
            await conn.close()

    # Phase B / B4 step 3 (2026-05-09): symmetric reader for the
    # session-close column. Audit finding IM-HIGH-2: with the writer
    # redirected in step 2, this getter is the dedicated entry point
    # for any future consumer that needs the timestamp without
    # touching the legacy ``user_memory`` decay-ranked store.
    #
    # Returns ``None`` when:
    #   * the user row does not exist, OR
    #   * the column is NULL (the user has never had a session close
    #     recorded yet, OR the operator hasn't yet run
    #     ``scripts/migrate_last_session_closed_at.py --apply`` to copy
    #     legacy rows over from ``user_memory``).
    #
    # Deliberately does NOT fall back to ``user_memory`` on None — the
    # whole point of step 2/3 is to retire that path. Callers that need
    # the historical legacy value during the cutover window should
    # either run the migration first, or accept None as "no recorded
    # session close" and use their own default.
    async def get_user_last_session_closed_at(
        self,
        *,
        owner_user_id: int,
    ) -> Optional[float]:
        """Return ``users.last_session_closed_at`` for the given owner.

        Coerces ``owner_user_id`` defensively (str/numeric → int) so
        callers can pass loosely-typed values without surprises, mirroring
        the setter convention. Normalizes the result to ``float`` when
        the underlying driver returns an int (some sqlite3 paths do this
        when the stored value is exactly representable as integer).
        """
        owner_id_int = int(owner_user_id)

        conn = await self._brain.get_connection()
        try:
            value = await conn.fetchval(
                """
                SELECT last_session_closed_at
                FROM users
                WHERE id = $1
                """,
                owner_id_int,
            )
        finally:
            await conn.close()
        if value is None:
            return None
        return float(value)
