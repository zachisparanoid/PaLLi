"""What `palli_ctl phone …` runs (the mobile door, 2026-10-13).

Kept out of scripts/palli_ctl.py, which is CRLF, so that file gains only a
thin hook. ``pair`` prints a QR and a one-use code (the same codes the door
page makes); ``list`` names the paired phones; ``revoke`` unpairs one, and
a phone connected right now is closed at its next ping.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Optional

from core.mobile_device_store import MobileDeviceStore
from core.mobile_pairing import display_code, new_pair_code, pair_link, qr_text

_ACTIONS = ("pair", "list", "revoke")


def _when(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "never"


def run_phone_command(action: str, *, device_id: Optional[int] = None, out: Callable[[str], None] = print,
                      db: Any = None, owner_id: Optional[int] = None, base_url: Optional[str] = None) -> int:
    if action not in _ACTIONS:
        raise RuntimeError("Specify a phone action: `phone pair`, `phone list`, or `phone revoke <id>`.")
    return asyncio.run(_run(action, device_id=device_id, out=out, db=db, owner_id=owner_id, base_url=base_url))


async def _run(action: str, *, device_id: Optional[int], out: Callable[[str], None], db: Any,
               owner_id: Optional[int], base_url: Optional[str]) -> int:
    from core.config import config

    if db is None:
        from core import database

        db = database.brain
    if owner_id is None:
        from core.local_mode import get_local_owner_refs

        owner_id = (await get_local_owner_refs()).get("app_user_id")
        if owner_id is None:
            raise RuntimeError("No local owner is set up yet, so there is no phone to pair.")
    store = MobileDeviceStore(db)

    if action == "pair":
        if not bool(getattr(config, "ENABLE_MOBILE_DOOR", False)):
            out("Note: the mobile door is switched off (ENABLE_MOBILE_DOOR=false). The phone will pair, "
                "but PaLLi will not answer there until you switch it on and restart her.")
        code = new_pair_code()
        await store.create_pair_code(owner_id=int(owner_id), code=code)
        url = (str(getattr(config, "MOBILE_PUBLIC_URL", "") or "") if base_url is None else base_url)
        url = url.strip().rstrip("/")
        if url:
            out(qr_text(pair_link(url, code)))
            out("Scan this with the PaLLi app, or type these in:")
            out(f"  Address: {url}")
        else:
            out("MOBILE_PUBLIC_URL is not set, so there is no QR. Type these into the app:")
            out("  Address: where your phone reaches this backend, e.g. http://<host>.<tailnet>.ts.net:8000")
        out(f"  Code:    {display_code(code)}  (good once, for 10 minutes)")
        return 0

    if action == "list":
        devices = await store.list_devices(owner_id=int(owner_id))
        if not devices:
            out("No phones are paired.")
            return 0
        for d in devices:
            out(f"  [{d['id']}] {d['name']} — paired {_when(d['created_at'])}, last seen {_when(d['last_seen_at'])}")
        return 0

    if device_id is None:
        raise RuntimeError("Say which phone: `phone revoke <id>` (`phone list` shows the ids).")
    if not await store.revoke(owner_id=int(owner_id), device_id=int(device_id)):
        raise RuntimeError(f"No paired phone has the id {device_id}. `phone list` shows them.")
    out(f"Unpaired phone {device_id}. If it is connected right now, it is closed at its next ping "
        "(within half a minute).")
    return 0
