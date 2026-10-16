"""The mobile door's reminders panel (spec §5).

A list of his open reminders, soonest first, from every door; snooze and
cancel go through the very store calls her own reschedule_reminder and
cancel_reminder tools make, with their rule: nothing changed means the
reminder is not his, or does not exist (Batch 4 task 4).
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, StrictInt

from api.mobile import require_device

router = APIRouter()

# The platforms a reminder can be filed under — the same set list_reminders probes.
REMINDER_PLATFORMS = ("dashboard", "discord", "telegram", "mobile", "voice", "local")

# The largest id the store can hold (a signed 64-bit integer). A bigger path id
# is no reminder at all; it is refused as 422 rather than sent to the store.
MAX_REMINDER_ID = 2**63 - 1
ReminderId = Annotated[int, Path(ge=1, le=MAX_REMINDER_ID)]


def _brain() -> Any:
    from core import database

    return database.brain


@router.get("/reminders")
async def list_reminders(device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    owner_id = int(device["owner_id"])
    rows: List[Dict[str, Any]] = []
    for platform in REMINDER_PLATFORMS:
        rows.extend(await _brain().list_user_scheduled_reminders(user_id=owner_id, platform=platform))
    rows.sort(key=lambda r: float(r["scheduled_time"]))
    return {"reminders": [
        {"id": r["id"], "text": r["reminder_text"], "due_at": float(r["scheduled_time"]),
         "kind": r["kind"], "platform": r["platform"]}
        for r in rows
    ]}


class SnoozeRequest(BaseModel):
    # Strict: a JSON true, "15", 15.0 or 1e2 is not a whole number of minutes.
    minutes: StrictInt = Field(ge=1, le=1440)


def _not_his() -> HTTPException:
    return HTTPException(status_code=404, detail="That reminder isn't yours, or doesn't exist.")


@router.post("/reminders/{reminder_id}/snooze")
async def snooze(reminder_id: ReminderId, payload: SnoozeRequest,
                 device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    owner_id = int(device["owner_id"])
    # The phone's list can be hours old. A reminder that is missing, not his,
    # or already completed (fired, or cancelled) is refused here: the store's
    # schedule update does not look at is_completed, so it would report success
    # for a row that never comes back.
    record = await _brain().get_scheduled_reminder_record(reminder_id)
    if record is None or int(record["user_id"]) != owner_id or record["is_completed"]:
        raise _not_his()
    due = time.time() + payload.minutes * 60
    changed = await _brain().update_scheduled_reminder_schedule(
        reminder_id=reminder_id, scheduled_time=due, user_id=owner_id)
    if not isinstance(changed, int) or changed <= 0:
        raise _not_his()
    return {"ok": True, "due_at": due}


@router.post("/reminders/{reminder_id}/cancel")
async def cancel(reminder_id: ReminderId, device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    changed = await _brain().mark_scheduled_reminder_completed(
        reminder_id=reminder_id, user_id=int(device["owner_id"]))
    if not isinstance(changed, int) or changed <= 0:
        raise _not_his()
    return {"ok": True}
