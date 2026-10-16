from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalyticsAuditStore:
    """Persistence helper for owner-visible analytics and account audit events."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    @staticmethod
    def _dump_json(value: Any) -> str:
        return json.dumps(value)

    @staticmethod
    def _audit_log_record(row: Any) -> Dict[str, Any]:
        return {
            "action": row[0],
            "ip_address": row[1],
            "user_agent": row[2],
            "details": row[3],
            "timestamp": row[4],
        }

    async def store_web_search(
        self,
        *,
        user_id: Optional[int],
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            search_id = await conn.fetchval(
                """
                INSERT INTO web_search_events (user_id, query, created_at, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id
                """,
                user_id,
                query,
                self._now(),
                self._dump_json(metadata) if metadata else None,
            )
            return int(search_id)
        finally:
            await conn.close()

    async def log_audit_event(
        self,
        *,
        user_id: int,
        action: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[str] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            audit_id = await conn.fetchval(
                """
                INSERT INTO audit_logs (user_id, action, ip_address, user_agent, details, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                user_id,
                action,
                ip_address,
                user_agent,
                details,
                self._now(),
            )
            return int(audit_id)
        except Exception as exc:
            logger.error("Failed to log audit event: %s", exc)
            return -1
        finally:
            await conn.close()

    async def get_audit_logs(self, *, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT action, ip_address, user_agent, details, timestamp
                FROM audit_logs
                WHERE user_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            return [self._audit_log_record(row) for row in rows]
        finally:
            await conn.close()
