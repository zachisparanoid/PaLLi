from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from core.config import config
from core.database import brain
from core.local_mode import ensure_local_owner, local_mode_enabled
from core.plugin_runtime import plugin_runtime
from core.runtime_control import runtime_control
from core.runtime_recovery import runtime_recovery

logger = logging.getLogger(__name__)

_READY_STAGE_STATUSES = {"ready", "skipped"}

# Batch 8 Task 11 A2: each chat door, the config knobs that open it, the env
# owner id, and the column on the stored owner's row that proves him there.
_OWNER_ID_DOORS = (
    ("Discord", "ENABLE_DISCORD_BOT", "DISCORD_TOKEN", "LOCAL_OWNER_DISCORD_USER_ID", "discord_user_id"),
    ("Telegram", "ENABLE_TELEGRAM_BOT", "TELEGRAM_BOT_TOKEN", "LOCAL_OWNER_TELEGRAM_USER_ID", "telegram_user_id"),
)


def owner_id_gap_warnings(owner: Any) -> list[str]:
    """The real "no owner id" line, one per open door that has none anywhere.

    The config-time line (core/config_schema.py) reads .env.local only, so it
    used to say "everyone is a guest" on a box whose owner id was stored on the
    owner's row all along (seeded once and never overwritten, or linked on the
    dashboard). This stage holds that row, so it says the truth: a line only
    when the door is on (enabled AND holding a token, as the config line
    gates it), the env id is blank, AND the stored owner has no id for that
    door. A set env id is the config line's business (numeric: seeded; not
    numeric: it already names the value), so it is never said twice. With no
    owner row (the stage failed) nothing can be proved, so nothing is said.
    """
    if not isinstance(owner, dict):
        return []
    lines: list[str] = []
    for door, enable_key, token_key, env_key, stored_key in _OWNER_ID_DOORS:
        if not (getattr(config, enable_key, False) and str(getattr(config, token_key, "") or "").strip()):
            continue
        if str(getattr(config, env_key, "") or "").strip():
            continue
        if str(owner.get(stored_key) or "").strip():
            continue
        on_door = "" if door == "Discord" else f" on {door}"
        lines.append(
            f"{env_key}: {door} is on and no owner id is set in .env.local or "
            f"stored on the owner's row; everyone she meets{on_door} is a guest "
            "(no shell, no files, no memory of hers). Set it in .env.local, or "
            "leave it and run guest-only."
        )
    return lines


class BackendBootstrapCoordinator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._startup_snapshot: Dict[str, Any] = self._empty_snapshot()

    @staticmethod
    def _empty_snapshot() -> Dict[str, Any]:
        return {
            "status": "not_started",
            "started_at": None,
            "completed_at": None,
            "owner_user_id": None,
            "counts": {
                "ready": 0,
                "skipped": 0,
                "degraded": 0,
                "failed": 0,
            },
            "stages": [],
        }

    def snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self._startup_snapshot)
        snapshot["counts"] = dict(snapshot.get("counts") or {})
        snapshot["stages"] = [dict(stage) for stage in snapshot.get("stages") or []]
        return snapshot

    def health_status(self) -> str:
        status = str((self._startup_snapshot.get("status") or "not_started")).strip().lower()
        if status == "ready":
            return "healthy"
        if status == "failed":
            return "unhealthy"
        return status

    def warning_items(self) -> list[Dict[str, str]]:
        warnings: list[Dict[str, str]] = []
        for stage in self._startup_snapshot.get("stages") or []:
            stage_status = str(stage.get("status") or "").strip().lower()
            if stage_status not in {"degraded", "failed"}:
                continue
            severity = "core_blocker" if bool(stage.get("required")) or stage_status == "failed" else "warning"
            warnings.append(
                {
                    "code": f"backend_startup_{stage.get('stage_id') or 'stage'}_{stage_status}",
                    "severity": severity,
                    "message": str(stage.get("message") or ""),
                }
            )
        return warnings

    async def run_startup(self) -> Dict[str, Any]:
        async with self._lock:
            owner_user_id: Optional[int] = None
            self._startup_snapshot = self._empty_snapshot()
            self._startup_snapshot["status"] = "starting"
            self._startup_snapshot["started_at"] = time.time()

            try:
                await self._run_stage(
                    stage_id="database",
                    label="Database",
                    required=True,
                    runner=brain.initialize,
                    success_message="Database initialized.",
                    failure_message="Database initialization failed.",
                )
                await self._run_stage(
                    stage_id="plugin_runtime",
                    label="Plugin runtime",
                    required=True,
                    runner=plugin_runtime.initialize,
                    success_message="Plugin runtime initialized.",
                    failure_message="Plugin runtime initialization failed.",
                )
                await self._run_stage(
                    stage_id="runtime_control",
                    label="Runtime control",
                    required=True,
                    runner=runtime_control.ensure_bootstrap,
                    success_message="Runtime control plane ready.",
                    failure_message="Runtime control bootstrap failed.",
                )
                await self._run_stage(
                    stage_id="runtime_recovery",
                    label="Runtime recovery",
                    required=False,
                    runner=runtime_recovery.run_startup_recovery,
                    success_message="Runtime recovery complete.",
                    failure_message="Runtime recovery failed; backend remains available in degraded mode.",
                )

                if local_mode_enabled():
                    owner = await self._run_stage(
                        stage_id="local_owner",
                        label="Local owner",
                        required=False,
                        runner=ensure_local_owner,
                        success_message="Local owner ready.",
                        failure_message="Local owner bootstrap failed; backend remains available in degraded mode.",
                    )
                    owner_user_id = self._coerce_owner_user_id(owner)
                    self._startup_snapshot["owner_user_id"] = owner_user_id
                    # A2: said out loud, never a stage verdict — a guest-only
                    # box is a legitimate way to run her, so the stage stays
                    # "ready".
                    for line in owner_id_gap_warnings(owner):
                        logger.warning("%s", line)
                else:
                    self._record_stage(
                        stage_id="local_owner",
                        label="Local owner",
                        required=False,
                        status="skipped",
                        message="Local owner bootstrap skipped outside local mode.",
                    )

                await self._run_stage(
                    stage_id="runtime_services",
                    label="Runtime services",
                    required=False,
                    runner=lambda: plugin_runtime.start_runtime_services(owner_user_id=owner_user_id),
                    success_message="Plugin runtime services ready.",
                    failure_message="Plugin runtime services failed to start; backend remains available in degraded mode.",
                )

            except Exception:
                self._finalize_snapshot()
                raise

            self._finalize_snapshot()
            return self.snapshot()

    async def _run_stage(
        self,
        *,
        stage_id: str,
        label: str,
        required: bool,
        runner: Callable[[], Awaitable[Any]],
        success_message: str,
        failure_message: str,
    ) -> Any:
        started_at = time.time()
        try:
            result = await runner()
        except Exception as exc:
            duration_ms = round((time.time() - started_at) * 1000.0, 3)
            status = "failed" if required else "degraded"
            error_text = f"{type(exc).__name__}: {exc}"
            self._record_stage(
                stage_id=stage_id,
                label=label,
                required=required,
                status=status,
                message=f"{failure_message} ({error_text})",
                error_type=type(exc).__name__,
                error=error_text,
                duration_ms=duration_ms,
            )
            if required:
                logger.error("%s", failure_message, exc_info=True)
                raise
            logger.error("%s", failure_message, exc_info=True)
            return None

        duration_ms = round((time.time() - started_at) * 1000.0, 3)
        message = self._success_message(stage_id=stage_id, default_message=success_message, result=result)
        self._record_stage(
            stage_id=stage_id,
            label=label,
            required=required,
            status="ready",
            message=message,
            duration_ms=duration_ms,
        )
        logger.info("%s", message)
        return result

    @staticmethod
    def _coerce_owner_user_id(owner: Any) -> Optional[int]:
        if not isinstance(owner, dict):
            return None
        raw_owner_id = owner.get("id")
        try:
            owner_id = int(raw_owner_id or 0)
        except Exception:
            return None
        return owner_id or None

    @staticmethod
    def _success_message(*, stage_id: str, default_message: str, result: Any) -> str:
        if stage_id != "local_owner" or not isinstance(result, dict):
            return default_message
        email = str(result.get("email") or "").strip()
        if email:
            return f"{default_message.rstrip('.')} ({email})."
        return default_message

    def _record_stage(
        self,
        *,
        stage_id: str,
        label: str,
        required: bool,
        status: str,
        message: str,
        error_type: str = "",
        error: str = "",
        duration_ms: Optional[float] = None,
    ) -> None:
        stages = list(self._startup_snapshot.get("stages") or [])
        stages.append(
            {
                "stage_id": stage_id,
                "label": label,
                "required": bool(required),
                "status": status,
                "message": message,
                "error_type": error_type,
                "error": error,
                "duration_ms": duration_ms,
            }
        )
        self._startup_snapshot["stages"] = stages

    def _finalize_snapshot(self) -> None:
        stages = list(self._startup_snapshot.get("stages") or [])
        counts = {
            "ready": 0,
            "skipped": 0,
            "degraded": 0,
            "failed": 0,
        }
        for stage in stages:
            stage_status = str(stage.get("status") or "").strip().lower()
            if stage_status in counts:
                counts[stage_status] += 1
        self._startup_snapshot["counts"] = counts
        self._startup_snapshot["completed_at"] = time.time()
        if counts["failed"]:
            self._startup_snapshot["status"] = "failed"
        elif counts["degraded"]:
            self._startup_snapshot["status"] = "degraded"
        elif stages and all(str(stage.get("status") or "").strip().lower() in _READY_STAGE_STATUSES for stage in stages):
            self._startup_snapshot["status"] = "ready"
        else:
            self._startup_snapshot["status"] = "not_started"


backend_bootstrap = BackendBootstrapCoordinator()
