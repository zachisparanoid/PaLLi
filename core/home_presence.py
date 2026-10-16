from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from core.ambient_service import ambient_service
from core.config import config
from core.database import brain
from core.host_platform import host_platform_service
from core.identity import identity_service
from core.identity.policy_access import policy_value
from core.runtime_control import maintenance_session_key, runtime_control

logger = logging.getLogger(__name__)

SIMULATABLE_CONTEXT_EVENTS = {
    "owner_arrived",
    "owner_departed",
    "machine_locked",
    "machine_unlocked",
    "machine_active",
    "machine_idle",
    "privacy_private",
    "privacy_shared",
}


@dataclass(slots=True)
class SourceHealth:
    status: str
    message: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "updated_at": float(self.updated_at or 0.0),
        }


async def _record_health_best_effort(service, **kwargs) -> None:
    """The health write must never kill the loop that reports health.

    Observed live: a transient "database is locked" raised INSIDE the
    except handler's degraded-status write, unwound ``_run``, and the
    presence task died silently until the next restart. Best-effort by
    design: presence keeps running even when its own heartbeat ledger
    is briefly unwritable.
    """
    try:
        await service.record_source_health(**kwargs)
    except Exception as exc:
        logger.warning("presence: health write failed (loop continues): %s", exc)


class HomeAssistantContextSource:
    def __init__(self, service: "HomePresenceService", owner_user_id: int) -> None:
        self.service = service
        self.owner_user_id = int(owner_user_id)
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False
        self._owner_is_home: Optional[bool] = None
        self._privacy_state: Optional[str] = None
        self._reconcile_every = max(10.0, float(getattr(config, "HOME_CONTEXT_RECONCILE_SECONDS", 120.0) or 120.0))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="home-assistant-context-source")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        backoff_seconds = 5.0
        while self._running:
            try:
                await self._reconcile_snapshot(bootstrap=True)
                await self.service.record_source_health(
                    owner_user_id=self.owner_user_id,
                    source_name="home_assistant",
                    status="ready",
                    message="connected",
                    emit_event=False,
                )
                await self._stream_events()
                backoff_seconds = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _record_health_best_effort(
                    self.service,
                    owner_user_id=self.owner_user_id,
                    source_name="home_assistant",
                    status="degraded",
                    message=str(exc),
                    emit_event=True,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2.0, 60.0)

    async def _stream_events(self) -> None:
        ws_url = self._websocket_url()
        ssl_context = None if bool(getattr(config, "HOME_ASSISTANT_VERIFY_SSL", True)) else False
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, ssl=ssl_context, heartbeat=30) as ws:
                auth_request = await ws.receive_json()
                if auth_request.get("type") != "auth_required":
                    raise RuntimeError(f"unexpected_home_assistant_auth_state:{auth_request.get('type')}")
                await ws.send_json({"type": "auth", "access_token": str(getattr(config, "HOME_ASSISTANT_TOKEN", ""))})
                auth_response = await ws.receive_json()
                if auth_response.get("type") != "auth_ok":
                    raise RuntimeError(f"home_assistant_auth_failed:{auth_response}")
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                subscribe_response = await ws.receive_json()
                if not subscribe_response.get("success", False):
                    raise RuntimeError(f"home_assistant_subscribe_failed:{subscribe_response}")

                last_reconcile = time.monotonic()
                await self.service.record_source_health(
                    owner_user_id=self.owner_user_id,
                    source_name="home_assistant",
                    status="ready",
                    message="streaming",
                    emit_event=True,
                )
                while self._running:
                    timeout = max(5.0, self._reconcile_every - (time.monotonic() - last_reconcile))
                    message = await ws.receive(timeout=timeout)
                    if message.type == aiohttp.WSMsgType.TEXT:
                        payload = {}
                        if isinstance(message.data, str) and message.data.strip():
                            payload = json.loads(message.data)
                        await self._handle_ws_payload(payload)
                    elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                        raise RuntimeError("home_assistant_websocket_closed")
                    if (time.monotonic() - last_reconcile) >= self._reconcile_every:
                        await self._reconcile_snapshot(bootstrap=False)
                        last_reconcile = time.monotonic()

    async def _handle_ws_payload(self, payload: Dict[str, Any]) -> None:
        if str(payload.get("type") or "").strip().lower() != "event":
            return
        event = payload.get("event") or {}
        data = event.get("data") or {}
        entity_id = str(data.get("entity_id") or "").strip()
        if not entity_id:
            return
        watched = set(getattr(config, "HOME_ASSISTANT_OWNER_ENTITY_IDS", []) or []) | set(
            getattr(config, "HOME_ASSISTANT_PRIVATE_ENTITY_IDS", []) or []
        ) | set(getattr(config, "HOME_ASSISTANT_SHARED_ENTITY_IDS", []) or [])
        if entity_id not in watched:
            return
        await self._reconcile_snapshot(bootstrap=False)

    async def _reconcile_snapshot(self, *, bootstrap: bool) -> None:
        snapshot = await self._fetch_relevant_states()
        owner_is_home = self._compute_owner_is_home(snapshot)
        privacy_state = self._compute_privacy_state(snapshot)

        if owner_is_home is not None and owner_is_home != self._owner_is_home:
            event_type = "owner_arrived" if owner_is_home else "owner_departed"
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="home_assistant",
                event_type=event_type,
                payload={
                    "entity_states": snapshot,
                    "owner_home": owner_is_home,
                },
                metadata={"bootstrap": bootstrap},
                dedupe_key=f"ha:{event_type}:{int(owner_is_home)}",
            )
            self._owner_is_home = owner_is_home
        elif self._owner_is_home is None:
            self._owner_is_home = owner_is_home

        if privacy_state and privacy_state != self._privacy_state:
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="home_assistant",
                event_type=f"privacy_{privacy_state}",
                payload={"entity_states": snapshot, "privacy_state": privacy_state},
                metadata={"bootstrap": bootstrap},
                dedupe_key=f"ha:privacy:{privacy_state}",
            )
            self._privacy_state = privacy_state
        elif self._privacy_state is None:
            self._privacy_state = privacy_state

    async def _fetch_relevant_states(self) -> Dict[str, str]:
        states: Dict[str, str] = {}
        entity_ids = []
        entity_ids.extend(list(getattr(config, "HOME_ASSISTANT_OWNER_ENTITY_IDS", []) or []))
        entity_ids.extend(list(getattr(config, "HOME_ASSISTANT_PRIVATE_ENTITY_IDS", []) or []))
        entity_ids.extend(list(getattr(config, "HOME_ASSISTANT_SHARED_ENTITY_IDS", []) or []))
        if not entity_ids:
            return states
        ssl_context: ssl.SSLContext | bool | None = None if bool(getattr(config, "HOME_ASSISTANT_VERIFY_SSL", True)) else False
        headers = {"Authorization": f"Bearer {str(getattr(config, 'HOME_ASSISTANT_TOKEN', ''))}", "Content-Type": "application/json"}
        base_url = str(getattr(config, "HOME_ASSISTANT_URL", "") or "").rstrip("/")
        async with aiohttp.ClientSession(headers=headers) as session:
            for entity_id in entity_ids:
                try:
                    async with session.get(f"{base_url}/api/states/{entity_id}", ssl=ssl_context) as response:
                        if response.status != 200:
                            continue
                        payload = await response.json()
                        states[entity_id] = str(payload.get("state") or "").strip().lower()
                except Exception:
                    logger.debug("Failed to fetch Home Assistant state for %s", entity_id, exc_info=True)
        return states

    def _websocket_url(self) -> str:
        parsed = urlparse(str(getattr(config, "HOME_ASSISTANT_URL", "") or "").strip())
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/api/websocket"

    @staticmethod
    def _state_is_present(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"home", "on", "present", "occupied", "open", "true"}

    def _compute_owner_is_home(self, states: Dict[str, str]) -> Optional[bool]:
        owner_entities = list(getattr(config, "HOME_ASSISTANT_OWNER_ENTITY_IDS", []) or [])
        if not owner_entities:
            return None
        return any(self._state_is_present(states.get(entity_id, "")) for entity_id in owner_entities)

    def _compute_privacy_state(self, states: Dict[str, str]) -> str:
        shared_entities = list(getattr(config, "HOME_ASSISTANT_SHARED_ENTITY_IDS", []) or [])
        private_entities = list(getattr(config, "HOME_ASSISTANT_PRIVATE_ENTITY_IDS", []) or [])
        if any(self._state_is_present(states.get(entity_id, "")) for entity_id in shared_entities):
            return "shared"
        if any(self._state_is_present(states.get(entity_id, "")) for entity_id in private_entities):
            return "private"
        return "unknown"


class MachinePresenceSource:
    def __init__(self, service: "HomePresenceService", owner_user_id: int) -> None:
        self.service = service
        self.owner_user_id = int(owner_user_id)
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False
        self._last_state: Optional[str] = None
        # Wall-clock time at which the current away period began — i.e. the
        # timestamp of the most recent `active → non-active` transition.
        # Held across `idle`/`locked` hops so the bridge can compute total
        # continuous away time instead of just time in the final hop. Cleared
        # to None whenever the machine returns to `active`.
        self._away_started_at: Optional[float] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="machine-presence-source")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        backend = host_platform_service.backend()
        if not backend.supports_machine_presence():
            await _record_health_best_effort(
                self.service,
                owner_user_id=self.owner_user_id,
                source_name="machine",
                status="unsupported",
                message=f"machine presence is unavailable on host backend {host_platform_service.platform_snapshot().get('host_backend')}",
                emit_event=False,
            )
            return
        poll_seconds = max(2.0, float(getattr(config, "LOCAL_MACHINE_PRESENCE_POLL_SECONDS", 10.0) or 10.0))
        while self._running:
            try:
                state = await self._detect_state()
                await self.service.record_source_health(
                    owner_user_id=self.owner_user_id,
                    source_name="machine",
                    status="ready",
                    message="ok",
                    emit_event=False,
                )
                await self._emit_state_changes(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _record_health_best_effort(
                    self.service,
                    owner_user_id=self.owner_user_id,
                    source_name="machine",
                    status="degraded",
                    message=str(exc),
                    emit_event=True,
                )
            await asyncio.sleep(poll_seconds)

    async def _emit_state_changes(self, state: str) -> None:
        bootstrap = self._last_state is None
        if state == self._last_state and not bootstrap:
            return
        prior_state = self._last_state
        away_started_at = self._away_started_at
        if self._last_state == "locked" and state in {"idle", "active"}:
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="machine",
                event_type="machine_unlocked",
                payload={"machine_state": state},
                metadata={"bootstrap": bootstrap},
                dedupe_key=f"machine:unlock:{state}",
            )
        if state == "locked":
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="machine",
                event_type="machine_locked",
                payload={"machine_state": state},
                metadata={"bootstrap": bootstrap},
                dedupe_key="machine:locked",
            )
        elif state == "idle":
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="machine",
                event_type="machine_idle",
                payload={"machine_state": state},
                metadata={"bootstrap": bootstrap},
                dedupe_key="machine:idle",
            )
        elif state == "active":
            await self.service.ingest_event(
                owner_user_id=self.owner_user_id,
                source="machine",
                event_type="machine_active",
                payload={"machine_state": state},
                metadata={"bootstrap": bootstrap},
                dedupe_key="machine:active",
            )
        await self._derive_owner_arrival_if_needed(
            prior_state=prior_state,
            new_state=state,
            away_started_at=away_started_at,
            bootstrap=bootstrap,
        )
        # Update the away clock. It starts when we leave the active state and
        # stays put across any number of hops between non-active states. It
        # clears whenever we return to active. Between two non-active states
        # (e.g. `idle → locked`) the clock is deliberately left alone so that
        # the total continuous away time survives the intermediate hop.
        if state == "active":
            self._away_started_at = None
        elif prior_state == "active":
            self._away_started_at = time.time()
        self._last_state = state

    @staticmethod
    def _derivation_threshold_for(prior_state: Optional[str]) -> Optional[float]:
        """Return the configured away-period threshold (seconds) for a recognized
        away state, or None if the state should never trigger derivation."""
        if prior_state == "locked":
            return float(
                getattr(config, "AMBIENT_ARRIVAL_LOCKED_THRESHOLD_SECONDS", 600.0)
            )
        if prior_state == "idle":
            return float(
                getattr(config, "AMBIENT_ARRIVAL_IDLE_THRESHOLD_SECONDS", 1200.0)
            )
        return None

    async def _derive_owner_arrival_if_needed(
        self,
        *,
        prior_state: Optional[str],
        new_state: str,
        away_started_at: Optional[float],
        bootstrap: bool,
    ) -> None:
        if bootstrap or new_state != "active":
            logger.debug(
                "machine derivation skipped: bootstrap=%s new_state=%s",
                bootstrap,
                new_state,
            )
            return
        threshold = self._derivation_threshold_for(prior_state)
        if threshold is None or away_started_at is None:
            logger.debug(
                "machine derivation skipped: no threshold for prior_state=%s (away_started_at=%s)",
                prior_state,
                away_started_at,
            )
            return
        away_duration = time.time() - float(away_started_at)
        if away_duration < threshold:
            logger.debug(
                "machine derivation skipped: away_duration=%.1fs below threshold=%.1fs for prior_state=%s",
                away_duration,
                threshold,
                prior_state,
            )
            return
        if await self._recent_owner_arrival_exists():
            logger.debug(
                "machine derivation skipped: recent owner_arrived already exists within dedup window",
            )
            return
        logger.debug(
            "machine derivation firing: prior_state=%s away_duration=%.1fs",
            prior_state,
            away_duration,
        )
        await self.service.ingest_event(
            owner_user_id=self.owner_user_id,
            source="machine_derived",
            event_type="owner_arrived",
            payload={
                "derived_from": str(prior_state),
                "away_duration_seconds": away_duration,
            },
            metadata={
                "bootstrap": False,
                "derived": True,
            },
            dedupe_key="machine_derived:owner_arrived",
        )

    async def _recent_owner_arrival_exists(self) -> bool:
        """Return True if any source has emitted an owner_arrived event within the
        context dedup window (default 20s). Used to prevent simultaneous double-fire
        when Home Assistant and the machine bridge both react to the same arrival.

        This is a *near-simultaneous* guard, not a "did HA handle this session" guard.
        The 20-second window is deliberately short because the real double-greeting
        protection lives in ambient_service's arrival cooldown (default 30 minutes) —
        this check only exists to avoid creating two context_event rows for the same
        wall-clock arrival moment. If the window is widened, it starts suppressing
        legitimately-separate arrivals and the cooldown stops being the single source
        of truth for "was this arrival already handled."

        Fails open (returns False) on any error from the backing service so a
        degraded event store cannot silently swallow real arrivals.
        """
        dedup_window = float(
            getattr(config, "HOME_CONTEXT_EVENT_DEDUP_SECONDS", 20.0) or 20.0
        )
        if dedup_window <= 0:
            return False
        try:
            recent = await self.service.list_recent_events(
                owner_user_id=self.owner_user_id,
                limit=20,
            )
        except Exception:
            logger.warning(
                "Failed to list recent context events for cross-source dedup; "
                "proceeding with derivation",
                exc_info=True,
            )
            return False
        now = time.time()
        for event in recent or []:
            if str(event.get("event_type") or "").strip().lower() != "owner_arrived":
                continue
            occurred_at = float(event.get("occurred_at") or 0.0)
            if occurred_at > 0 and (now - occurred_at) <= dedup_window:
                return True
        return False

    async def _detect_state(self) -> str:
        return await asyncio.to_thread(host_platform_service.machine_presence_state)

    async def _resolve_session_id(self) -> Optional[str]:
        env_session = str(os.environ.get("XDG_SESSION_ID", "")).strip()
        if env_session:
            return env_session
        username = getpass.getuser()
        proc = await asyncio.create_subprocess_exec(
            "loginctl",
            "list-sessions",
            "--no-legend",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("loginctl_list_sessions_failed")
        for raw_line in stdout.decode("utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            session_id, _, session_user = parts[:3]
            if session_user == username:
                return session_id
        return None

    async def _show_session_props(self, session_id: str) -> Dict[str, str]:
        proc = await asyncio.create_subprocess_exec(
            "loginctl",
            "show-session",
            str(session_id),
            "-p",
            "Active",
            "-p",
            "LockedHint",
            "-p",
            "IdleHint",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("loginctl_show_session_failed")
        props: Dict[str, str] = {}
        for raw_line in stdout.decode("utf-8", errors="ignore").splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            props[key.strip()] = value.strip()
        return props


class HomePresenceService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner_user_id: Optional[int] = None
        self._maintenance_session_key = ""
        self._started = False
        self._ha_source: Optional[HomeAssistantContextSource] = None
        self._machine_source: Optional[MachinePresenceSource] = None

    async def start(self, *, owner_user_id: int) -> None:
        if self._started and self._owner_user_id == int(owner_user_id):
            return
        async with self._lock:
            if self._started and self._owner_user_id == int(owner_user_id):
                return
            await self.stop()
            self._owner_user_id = int(owner_user_id)
            session = await runtime_control.ensure_surface_session(
                owner_user_id=int(owner_user_id),
                platform="local",
                maintenance=True,
                status="ready",
                metadata={"origin": "home_presence"},
            )
            self._maintenance_session_key = str(session.get("session_key") or maintenance_session_key(int(owner_user_id)))
            await self._ensure_default_state(owner_user_id=int(owner_user_id))

            # E.5 Wave 4 step 3 (2026-05-13): home_assistant toggle now
            # lives in palli.yaml features (read via settings_service);
            # the legacy config.HOME_ASSISTANT_ENABLED attribute was
            # deleted in the same commit. Default to disabled if the
            # YAML snapshot can't be loaded (e.g. fresh-install bring-up).
            try:
                from core.settings_boot import settings_service as _settings
                home_assistant_enabled = bool(
                    _settings.snapshot().features.home_assistant
                )
            except Exception:
                home_assistant_enabled = False
            if home_assistant_enabled:
                self._ha_source = HomeAssistantContextSource(self, int(owner_user_id))
                await self._ha_source.start()
            else:
                await self.record_source_health(
                    owner_user_id=int(owner_user_id),
                    source_name="home_assistant",
                    status="disabled",
                    message="disabled",
                    emit_event=False,
                )
            if bool(getattr(config, "LOCAL_MACHINE_PRESENCE_ENABLED", True)):
                self._machine_source = MachinePresenceSource(self, int(owner_user_id))
                await self._machine_source.start()
            else:
                await self.record_source_health(
                    owner_user_id=int(owner_user_id),
                    source_name="machine",
                    status="disabled",
                    message="disabled",
                    emit_event=False,
                )
            self._started = True

    async def stop(self) -> None:
        if self._ha_source is not None:
            await self._ha_source.stop()
            self._ha_source = None
        if self._machine_source is not None:
            await self._machine_source.stop()
            self._machine_source = None
        self._started = False
        self._owner_user_id = None
        self._maintenance_session_key = ""
        await ambient_service.stop()

    async def get_context_snapshot(self, *, owner_user_id: int) -> Dict[str, Any]:
        state = await brain.get_home_context_state(owner_user_id=int(owner_user_id))
        if state:
            return state
        return {
            "owner_user_id": int(owner_user_id),
            "owner_presence_state": "unknown",
            "machine_state": "unknown",
            "privacy_state": "unknown",
            "quiet_hours_active": False,
            "last_arrival_at": None,
            "last_departure_at": None,
            "last_machine_active_at": None,
            "last_event_at": None,
            "source_health": {},
            "metadata": {},
        }

    async def list_recent_events(self, *, owner_user_id: int, limit: int = 12) -> List[Dict[str, Any]]:
        return await brain.list_context_events(owner_user_id=int(owner_user_id), limit=max(int(limit or 12), 1))

    async def simulate_event(
        self,
        *,
        owner_user_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized = str(event_type or "").strip().lower()
        if normalized not in SIMULATABLE_CONTEXT_EVENTS:
            raise ValueError(f"Unsupported simulated context event: {event_type}")
        return await self.ingest_event(
            owner_user_id=int(owner_user_id),
            source="simulation",
            event_type=normalized,
            payload=payload or {},
            metadata={"simulation": True, **dict(metadata or {})},
            dedupe_key=None,
        )

    async def record_source_health(
        self,
        *,
        owner_user_id: int,
        source_name: str,
        status: str,
        message: str,
        emit_event: bool,
    ) -> None:
        state = await self.get_context_snapshot(owner_user_id=int(owner_user_id))
        source_health = dict(state.get("source_health") or {})
        previous_status = str((source_health.get(source_name) or {}).get("status") or "")
        source_health[source_name] = SourceHealth(
            status=str(status or "unknown"),
            message=str(message or ""),
            updated_at=time.time(),
        ).to_dict()
        await brain.upsert_home_context_state(
            owner_user_id=int(owner_user_id),
            owner_presence_state=str(state.get("owner_presence_state") or "unknown"),
            machine_state=str(state.get("machine_state") or "unknown"),
            privacy_state=str(state.get("privacy_state") or "unknown"),
            quiet_hours_active=bool(state.get("quiet_hours_active")),
            last_arrival_at=state.get("last_arrival_at"),
            last_departure_at=state.get("last_departure_at"),
            last_machine_active_at=state.get("last_machine_active_at"),
            last_event_at=state.get("last_event_at"),
            source_health=source_health,
            metadata=state.get("metadata") or {},
        )
        if emit_event and previous_status != status:
            event_type = f"{source_name}_source_restored" if status == "ready" else f"{source_name}_source_unavailable"
            await self.ingest_event(
                owner_user_id=int(owner_user_id),
                source=source_name,
                event_type=event_type,
                payload={"status": status, "message": message},
                metadata={"bootstrap": False, "source_health_only": True},
                dedupe_key=f"{source_name}:health:{status}",
            )
        else:
            await self._emit_context_update(owner_user_id=int(owner_user_id))

    async def ingest_event(
        self,
        *,
        owner_user_id: int,
        source: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload = dict(payload or {})
        metadata = dict(metadata or {})
        occurred_at = float(metadata.get("occurred_at") or time.time())
        dedupe_window = float(getattr(config, "HOME_CONTEXT_EVENT_DEDUP_SECONDS", 20.0) or 20.0)
        clean_dedupe_key = str(dedupe_key or self._default_dedupe_key(source=source, event_type=event_type, payload=payload)).strip()
        if clean_dedupe_key and dedupe_window > 0:
            existing = await brain.get_recent_context_event_by_dedupe(
                owner_user_id=int(owner_user_id),
                dedupe_key=clean_dedupe_key,
                since=occurred_at - dedupe_window,
            )
            if existing:
                return existing

        event_record = {
            "event_id": f"ctx_{uuid.uuid4().hex[:16]}",
            "owner_user_id": int(owner_user_id),
            "source": str(source or "unknown"),
            "event_type": str(event_type or "").strip().lower(),
            "occurred_at": occurred_at,
            "dedupe_key": clean_dedupe_key,
            "runtime_session_key": self._maintenance_session_key or maintenance_session_key(int(owner_user_id)),
            "payload": payload,
            "metadata": metadata,
        }
        await brain.create_context_event(**event_record)

        previous_state = await self.get_context_snapshot(owner_user_id=int(owner_user_id))
        policy = await identity_service.get_compiled_action_policy(
            user_id=int(owner_user_id),
            platform="owner",
            identity_owner_id=int(owner_user_id),
        )
        next_state = self._reduce_context(
            previous_state=previous_state,
            event=event_record,
            policy=policy,
        )
        await brain.upsert_home_context_state(
            owner_user_id=int(owner_user_id),
            owner_presence_state=str(next_state.get("owner_presence_state") or "unknown"),
            machine_state=str(next_state.get("machine_state") or "unknown"),
            privacy_state=str(next_state.get("privacy_state") or "unknown"),
            quiet_hours_active=bool(next_state.get("quiet_hours_active")),
            last_arrival_at=next_state.get("last_arrival_at"),
            last_departure_at=next_state.get("last_departure_at"),
            last_machine_active_at=next_state.get("last_machine_active_at"),
            last_event_at=next_state.get("last_event_at"),
            source_health=next_state.get("source_health") or {},
            metadata=next_state.get("metadata") or {},
        )
        await self._emit_context_update(owner_user_id=int(owner_user_id))
        await ambient_service.handle_context_event(
            owner_user_id=int(owner_user_id),
            event=event_record,
            context_snapshot=next_state,
            previous_context=previous_state,
        )
        # Present Moment sense C (2026-07-24): presence transition, kinds
        # passed through (owner_arrived / owner_departed / machine state
        # changes). absence_s lets the engine promote long-absence arrivals
        # to priority wakes. Structural only.
        #
        # 2026-07-24 review fix: the original "time since last_machine_
        # active_at" math was wrong in both directions. (a) Machine-derived
        # arrivals fire a sibling machine_active event first — it commits
        # last_machine_active_at = now, and since each ingest_event call
        # re-reads previous_state fresh from the DB, the owner_arrived call
        # right behind it saw last_machine_active_at ≈ now ⇒ absence ≈ 0,
        # exactly on the arrivals this was supposed to catch. (b)
        # last_machine_active_at defaults to None (fresh/reset
        # home_context_state row, LOCAL_MACHINE_PRESENCE_ENABLED=false
        # HA-only deployments, or any boot where the machine starts
        # non-active) — float(None or 0.0) silently became 0.0, so
        # absence_s read ~1.78e9s (~56 years) and forced a priority wake on
        # every single arrival. Fixed by preferring the real measured
        # away_duration_seconds _derive_owner_arrival_if_needed already
        # computes and puts in the payload (a float — structure, not the
        # content tier-1 law forbids), falling back to last_departure_at
        # (the HA-sourced-arrival case, which fires owner_departed first)
        # or last_machine_active_at, and treating a wholly-unknown prior as
        # zero absence rather than epoch-zero — unknown means no promotion,
        # the conservative reading for a still-shadow organ.
        try:
            from ai.present_moment import notice as _pm_notice
            _absence = 0.0
            try:
                _absence = float(payload.get("away_duration_seconds") or 0.0)
                if _absence <= 0.0:
                    _prior = previous_state.get("last_departure_at") or previous_state.get(
                        "last_machine_active_at"
                    )
                    _absence = max(0.0, time.time() - float(_prior)) if _prior else 0.0
            except Exception:
                _absence = 0.0
            _pm_notice(str(event_record["event_type"]), absence_s=_absence)
        except Exception:
            pass
        return event_record

    async def _ensure_default_state(self, *, owner_user_id: int) -> None:
        current = await brain.get_home_context_state(owner_user_id=int(owner_user_id))
        if current:
            return
        await brain.upsert_home_context_state(
            owner_user_id=int(owner_user_id),
            owner_presence_state="unknown",
            machine_state="unknown",
            privacy_state="unknown",
            quiet_hours_active=self._quiet_hours_active("adaptive"),
            source_health={},
            metadata={},
        )

    def _reduce_context(
        self,
        *,
        previous_state: Dict[str, Any],
        event: Dict[str, Any],
        policy: Any,
    ) -> Dict[str, Any]:
        next_state = {
            "owner_user_id": int(previous_state.get("owner_user_id") or event.get("owner_user_id") or 0),
            "owner_presence_state": str(previous_state.get("owner_presence_state") or "unknown"),
            "machine_state": str(previous_state.get("machine_state") or "unknown"),
            "privacy_state": str(previous_state.get("privacy_state") or "unknown"),
            "quiet_hours_active": bool(previous_state.get("quiet_hours_active")),
            "last_arrival_at": previous_state.get("last_arrival_at"),
            "last_departure_at": previous_state.get("last_departure_at"),
            "last_machine_active_at": previous_state.get("last_machine_active_at"),
            "last_event_at": float(event.get("occurred_at") or time.time()),
            "source_health": dict(previous_state.get("source_health") or {}),
            "metadata": dict(previous_state.get("metadata") or {}),
        }
        event_type = str(event.get("event_type") or "").strip().lower()
        payload = dict(event.get("payload") or {})
        source = str(event.get("source") or "").strip().lower()

        if event_type == "owner_arrived":
            next_state["owner_presence_state"] = "home"
            next_state["last_arrival_at"] = float(event.get("occurred_at") or time.time())
        elif event_type == "owner_departed":
            next_state["owner_presence_state"] = "away"
            next_state["last_departure_at"] = float(event.get("occurred_at") or time.time())
        elif event_type == "machine_locked":
            next_state["machine_state"] = "locked"
        elif event_type == "machine_unlocked":
            next_state["machine_state"] = str(payload.get("machine_state") or "active")
        elif event_type == "machine_active":
            next_state["machine_state"] = "active"
            next_state["last_machine_active_at"] = float(event.get("occurred_at") or time.time())
        elif event_type == "machine_idle":
            next_state["machine_state"] = "idle"
        elif event_type == "privacy_private":
            next_state["privacy_state"] = "private"
        elif event_type == "privacy_shared":
            next_state["privacy_state"] = "shared"
        elif event_type == "privacy_unknown":
            next_state["privacy_state"] = "unknown"
        elif event_type.endswith("_source_unavailable"):
            next_state["source_health"][source] = SourceHealth(
                status="degraded",
                message=str(payload.get("message") or ""),
                updated_at=float(event.get("occurred_at") or time.time()),
            ).to_dict()
        elif event_type.endswith("_source_restored"):
            next_state["source_health"][source] = SourceHealth(
                status="ready",
                message=str(payload.get("message") or "ok"),
                updated_at=float(event.get("occurred_at") or time.time()),
            ).to_dict()

        next_state["quiet_hours_active"] = self._quiet_hours_active(
            str(policy_value(policy, "quiet_hours_mode", "adaptive") or "adaptive")
        )
        next_state["metadata"].update(
            {
                "last_event_type": event_type,
                "last_event_source": source,
            }
        )
        return next_state

    @staticmethod
    def _default_dedupe_key(*, source: str, event_type: str, payload: Dict[str, Any]) -> str:
        fragments = [str(source or "").strip().lower(), str(event_type or "").strip().lower()]
        for key in sorted(payload.keys()):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)):
                fragments.append(f"{key}={value}")
        return "|".join(item for item in fragments if item)

    @staticmethod
    def _quiet_hours_active(mode: str) -> bool:
        normalized = str(mode or "adaptive").strip().lower()
        if normalized == "off":
            return False
        current_hour = time.localtime().tm_hour
        if normalized == "gentle":
            return current_hour >= 22 or current_hour < 9
        return current_hour >= 23 or current_hour < 8

    async def _emit_context_update(self, *, owner_user_id: int) -> None:
        # Legacy dashboard section-update emit removed in W5.4.
        return


home_presence_service = HomePresenceService()
