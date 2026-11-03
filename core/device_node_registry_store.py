from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class DeviceNodeRegistryStore:
    """Pairing-code store (post-excision).

    Great Excision (2026-07-09): the device-node fleet methods were deleted
    with the device-node runtime. What survives is the LIVE pairing-code
    flow: api/web_account.py issues codes (store_pairing_code) and
    bots/discord/discord_commands.py redeems them
    (get_user_from_pairing_code) to link the owner's Discord account. The
    filename is historical; the pairing_codes table stays.
    """

    """Persistence helper for device-node registry, pairing state, and one-time link codes."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _clean_text(self, value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        text = self._clean_text(value)
        return text or None

    def _clean_text_list(self, values: Optional[List[Any]]) -> List[str]:
        items: List[str] = []
        for value in values or []:
            text = self._clean_text(value)
            if text:
                items.append(text)
        return items

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _device_node_record(self, row: Any) -> Dict[str, Any]:
        return {
            "node_id": self._clean_text(row[0]),
            "owner_user_id": int(row[1]),
            "node_kind": self._clean_text(row[2]),
            "label": self._clean_text(row[3]),
            "platform": self._clean_text(row[4]),
            "host_backend": self._clean_text(row[5]),
            "device_family": self._clean_text(row[6]),
            "status": self._clean_text(row[7]),
            "trust_mode": self._clean_text(row[8]),
            "preferred_for_notifications": bool(row[9]),
            "enabled_capabilities": self._load_json(row[10], {}),
            "capability_health": self._load_json(row[11], {}),
            "last_error": self._clean_text(row[12]),
            "reconnect_token_hash": self._clean_text(row[13]),
            "last_seen_at": float(row[14]) if row[14] is not None else None,
            "metadata": self._load_json(row[15], {}),
            "created_at": float(row[16] or 0.0),
            "updated_at": float(row[17] or 0.0),
        }

    def _device_node_pairing_record(self, row: Any) -> Dict[str, Any]:
        return {
            "pairing_id": self._clean_text(row[0]),
            "owner_user_id": int(row[1]),
            "node_id": self._clean_text(row[2]),
            "pairing_code": self._clean_text(row[3]),
            "node_token_hash": self._clean_text(row[4]),
            "status": self._clean_text(row[5]),
            "expires_at": float(row[6] or 0.0),
            "paired_at": float(row[7]) if row[7] is not None else None,
            "last_used_at": float(row[8]) if row[8] is not None else None,
            "metadata": self._load_json(row[9], {}),
            "created_at": float(row[10] or 0.0),
            "updated_at": float(row[11] or 0.0),
        }

    async def store_pairing_code(self, code: str, user_id: int, expires_at: float) -> None:
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            await conn.execute(
                """
                INSERT INTO pairing_codes (code, user_id, expires_at, created_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (code) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    expires_at = EXCLUDED.expires_at,
                    created_at = EXCLUDED.created_at
                """,
                self._clean_text(code),
                int(user_id),
                float(expires_at),
                now,
            )
        finally:
            await conn.close()

    async def get_user_from_pairing_code(self, code: str) -> Optional[int]:
        conn = await self._brain.get_connection()
        clean_code = self._clean_text(code)
        try:
            async with conn.transaction():
                now = self._now()
                await conn.execute(
                    "DELETE FROM pairing_codes WHERE expires_at < $1",
                    now,
                )
                row = await conn.fetchrow(
                    "SELECT user_id FROM pairing_codes WHERE code = $1 AND expires_at > $2",
                    clean_code,
                    now,
                )
                if not row:
                    return None
                await conn.execute("DELETE FROM pairing_codes WHERE code = $1", clean_code)
                return int(row[0])
        finally:
            await conn.close()

