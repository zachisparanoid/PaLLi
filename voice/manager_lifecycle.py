from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.runtime_control import runtime_control
from voice.manager_registry import ManagedVoiceSession, VoiceRuntimeSessionRegistry
from voice.types import VoiceTransport


EnsureSurfaceSessionCallback = Callable[..., Awaitable[Dict[str, Any]]]
SessionFactory = Callable[..., ManagedVoiceSession]

_MODULE_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceRuntimeSessionStartRequest:
    channel_id: int
    text_channel_id: int
    transport: VoiceTransport
    initiator_user_id: int | None = None
    account_user_id: int | None = None
    account_features: Dict[str, bool] | None = None
    platform: str = "discord"
    is_dm: bool = False
    event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None


async def _resolve_runtime_session_key(
    *,
    registry: VoiceRuntimeSessionRegistry,
    request: VoiceRuntimeSessionStartRequest,
    stop_session_by_key: Callable[..., Awaitable[bool]],
    ensure_surface_session: EnsureSurfaceSessionCallback,
) -> str:
    owner_user_id = (
        request.account_user_id
        if request.account_user_id is not None
        else request.initiator_user_id
    )
    runtime_session_key = ""

    if owner_user_id is not None:
        runtime_session = await ensure_surface_session(
            owner_user_id=int(owner_user_id),
            platform=request.platform,
            channel_id=request.channel_id,
            is_dm=request.is_dm,
            voice=True,
            status="active",
            metadata={"origin": "voice_runtime"},
        )
        runtime_session_key = str(runtime_session.get("session_key") or "")
        existing = registry.get_session(runtime_session_key)
        if existing is not None:
            await stop_session_by_key(runtime_session_key, disconnect_transport=False)
    else:
        existing = registry.get_session_by_channel(
            request.channel_id,
            platform=request.platform,
        )
        if existing is not None and existing.runtime_session_key:
            await stop_session_by_key(
                existing.runtime_session_key,
                disconnect_transport=False,
            )

    if runtime_session_key:
        return runtime_session_key
    normalized_platform = str(request.platform or "discord").strip().lower() or "discord"
    return f"voice:{normalized_platform}:{int(request.channel_id)}"


async def start_runtime_session(
    *,
    registry: VoiceRuntimeSessionRegistry,
    request: VoiceRuntimeSessionStartRequest,
    session_factory: SessionFactory,
    stop_session_by_key: Callable[..., Awaitable[bool]],
    ensure_surface_session: EnsureSurfaceSessionCallback = runtime_control.ensure_surface_session,
    logger: logging.Logger | None = None,
) -> bool:
    active_logger = logger or _MODULE_LOGGER
    owner_user_id = (
        request.account_user_id
        if request.account_user_id is not None
        else request.initiator_user_id
    )
    runtime_session_key = await _resolve_runtime_session_key(
        registry=registry,
        request=request,
        stop_session_by_key=stop_session_by_key,
        ensure_surface_session=ensure_surface_session,
    )

    session = session_factory(
        channel_id=request.channel_id,
        text_channel_id=request.text_channel_id,
        transport=request.transport,
        owner_user_id=owner_user_id,
        initiator_user_id=request.initiator_user_id,
        account_user_id=request.account_user_id,
        account_features=request.account_features,
        platform=request.platform,
        is_dm=request.is_dm,
        event_callback=request.event_callback,
    )
    session.runtime_session_key = runtime_session_key
    registry.index_session(session)
    try:
        await session.start()
        await session._record_session_started()
        return True
    except Exception:
        registry.remove_session(session)
        try:
            session._begin_runtime_updates_stop()
            await session.stop()
        except Exception:
            active_logger.debug(
                "Failed to stop partially started voice session",
                exc_info=True,
            )
        raise


async def stop_runtime_session_by_key(
    *,
    registry: VoiceRuntimeSessionRegistry,
    runtime_session_key: str,
    disconnect_transport: bool = False,
    logger: logging.Logger | None = None,
) -> bool:
    active_logger = logger or _MODULE_LOGGER
    session = registry.get_session(runtime_session_key)
    if session is None:
        return False

    transport = session.transport
    registry.remove_session(session)
    session._begin_runtime_updates_stop()
    await session.stop()
    try:
        await session._record_session_stopped()
    except Exception:
        active_logger.debug("Failed to record voice session end", exc_info=True)

    if disconnect_transport:
        await _disconnect_transport_voice_client(
            transport=transport,
            logger=active_logger,
        )
    return True


async def _disconnect_transport_voice_client(
    *,
    transport: VoiceTransport,
    logger: logging.Logger,
) -> None:
    if not hasattr(transport, "voice_client"):
        return
    voice_client = getattr(transport, "voice_client", None)
    is_connected = getattr(voice_client, "is_connected", None)
    if voice_client is None or not callable(is_connected) or not is_connected():
        return
    try:
        await voice_client.disconnect()
    except Exception:
        logger.debug("Failed to disconnect voice client during stop", exc_info=True)
