"""The mobile door's timeline and the files its events point at (spec §4).

``mobile_events`` is the door's ordered record in BOTH directions: what the
owner sent (``user_message``, written once its content is resolved) and
everything she said there (``message`` per beat, ``voice_note``, ``file``),
plus plain ``notice`` lines that are not hers. An append IS the delivery —
the hub's push is the fast path and replay covers the rest. The autoincrement
id is the app's cursor.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

EVENT_KINDS = ("user_message", "message", "voice_note", "file", "notice")


def to_wire(event_id: int, kind: str, created_at: float, payload: Dict[str, Any],
            client_id: Optional[str]) -> Dict[str, Any]:
    """One event as the app receives it: flat, with its id, kind and time."""
    wire: Dict[str, Any] = {"id": int(event_id), "kind": str(kind), "at": float(created_at)}
    wire.update(payload)
    if client_id is not None:
        wire["client_id"] = client_id
    return wire


def _from_row(row: Any) -> Dict[str, Any]:
    payload = json.loads(row["payload"]) if row["payload"] else {}
    return to_wire(row["id"], row["kind"], row["created_at"], payload, row["client_id"])


def _file_ref(row: Any) -> Dict[str, Any]:
    ref: Dict[str, Any] = {
        "id": str(row["id"]),
        "filename": str(row["filename"]),
        "content_type": str(row["content_type"]),
        "size": None if row["size"] is None else int(row["size"]),
    }
    if row["duration_s"] is not None:
        ref["duration_s"] = float(row["duration_s"])
    return ref


class MobileEventsStore:
    def __init__(self, db: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._db = db
        self._now = now or time.time

    async def append(self, *, owner_id: int, kind: str, payload: Dict[str, Any],
                     client_id: Optional[str] = None) -> Dict[str, Any]:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown mobile event kind: {kind!r}")
        now = self._now()
        conn = await self._db.get_connection()
        try:
            event_id = await conn.fetchval(
                "INSERT INTO mobile_events (owner_id, kind, client_id, payload, created_at) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                int(owner_id), kind, client_id, json.dumps(payload), now,
            )
        finally:
            await conn.close()
        return to_wire(event_id, kind, now, payload, client_id)

    async def has_client_id(self, client_id: str) -> bool:
        conn = await self._db.get_connection()
        try:
            found = await conn.fetchval(
                "SELECT 1 FROM mobile_events WHERE client_id = $1 LIMIT 1", str(client_id))
        finally:
            await conn.close()
        return found is not None

    async def latest_id(self, *, owner_id: int) -> int:
        """The newest event id on this owner's timeline; 0 when it is empty."""
        conn = await self._db.get_connection()
        try:
            newest = await conn.fetchval(
                "SELECT MAX(id) FROM mobile_events WHERE owner_id = $1", int(owner_id))
        finally:
            await conn.close()
        return int(newest or 0)

    async def after(self, *, owner_id: int, after: int, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
        conn = await self._db.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT id, kind, client_id, payload, created_at FROM mobile_events "
                "WHERE owner_id = $1 AND id > $2 ORDER BY id ASC LIMIT $3",
                int(owner_id), int(after), int(limit) + 1,
            )
        finally:
            await conn.close()
        return [_from_row(r) for r in rows[:limit]], len(rows) > limit

    async def before(self, *, owner_id: int, before: int, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
        conn = await self._db.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT id, kind, client_id, payload, created_at FROM mobile_events "
                "WHERE owner_id = $1 AND id < $2 ORDER BY id DESC LIMIT $3",
                int(owner_id), int(before), int(limit) + 1,
            )
        finally:
            await conn.close()
        return [_from_row(r) for r in rows[:limit]], len(rows) > limit

    async def register_file(self, *, owner_id: int, path: str, filename: str, content_type: str,
                            duration_s: Optional[float] = None) -> Dict[str, Any]:
        file_id = "f_" + secrets.token_urlsafe(12)
        try:
            size: Optional[int] = os.path.getsize(path)
        except OSError:
            size = None
        conn = await self._db.get_connection()
        try:
            await conn.execute(
                "INSERT INTO mobile_files (id, owner_id, path, filename, content_type, size, duration_s, "
                "created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                file_id, int(owner_id), str(path), str(filename), str(content_type), size,
                duration_s, self._now(),
            )
            row = await conn.fetchrow("SELECT * FROM mobile_files WHERE id = $1", file_id)
        finally:
            await conn.close()
        return _file_ref(row)

    async def file(self, *, owner_id: int, file_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._db.get_connection()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM mobile_files WHERE id = $1 AND owner_id = $2",
                str(file_id), int(owner_id),
            )
        finally:
            await conn.close()
        if row is None:
            return None
        found = _file_ref(row)
        found["path"] = str(row["path"])
        return found


def events_store() -> MobileEventsStore:
    # Resolved at call time, like core.mobile_auth.device_store.
    from core import database

    return MobileEventsStore(database.brain)
