"""
Dashboard chat endpoints.

Two surfaces are exposed:

* ``GET /api/chat/ws/stream`` — websocket Neural Feed for the dashboard. This
  has always been the primary client; it predates the new spine.
* ``POST /api/chat/message`` — HTTP chat endpoint that routes the user's
  message through ``core.turn.turn.process_turn``.

The dashboard frontend currently consumes the websocket only — the HTTP
endpoint is here so the new spine has a real entry point on the dashboard
surface.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api.web_auth import _require_user
from core.local_mode import ensure_local_owner, get_local_owner_refs, local_auth_bypass_enabled
from core.socket_manager import socket_manager
from core.turn.ingest import normalize_turn
from core.agent.dispatch import dispatch_turn
from core.web_security import get_user_id_from_request, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessageRequest(BaseModel):
    """Body for ``POST /api/chat/message``."""

    message: str


class ChatAttachment(BaseModel):
    """An attachment (file path / URL) emitted by a Tier-2 delegation that
    completed during this turn."""

    filename: str = ""
    path: str = ""
    url: str = ""
    content_type: str = ""


class ChatBeat(BaseModel):
    """One beat of a cadenced reply — a single message PaLLi would send
    as part of a multi-message response. The dashboard frontend can
    render beats as separate messages with the assigned delays, or
    concatenate them into one block (the ``response`` field is still
    the full concatenated text for backward-compat consumers).

    ``delay_after_previous_seconds`` is 0 for the first beat. The
    frontend should wait that many seconds before rendering this beat,
    relative to when the previous beat appeared.
    """

    text: str
    delay_after_previous_seconds: float = 0.0


class ChatMessageResponse(BaseModel):
    """Response for ``POST /api/chat/message``.

    Shape preserved across the legacy/turn-loop split so the dashboard
    frontend doesn't need to change.

    ``attachments`` (companion-100, Workstream D): when a Tier-2
    delegation completes during this turn and emits a delivery envelope
    via the channel runtime, the dashboard adapter buffers it and the
    HTTP handler drains it on the way out. The drained envelope's
    attachments land here so the dashboard can render them. Empty list
    when no Tier-2 envelope landed during this turn.

    ``delegation_text`` (companion-100, Workstream D): when a Tier-2
    voicer reply lands via the dashboard adapter, its text body lands
    here. The frontend can append this to the main ``response`` field
    or render it as a separate "delegation completed" pane.

    ``beats`` (cadenced-delivery plan): when the respond stage
    produced a multi-beat CadencedReply, the structured beat list
    lands here. The frontend can choose to render beats as separate
    messages with the assigned delays, or fall back to using
    ``response`` (the concatenated total). Empty list when the reply
    was single-beat (the common case).

    ``quiet`` (Batch 8, task 2a — ruling T2-3): she chose to stay quiet.
    ``response`` is empty and the page draws nothing for her — never the
    "answered with nothing at all" trouble bubble. A file she delivered
    earlier in the turn still comes in ``attachments``.
    """

    response: str
    delegation_text: str = ""
    attachments: list[ChatAttachment] = []
    beats: list[ChatBeat] = []
    quiet: bool = False


# ---------------------------------------------------------------------------
# Websocket (Neural Feed) — unchanged from the previous implementation.
# ---------------------------------------------------------------------------


@router.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    if local_auth_bypass_enabled():
        user = await ensure_local_owner()
        user_id = int(user["id"])
    else:
        user_id = get_user_id_from_request(websocket)
    if not user_id:
        # Policy violation: this websocket is only for authenticated dashboard sessions.
        await websocket.close(code=1008)
        return

    await socket_manager.connect(websocket, user_id)
    try:
        while True:
            # Keep connection alive, maybe listen for client pings
            await websocket.receive_text()
            # We don't really expect input from dashboard yet, but this keeps the loop running
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in dashboard websocket")
    finally:
        socket_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Helpers — kept module-level so tests can monkeypatch them by name.
# ---------------------------------------------------------------------------


async def _resolve_local_owner_id() -> int:
    """Resolve the local owner's app_user_id via the canonical resolver.

    Single-owner local mode resolution goes through
    `core.local_mode.get_local_owner_refs()` (Decision 2 of the
    2026-05-08 completion refactor). Raises if the resolver returns
    None — the caller must surface an error rather than silently
    attribute the request to user_id=1.
    """
    refs = await get_local_owner_refs()
    app_user_id = refs.get("app_user_id")
    if app_user_id is None:
        raise RuntimeError("local owner could not be resolved")
    return int(app_user_id)


_dashboard_adapter_registered: bool = False


async def _ensure_dashboard_adapter_registered() -> None:
    """Register the dashboard channel adapter once, on first chat dispatch.

    Companion-100 (Workstream D): the dashboard surface lives in api/, not
    core/. Registering the adapter from api/ honors the core-↛-bots
    architecture rule. Idempotent and lazy — only fires on the first
    dashboard chat, so backends with no chat traffic don't pay the cost.
    """
    global _dashboard_adapter_registered
    if _dashboard_adapter_registered:
        return
    try:
        from bots.dashboard.channel_adapter import register_dashboard_adapter
        await register_dashboard_adapter()
        _dashboard_adapter_registered = True
    except Exception as exc:
        logger.warning("dashboard adapter registration failed (lazy): %s", exc)


async def _dispatch_to_spine(
    message: str,
    *,
    outcome: Optional[dict[str, Any]] = None,
) -> tuple[str, str, list[ChatAttachment], list[ChatBeat]]:
    """Route the dashboard chat message through ``core.turn.turn.process_turn``
    and drain any delegation envelopes the dashboard channel adapter
    buffered during the turn.

    Returns a tuple of (response_text, delegation_text, attachments, beats)
    — never raises. Errors from ``process_turn`` are coerced into a
    human-readable response_text so the dashboard frontend behaves
    consistently — but only when the turn left nothing of HERS to say. A
    turn that errored and still answered (the provider-outage fallback)
    hands over her own words; see the branch below.

    Companion-100 (Workstream D): the third return value is the drain from
    the dashboard adapter. When a Tier-2 delegation completes during this
    turn and the bridge calls ``channel_runtime.send_envelope("dashboard",
    ...)``, the dashboard adapter buffers the envelope. We drain it after
    process_turn so the HTTP response carries both the Tier-0 reply text
    and the Tier-2 delivery (text body + attachment paths).

    Cadenced-delivery plan: the fourth return value is the structured
    list of beats when the respond stage produced a multi-beat reply.
    The frontend can render beats as staggered messages or fall back to
    the concatenated ``response_text``. Empty when the reply was single-
    beat (the common case).

    ``outcome`` (Batch 8, task 2a): when given, ``outcome["quiet"]`` is set
    to whether she chose to stay quiet. A side channel rather than a fifth
    return value, so every caller that unpacks four keeps working.
    """
    # Lazily register the dashboard channel adapter on first dispatch.
    await _ensure_dashboard_adapter_registered()
    try:
        owner_id = await _resolve_local_owner_id()
        turn = normalize_turn(
            {
                "surface": "dashboard",
                "owner_id": owner_id,
                "content": message,
                "metadata": {
                    "is_dm": True,
                    "channel_id": None,
                    "discord_username": None,
                    # Step 2 (2026-10-08): the web chat is always the owner.
                    "speaker": {"kind": "owner", "platform": "web"},
                },
            }
        )
        result = await dispatch_turn(turn)
    except Exception as exc:
        logger.exception("turn loop dispatch raised: %s", exc)
        return (f"(turn loop error: {exc})", "", [], [])

    # Her words win over the log line whenever she has any (first door, final
    # wave round 2, 2026-09-17). A turn whose provider failed does NOT come
    # back empty for a waiting human: dispatch's `_apply_outage_fallback`
    # (core/agent/dispatch.py:64-82) turns error-silence into her own honest
    # sentence and returns `responded=True` with that text — and it carries
    # the error onward, because the error is true too and the log wants it.
    # Reading `error` alone put "(turn loop error: agent loop error: …)" in
    # HER bubble on the door page, which is an internal string in her voice
    # and the one thing this surface must never do. The condition is "she said
    # nothing": with words we hand them over and drain the delegations as
    # usual; with none, the error text is still the only thing there is to
    # show, so it is still shown.
    if result.error and not (result.responded and str(result.response_text or "").strip()):
        return (f"(turn loop error: {result.error})", "", [], [])

    response_text = result.response_text or ""
    if outcome is not None:
        outcome["quiet"] = bool(getattr(result, "quiet", False))

    # Cadenced-delivery: extract beats list from the TurnResult when
    # the respond stage produced a multi-beat reply.
    beats: list[ChatBeat] = []
    if result.cadenced_reply is not None and len(result.cadenced_reply.beats) > 1:
        for beat in result.cadenced_reply.beats:
            beats.append(ChatBeat(
                text=beat.text,
                delay_after_previous_seconds=beat.delay_after_previous_seconds,
            ))

    # Drain any Tier-2 delegation envelopes that landed during this turn.
    delegation_text = ""
    attachments: list[ChatAttachment] = []
    try:
        from bots.dashboard.channel_adapter import dashboard_channel_adapter
        envelopes = await dashboard_channel_adapter.drain(
            owner_id=owner_id, surface_id="default",
        )
        if envelopes:
            text_parts = [str(e.text or "").strip() for e in envelopes if (e.text or "").strip()]
            delegation_text = "\n\n".join(text_parts)
            for env in envelopes:
                for att in (env.attachments or []):
                    attachments.append(ChatAttachment(
                        filename=str(att.filename or ""),
                        path=str(att.path or ""),
                        url=str(att.url or ""),
                        content_type=str(att.content_type or ""),
                    ))
    except Exception as exc:
        logger.warning("dashboard envelope drain failed: %s", exc, exc_info=True)

    # 2026-06-11: the ReAct loop's own deliveries (deliver_artifact →
    # TurnResult.attachments) were silently dropped on the dashboard — only
    # Tier-2 envelopes were collected above, so files PaLLi delivered in a
    # normal turn (screenshots, generated files) never reached the frontend.
    # Discord already handled these; now the dashboard does too.
    try:
        import os as _os
        seen_paths = {a.path for a in attachments}
        for path in tuple(getattr(result, "attachments", ()) or ()):
            p = str(path or "").strip()
            if not p or p in seen_paths:
                continue
            attachments.append(ChatAttachment(
                filename=_os.path.basename(p),
                path=p,
                url="",
                content_type="",
            ))
    except Exception as exc:
        logger.warning("turn-result attachment mapping failed: %s", exc)

    return (response_text, delegation_text, attachments, beats)


# ---------------------------------------------------------------------------
# HTTP chat endpoint — the dashboard's invocation site for the new spine.
# ---------------------------------------------------------------------------


@router.post("/message", response_model=ChatMessageResponse)
async def chat_message(payload: ChatMessageRequest, http_request: Request) -> ChatMessageResponse:
    # CSRF (2026-06-11): chat is a state-changing endpoint with real-world
    # reach — a turn can invoke local_shell. Without this, any webpage open
    # on a device that can reach this backend could post messages to PaLLi.
    # Auth runs first so anonymous callers get a clean 401, not a CSRF 403.
    await _require_user(http_request)
    require_csrf(http_request)
    outcome: dict[str, Any] = {}
    response_text, delegation_text, attachments, beats = await _dispatch_to_spine(
        message=payload.message, outcome=outcome,
    )
    return ChatMessageResponse(
        response=response_text,
        delegation_text=delegation_text,
        attachments=attachments,
        beats=beats,
        quiet=bool(outcome.get("quiet", False)),
    )


# ── GET /history (web-page reminders, 2026-10-10) ─────────────────────────────
# The page used to show only replies to what was just typed, and started blank
# on every visit. It now loads the recent web conversation when it opens, and
# every 15 seconds asks for reminders that arrived on their own
# (``reminders_only``) after the newest message it has (``since_id``). A
# reminder is the row the reminder delivery writes: metadata type
# "deferred_action" (core/reminders/binding.py). Read-only, so no CSRF; the
# same sign-in gate as POST /message. It reads the local owner's web
# conversation, exactly the one POST /message speaks into: with sign-in on,
# a signed-in user reads what that user could already write as the owner.

class ChatHistoryMessage(BaseModel):
    id: int
    role: str
    text: str
    timestamp: float
    reminder: bool = False


class ChatHistoryResponse(BaseModel):
    messages: list[ChatHistoryMessage]


HISTORY_LIMIT_MAX = 200


@router.get("/history", response_model=ChatHistoryResponse)
async def chat_history(
    http_request: Request,
    since_id: int = 0,
    limit: int = 50,
    reminders_only: bool = False,
) -> ChatHistoryResponse:
    await _require_user(http_request)
    try:
        owner_id = await _resolve_local_owner_id()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The local owner is not set up yet.") from exc
    from core.database import brain

    rows = await brain.get_conversation_history(
        user_id=owner_id,
        platform="dashboard",
        channel_id=0,
        limit=max(1, min(int(limit), HISTORY_LIMIT_MAX)),
        include_metadata=True,
    )
    messages: list[ChatHistoryMessage] = []
    for row in rows or []:
        row_id = int(row.get("id") or 0)
        role = str(row.get("role") or "")
        text = str(row.get("content") or "").strip()
        if row_id <= since_id or role not in ("user", "assistant") or not text:
            continue
        meta = row.get("metadata") or {}
        reminder = isinstance(meta, dict) and meta.get("type") == "deferred_action"
        if reminders_only and not reminder:
            continue
        messages.append(ChatHistoryMessage(
            id=row_id, role=role, text=text,
            timestamp=float(row.get("timestamp") or 0.0), reminder=reminder,
        ))
    return ChatHistoryResponse(messages=messages)
