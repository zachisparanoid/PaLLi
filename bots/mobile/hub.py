"""Open phones on the mobile door (spec §5).

Every open socket gets every event for its owner, through a queue the
websocket route drains. The timeline (core/mobile_events_store.py) is the
record; the hub is only the fast path, so a phone that was not open misses
nothing — it replays from its cursor when it connects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

CLOSE_REVOKED = 4401


class Connection:
    def __init__(self, *, device_id: int, owner_id: int) -> None:
        self.device_id = int(device_id)
        self.owner_id = int(owner_id)
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()


class MobileHub:
    def __init__(self) -> None:
        self._connections: Set[Connection] = set()

    def register(self, *, device_id: int, owner_id: int) -> Connection:
        conn = Connection(device_id=device_id, owner_id=owner_id)
        self._connections.add(conn)
        return conn

    def unregister(self, conn: Connection) -> None:
        self._connections.discard(conn)

    def _for(self, owner_id: int) -> List[Connection]:
        return [c for c in self._connections if c.owner_id == int(owner_id)]

    async def publish(self, event: Dict[str, Any], *, owner_id: int) -> int:
        frame = {"type": "event", "event": event}
        targets = self._for(owner_id)
        for conn in targets:
            conn.queue.put_nowait(frame)
        return len(targets)

    async def set_typing(self, on: bool, *, owner_id: int) -> None:
        for conn in self._for(owner_id):
            conn.queue.put_nowait({"type": "typing", "on": bool(on)})

    def revoke(self, device_id: int) -> int:
        """Close every open socket of a revoked phone (4401)."""
        closing = [c for c in self._connections if c.device_id == int(device_id)]
        for conn in closing:
            conn.queue.put_nowait({"type": "_close", "code": CLOSE_REVOKED})
        if closing:
            logger.info("mobile hub: closing %d socket(s) of revoked device %s", len(closing), device_id)
        return len(closing)


hub = MobileHub()
