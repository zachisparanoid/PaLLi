"""The mobile door's routes (spec §5, 2026-10-13).

Pairing (``/pair/start`` for the owner on the door page, ``/pair`` for the
phone holding a code) and everything else behind a device token. The token
is required even with LOCAL_DASHBOARD_NO_AUTH on (spec §3 rule 1): a new door
is not open to everyone on the tailnet.

The phone's way in lives here too: ``POST /messages`` takes a send (202 at
once; saving, hearing and the owner lane run behind it) and ``WS /stream``
replays from a cursor, then carries live events, typing and pings.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Annotated, Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as FormUpload

from api.web_auth import _require_user
from bots.mobile import intake
from bots.mobile.client import active_door
from bots.mobile.hub import CLOSE_REVOKED, hub
from bots.mobile.reply import emit_notice, inbox_dir
from core.config import config
from core.mobile_auth import bearer_token, device_for_request, device_store
from core.mobile_device_store import PairCodeRefused
from core.mobile_events_store import events_store
from core.mobile_pairing import (
    API_VERSION,
    display_code,
    new_device_token,
    new_pair_code,
    normalize_pair_code,
    pair_link,
    qr_svg,
)
from core.rate_limit import limiter
from core.web_security import require_csrf

logger = logging.getLogger(__name__)

router = APIRouter()

# The largest id the stores can hold (a signed 64-bit integer), the same bound
# the reminders routes use (api/mobile_reminders.py). A bigger id is refused as
# 422 rather than reaching sqlite as an OverflowError (a 500).
MAX_ID = 2**63 - 1
DeviceId = Annotated[int, Path(ge=1, le=MAX_ID)]
EventCursor = Annotated[Optional[int], Query(ge=0, le=MAX_ID)]


async def require_device(request: Request) -> Dict[str, Any]:
    device = await device_for_request(request)
    if device is None:
        raise HTTPException(status_code=401, detail="This phone is not paired.")
    return device


def public_url() -> str:
    return str(getattr(config, "MOBILE_PUBLIC_URL", "") or "").strip().rstrip("/")


def door_state() -> str:
    """What the phone is told about its door: no brain yet, shut, or open."""
    if not bool(getattr(config, "brain_ready", True)):
        return "no_brain"
    if not bool(getattr(config, "effective_mobile_enabled", False)):
        return "closed"
    return "open"


async def _owner_or_device(request: Request) -> Tuple[int, Optional[Dict[str, Any]]]:
    """The owner id behind a request, and the device when it came with a token."""
    if bearer_token(request) is not None:
        device = await require_device(request)
        return int(device["owner_id"]), device
    user = await _require_user(request)
    return int(user["id"]), None


@router.post("/pair/start")
async def pair_start(request: Request) -> Dict[str, Any]:
    user = await _require_user(request)
    require_csrf(request)
    code = new_pair_code()
    expires_at = await device_store().create_pair_code(owner_id=int(user["id"]), code=code)
    url = public_url() or None
    body: Dict[str, Any] = {"code": display_code(code), "expires_at": expires_at, "url": url, "link": None}
    if url:
        link = pair_link(url, code)
        body["link"] = link
        body["qr_svg"] = qr_svg(link)
    return body


class PairRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    device_name: str = Field(min_length=1, max_length=80)


@router.post("/pair")
@limiter.limit("5/minute")
async def pair(request: Request, payload: PairRequest) -> Dict[str, Any]:
    name = payload.device_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give this phone a name.")
    token = new_device_token()
    try:
        device = await device_store().redeem(
            code=normalize_pair_code(payload.code), name=name, token=token)
    except PairCodeRefused as refused:
        raise HTTPException(status_code=refused.status, detail=refused.detail) from None
    logger.info("mobile door: paired device %s (%r)", device["id"], name)
    return {"token": token, "device_id": device["id"], "api_version": API_VERSION}


@router.get("/me")
async def me(device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    return {
        "device": {"id": device["id"], "name": device["name"]},
        "door_state": door_state(),
        "owner_display_name": str(getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "") or ""),
        "api_version": API_VERSION,
    }


@router.get("/devices")
async def list_devices(request: Request) -> Dict[str, Any]:
    owner_id, device = await _owner_or_device(request)
    this = device["id"] if device else None
    devices = await device_store().list_devices(owner_id=owner_id)
    return {"devices": [
        {"id": d["id"], "name": d["name"], "created_at": d["created_at"],
         "last_seen_at": d["last_seen_at"], "this_device": d["id"] == this}
        for d in devices
    ]}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: DeviceId, request: Request) -> Dict[str, Any]:
    owner_id, device = await _owner_or_device(request)
    if device is None:
        require_csrf(request)
    if not await device_store().revoke(owner_id=owner_id, device_id=device_id):
        raise HTTPException(status_code=404, detail="No paired phone has that id.")
    hub.revoke(device_id)
    logger.info("mobile door: revoked device %s", device_id)
    return {"ok": True}


@router.get("/events")
async def list_events(after: EventCursor = None, before: EventCursor = None, limit: int = 0,
                      device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    if (after is None) == (before is None):
        raise HTTPException(status_code=422, detail="Ask for events after an id, or before one.")
    owner_id = int(device["owner_id"])
    if after is not None:
        page, more = await events_store().after(
            owner_id=owner_id, after=max(0, int(after)), limit=min(max(limit or 200, 1), 200))
    else:
        page, more = await events_store().before(
            owner_id=owner_id, before=int(before), limit=min(max(limit or 50, 1), 50))
    return {"events": page, "more": more}


@router.get("/files/{file_id}")
async def get_file(file_id: str, device: Dict[str, Any] = Depends(require_device)) -> FileResponse:
    found = await events_store().file(owner_id=int(device["owner_id"]), file_id=file_id)
    if found is None or not os.path.isfile(found["path"]):
        raise HTTPException(status_code=404, detail="That file is no longer on PaLLi's machine.")
    return FileResponse(found["path"], media_type=found["content_type"], filename=found["filename"])


PING_EVERY_S = 25.0
SILENT_AFTER_S = 60.0
# A socket whose token cannot be re-checked this many pings in a row is closed
# (1013, try again later): the phone reconnects and is judged at connect, which
# fails closed. One blip keeps the socket.
RECHECKS_BEFORE_CLOSE = 3
CLOSE_TRY_AGAIN = 1013

# What a send may cost before it is parsed: the brief's per-part and total
# limits (bots/mobile/intake.py) plus room for the multipart framing and the
# two text fields; and how many parts a send may have.
FORM_OVERHEAD_BYTES = 1024 * 1024
MAX_FILE_PARTS = 20
MAX_FIELD_PARTS = 8

_NOT_TAKEN_IN = "I couldn't take in that message — the details are in PaLLi's log."
_NOT_STARTED = "That message reached PaLLi, but she couldn't start on it — please send it again."

# Sends being taken in (saved, heard, recorded): a retry of one of these is
# answered 202 without a second turn, like one already in the timeline.
_ACCEPTING: Set[str] = set()
_INTAKE_TASKS: Set[asyncio.Task] = set()


async def drain_intake() -> None:
    """Wait for every send being taken in (tests)."""
    while _INTAKE_TASKS:
        await asyncio.gather(*list(_INTAKE_TASKS), return_exceptions=True)


async def _read(field: str, part: FormUpload) -> intake.Upload:
    data = await part.read(intake.MAX_PART_BYTES + 1)
    return intake.Upload(field=field, filename=str(part.filename or field),
                         content_type=str(part.content_type or "application/octet-stream"), data=data)


async def _tell(owner_id: int, text: str, store: Any) -> None:
    """A plain trouble line on the phone. The detail is in the log, not the line."""
    try:
        await emit_notice(owner_id, text, level="trouble", store=store, hub=hub)
    except Exception:
        logger.exception("mobile door: the trouble notice failed too")


async def _take_in(door: Any, owner_id: int, client_id: str, text: str, uploads: List[intake.Upload]) -> None:
    store = events_store()
    try:
        try:
            turn, payload = await intake.build_turn(
                owner_id=owner_id, client_id=client_id, text=text, uploads=uploads,
                directory=inbox_dir() / date.today().isoformat(), store=store)
            event = await store.append(owner_id=owner_id, kind="user_message", payload=payload,
                                       client_id=client_id)
        except Exception:  # noqa: BLE001 — the phone hears it as a notice
            logger.exception("mobile door: a send could not be taken in")
            await _tell(owner_id, _NOT_TAKEN_IN, store)
            return
        # The message is in the timeline from here on: a retry with its client_id is answered
        # 202 without a second turn, so what goes wrong next is told to the phone, not retried.
        try:
            await hub.publish(event, owner_id=owner_id)
        except Exception:  # noqa: BLE001 — the row is the record; replay carries it
            logger.warning("mobile door: event %s is in the timeline but its push failed",
                           event.get("id"), exc_info=True)
        try:
            await door.accept(turn)
        except Exception:  # noqa: BLE001
            logger.exception("mobile door: a recorded send could not be started")
            await _tell(owner_id, _NOT_STARTED, store)
    finally:
        _ACCEPTING.discard(client_id)


def _text_field(form: Any, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


@router.post("/messages", status_code=202)
async def send_message(request: Request, device: Dict[str, Any] = Depends(require_device)) -> Dict[str, Any]:
    # The route takes no Form/File parameters on purpose: FastAPI would parse (and spool to disk)
    # the body before running require_device, so any peer could stream bytes at an unpaired door.
    # Auth runs first; then the size the sender claims; then the parse, with bounds.
    state = door_state()
    door = active_door()
    if state != "open" or door is None:
        reason = "no_brain" if state == "no_brain" else "door_closed"
        raise HTTPException(status_code=503, detail={"reason": reason})
    owner_id = int(device["owner_id"])
    if owner_id != door.owner_id:
        raise HTTPException(status_code=403, detail="This phone is not paired to this door's owner.")
    try:
        claimed = int(request.headers.get("content-length", ""))
    except ValueError:
        claimed = -1
    if not 0 <= claimed <= intake.MAX_TOTAL_BYTES + FORM_OVERHEAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"A send is at most {intake.MAX_TOTAL_BYTES // (1024 * 1024)} MB, and says how big it is.")
    async with request.form(max_files=MAX_FILE_PARTS, max_fields=MAX_FIELD_PARTS) as form:
        client_id = _text_field(form, "client_id")
        text = _text_field(form, "text")
        parts = [(field, part) for field in ("images", "files", "voice")
                 for part in form.getlist(field) if isinstance(part, FormUpload)]
        try:
            # Sizes first, from what the parse already counted: nothing is read into memory
            # until every part is within its limit.
            intake.check_sizes([(str(p.filename or f), int(p.size or 0)) for f, p in parts])
            uploads = [await _read(field, part) for field, part in parts]
            intake.check_send(client_id, text, uploads)
        except intake.SendRefused as refused:
            raise HTTPException(status_code=refused.status, detail=refused.detail) from None
    # Claim the id before the first await: two retries racing each other must
    # not both pass the timeline check below and start two turns.
    if client_id in _ACCEPTING:
        return {"client_id": client_id}
    _ACCEPTING.add(client_id)
    try:
        already = await events_store().has_client_id(client_id)
    except BaseException:
        _ACCEPTING.discard(client_id)
        raise
    if already:
        _ACCEPTING.discard(client_id)
        return {"client_id": client_id}
    task = asyncio.create_task(_take_in(door, owner_id, client_id, text, uploads))
    _INTAKE_TASKS.add(task)
    task.add_done_callback(_INTAKE_TASKS.discard)
    return {"client_id": client_id}


async def _send_loop(websocket: WebSocket, conn: Any, replayed_through: int) -> None:
    """The one writer: live events, typing, pings, and the close a revoke asks for.

    A live event is skipped only when the replay already sent it (id <= the
    replay's high-water mark). Live sends never move that mark: two emits for
    one owner can publish in reverse id order (the append and the publish are
    separate awaits), and the lower id must still go out."""
    while True:
        frame = await conn.queue.get()
        kind = frame.get("type")
        if kind == "_close":
            await websocket.close(code=int(frame["code"]))
            return
        if kind == "event" and int(frame["event"]["id"]) <= replayed_through:
            continue
        await websocket.send_json(frame)


async def _ping_loop(conn: Any, token: str) -> None:
    """Keep the path warm, and re-check the token each time: a phone revoked
    from elsewhere (palli_ctl, another process) is closed at the next ping.
    A re-check that cannot be made is not a revoke, but it is not a yes either:
    after RECHECKS_BEFORE_CLOSE in a row the socket is closed (1013)."""
    failures = 0
    while True:
        await asyncio.sleep(PING_EVERY_S)
        try:
            still_paired = await device_store().device_for_token(token) is not None
            failures = 0
        except Exception:  # noqa: BLE001
            failures += 1
            logger.warning("mobile door: could not re-check a phone's token (%d in a row)", failures,
                           exc_info=True)
            if failures >= RECHECKS_BEFORE_CLOSE:
                conn.queue.put_nowait({"type": "_close", "code": CLOSE_TRY_AGAIN})
                return
            still_paired = True
        if not still_paired:
            conn.queue.put_nowait({"type": "_close", "code": CLOSE_REVOKED})
            return
        conn.queue.put_nowait({"type": "ping"})


async def _record_ack(device: Dict[str, Any], raw: Any) -> None:
    """Move the phone's cursor to what it says it has through — never past the
    newest event on the owner's timeline, and a garbled or non-finite value
    moves nothing."""
    try:
        through = int(raw or 0)
    except (TypeError, ValueError, OverflowError):
        return
    if through <= 0:
        return
    try:
        newest = await events_store().latest_id(owner_id=int(device["owner_id"]))
        through = min(through, newest)
        if through > 0:
            await device_store().set_acked_through(device_id=device["id"], through=through)
    except Exception:  # noqa: BLE001 — a cursor that did not move is re-sent with the next ack
        logger.warning("mobile door: an ack was not recorded", exc_info=True)


async def _receive_loop(websocket: WebSocket, device: Dict[str, Any]) -> None:
    owner_id = int(device["owner_id"])
    while True:
        try:
            frame = await asyncio.wait_for(websocket.receive_json(), timeout=SILENT_AFTER_S)
        except asyncio.TimeoutError:
            await websocket.close(code=1001)
            return
        except (WebSocketDisconnect, RuntimeError, ValueError):
            return
        kind = frame.get("type") if isinstance(frame, dict) else None
        if kind == "ack":
            await _record_ack(device, frame.get("through"))
        elif kind == "typing":
            door = active_door()
            if door is not None and door.owner_id == owner_id:     # only his typing holds his burst
                door.note_typing()


def _reaped(task: asyncio.Task) -> None:
    """A stream task nobody awaits still has its failure seen. A socket that the
    phone already closed raises on the next write; that is not news."""
    if task.cancelled():
        return
    failure = task.exception()
    if failure is None:
        return
    expected = isinstance(failure, (WebSocketDisconnect, RuntimeError))
    (logger.debug if expected else logger.warning)("mobile door: a stream task ended with %r", failure)


@router.websocket("/stream")
async def stream(websocket: WebSocket, after: int = 0) -> None:
    await websocket.accept()
    device = await device_for_request(websocket)
    if device is None:
        await websocket.close(code=CLOSE_REVOKED)
        return
    owner_id = int(device["owner_id"])
    token = bearer_token(websocket) or ""
    conn = hub.register(device_id=device["id"], owner_id=owner_id)
    tasks: List[asyncio.Task] = []
    try:
        # A cursor ahead of the timeline (a database restored from a backup, a
        # corrupted cursor) would skip every live event for the life of the
        # socket; it is clamped to the newest id (review M1).
        newest = await events_store().latest_id(owner_id=owner_id)
        replayed_through = min(max(0, int(after)), newest)
        if replayed_through < int(after):
            logger.info("mobile door: a phone's cursor %s is past the newest event %s; "
                        "it replays from %s", int(after), newest, replayed_through)
        while True:
            page, more = await events_store().after(owner_id=owner_id, after=replayed_through, limit=200)
            for event in page:
                await websocket.send_json({"type": "event", "event": event})
                replayed_through = int(event["id"])
            if not more:
                break
        tasks = [asyncio.create_task(_send_loop(websocket, conn, replayed_through)),
                 asyncio.create_task(_receive_loop(websocket, device)),
                 asyncio.create_task(_ping_loop(conn, token))]
        for task in tasks:
            task.add_done_callback(_reaped)
        await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        # The teardown awaits nothing: this handler can be cancelled at any await (a server
        # shutdown, a test client closing), and a second cancel landing on an await in here
        # would escape with the hub still holding its unbounded queue. Leave the hub, then
        # cancel the siblings; they finish on their own (_reaped sees any failure).
        hub.unregister(conn)
        for task in tasks:
            task.cancel()
