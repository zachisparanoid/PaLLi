from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class PassiveListeningStore:
    """Persistence helper for passive-listening interjection analytics."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    @staticmethod
    def _dump_json(value: Any) -> str:
        return json.dumps(value)

    async def store_passive_listening_event(
        self,
        *,
        channel_id: int,
        utterance_count: int,
        check_reason: str,
        decision: Dict[str, Any],
        did_respond: bool = False,
        passive_context: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            should_interject = decision.get("should_interject", False)
            confidence = decision.get("confidence", 0.0)
            reasoning = decision.get("reasoning", "")
            urgency = decision.get("urgency", "none")
            suggested_approach = decision.get("suggested_approach", "none")

            event_id = await conn.fetchval(
                """
                INSERT INTO passive_listening_events (
                    channel_id, timestamp, utterance_count, check_reason,
                    should_interject, confidence, reasoning, urgency,
                    suggested_approach, did_respond, passive_context, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb)
                RETURNING id
                """,
                channel_id,
                self._now(),
                utterance_count,
                check_reason,
                should_interject,
                confidence,
                reasoning,
                urgency,
                suggested_approach,
                did_respond,
                self._dump_json(passive_context) if passive_context else None,
                self._dump_json(metadata) if metadata else None,
            )
            return int(event_id)
        finally:
            await conn.close()

    async def get_passive_listening_stats(
        self,
        *,
        channel_id: Optional[int] = None,
        days: int = 7,
    ) -> Dict[str, Any]:
        conn = await self._brain.get_connection()
        try:
            cutoff_time = self._now() - (days * 24 * 60 * 60)
            query = """
                SELECT
                    COUNT(*) as total_checks,
                    SUM(CASE WHEN should_interject THEN 1 ELSE 0 END) as recommended_interjections,
                    SUM(CASE WHEN did_respond THEN 1 ELSE 0 END) as actual_responses,
                    AVG(confidence) as avg_confidence,
                    AVG(utterance_count) as avg_utterance_count,
                    check_reason,
                    urgency,
                    suggested_approach
                FROM passive_listening_events
                WHERE timestamp >= $1
            """
            params: List[Any] = [cutoff_time]

            if channel_id:
                params.append(channel_id)
                query += f" AND channel_id = ${len(params)}"

            query += " GROUP BY check_reason, urgency, suggested_approach"
            rows = await conn.fetch(query, *params)

            stats: Dict[str, Any] = {
                "period_days": days,
                "total_checks": 0,
                "recommended_interjections": 0,
                "actual_responses": 0,
                "avg_confidence": 0.0,
                "avg_utterance_count": 0.0,
                "by_check_reason": {},
                "by_urgency": {},
                "by_approach": {},
            }

            for row in rows:
                stats["total_checks"] += row[0]
                stats["recommended_interjections"] += row[1]
                stats["actual_responses"] += row[2]

                check_reason = row[5]
                urgency = row[6]
                approach = row[7]

                if check_reason not in stats["by_check_reason"]:
                    stats["by_check_reason"][check_reason] = {"count": 0, "interjections": 0}
                stats["by_check_reason"][check_reason]["count"] += row[0]
                stats["by_check_reason"][check_reason]["interjections"] += row[1]

                if urgency not in stats["by_urgency"]:
                    stats["by_urgency"][urgency] = 0
                stats["by_urgency"][urgency] += row[1]

                if approach not in stats["by_approach"]:
                    stats["by_approach"][approach] = 0
                stats["by_approach"][approach] += row[1]

            if rows:
                total_confidence = sum(row[3] * row[0] for row in rows)
                total_utterances = sum(row[4] * row[0] for row in rows)
                if stats["total_checks"] > 0:
                    stats["avg_confidence"] = total_confidence / stats["total_checks"]
                    stats["avg_utterance_count"] = total_utterances / stats["total_checks"]

            return stats
        finally:
            await conn.close()
