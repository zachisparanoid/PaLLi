from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MediaHistoryStore:
    """Persistence helper for image/media history used by chat and vision flows."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _image_record(self, row: Any, *, include_image_data: bool) -> Dict[str, Any]:
        record = {
            "id": row[0],
            "user_id": row[1],
            "platform": row[2],
            "channel_id": row[3],
            "surface_id": str(row[4] or row[3] or ""),
            "message_id": row[5],
            "content_type": row[7],
            "original_filename": row[8],
            "description": row[9],
            "conversation_context": row[10],
            "timestamp": row[11],
        }
        if include_image_data:
            record["image_data"] = row[6]
        if row[12]:
            record["metadata"] = self._load_json(row[12], {})
        return record

    async def store_image(
        self,
        *,
        user_id: int,
        platform: str,
        channel_id: int,
        image_data: bytes,
        content_type: str,
        description: str = None,
        original_filename: str = None,
        conversation_context: str = None,
        message_id: int = None,
        metadata: Dict = None,
        surface_id: Optional[str] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            image_id = await conn.fetchval(
                """
                INSERT INTO images
                (user_id, platform, channel_id, surface_id, message_id, image_data, content_type,
                 original_filename, description, conversation_context, timestamp, metadata)
                VALUES ($1, $2, $3, NULLIF($4, ''), $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                RETURNING id
                """,
                user_id,
                platform,
                channel_id,
                str(surface_id or "").strip() or None,
                message_id,
                image_data,
                content_type,
                original_filename,
                description,
                conversation_context,
                self._now(),
                self._dump_json(metadata) if metadata else None,
            )
            logger.debug("Stored image %s for user %s", image_id, user_id)
            return int(image_id)
        finally:
            await conn.close()

    async def get_recent_images(
        self,
        *,
        user_id: int = None,
        channel_id: int = None,
        surface_id: str = None,
        limit: int = 10,
        include_image_data: bool = False,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            query = "SELECT * FROM images WHERE 1=1"
            params: List[Any] = []

            if user_id:
                params.append(user_id)
                query += f" AND user_id = ${len(params)}"
            if channel_id:
                params.append(channel_id)
                query += f" AND channel_id = ${len(params)}"
            if surface_id:
                params.append(str(surface_id))
                query += f" AND surface_id = ${len(params)}"

            params.append(limit)
            query += f" ORDER BY timestamp DESC LIMIT ${len(params)}"

            rows = await conn.fetch(query, *params)
            images = [
                self._image_record(row, include_image_data=include_image_data)
                for row in rows
            ]
            images.reverse()
            return images
        finally:
            await conn.close()

    async def get_image_by_id(
        self,
        image_id: int,
        *,
        include_image_data: bool = True,
    ) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM images WHERE id = $1",
                image_id,
            )
            if not row:
                return None
            return self._image_record(row, include_image_data=include_image_data)
        finally:
            await conn.close()
