"""The mobile door's paired phones and one-use pair codes (spec §3, §4).

A store in the PalliBrain style: one connection per call, asyncpg-style
``$N`` SQL that the brain adapts to SQLite. It is constructed with the brain
(``MobileDeviceStore(brain)``) rather than delegated through it, so
core/database.py does not grow a door.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from core.mobile_pairing import CODE_TTL_SECONDS, sha256_hex

# How often a device's last_seen_at is written: it is touched on every
# authenticated request, and a write per request would be noise.
LAST_SEEN_EVERY_S = 60.0


class PairCodeRefused(Exception):
    """A pair code that does not pair: ``status`` 401 (not one PaLLi made)
    or 410 (expired, or already used)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = int(status)
        self.detail = detail


def _device(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "owner_id": int(row["owner_id"]),
        "name": str(row["name"]),
        "created_at": float(row["created_at"]),
        "last_seen_at": None if row["last_seen_at"] is None else float(row["last_seen_at"]),
        "acked_through": int(row["acked_through"] or 0),
    }


_DEVICE_COLUMNS = "id, owner_id, name, created_at, last_seen_at, acked_through"


class MobileDeviceStore:
    def __init__(self, db: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._db = db
        self._now = now or time.time

    async def create_pair_code(self, *, owner_id: int, code: str) -> float:
        now = self._now()
        expires_at = now + CODE_TTL_SECONDS
        conn = await self._db.get_connection()
        try:
            await conn.execute(
                "INSERT INTO mobile_pair_codes (code_hash, owner_id, created_at, expires_at, used_at) "
                "VALUES ($1, $2, $3, $4, NULL)",
                sha256_hex(code), int(owner_id), now, expires_at,
            )
        finally:
            await conn.close()
        return expires_at

    async def redeem(self, *, code: str, name: str, token: str) -> Dict[str, Any]:
        now = self._now()
        code_hash = sha256_hex(code)
        conn = await self._db.get_connection()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT owner_id, expires_at, used_at FROM mobile_pair_codes WHERE code_hash = $1",
                    code_hash,
                )
                if row is None:
                    raise PairCodeRefused(401, "That code is not one PaLLi made.")
                if row["used_at"] is not None or float(row["expires_at"]) <= now:
                    raise PairCodeRefused(
                        410, "That code expired or was already used. Make a new one on the door page.")
                await conn.execute(
                    "UPDATE mobile_pair_codes SET used_at = $1 WHERE code_hash = $2", now, code_hash)
                device_id = await conn.fetchval(
                    "INSERT INTO mobile_devices (owner_id, name, token_hash, created_at, last_seen_at, "
                    "acked_through, revoked_at) VALUES ($1, $2, $3, $4, $4, 0, NULL) RETURNING id",
                    int(row["owner_id"]), str(name), sha256_hex(token), now,
                )
                owner_id = int(row["owner_id"])
        finally:
            await conn.close()
        return {"id": int(device_id), "owner_id": owner_id, "name": str(name)}

    async def device_for_token(self, token: str) -> Optional[Dict[str, Any]]:
        conn = await self._db.get_connection()
        try:
            row = await conn.fetchrow(
                f"SELECT {_DEVICE_COLUMNS} FROM mobile_devices "
                "WHERE token_hash = $1 AND revoked_at IS NULL",
                sha256_hex(token),
            )
            if row is None:
                return None
            device = _device(row)
            now = self._now()
            seen = device["last_seen_at"]
            if seen is None or now - seen >= LAST_SEEN_EVERY_S:
                await conn.execute(
                    "UPDATE mobile_devices SET last_seen_at = $1 WHERE id = $2", now, device["id"])
                device["last_seen_at"] = now
            return device
        finally:
            await conn.close()

    async def list_devices(self, *, owner_id: int) -> List[Dict[str, Any]]:
        conn = await self._db.get_connection()
        try:
            rows = await conn.fetch(
                f"SELECT {_DEVICE_COLUMNS} FROM mobile_devices "
                "WHERE owner_id = $1 AND revoked_at IS NULL ORDER BY created_at, id",
                int(owner_id),
            )
        finally:
            await conn.close()
        return [_device(row) for row in rows]

    async def count_devices(self, *, owner_id: Optional[int] = None) -> int:
        conn = await self._db.get_connection()
        try:
            if owner_id is None:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM mobile_devices WHERE revoked_at IS NULL")
            else:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM mobile_devices WHERE owner_id = $1 AND revoked_at IS NULL",
                    int(owner_id),
                )
        finally:
            await conn.close()
        return int(count or 0)

    async def revoke(self, *, owner_id: int, device_id: int) -> bool:
        conn = await self._db.get_connection()
        try:
            status = await conn.execute(
                "UPDATE mobile_devices SET revoked_at = $1 "
                "WHERE id = $2 AND owner_id = $3 AND revoked_at IS NULL",
                self._now(), int(device_id), int(owner_id),
            )
        finally:
            await conn.close()
        return str(status).endswith(" 1")

    async def set_acked_through(self, *, device_id: int, through: int) -> None:
        conn = await self._db.get_connection()
        try:
            await conn.execute(
                "UPDATE mobile_devices SET acked_through = MAX(acked_through, $1) WHERE id = $2",
                int(through), int(device_id),
            )
        finally:
            await conn.close()
