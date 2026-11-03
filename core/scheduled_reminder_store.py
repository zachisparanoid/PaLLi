from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _parse_update_rowcount(status: Any) -> int:
    """``conn.execute`` returns e.g. ``"UPDATE 1"`` for a write; parse the
    trailing count. Batch 4 task 4 (2026-09-28): ownership checks need a
    real rowcount so 0 rows changed can mean "not yours". Unit-test fakes
    return a bare ``"OK"`` with no count — that has no rows to report, so
    it reads as 0 rather than raising."""
    try:
        return int(str(status).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


class ScheduledReminderStore:
    """Persistence helper for scheduled reminder lookup and mutation state."""

    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _reminder_record(self, row: Any) -> Dict[str, Any]:
        channel_id = int(row[3]) if row[3] is not None else 0
        return {
            "id": int(row[0]),
            "user_id": int(row[1]),
            "platform": row[2],
            "channel_id": channel_id,
            "surface_id": str(row[4] or channel_id or ""),
            "reminder_text": row[5] or "",
            "scheduled_time": float(row[6] or 0.0),
            "created_time": float(row[7] or 0.0),
            "is_completed": bool(row[8]),
            "kind": row[9] or "reminder",
            "runtime_job_id": row[10] or "",
            "runtime_session_key": row[11] or "",
            "metadata": self._load_json(row[12], {}),
        }

    async def get_scheduled_reminder_record(self, reminder_id: int) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, platform, channel_id, surface_id, reminder_text, scheduled_time,
                       created_time, is_completed, kind, runtime_job_id, runtime_session_key, metadata
                FROM scheduled_reminders
                WHERE id = $1
                LIMIT 1
                """,
                int(reminder_id),
            )
            return self._reminder_record(row) if row else None
        finally:
            await conn.close()

    async def list_user_scheduled_reminders(
        self,
        *,
        user_id: int,
        platform: str,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, user_id, platform, channel_id, surface_id, reminder_text, scheduled_time,
                       created_time, is_completed, kind, runtime_job_id, runtime_session_key, metadata
                FROM scheduled_reminders
                WHERE user_id = $1 AND platform = $2 AND is_completed = FALSE
                ORDER BY scheduled_time ASC
                """,
                int(user_id),
                str(platform),
            )
            return [self._reminder_record(row) for row in rows]
        finally:
            await conn.close()

    async def list_open_scheduled_reminders(self, *, limit: int = 128) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT id, user_id, platform, channel_id, surface_id, reminder_text, scheduled_time,
                       created_time, is_completed, kind, runtime_job_id, runtime_session_key, metadata
                FROM scheduled_reminders
                WHERE is_completed = FALSE
                ORDER BY scheduled_time ASC
                LIMIT $1
                """,
                max(int(limit or 128), 1),
            )
            return [self._reminder_record(row) for row in rows]
        finally:
            await conn.close()

    async def list_due_scheduled_reminders(
        self,
        *,
        as_of: float,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            if limit is None:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, platform, channel_id, surface_id, reminder_text, scheduled_time,
                           created_time, is_completed, kind, runtime_job_id, runtime_session_key, metadata
                    FROM scheduled_reminders
                    WHERE scheduled_time <= $1 AND is_completed = FALSE
                    ORDER BY scheduled_time ASC
                    """,
                    float(as_of),
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, platform, channel_id, surface_id, reminder_text, scheduled_time,
                           created_time, is_completed, kind, runtime_job_id, runtime_session_key, metadata
                    FROM scheduled_reminders
                    WHERE scheduled_time <= $1 AND is_completed = FALSE
                    ORDER BY scheduled_time ASC
                    LIMIT $2
                    """,
                    float(as_of),
                    max(int(limit or 128), 1),
                )
            return [self._reminder_record(row) for row in rows]
        finally:
            await conn.close()

    async def mark_scheduled_reminder_completed(
        self, *, reminder_id: int, user_id: Optional[int] = None,
    ) -> int:
        """Mark a row completed; returns the number of rows changed.

        Batch 4 task 4 (2026-09-28, ruling R8): ``user_id`` is OPTIONAL so
        the due loop, snooze and dead-letter paths (none of which know an
        owner) keep calling this with just ``reminder_id``. When it IS
        given, the row must also match it — that is what lets
        cancel_reminder refuse someone else's row instead of completing it."""
        conn = await self._brain.get_connection()
        try:
            if user_id is None:
                status = await conn.execute(
                    """
                    UPDATE scheduled_reminders
                    SET is_completed = TRUE
                    WHERE id = $1
                    """,
                    int(reminder_id),
                )
            else:
                status = await conn.execute(
                    """
                    UPDATE scheduled_reminders
                    SET is_completed = TRUE
                    WHERE id = $1 AND user_id = $2
                    """,
                    int(reminder_id),
                    int(user_id),
                )
            return _parse_update_rowcount(status)
        finally:
            await conn.close()

    async def merge_scheduled_reminder_metadata(
        self,
        *,
        reminder_id: int,
        patch: Dict[str, Any],
    ) -> None:
        """Merge ``patch`` into the row's metadata JSON (its keys win).

        Batch 4 task 2 (2026-09-28): completion alone cannot say HOW a row was
        retired, so the late-fire digest stamps ``late_fired`` and a digest
        that never landed stamps ``dead_lettered``. A missing row is a no-op."""
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT metadata
                FROM scheduled_reminders
                WHERE id = $1
                LIMIT 1
                """,
                int(reminder_id),
            )
            if row is None:
                return
            current = self._load_json(row[0], {})
            if isinstance(current, dict):
                merged = dict(current)
            elif isinstance(current, list):
                # The shape the binding's normalize_reminder_metadata reads a
                # list as, so nothing already stored is dropped.
                merged = {"items": list(current)}
            else:
                merged = {}
            merged.update(dict(patch or {}))
            await conn.execute(
                """
                UPDATE scheduled_reminders
                SET metadata = $1::jsonb
                WHERE id = $2
                """,
                json.dumps(merged),
                int(reminder_id),
            )
        finally:
            await conn.close()

    async def update_scheduled_reminder_schedule(
        self,
        *,
        reminder_id: int,
        scheduled_time: float,
        user_id: Optional[int] = None,
    ) -> int:
        """Move a row's due time; returns the number of rows changed.

        Same ruling as ``mark_scheduled_reminder_completed`` above:
        ``user_id`` is OPTIONAL so binding.py's snooze path keeps calling
        this with no owner in hand, and reschedule_reminder passes one to
        refuse moving someone else's row."""
        conn = await self._brain.get_connection()
        try:
            if user_id is None:
                status = await conn.execute(
                    """
                    UPDATE scheduled_reminders
                    SET scheduled_time = $1
                    WHERE id = $2
                    """,
                    float(scheduled_time),
                    int(reminder_id),
                )
            else:
                status = await conn.execute(
                    """
                    UPDATE scheduled_reminders
                    SET scheduled_time = $1
                    WHERE id = $2 AND user_id = $3
                    """,
                    float(scheduled_time),
                    int(reminder_id),
                    int(user_id),
                )
            return _parse_update_rowcount(status)
        finally:
            await conn.close()

    async def update_scheduled_reminder_runtime_link(
        self,
        *,
        reminder_id: int,
        runtime_job_id: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                UPDATE scheduled_reminders
                SET runtime_job_id = COALESCE($2, runtime_job_id),
                    runtime_session_key = COALESCE($3, runtime_session_key)
                WHERE id = $1
                """,
                int(reminder_id),
                str(runtime_job_id or "").strip() or None,
                str(runtime_session_key or "").strip() or None,
            )
        finally:
            await conn.close()
