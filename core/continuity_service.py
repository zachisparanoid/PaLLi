from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.database import brain
from core.surface_ids import normalize_surface_id, session_surface_suffix

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_THREAD_CONTROL_RESET_RE = re.compile(r"^\s*(reset this conversation|new thread)\s*$", re.IGNORECASE)
_THREAD_CONTROL_ARCHIVE_RE = re.compile(r"^\s*archive this thread\s*$", re.IGNORECASE)
_THREAD_CONTROL_STATUS_RE = re.compile(r"^\s*what thread is this\??\s*$", re.IGNORECASE)


def _slugify(text: str) -> str:
    clean = _SLUG_RE.sub("-", str(text or "").strip().lower()).strip("-")
    return clean or "thread"


def _workspace_slug(workspace_root: str) -> str:
    root = str(workspace_root or "").strip()
    path = Path(root)
    base = _slugify(path.name or path.as_posix().split("/")[-1] or "workspace")
    suffix = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{suffix}"


class ContinuityService:
    def __init__(self) -> None:
        self._bootstrap_lock = asyncio.Lock()
        self._bootstrapped_owner_ids: set[int] = set()
        self._bootstrap_tasks: dict[int, asyncio.Task[None]] = {}

    @staticmethod
    def owner_main_thread_base(owner_user_id: int) -> str:
        return f"owner:{int(owner_user_id)}:thread:main"

    @staticmethod
    def maintenance_thread_base(owner_user_id: int) -> str:
        return f"owner:{int(owner_user_id)}:thread:maintenance"

    @staticmethod
    def channel_thread_base(owner_user_id: int, platform: str, channel_id: Optional[int], surface_id: Optional[str] = None) -> str:
        clean_platform = str(platform or "local").strip().lower() or "local"
        suffix = session_surface_suffix(surface_id, channel_id=channel_id)
        return f"owner:{int(owner_user_id)}:thread:{clean_platform}:channel:{suffix}"

    @staticmethod
    def workspace_thread_base(owner_user_id: int, workspace_root: str) -> str:
        return f"owner:{int(owner_user_id)}:thread:workspace:{_workspace_slug(workspace_root)}"

    @staticmethod
    def _thread_id_for_reset(base_thread_id: str, reset_count: int) -> str:
        if int(reset_count or 0) <= 0:
            return str(base_thread_id or "").strip()
        return f"{str(base_thread_id or '').strip()}:r{int(reset_count)}"

    @staticmethod
    def _owner_shared_surface(
        *,
        platform: str,
        is_dm: bool,
        voice: bool,
        maintenance: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        runtime_metadata = dict(metadata or {})
        if bool(runtime_metadata.get("probe_isolated")):
            return False
        if maintenance:
            return False
        if str(platform or "").strip().lower() == "local":
            return True
        if is_dm:
            return True
        if voice and str(platform or "").strip().lower() == "local":
            return True
        return False

    async def _ensure_owner_bootstrap(self, *, owner_user_id: int) -> None:
        owner_id = int(owner_user_id)
        if owner_id in self._bootstrapped_owner_ids:
            return
        current_task = asyncio.current_task()
        bootstrap_task: asyncio.Task[None] | None = None

        async with self._bootstrap_lock:
            if owner_id in self._bootstrapped_owner_ids:
                return
            bootstrap_task = self._bootstrap_tasks.get(owner_id)
            if bootstrap_task is None or bootstrap_task.done():
                bootstrap_task = asyncio.create_task(
                    self._run_owner_bootstrap(owner_id),
                    name=f"continuity-bootstrap-{owner_id}",
                )
                self._bootstrap_tasks[owner_id] = bootstrap_task

        if bootstrap_task is current_task:
            return

        await bootstrap_task

    async def _run_owner_bootstrap(self, owner_user_id: int) -> None:
        owner_id = int(owner_user_id)
        try:
            await self._backfill_owner(owner_user_id=owner_id)
            async with self._bootstrap_lock:
                self._bootstrapped_owner_ids.add(owner_id)
        finally:
            async with self._bootstrap_lock:
                if self._bootstrap_tasks.get(owner_id) is asyncio.current_task():
                    self._bootstrap_tasks.pop(owner_id, None)

    async def _ensure_thread(
        self,
        *,
        owner_user_id: int,
        thread_kind: str,
        scope_key: str,
        base_thread_id: str,
        label: str,
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        active = await brain.find_continuity_thread_by_scope(
            owner_user_id=int(owner_user_id),
            thread_kind=str(thread_kind or "").strip(),
            scope_key=str(scope_key or "").strip(),
            include_archived=False,
        )
        merged_metadata = {"scope_key": str(scope_key or "").strip(), **dict(metadata or {})}
        if active:
            await brain.update_continuity_thread(
                thread_id=str(active.get("thread_id") or ""),
                label=str(label or "").strip() or None,
                workspace_root=str(workspace_root or "").strip() or None,
                workspace_label=str(workspace_label or "").strip() or None,
                last_activity=time.time(),
                metadata=merged_metadata,
            )
            refreshed = await brain.get_continuity_thread(str(active.get("thread_id") or ""))
            return refreshed or active

        prior = await brain.find_continuity_thread_by_scope(
            owner_user_id=int(owner_user_id),
            thread_kind=str(thread_kind or "").strip(),
            scope_key=str(scope_key or "").strip(),
            include_archived=True,
        )
        next_reset_count = int((prior or {}).get("reset_count") or 0) + (1 if prior else 0)
        thread_id = self._thread_id_for_reset(base_thread_id, next_reset_count if prior else 0)
        await brain.upsert_continuity_thread(
            thread_id=thread_id,
            owner_user_id=int(owner_user_id),
            thread_kind=str(thread_kind or "").strip(),
            status="active",
            label=str(label or "").strip(),
            workspace_root=str(workspace_root or "").strip() or None,
            workspace_label=str(workspace_label or "").strip() or None,
            root_agent_session_key=thread_id,
            reset_count=next_reset_count if prior else 0,
            last_activity=time.time(),
            metadata=merged_metadata,
        )
        return (await brain.get_continuity_thread(thread_id)) or {
            "thread_id": thread_id,
            "owner_user_id": int(owner_user_id),
            "thread_kind": str(thread_kind or "").strip(),
            "status": "active",
            "label": str(label or "").strip(),
            "workspace_root": str(workspace_root or "").strip(),
            "workspace_label": str(workspace_label or "").strip(),
            "metadata": merged_metadata,
        }

    async def resolve_surface_thread(
        self,
        *,
        owner_user_id: int,
        platform: str,
        channel_id: Optional[int] = None,
        surface_id: Optional[str] = None,
        is_dm: bool = False,
        voice: bool = False,
        maintenance: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        owner_id = int(owner_user_id)
        await self._ensure_owner_bootstrap(owner_user_id=owner_id)
        return await self._resolve_surface_thread_without_bootstrap(
            owner_user_id=owner_id,
            platform=platform,
            channel_id=channel_id,
            surface_id=surface_id,
            is_dm=is_dm,
            voice=voice,
            maintenance=maintenance,
            metadata=metadata,
        )

    async def _resolve_surface_thread_without_bootstrap(
        self,
        *,
        owner_user_id: int,
        platform: str,
        channel_id: Optional[int] = None,
        surface_id: Optional[str] = None,
        is_dm: bool = False,
        voice: bool = False,
        maintenance: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        owner_id = int(owner_user_id)
        clean_platform = str(platform or "local").strip().lower() or "local"
        clean_channel_id = int(channel_id) if channel_id is not None else None
        clean_surface_id = normalize_surface_id(surface_id, channel_id=channel_id)
        runtime_metadata = dict(metadata or {})

        if maintenance:
            return await self._ensure_thread(
                owner_user_id=owner_id,
                thread_kind="maintenance",
                scope_key="maintenance",
                base_thread_id=self.maintenance_thread_base(owner_id),
                label="Maintenance",
                metadata={"maintenance": True},
            )
        if self._owner_shared_surface(
            platform=clean_platform,
            is_dm=is_dm,
            voice=voice,
            maintenance=maintenance,
            metadata=runtime_metadata,
        ):
            return await self._ensure_thread(
                owner_user_id=owner_id,
                thread_kind="owner_main",
                scope_key="owner_main",
                base_thread_id=self.owner_main_thread_base(owner_id),
                label="Main Thread",
                metadata={"platform": clean_platform, "is_dm": bool(is_dm), "voice": bool(voice)},
            )

        channel_metadata = {
            "platform": clean_platform,
            "channel_id": clean_channel_id,
            "surface_id": clean_surface_id,
            "is_dm": bool(is_dm),
            "voice": bool(voice),
        }
        if bool(runtime_metadata.get("probe")):
            channel_metadata["probe"] = True
        if bool(runtime_metadata.get("probe_isolated")):
            channel_metadata["probe_isolated"] = True
        probe_lane_id = str(runtime_metadata.get("probe_lane_id") or "").strip()
        if probe_lane_id:
            channel_metadata["probe_lane_id"] = probe_lane_id
        label = f"{clean_platform.title()} Channel {clean_surface_id or int(clean_channel_id or 0)}"
        if bool(runtime_metadata.get("probe_isolated")):
            label = f"Local Probe {clean_surface_id or int(clean_channel_id or 0)}"
        return await self._ensure_thread(
            owner_user_id=owner_id,
            thread_kind="channel_conversation",
            scope_key=f"{clean_platform}:channel:{clean_surface_id or int(clean_channel_id or 0)}",
            base_thread_id=self.channel_thread_base(owner_id, clean_platform, clean_channel_id, clean_surface_id),
            label=label,
            metadata=channel_metadata,
        )


    async def bind_runtime_session(
        self,
        *,
        session_key: str,
        thread: Dict[str, Any],
        last_activity: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        clean_session_key = str(session_key or "").strip()
        if not clean_session_key:
            return None
        await brain.update_runtime_session(
            session_key=clean_session_key,
            thread_id=str(thread.get("thread_id") or "").strip() or None,
            thread_kind=str(thread.get("thread_kind") or "").strip() or None,
            thread_label=str(thread.get("label") or "").strip() or None,
            last_activity=float(last_activity) if last_activity is not None else time.time(),
        )
        await brain.update_continuity_thread(
            thread_id=str(thread.get("thread_id") or ""),
            last_runtime_session_key=clean_session_key,
            last_activity=float(last_activity) if last_activity is not None else time.time(),
        )
        return await brain.get_runtime_session(clean_session_key)

    # Great Excision (2026-07-09): touch_workspace / list_recent_workspaces
    # deleted with the operator workspace store — their only callers were
    # this service's own operator-era bootstrap backfill (also removed) and
    # the deleted operator engine. workspace_thread_base and the
    # workspace_lane branch in _base_thread_id_from_scope stay: 478 legacy
    # workspace_lane rows exist in continuity_threads and reset/describe
    # still operate on them.

    async def list_threads(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 16,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        await self._ensure_owner_bootstrap(owner_user_id=int(owner_user_id))
        return await brain.list_continuity_threads(
            owner_user_id=int(owner_user_id),
            statuses=statuses,
            limit=max(int(limit or 16), 1),
            include_archived=bool(include_archived),
        )

    async def get_current_thread(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        threads = await self.list_threads(owner_user_id=int(owner_user_id), limit=1)
        return threads[0] if threads else None

    async def reset_thread(self, *, owner_user_id: int, thread_id: str) -> Optional[Dict[str, Any]]:
        current = await brain.get_continuity_thread(str(thread_id or "").strip())
        if not current:
            return None
        now = time.time()
        await brain.update_continuity_thread(
            thread_id=str(current.get("thread_id") or ""),
            status="archived",
            archived_at=now,
            last_activity=now,
        )
        metadata = dict(current.get("metadata") or {})
        scope_key = str(metadata.get("scope_key") or "").strip()
        return await self._ensure_thread(
            owner_user_id=int(owner_user_id),
            thread_kind=str(current.get("thread_kind") or ""),
            scope_key=scope_key,
            base_thread_id=self._base_thread_id_from_scope(
                owner_user_id=int(owner_user_id),
                thread_kind=str(current.get("thread_kind") or ""),
                current=current,
            ),
            label=str(current.get("label") or ""),
            workspace_root=str(current.get("workspace_root") or "").strip() or None,
            workspace_label=str(current.get("workspace_label") or "").strip() or None,
            metadata=metadata,
        )

    async def archive_thread(self, *, thread_id: str) -> Optional[Dict[str, Any]]:
        current = await brain.get_continuity_thread(str(thread_id or "").strip())
        if not current:
            return None
        now = time.time()
        await brain.update_continuity_thread(
            thread_id=str(current.get("thread_id") or ""),
            status="archived",
            archived_at=now,
            last_activity=now,
        )
        return await brain.get_continuity_thread(str(thread_id or "").strip())

    def parse_thread_control(self, message: str) -> Optional[str]:
        if _THREAD_CONTROL_RESET_RE.match(str(message or "")):
            return "reset"
        if _THREAD_CONTROL_ARCHIVE_RE.match(str(message or "")):
            return "archive"
        if _THREAD_CONTROL_STATUS_RE.match(str(message or "")):
            return "status"
        return None

    def describe_thread(self, thread: Optional[Dict[str, Any]]) -> str:
        if not thread:
            return "I don’t have a continuity thread bound here yet."
        label = str(thread.get("label") or thread.get("thread_kind") or "thread").strip() or "thread"
        thread_id = str(thread.get("thread_id") or "").strip()
        workspace_root = str(thread.get("workspace_root") or "").strip()
        detail = f"This is `{label}`"
        if thread_id:
            detail += f" (`{thread_id}`)"
        if workspace_root:
            detail += f", currently bound to `{workspace_root}`"
        detail += "."
        return detail

    async def _backfill_owner(self, *, owner_user_id: int) -> None:
        owner_id = int(owner_user_id)
        try:
            sessions = await brain.list_runtime_sessions(owner_user_id=owner_id, limit=128, include_archived=False)
            for session in sessions:
                if str(session.get("thread_id") or "").strip():
                    continue
                session_type = str(session.get("session_type") or "").strip().lower()
                platform = str(session.get("platform") or "local").strip().lower() or "local"
                is_dm = session_type.endswith("_dm")
                voice = "voice" in session_type
                maintenance = session_type == "maintenance"
                thread = await self._resolve_surface_thread_without_bootstrap(
                    owner_user_id=owner_id,
                    platform=platform,
                    channel_id=session.get("channel_id"),
                    is_dm=is_dm,
                    voice=voice,
                    maintenance=maintenance,
                )
                await self.bind_runtime_session(
                    session_key=str(session.get("session_key") or ""),
                    thread=thread,
                    last_activity=float(session.get("last_activity") or time.time()),
                )
                try:
                    await brain.backfill_conversation_thread_id(
                        platform=platform,
                        channel_id=int(session.get("channel_id") or 0),
                        thread_id=str(thread.get("thread_id") or ""),
                    )
                except Exception:
                    logger.debug("Failed to backfill conversation thread ids for %s:%s", platform, session.get("channel_id"), exc_info=True)
        except Exception:
            logger.debug("Continuity bootstrap failed for owner %s", owner_id, exc_info=True)

    def _base_thread_id_from_scope(
        self,
        *,
        owner_user_id: int,
        thread_kind: str,
        current: Dict[str, Any],
    ) -> str:
        kind = str(thread_kind or "").strip()
        if kind == "owner_main":
            return self.owner_main_thread_base(owner_user_id)
        if kind == "maintenance":
            return self.maintenance_thread_base(owner_user_id)
        if kind == "workspace_lane":
            return self.workspace_thread_base(owner_user_id, str(current.get("workspace_root") or "workspace"))
        metadata = dict(current.get("metadata") or {})
        platform = str(metadata.get("platform") or "local").strip().lower() or "local"
        channel_id = metadata.get("channel_id")
        surface_id = metadata.get("surface_id")
        return self.channel_thread_base(owner_user_id, platform, channel_id, surface_id)


continuity_service = ContinuityService()
