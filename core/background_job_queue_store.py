from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class BackgroundJobQueueStore:
    """Persistence helper for queued notification and delivery jobs."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _job_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": int(row[0]),
            "type": row[1],
            "payload": self._load_json(row[2], {}),
        }

    async def queue_job(self, *, job_type: str, payload: Dict[str, Any]) -> int:
        conn = await self._brain.get_connection()
        try:
            job_id = await conn.fetchval(
                """
                INSERT INTO job_queue (job_type, payload, created_at)
                VALUES ($1, $2::jsonb, $3)
                RETURNING id
                """,
                job_type,
                json.dumps(payload),
                self._now(),
            )
            return int(job_id)
        finally:
            await conn.close()

    async def pop_job(self, *, job_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = "SELECT id, job_type, payload FROM job_queue WHERE status = 'pending'"
            params: List[Any] = []
            if job_type:
                params.append(job_type)
                query += f" AND job_type = ${len(params)}"
            query += " ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"

            async with conn.transaction():
                row = await conn.fetchrow(query, *params)
                if not row:
                    return None

                job_id = int(row[0])
                await conn.execute(
                    "UPDATE job_queue SET status = 'processing', processed_at = $1 WHERE id = $2",
                    self._now(),
                    job_id,
                )
                return self._job_record(row)
        finally:
            await conn.close()
