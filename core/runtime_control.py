from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config import config
from core.database import brain
from core.surface_ids import coerce_legacy_channel_id, normalize_surface_id, session_surface_suffix

logger = logging.getLogger(__name__)

RUNTIME_JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "skipped", "timed_out"}
RUNTIME_JOB_ACTIVE_STATUSES = {"scheduled", "queued", "running", "waiting"}


def owner_root_key(owner_user_id: int) -> str:
    return f"owner:{int(owner_user_id)}"


def owner_surface_key(owner_user_id: int, *parts: str) -> str:
    clean_parts = [str(part).strip().replace(" ", "_") for part in parts if str(part).strip()]
    return ":".join([owner_root_key(owner_user_id), "surface", *clean_parts])


def maintenance_session_key(owner_user_id: int) -> str:
    return owner_surface_key(int(owner_user_id), "maintenance")


def describe_surface_session(
    owner_user_id: int,
    *,
    platform: str,
    channel_id: Optional[int] = None,
    surface_id: Optional[str] = None,
    is_dm: bool = False,
    voice: bool = False,
    maintenance: bool = False,
) -> Dict[str, Any]:
    owner_id = int(owner_user_id)
    normalized_platform = str(platform or "local").strip().lower() or "local"
    clean_surface_id = normalize_surface_id(surface_id, channel_id=channel_id)
    clean_channel_id = coerce_legacy_channel_id(surface_id, channel_id=channel_id)

    if maintenance:
        return {
            "session_key": maintenance_session_key(owner_id),
            "session_type": "maintenance",
            "platform": "local",
            "channel_id": None,
            "surface_id": "maintenance",
            "label": "Maintenance",
        }

    if voice:
        if normalized_platform == "local":
            return {
                "session_key": owner_surface_key(owner_id, "local", "voice"),
                "session_type": "local_voice",
                "platform": "local",
                "channel_id": clean_channel_id,
                "surface_id": clean_surface_id or "local-voice",
                "label": "Local Voice",
            }
        if normalized_platform == "discord":
            suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
            return {
                "session_key": owner_surface_key(owner_id, "discord", "voice", suffix),
                "session_type": "discord_voice",
                "platform": "discord",
                "channel_id": clean_channel_id,
                "surface_id": clean_surface_id or suffix,
                "label": f"Discord Voice {suffix}",
            }
        suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
        return {
            "session_key": owner_surface_key(owner_id, normalized_platform, "voice", suffix),
            "session_type": f"{normalized_platform}_voice",
            "platform": normalized_platform,
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id or suffix,
            "label": f"{normalized_platform.title()} Voice",
        }

    if normalized_platform == "local":
        return {
            "session_key": owner_surface_key(owner_id, "local", "chat"),
            "session_type": "local_chat",
            "platform": "local",
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id or "local-chat",
            "label": "Local Chat",
        }
    if normalized_platform == "discord" and is_dm:
        suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
        return {
            "session_key": owner_surface_key(owner_id, "discord", "dm", suffix),
            "session_type": "discord_dm",
            "platform": "discord",
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id or suffix,
            "label": "Discord DM",
        }
    if normalized_platform == "discord":
        suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
        return {
            "session_key": owner_surface_key(owner_id, "discord", "channel", suffix),
            "session_type": "discord_channel",
            "platform": "discord",
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id or suffix,
            "label": f"Discord Channel {suffix}",
        }
    if is_dm:
        suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
        return {
            "session_key": owner_surface_key(owner_id, normalized_platform, "dm", suffix),
            "session_type": f"{normalized_platform}_dm",
            "platform": normalized_platform,
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id or suffix,
            "label": f"{normalized_platform.title()} DM",
        }
    suffix = session_surface_suffix(clean_surface_id, channel_id=clean_channel_id)
    return {
        "session_key": owner_surface_key(owner_id, normalized_platform, "channel", suffix),
        "session_type": f"{normalized_platform}_channel",
        "platform": normalized_platform,
        "channel_id": clean_channel_id,
        "surface_id": clean_surface_id or suffix,
        "label": f"{normalized_platform.title()} Channel {suffix}",
    }


class RuntimeControlPlane:
    def __init__(self) -> None:
        self._bootstrap_lock = asyncio.Lock()
        self._bootstrap_complete = False
        self._bootstrap_task: Optional[asyncio.Task[Any]] = None

    async def ensure_bootstrap(self) -> None:
        if self._bootstrap_complete:
            return
        current_task = asyncio.current_task()
        if self._bootstrap_task is current_task:
            return
        async with self._bootstrap_lock:
            if self._bootstrap_complete:
                return
            current_task = asyncio.current_task()
            self._bootstrap_task = current_task
            try:
                await self._backfill_open_reminders()
                self._bootstrap_complete = True
            finally:
                if self._bootstrap_task is current_task:
                    self._bootstrap_task = None

    async def ensure_surface_session(
        self,
        *,
        owner_user_id: int,
        platform: str,
        channel_id: Optional[int] = None,
        surface_id: Optional[str] = None,
        is_dm: bool = False,
        voice: bool = False,
        maintenance: bool = False,
        status: str = "ready",
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_bootstrap()
        descriptor = describe_surface_session(
            int(owner_user_id),
            platform=platform,
            channel_id=channel_id,
            surface_id=surface_id,
            is_dm=is_dm,
            voice=voice,
            maintenance=maintenance,
        )
        await brain.upsert_runtime_session(
            session_key=str(descriptor["session_key"]),
            owner_user_id=int(owner_user_id),
            thread_id=str(thread_id or "").strip() or None,
            thread_kind=str(thread_kind or "").strip() or None,
            thread_label=str(thread_label or "").strip() or None,
            session_type=str(descriptor["session_type"]),
            platform=str(descriptor["platform"]),
            channel_id=descriptor.get("channel_id"),
            surface_id=str(descriptor.get("surface_id") or ""),
            status=str(status or "ready"),
            label=str(descriptor.get("label") or ""),
            metadata=dict(metadata or {}),
            last_activity=time.time(),
        )
        session = await brain.get_runtime_session(str(descriptor["session_key"]))
        return session or descriptor

    async def touch_session(
        self,
        *,
        session_key: str,
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
        last_activity: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_bootstrap()
        await brain.update_runtime_session(
            session_key=str(session_key or "").strip(),
            thread_id=str(thread_id or "").strip() or None,
            thread_kind=str(thread_kind or "").strip() or None,
            thread_label=str(thread_label or "").strip() or None,
            status=status,
            metadata=metadata,
            label=label,
            last_activity=float(last_activity or time.time()),
        )
        return await brain.get_runtime_session(str(session_key or "").strip())

    async def list_sessions(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 24,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        await self.ensure_bootstrap()
        return await brain.list_runtime_sessions(
            owner_user_id=int(owner_user_id),
            statuses=statuses,
            limit=max(int(limit or 24), 1),
            include_archived=bool(include_archived),
        )

    async def create_job(
        self,
        *,
        owner_user_id: int,
        runtime_session_key: str,
        job_type: str,
        subtype: str = "",
        status: str = "queued",
        request_text: str = "",
        scheduled_for: Optional[float] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.ensure_bootstrap()
        clean_job_id = str(job_id or f"job_{uuid.uuid4().hex[:16]}").strip()
        await brain.create_runtime_job(
            job_id=clean_job_id,
            owner_user_id=int(owner_user_id),
            runtime_session_key=str(runtime_session_key or "").strip(),
            job_type=str(job_type or "").strip(),
            subtype=str(subtype or "").strip(),
            status=str(status or "queued").strip(),
            request_text=str(request_text or ""),
            scheduled_for=float(scheduled_for) if scheduled_for is not None else None,
            last_transition_reason=str(last_transition_reason or "").strip() or None,
            retry_of_job_id=str(retry_of_job_id or "").strip() or None,
            replacement_job_id=str(replacement_job_id or "").strip() or None,
            source_table=str(source_table or "").strip() or None,
            source_id=str(source_id or "").strip() or None,
            metadata=dict(metadata or {}),
        )
        job = await brain.get_runtime_job(clean_job_id)
        return job or {"job_id": clean_job_id}

    async def update_job(
        self,
        *,
        job_id: str,
        status: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
        request_text: Optional[str] = None,
        scheduled_for: Optional[float] = None,
        started_at: Optional[float] = None,
        completed_at: Optional[float] = None,
        attempt_count: Optional[int] = None,
        increment_attempt: bool = False,
        last_error: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_bootstrap()
        await brain.update_runtime_job(
            job_id=str(job_id or "").strip(),
            status=status,
            runtime_session_key=runtime_session_key,
            request_text=request_text,
            scheduled_for=scheduled_for,
            started_at=started_at,
            completed_at=completed_at,
            attempt_count=attempt_count,
            increment_attempt=increment_attempt,
            last_error=last_error,
            last_transition_reason=last_transition_reason,
            retry_of_job_id=retry_of_job_id,
            replacement_job_id=replacement_job_id,
            source_table=source_table,
            source_id=source_id,
            metadata=metadata,
        )
        return await brain.get_runtime_job(str(job_id or "").strip())

    async def begin_job(self, *, job_id: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        return await self.update_job(
            job_id=job_id,
            status="running",
            started_at=time.time(),
            increment_attempt=True,
            last_error="",
            metadata=metadata,
        )

    async def complete_job(
        self,
        *,
        job_id: str,
        status: str = "completed",
        last_error: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.update_job(
            job_id=job_id,
            status=status,
            completed_at=time.time(),
            last_error=last_error,
            last_transition_reason=last_transition_reason,
            metadata=metadata,
        )

    async def list_jobs(
        self,
        *,
        owner_user_id: Optional[int] = None,
        statuses: Optional[List[str]] = None,
        job_types: Optional[List[str]] = None,
        limit: int = 32,
    ) -> List[Dict[str, Any]]:
        await self.ensure_bootstrap()
        return await brain.list_runtime_jobs(
            owner_user_id=int(owner_user_id) if owner_user_id is not None else None,
            statuses=statuses,
            job_types=job_types,
            limit=max(int(limit or 32), 1),
        )

    async def get_job(self, *, job_id: str) -> Optional[Dict[str, Any]]:
        await self.ensure_bootstrap()
        return await brain.get_runtime_job(str(job_id or "").strip())

    async def _backfill_open_reminders(self) -> None:
        reminders = await brain.list_open_scheduled_reminders(limit=256)
        for reminder in reminders:
            runtime_job_id = str(reminder.get("runtime_job_id") or "").strip()
            runtime_session_key = str(reminder.get("runtime_session_key") or "").strip()
            metadata = dict(reminder.get("metadata") or {})
            # Local import to break the cycle: core.reminders.scheduler imports
            # core.runtime_control at module top, so resolve build_runtime_binding_seed
            # lazily here. C1.4 will land the proper wired import once the
            # canonical home is established.
            from core.reminders.scheduler import build_runtime_binding_seed
            binding_seed = build_runtime_binding_seed(
                reminder_id=int(reminder.get("id") or 0),
                user_id=int(reminder.get("user_id") or 0),
                account_user_id=(
                    int(metadata.get("account_user_id"))
                    if metadata.get("account_user_id") is not None
                    else None
                ),
                platform=str(reminder.get("platform") or "local"),
                channel_id=int(reminder.get("channel_id") or 0),
                source_is_dm=(
                    bool(metadata.get("source_is_dm"))
                    if metadata.get("source_is_dm") is not None
                    else False
                ),
                surface_id=str(reminder.get("surface_id") or ""),
                scheduled_time=float(reminder.get("scheduled_time") or 0.0),
                reminder_text=str(reminder.get("reminder_text") or ""),
                kind=str(reminder.get("kind") or "reminder"),
                delivery_preference=metadata.get("delivery_preference"),
                metadata=metadata,
                current_time=time.time(),
            )
            owner_user_id = int(binding_seed.owner_user_id or 0)
            if owner_user_id <= 0:
                continue
            session = await self.ensure_surface_session(**binding_seed.session_kwargs)
            runtime_session_key = runtime_session_key or str(session.get("session_key") or "")
            if not runtime_job_id:
                job = await self.create_job(
                    runtime_session_key=runtime_session_key,
                    **binding_seed.job_kwargs,
                )
                runtime_job_id = str(job.get("job_id") or "")
            await brain.update_scheduled_reminder_runtime_link(
                reminder_id=int(reminder.get("id") or 0),
                runtime_job_id=runtime_job_id,
                runtime_session_key=runtime_session_key,
            )

runtime_control = RuntimeControlPlane()
