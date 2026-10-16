from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional

from core.llm_runtime_activity import llm_runtime_activity_service


logger = logging.getLogger(__name__)

LLMRuntimeTelemetryCallback = Callable[[Dict[str, Any]], Awaitable[None] | None]

_runtime_activity_owner_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "llm_runtime_activity_owner_user_id",
    default=None,
)


def normalize_owner_user_id(value: Any) -> Optional[int]:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@contextmanager
def runtime_activity_owner_scope(*, owner_user_id: Optional[int]) -> Iterator[None]:
    token = _runtime_activity_owner_user_id.set(normalize_owner_user_id(owner_user_id))
    try:
        yield
    finally:
        _runtime_activity_owner_user_id.reset(token)


async def emit_runtime_telemetry(
    callback: Optional[LLMRuntimeTelemetryCallback],
    payload: Dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        result = callback(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug("Failed to deliver LLM runtime telemetry callback: %s", exc)


@dataclass
class RuntimeActivityState:
    recorded_count: int = 0
    last_payload: Optional[Dict[str, Any]] = None

    def record(self, payload: Dict[str, Any]) -> None:
        self.recorded_count += 1
        self.last_payload = dict(payload or {})

    @property
    def has_recorded(self) -> bool:
        return self.recorded_count > 0


@dataclass
class LLMRuntimeActivityRecorder:
    telemetry_callback: Optional[LLMRuntimeTelemetryCallback]
    requested_model_reference: Optional[str]
    resolved_provider: str
    resolved_model: Optional[str]
    state: RuntimeActivityState = field(default_factory=RuntimeActivityState)

    async def record(self, payload: Dict[str, Any]) -> None:
        safe_payload = dict(payload or {})
        self.state.record(safe_payload)
        owner_user_id = normalize_owner_user_id(_runtime_activity_owner_user_id.get())
        event = llm_runtime_activity_service.record_call(
            payload=safe_payload,
            requested_model_reference=str(self.requested_model_reference or "").strip(),
            resolved_provider=str(self.resolved_provider or "").strip(),
            resolved_model=str(self.resolved_model or "").strip(),
            owner_user_id=owner_user_id,
        )
        await llm_runtime_activity_service.notify_listeners(event)
        await emit_runtime_telemetry(self.telemetry_callback, safe_payload)

    async def callback(self, payload: Dict[str, Any]) -> None:
        await self.record(payload)

    async def record_failure_if_missing(
        self,
        *,
        log_context: str,
        streamed: bool,
        error: str,
    ) -> None:
        if self.state.has_recorded:
            return
        await self.record(
            {
                "provider": self.resolved_provider,
                "model": self.resolved_model,
                "streamed": streamed,
                "log_context": log_context,
                "latency_seconds": 0.0,
                "usage": {},
                "provider_retry_count": 0,
                "success": False,
                "error": str(error or "unknown_error"),
            }
        )
