from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from core.database import brain
from core.runtime_control import runtime_control

logger = logging.getLogger(__name__)

_ACTIVE_RUNTIME_SESSION_STATUSES = {"active", "running", "waiting", "listening"}


def _voice_runtime_snapshots() -> List[Dict[str, Any]]:
    """Live voice-runtime registrations, or [] when the voice runtime is
    parked/absent.

    Great Excision (2026-07-09): the voice tree stays on disk (owner
    deferred the voice decision) but the boot path must not hard-import it.
    The in-process registry is empty whenever the runtime has no entry
    point — exactly today's live behavior — so the lazy import degrades to
    the same result if voice/ is ever excised later."""
    try:
        from voice.runtime import list_registered_voice_runtime_snapshots
    except Exception:
        return []
    try:
        return list(list_registered_voice_runtime_snapshots() or [])
    except Exception as exc:
        logger.debug("runtime_recovery: voice snapshot read failed: %s", exc)
        return []


class RuntimeRecoveryCoordinator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._completed = False

    @staticmethod
    def _recovery_metadata(*, attempt_count: Optional[int] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "recovered_after_restart": True,
            "recovery_source": "startup",
        }
        if attempt_count is not None:
            payload["recovery_attempt_count"] = int(attempt_count)
        return payload

    async def run_startup_recovery(self) -> None:
        if self._completed:
            return
        async with self._lock:
            if self._completed:
                return
            await self._recover_voice_sessions()
            await self._recover_runtime_sessions()
            await self._recover_deferred_jobs()
            await self._recover_internal_jobs()
            self._completed = True

    async def _recover_voice_sessions(self) -> None:
        live_snapshots = _voice_runtime_snapshots()
        live_session_keys = {
            str(snapshot.get("runtime_session_key") or "").strip()
            for snapshot in live_snapshots
            if str(snapshot.get("runtime_session_key") or "").strip()
        }
        live_channel_keys = {
            (
                int(snapshot.get("channel_id") or 0),
                str(snapshot.get("platform") or "discord").strip().lower(),
            )
            for snapshot in live_snapshots
        }

        for persisted in await brain.list_open_voice_sessions(limit=128):
            channel_id = int(persisted.get("channel_id") or 0)
            platform = str(persisted.get("platform") or "discord").strip().lower() or "discord"
            runtime_session_key = str(persisted.get("runtime_session_key") or "").strip()
            if (channel_id, platform) not in live_channel_keys:
                await brain.close_voice_session(session_id=int(persisted.get("id") or 0), end_time=time.time())
                if runtime_session_key:
                    await runtime_control.touch_session(
                        session_key=runtime_session_key,
                        status="ready",
                        metadata=self._recovery_metadata(),
                    )

        for session_key in live_session_keys:
            await runtime_control.touch_session(
                session_key=session_key,
                status="active",
                metadata={"recovered_after_restart": False},
            )

    async def _recover_runtime_sessions(self) -> None:
        live_voice_session_keys = {
            str(snapshot.get("runtime_session_key") or "").strip()
            for snapshot in _voice_runtime_snapshots()
            if str(snapshot.get("runtime_session_key") or "").strip()
        }
        rows = await brain.list_runtime_sessions(
            statuses=list(_ACTIVE_RUNTIME_SESSION_STATUSES),
            session_types=["maintenance", "local_voice", "discord_voice"],
            limit=512,
            include_archived=False,
        )

        for row in rows:
            session_key = str(row.get("session_key") or "").strip()
            session_type = str(row.get("session_type") or "").strip().lower()
            if not session_key:
                continue
            if session_type in {"local_voice", "discord_voice"}:
                if session_key in live_voice_session_keys:
                    continue
                await runtime_control.touch_session(
                    session_key=session_key,
                    status="ready",
                    metadata=self._recovery_metadata(),
                )
                continue
            await runtime_control.touch_session(
                session_key=session_key,
                status="ready",
                metadata=self._recovery_metadata(),
            )

    async def _recover_deferred_jobs(self) -> None:
        now = time.time()
        jobs = await runtime_control.list_jobs(
            statuses=["running", "waiting"],
            job_types=["deferred_action"],
            limit=256,
        )
        for job in jobs:
            job_id = str(job.get("job_id") or "").strip()
            source_table = str(job.get("source_table") or "").strip().lower()
            source_id = str(job.get("source_id") or "").strip()
            if not job_id or source_table != "scheduled_reminders" or not source_id:
                continue
            reminder = await brain.get_scheduled_reminder_record(int(source_id))
            if reminder and not bool(reminder.get("is_completed")):
                next_status = "queued" if float(reminder.get("scheduled_time") or 0.0) <= now else "scheduled"
                await runtime_control.update_job(
                    job_id=job_id,
                    status=next_status,
                    completed_at=None,
                    last_error="",
                    last_transition_reason="recovered_after_restart",
                    metadata=self._recovery_metadata(
                        attempt_count=int((job.get("metadata") or {}).get("recovery_attempt_count") or 0)
                    ),
                )
            else:
                await runtime_control.complete_job(
                    job_id=job_id,
                    status="completed",
                    last_transition_reason="recovered_after_restart",
                    metadata=self._recovery_metadata(
                        attempt_count=int((job.get("metadata") or {}).get("recovery_attempt_count") or 0)
                    ),
                )

    async def _recover_internal_jobs(self) -> None:
        jobs = await runtime_control.list_jobs(
            statuses=["running", "waiting"],
            job_types=["reflection", "proactive_check", "proactive_message"],
            limit=256,
        )
        touched_sessions: Set[str] = set()
        for job in jobs:
            job_id = str(job.get("job_id") or "").strip()
            if not job_id:
                continue
            job_type = str(job.get("job_type") or "").strip().lower()
            runtime_session_key = str(job.get("runtime_session_key") or "").strip()
            next_status = "failed" if job_type == "proactive_message" else "skipped"
            next_error = (
                "Recovered stale proactive delivery after restart."
                if job_type == "proactive_message"
                else "Recovered stale maintenance job after restart."
            )
            await runtime_control.complete_job(
                job_id=job_id,
                status=next_status,
                last_error=next_error,
                last_transition_reason="recovered_after_restart",
                metadata=self._recovery_metadata(
                    attempt_count=int((job.get("metadata") or {}).get("recovery_attempt_count") or 0)
                ),
            )
            if runtime_session_key and runtime_session_key not in touched_sessions:
                touched_sessions.add(runtime_session_key)
                await runtime_control.touch_session(
                    session_key=runtime_session_key,
                    status="ready",
                    metadata=self._recovery_metadata(),
                )


runtime_recovery = RuntimeRecoveryCoordinator()
