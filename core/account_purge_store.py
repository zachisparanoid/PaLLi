from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

# Great Excision stage 7 (2026-07-10): sessions, password_reset_tokens,
# email_verification_tokens, account_deletion_tokens, beta_seat_reservations,
# learning_analytics, reflection_runs, and email_events were DROPPED with
# their (already deleted) writers — entries removed so the purge only names
# tables that exist.
_ACCOUNT_PURGE_TABLES = (
    "user_security",
    "audit_logs",
    "platform_connections",
    "oauth_connections",
    "onboarding_progress",
    "pairing_codes",
    "user_profiles",
    "scheduled_reminders",
    # C1.5: proactive_actions absorbed into open_threads with
    # source_kind='proactive_record'. The purge of those rows lives in
    # _purge_owned_account_rows below (special-cased on owner_user_id +
    # source_kind, since open_threads also stores rows for other
    # source_kinds we don't own here).
    "user_memory",
    "voice_sessions",
    "images",
    "web_search_events",
)


class AccountPurgeStore:
    """Persistence helper for owner data purge and account scrubbing flows."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    @staticmethod
    def _rowcount(status: str) -> int:
        if not status:
            return 0
        parts = status.split()
        if not parts:
            return 0
        last = parts[-1]
        return int(last) if last.isdigit() else 0

    @staticmethod
    def _placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"${i}" for i in range(start, start + count))

    @staticmethod
    def _pair_placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"(${i}, ${i + 1})" for i in range(start, start + count * 2, 2))

    @staticmethod
    def _owned_user_ids(user_id: int, *, palli_user_id: Any, discord_user_id: Any) -> List[int]:
        ids = {int(user_id)}
        if isinstance(palli_user_id, int):
            ids.add(int(palli_user_id))
        if discord_user_id:
            try:
                ids.add(int(discord_user_id))
            except (TypeError, ValueError):
                pass
        return sorted(ids)

    async def purge_conversations_for_user_ids(self, conn: Any, user_ids: List[int]) -> int:
        """
        Purge conversation data for the given user IDs.

        This is more robust than a simple `DELETE ... WHERE user_id IN (...)`
        because older assistant messages were stored with `user_id = NULL`. We:
        - fully delete user-exclusive threads (only these user_ids + assistant)
        - delete remaining user-owned rows in shared threads (user_id IN user_ids)
        - delete legacy proactive assistant rows tagged with metadata.target_user_id
        """
        unique_ids = sorted({uid for uid in user_ids if isinstance(uid, int)})
        if not unique_ids:
            return 0

        deleted = 0
        id_placeholders = self._placeholders(len(unique_ids))
        safe_query = f"""
            SELECT platform, channel_id
            FROM conversations
            WHERE (platform, channel_id) IN (
                SELECT DISTINCT platform, channel_id
                FROM conversations
                WHERE user_id IN ({id_placeholders})
            )
            GROUP BY platform, channel_id
            HAVING SUM(
                CASE
                    WHEN user_id IS NOT NULL AND user_id NOT IN ({id_placeholders}) THEN 1
                    ELSE 0
                END
            ) = 0
        """
        rows = await conn.fetch(safe_query, *unique_ids, *unique_ids)
        safe_pairs = [(row[0], row[1]) for row in rows]

        if safe_pairs:
            pair_placeholders = self._pair_placeholders(len(safe_pairs))
            pair_params: List[Any] = []
            for platform, channel_id in safe_pairs:
                pair_params.extend([platform, channel_id])

            status = await conn.execute(
                f"""
                DELETE FROM conversations
                WHERE (platform, channel_id) IN ({pair_placeholders})
                """,
                *pair_params,
            )
            deleted += self._rowcount(status)

        status = await conn.execute(
            f"DELETE FROM conversations WHERE user_id IN ({id_placeholders})",
            *unique_ids,
        )
        deleted += self._rowcount(status)

        status = await conn.execute(
            f"""
            DELETE FROM conversations
            WHERE user_id IS NULL
              AND metadata IS NOT NULL
              AND CAST(json_extract(metadata, '$.target_user_id') AS INTEGER) IN ({id_placeholders})
            """,
            *unique_ids,
        )
        deleted += self._rowcount(status)

        return deleted

    async def purge_user_data(self, user_ids: List[int], tables: List[str]) -> Dict[str, int]:
        """
        Delete user-owned data across selected tables.

        Account-auth rows and compatibility-only legacy hosted-access rows are
        intentionally preserved.
        """
        unique_ids = sorted({uid for uid in user_ids if isinstance(uid, int)})
        unique_tables = [table for table in tables if table]
        if not unique_ids or not unique_tables:
            return {}

        conn = await self._brain.get_connection()
        counts: Dict[str, int] = {}
        try:
            id_placeholders = self._placeholders(len(unique_ids))
            for table in unique_tables:
                if table == "conversations":
                    counts[table] = await self.purge_conversations_for_user_ids(conn, unique_ids)
                    continue

                # C1.5: proactive_actions absorbed into open_threads with
                # source_kind='proactive_record'. Caller-facing key stays
                # 'proactive_actions' for API stability.
                if table == "proactive_actions":
                    status = await conn.execute(
                        f"""
                        DELETE FROM open_threads
                        WHERE owner_user_id IN ({id_placeholders})
                          AND source_kind = 'proactive_record'
                        """,
                        *unique_ids,
                    )
                    counts[table] = self._rowcount(status)
                    continue

                status = await conn.execute(
                    f"DELETE FROM {table} WHERE user_id IN ({id_placeholders})",
                    *unique_ids,
                )
                counts[table] = self._rowcount(status)
            return counts
        finally:
            await conn.close()

    async def _load_soft_delete_subject(self, conn: Any, *, user_id: int) -> Optional[Dict[str, Any]]:
        row = await conn.fetchrow(
            """
            SELECT id, email, palli_user_id, discord_user_id
            FROM users
            WHERE id = $1
            FOR UPDATE
            """,
            int(user_id),
        )
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "email": row["email"],
            "palli_user_id": row["palli_user_id"],
            "discord_user_id": row["discord_user_id"],
        }

    async def _purge_owned_account_rows(
        self,
        conn: Any,
        *,
        account_user_id: int,
        owned_user_ids: List[int],
    ) -> Dict[str, int]:
        deleted: Dict[str, int] = {}
        deleted["conversations"] = await self.purge_conversations_for_user_ids(conn, owned_user_ids)

        id_placeholders = self._placeholders(len(owned_user_ids))
        for table in _ACCOUNT_PURGE_TABLES:
            status = await conn.execute(
                f"DELETE FROM {table} WHERE user_id IN ({id_placeholders})",
                *owned_user_ids,
            )
            deleted[table] = self._rowcount(status)

        # C1.5: proactive_actions rows now live in open_threads with
        # source_kind='proactive_record'. Purge them by owner_user_id +
        # source_kind so we don't sweep other source_kinds (RECURRING /
        # FOLLOWTHROUGH) that happen to be owned by the same user. The
        # legacy "proactive_actions" key is preserved in the deleted
        # dict so any caller that introspects it still sees a count
        # (now from the unified table).
        status = await conn.execute(
            f"""
            DELETE FROM open_threads
            WHERE owner_user_id IN ({id_placeholders})
              AND source_kind = 'proactive_record'
            """,
            *owned_user_ids,
        )
        deleted["proactive_actions"] = self._rowcount(status)

        status = await conn.execute(
            "DELETE FROM discord_guild_accounts WHERE account_user_id = $1",
            int(account_user_id),
        )
        deleted["discord_guild_accounts"] = self._rowcount(status)
        status = await conn.execute(
            "DELETE FROM account_features WHERE account_user_id = $1",
            int(account_user_id),
        )
        deleted["account_features"] = self._rowcount(status)
        return deleted

    async def _scrub_soft_deleted_user(
        self,
        conn: Any,
        *,
        user_id: int,
        scrubbed_email: str,
        updated_at: float,
    ) -> None:
        await conn.execute(
            """
            UPDATE users
            SET email = $1,
                password_hash = NULL,
                google_sub = NULL,
                has_access = FALSE,
                stripe_customer_id = NULL,
                stripe_checkout_session_id = NULL,
                stripe_payment_intent_id = NULL,
                discord_user_id = NULL,
                telegram_username = NULL,
                email_verified = FALSE,
                email_verified_at = NULL,
                age_confirmed = FALSE,
                age_confirmed_at = NULL,
                tos_accepted_at = NULL,
                privacy_accepted_at = NULL,
                tos_version = NULL,
                privacy_version = NULL,
                consent_ip = NULL,
                consent_user_agent = NULL,
                phone_number = NULL,
                mfa_secret = NULL,
                mfa_enabled = FALSE,
                updated_at = $2
            WHERE id = $3
            """,
            scrubbed_email,
            updated_at,
            int(user_id),
        )

    async def soft_delete_account(self, user_id: int) -> Dict[str, Any]:
        conn = await self._brain.get_connection()
        txn = conn.transaction()
        try:
            clean_user_id = int(user_id)
            await txn.start()
            now = self._now()

            subject = await self._load_soft_delete_subject(conn, user_id=clean_user_id)
            if not subject:
                await txn.rollback()
                return {"ok": False, "reason": "not_found"}

            owned_user_ids = self._owned_user_ids(
                clean_user_id,
                palli_user_id=subject["palli_user_id"],
                discord_user_id=subject["discord_user_id"],
            )
            deleted = await self._purge_owned_account_rows(
                conn,
                account_user_id=clean_user_id,
                owned_user_ids=owned_user_ids,
            )

            scrubbed_email = f"deleted+{clean_user_id}@trypalli.com"
            await self._scrub_soft_deleted_user(
                conn,
                user_id=clean_user_id,
                scrubbed_email=scrubbed_email,
                updated_at=now,
            )

            await txn.commit()
            return {
                "ok": True,
                "old_email": subject["email"],
                "new_email": scrubbed_email,
                "deleted": deleted,
            }
        except Exception:
            try:
                await txn.rollback()
            except Exception:
                pass
            raise
        finally:
            await conn.close()
