from __future__ import annotations

import inspect
import importlib
import importlib.util
import json
import logging
import sqlite3
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

from core.config import config
from core.database import brain
from core.host_platform import host_platform_service

logger = logging.getLogger(__name__)

PLUGIN_RUNTIME_RELOAD_FAMILIES = {
    "llm_provider",
    "stt_provider",
    "tts_provider",
}
PLUGIN_RESTART_REQUIRED_FAMILIES = {
    "api_router",
    "runtime_service",
    "channel_integration",
}
PLUGIN_MANIFEST_FILENAME = "palli_plugin.json"


@dataclass(slots=True)
class PluginRouterContribution:
    router: Any
    prefixes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass(slots=True)
class PluginServiceContribution:
    service_id: str
    label: str
    start: Callable[..., Awaitable[None]]
    stop: Optional[Callable[[], Awaitable[None]]] = None


@dataclass(slots=True)
class PluginChannelContribution:
    channel_id: str
    label: str
    start: Callable[[], Awaitable[Any]]


@dataclass(slots=True)
class PluginContributionBundle:
    llm_providers: Dict[str, Any] = field(default_factory=dict)
    stt_providers: Dict[str, Any] = field(default_factory=dict)
    tts_providers: Dict[str, Any] = field(default_factory=dict)
    api_routers: List[PluginRouterContribution] = field(default_factory=list)
    runtime_services: List[PluginServiceContribution] = field(default_factory=list)
    channel_integrations: List[PluginChannelContribution] = field(default_factory=list)


def _now() -> float:
    return time.time()


def _clean_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in items:
            items.append(clean)
    return items


def _sync_db_path() -> Path:
    configured_path = str(getattr(config, "DATABASE_PATH", "") or "data/palli-local.sqlite3").strip()
    candidate = Path(configured_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[1] / candidate
    return candidate


def _host_backend_name() -> str:
    snapshot = host_platform_service.platform_snapshot()
    return str(snapshot.get("host_backend") or snapshot.get("host_platform") or "unknown").strip().lower()


def _host_compatible(host_support: Iterable[Any]) -> tuple[bool, str]:
    allowed = {str(item or "").strip().lower() for item in host_support if str(item or "").strip()}
    if not allowed or "any" in allowed or "*" in allowed:
        return True, "compatible"
    current = _host_backend_name()
    if current in allowed:
        return True, "compatible"
    return False, "incompatible"


def _default_enabled(*, source_kind: str, criticality: str) -> bool:
    if str(criticality or "").strip().lower() == "required":
        return True
    if str(source_kind or "").strip().lower() == "builtin":
        return str(criticality or "").strip().lower() in {"default", "required"}
    return False


def _module_from_file(module_name: str, file_path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginRuntime:
    def __init__(self) -> None:
        self._startup_catalog: List[Dict[str, Any]] = []
        self._started_services: Dict[str, PluginServiceContribution] = {}

    def _builtin_specs(self) -> List[Dict[str, Any]]:
        api_version = str(getattr(config, "PLUGIN_API_VERSION", "1") or "1").strip()
        return [
            {
                "plugin_id": "builtin.llm.providers",
                "display_name": "Built-in LLM Providers",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Core OpenAI, DeepSeek, local OpenAI-compatible, and Codex CLI providers.",
                "entrypoint": "builtin:llm_providers",
                "families": ["llm_provider"],
                "criticality": "required",
                "reload_mode": "runtime_reload",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.voice.providers",
                "display_name": "Built-in Speech Providers",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Core Deepgram, Lemonfox, and local speech providers.",
                "entrypoint": "builtin:voice_providers",
                "families": ["stt_provider", "tts_provider"],
                "criticality": "required",
                "reload_mode": "runtime_reload",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.api.core",
                "display_name": "Core API Routers",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Core prompts, chat, and agent routes.",
                "entrypoint": "builtin:api_core",
                "families": ["api_router"],
                "criticality": "required",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.api.web",
                "display_name": "Web Runtime Routers",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Local web auth, account, identity, and runtime routes.",
                "entrypoint": "builtin:api_web",
                "families": ["api_router"],
                "criticality": "required",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.channel.discord_bot",
                "display_name": "Discord Bot Integration",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Discord bot channel integration.",
                "entrypoint": "builtin:discord_bot",
                "families": ["channel_integration"],
                "criticality": "default",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.channel.telegram_bot",
                "display_name": "Telegram Bot Integration",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Telegram bot channel integration.",
                "entrypoint": "builtin:telegram_bot",
                "families": ["channel_integration"],
                "criticality": "default",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.channel.mobile_door",
                "display_name": "Phone App Door",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "The owner's phone app: paired devices, a timeline, a websocket.",
                "entrypoint": "builtin:mobile_door",
                "families": ["channel_integration"],
                "criticality": "default",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.service.home_presence",
                "display_name": "Home Presence Service",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Local machine and home-context presence ingestion.",
                "entrypoint": "builtin:home_presence",
                "families": ["runtime_service"],
                "criticality": "default",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
            {
                "plugin_id": "builtin.service.desktop_activity_watcher",
                "display_name": "Desktop Activity Watcher",
                "version": "builtin",
                "plugin_api_version": api_version,
                "description": "Rolling activity timeline (focused window, machine state, recent files) for Jarvis-grade ambient awareness.",
                "entrypoint": "builtin:desktop_activity_watcher",
                "families": ["runtime_service"],
                "criticality": "default",
                "reload_mode": "restart_required",
                "source_kind": "builtin",
                "source_path": "",
                "host_support": ["any"],
                "requires_json": {},
                "manifest": {},
            },
        ]

    def _discover_local_manifests(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        api_version = str(getattr(config, "PLUGIN_API_VERSION", "1") or "1").strip()
        for plugin_root in list(getattr(config, "PLUGIN_DIRECTORIES", []) or []):
            base = Path(str(plugin_root or "")).expanduser()
            if not base.exists() or not base.is_dir():
                continue
            for candidate in sorted(base.iterdir(), key=lambda path: path.name.lower()):
                manifest_path = candidate / PLUGIN_MANIFEST_FILENAME if candidate.is_dir() else None
                if not manifest_path or not manifest_path.exists():
                    continue
                plugin_id = f"local.invalid.{candidate.name}"
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("manifest must be an object")
                    plugin_id = str(payload.get("plugin_id") or "").strip() or plugin_id
                    item = {
                        "plugin_id": plugin_id,
                        "display_name": str(payload.get("display_name") or plugin_id),
                        "version": str(payload.get("version") or "0.0.0"),
                        "plugin_api_version": str(payload.get("plugin_api_version") or api_version),
                        "description": str(payload.get("description") or ""),
                        "entrypoint": str(payload.get("entrypoint") or "").strip(),
                        "families": _clean_list(payload.get("families") or []),
                        "criticality": str(payload.get("criticality") or "optional").strip().lower() or "optional",
                        "reload_mode": str(payload.get("reload_mode") or "runtime_reload").strip().lower() or "runtime_reload",
                        "source_kind": "local",
                        "source_path": str(candidate.resolve()),
                        "host_support": _clean_list(payload.get("host_support") or ["any"]),
                        "requires_json": dict(payload.get("requires") or {}),
                        "manifest": payload,
                    }
                    if not item["entrypoint"]:
                        item["compatibility_status"] = "manifest_invalid"
                        item["health_status"] = "error"
                        item["last_error"] = "Missing entrypoint"
                    items.append(item)
                except Exception as exc:
                    items.append(
                        {
                            "plugin_id": plugin_id,
                            "display_name": candidate.name,
                            "version": "0.0.0",
                            "plugin_api_version": api_version,
                            "description": "",
                            "entrypoint": "",
                            "families": [],
                            "criticality": "optional",
                            "reload_mode": "runtime_reload",
                            "source_kind": "local",
                            "source_path": str(candidate.resolve()),
                            "host_support": ["any"],
                            "requires_json": {},
                            "manifest": {},
                            "compatibility_status": "manifest_invalid",
                            "health_status": "error",
                            "last_error": str(exc),
                        }
                    )
        return items

    def _load_sync_state(self) -> Dict[str, Dict[str, Any]]:
        db_path = _sync_db_path()
        if not db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(db_path.as_posix())
            try:
                rows = conn.execute(
                    """
                    SELECT plugin_id, desired_enabled, applied_enabled, compatibility_status,
                           health_status, restart_required, last_error, last_applied_at
                    FROM plugin_runtime_plugins
                    """
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return {}
        return {
            str(row[0] or ""): {
                "desired_enabled": bool(row[1]),
                "applied_enabled": bool(row[2]),
                "compatibility_status": str(row[3] or ""),
                "health_status": str(row[4] or ""),
                "restart_required": bool(row[5]),
                "last_error": str(row[6] or ""),
                "last_applied_at": float(row[7]) if row[7] is not None else None,
            }
            for row in rows
        }

    def prepare_startup_catalog(self, *, force: bool = False) -> List[Dict[str, Any]]:
        if self._startup_catalog and not force:
            return list(self._startup_catalog)
        state = self._load_sync_state()
        catalog: List[Dict[str, Any]] = []
        for manifest in [*self._builtin_specs(), *self._discover_local_manifests()]:
            current = dict(manifest)
            plugin_id = str(current.get("plugin_id") or "").strip()
            existing = state.get(plugin_id, {})
            desired_enabled = existing.get("desired_enabled")
            if desired_enabled is None:
                desired_enabled = _default_enabled(
                    source_kind=str(current.get("source_kind") or ""),
                    criticality=str(current.get("criticality") or ""),
                )
            compatible, compatibility_status = _host_compatible(current.get("host_support") or ["any"])
            current["desired_enabled"] = bool(desired_enabled)
            current["applied_enabled"] = bool(existing.get("applied_enabled", False))
            current["compatibility_status"] = str(current.get("compatibility_status") or compatibility_status)
            current["health_status"] = str(current.get("health_status") or existing.get("health_status") or ("disabled" if not desired_enabled else "ready"))
            current["restart_required"] = bool(existing.get("restart_required", False))
            current["last_error"] = str(current.get("last_error") or existing.get("last_error") or "")
            current["compatible"] = bool(compatible) and current["compatibility_status"] not in {"manifest_invalid", "dependency_missing"}
            catalog.append(current)
        self._startup_catalog = catalog
        return list(catalog)

    def _import_local_entrypoint(self, plugin_id: str, source_path: str, entrypoint: str) -> Callable[..., Any]:
        raw = str(entrypoint or "").strip()
        if ":" in raw:
            module_ref, callable_name = raw.split(":", 1)
        else:
            module_ref, callable_name = raw, "get_plugin_contributions"
        root = Path(str(source_path or "")).expanduser()
        module_name = f"palli_plugin_{plugin_id.replace('.', '_').replace('-', '_')}"
        if module_ref.endswith(".py"):
            module = _module_from_file(module_name, root / module_ref)
        else:
            sys.path.insert(0, root.as_posix())
            try:
                module = importlib.import_module(module_ref)
            finally:
                try:
                    sys.path.remove(root.as_posix())
                except ValueError:
                    pass
        target = getattr(module, callable_name.strip(), None)
        if not callable(target):
            raise RuntimeError(f"Plugin entrypoint callable not found: {callable_name}")
        return target

    def _load_builtin_bundle(self, plugin_id: str) -> PluginContributionBundle:
        if plugin_id == "builtin.llm.providers":
            # SINGLE SOURCE OF TRUTH for the built-in provider set. This bundle
            # is installed at boot via replace_provider_registry(), which sets
            # llm.providers to EXACTLY the dict below (the non-empty branch of
            # configure_provider_registry skips the defaults) — so any provider
            # missing here is silently absent from the live server's registry,
            # even though build_default_provider_registry() (what isolated
            # scripts/tests use) has it. That drift shipped a real outage
            # (2026-07-06): this hand-maintained dict omitted "anthropic", so
            # turn_read / cadence / stake_appraisal — all routed to
            # anthropic::claude-haiku — resolved to "unknown provider",
            # fell back to OpenAI, and 404'd to None on every live turn.
            # Derive from build_default_provider_registry so it can't drift.
            from ai.llm_provider_registry import build_default_provider_registry

            return PluginContributionBundle(
                llm_providers=build_default_provider_registry(),
            )
        if plugin_id == "builtin.voice.providers":
            # Step 31 (2026-08-20): derive from voice/speech.py's builders so
            # the boot bundle can't drift from the default registry — this
            # bundle REPLACES the live registry at boot, and a hand-maintained
            # dict here once omitted lemonfox entirely (the same drift class
            # as the 2026-07-06 anthropic outage above). Cartesia is cut;
            # lemonfox is her provider of record.
            from voice.speech import (
                _build_default_stt_provider_registry,
                _build_default_tts_provider_registry,
            )

            return PluginContributionBundle(
                stt_providers=_build_default_stt_provider_registry(),
                tts_providers=_build_default_tts_provider_registry(),
            )
        if plugin_id == "builtin.api.core":
            # Wave 5: api.agent deleted (no live consumers); the
            # /api/agent surface is replaced by tier-2 delegation
            # (core/turn/respond_tier2.py) and the bridge's own
            # delivery path through channel_runtime.
            from api import chat, mobile, mobile_reminders, prompts

            return PluginContributionBundle(
                api_routers=[
                    PluginRouterContribution(router=prompts.router, prefixes=["/api/prompts"], tags=["prompts"]),
                    PluginRouterContribution(router=chat.router, prefixes=["/api/chat"], tags=["chat"]),
                    PluginRouterContribution(router=mobile.router, prefixes=["/api/mobile"], tags=["mobile"]),
                    PluginRouterContribution(router=mobile_reminders.router, prefixes=["/api/mobile"], tags=["mobile"]),
                ]
            )
        if plugin_id == "builtin.api.web":
            # Wave 5 deleted api.web_runtime; restored 2026-06-11 as a slim
            # three-endpoint router (mission + under-hood + cognitive stub)
            # so the dashboard mission card and admin observatory render
            # again. api.web_voice remains deleted.
            from api import web_account, web_auth, web_identity, web_interiority, web_runtime

            return PluginContributionBundle(
                api_routers=[
                    PluginRouterContribution(router=web_auth.router, prefixes=["/api/web", "/web"], tags=["web"]),
                    PluginRouterContribution(router=web_account.router, prefixes=["/api/web", "/web"], tags=["web"]),
                    PluginRouterContribution(router=web_identity.router, prefixes=["/api/web", "/web"], tags=["web"]),
                    PluginRouterContribution(router=web_runtime.router, prefixes=["/api/web", "/web"], tags=["web"]),
                    PluginRouterContribution(router=web_interiority.router, prefixes=["/api/web", "/web"], tags=["web"]),
                ]
            )
        if plugin_id == "builtin.channel.discord_bot":
            # Wave 5: legacy bots/discord/bot.py is deleted; the new
            # spine client is the only Discord entry point. The
            # turn_loop.surfaces.discord_text flag survives until Wave 6
            # for symmetry with the other surface flags.
            from bots.discord.client import start_discord_text_client as start

            return PluginContributionBundle(
                channel_integrations=[
                    PluginChannelContribution(
                        channel_id="discord_bot",
                        label="Discord Bot",
                        start=start,
                    )
                ]
            )
        if plugin_id == "builtin.channel.telegram_bot":
            # Telegram door (2026-09-14, task 2). The import is at CALL time,
            # inside this branch, like Discord's — and it matters more here:
            # bots.telegram never imports the library at module level either,
            # so this resolves cleanly on a tree where it is not installed
            # and the client itself reports the closed door.
            from bots.telegram.client import start_telegram_client as start

            return PluginContributionBundle(
                channel_integrations=[
                    PluginChannelContribution(
                        channel_id="telegram_bot",
                        label="Telegram Bot",
                        start=start,
                    )
                ]
            )
        if plugin_id == "builtin.channel.mobile_door":
            # The mobile door (2026-10-13). Imported at call time, like the
            # other doors; its routes are mounted with the core API either way.
            from bots.mobile.client import start_mobile_door as start

            return PluginContributionBundle(
                channel_integrations=[
                    PluginChannelContribution(channel_id="mobile_door", label="Phone App", start=start)
                ]
            )
        if plugin_id == "builtin.service.home_presence":
            from core.home_presence import home_presence_service

            async def _start(owner_user_id: Optional[int] = None, **_: Any) -> None:
                if owner_user_id is None:
                    return
                await home_presence_service.start(owner_user_id=int(owner_user_id))

            async def _stop() -> None:
                await home_presence_service.stop()

            return PluginContributionBundle(
                runtime_services=[
                    PluginServiceContribution(
                        service_id="home_presence",
                        label="Home Presence",
                        start=_start,
                        stop=_stop,
                    )
                ]
            )
        if plugin_id == "builtin.service.desktop_activity_watcher":
            from core.desktop_activity_watcher import desktop_activity_watcher

            async def _start(owner_user_id: Optional[int] = None, **_: Any) -> None:
                if owner_user_id is None:
                    return
                await desktop_activity_watcher.start(owner_user_id=int(owner_user_id))

            async def _stop() -> None:
                await desktop_activity_watcher.stop()

            return PluginContributionBundle(
                runtime_services=[
                    PluginServiceContribution(
                        service_id="desktop_activity_watcher",
                        label="Desktop Activity Watcher",
                        start=_start,
                        stop=_stop,
                    )
                ]
            )
        raise RuntimeError(f"Unknown builtin plugin: {plugin_id}")

    def _normalize_bundle(self, payload: Any) -> PluginContributionBundle:
        if isinstance(payload, PluginContributionBundle):
            return payload
        record = dict(payload or {})
        routers: List[PluginRouterContribution] = []
        for item in list(record.get("api_routers") or []):
            if isinstance(item, PluginRouterContribution):
                routers.append(item)
                continue
            if isinstance(item, dict) and item.get("router") is not None:
                routers.append(
                    PluginRouterContribution(
                        router=item.get("router"),
                        prefixes=_clean_list(item.get("prefixes") or []),
                        tags=_clean_list(item.get("tags") or []),
                    )
                )
        services: List[PluginServiceContribution] = []
        for item in list(record.get("runtime_services") or []):
            if isinstance(item, PluginServiceContribution):
                services.append(item)
                continue
            if isinstance(item, dict) and callable(item.get("start")):
                services.append(
                    PluginServiceContribution(
                        service_id=str(item.get("service_id") or item.get("label") or "service"),
                        label=str(item.get("label") or item.get("service_id") or "Service"),
                        start=item["start"],
                        stop=item.get("stop"),
                    )
                )
        channels: List[PluginChannelContribution] = []
        for item in list(record.get("channel_integrations") or []):
            if isinstance(item, PluginChannelContribution):
                channels.append(item)
                continue
            if isinstance(item, dict) and callable(item.get("start")):
                channels.append(
                    PluginChannelContribution(
                        channel_id=str(item.get("channel_id") or item.get("label") or "channel"),
                        label=str(item.get("label") or item.get("channel_id") or "Channel"),
                        start=item["start"],
                    )
                )
        return PluginContributionBundle(
            llm_providers=dict(record.get("llm_providers") or {}),
            stt_providers=dict(record.get("stt_providers") or {}),
            tts_providers=dict(record.get("tts_providers") or {}),
            api_routers=routers,
            runtime_services=services,
            channel_integrations=channels,
        )

    def _load_contribution_bundle(self, manifest: Dict[str, Any]) -> PluginContributionBundle:
        plugin_id = str(manifest.get("plugin_id") or "").strip()
        if str(manifest.get("source_kind") or "") == "builtin":
            return self._load_builtin_bundle(plugin_id)
        target = self._import_local_entrypoint(
            plugin_id=plugin_id,
            source_path=str(manifest.get("source_path") or ""),
            entrypoint=str(manifest.get("entrypoint") or ""),
        )
        signature = inspect.signature(target)
        payload = target() if len(signature.parameters) == 0 else target({"project_root": str(Path(__file__).resolve().parents[1])})
        return self._normalize_bundle(payload)

    async def _persist_catalog(self, catalog: List[Dict[str, Any]], *, startup_apply_restart_required: bool = False) -> None:
        now = _now()
        for item in catalog:
            restart_required = bool(item.get("restart_required", False))
            applied_enabled = bool(item.get("applied_enabled", False))
            desired_enabled = bool(item.get("desired_enabled", False))
            health_status = str(item.get("health_status") or "")
            if startup_apply_restart_required and str(item.get("reload_mode") or "") == "restart_required":
                applied_enabled = desired_enabled and bool(item.get("compatible", False))
                restart_required = False
                if health_status not in {"error", "disabled"}:
                    health_status = "ready" if applied_enabled else "disabled"
            await brain.upsert_plugin_runtime_plugin(
                plugin_id=str(item.get("plugin_id") or ""),
                display_name=str(item.get("display_name") or ""),
                version=str(item.get("version") or "") or None,
                plugin_api_version=str(item.get("plugin_api_version") or "") or None,
                description=str(item.get("description") or "") or None,
                entrypoint=str(item.get("entrypoint") or "") or None,
                source_kind=str(item.get("source_kind") or "local"),
                source_path=str(item.get("source_path") or "") or None,
                families=list(item.get("families") or []),
                criticality=str(item.get("criticality") or "optional"),
                reload_mode=str(item.get("reload_mode") or "runtime_reload"),
                host_support=list(item.get("host_support") or []),
                requires_json=dict(item.get("requires_json") or {}),
                manifest=dict(item.get("manifest") or {}),
                desired_enabled=desired_enabled,
                applied_enabled=applied_enabled,
                compatibility_status=str(item.get("compatibility_status") or "unknown"),
                health_status=health_status or ("disabled" if not desired_enabled else "ready"),
                restart_required=restart_required,
                last_error=str(item.get("last_error") or "") or None,
                last_discovered_at=now,
                last_applied_at=now if applied_enabled else item.get("last_applied_at"),
                metadata={"compatible": bool(item.get("compatible", False))},
            )

    async def _apply_runtime_reload_plugins(self, catalog: List[Dict[str, Any]], *, trigger_kind: str) -> Dict[str, Any]:
        from ai.llm_client import llm
        from voice.speech import voice_speech_service

        llm_providers: Dict[str, Any] = {}
        stt_providers: Dict[str, Any] = {}
        tts_providers: Dict[str, Any] = {}
        changed_plugin_ids: List[str] = []
        load_errors: Dict[str, str] = {}
        restart_pending = False

        for item in catalog:
            plugin_id = str(item.get("plugin_id") or "")
            reload_mode = str(item.get("reload_mode") or "runtime_reload")
            desired_enabled = bool(item.get("desired_enabled", False))
            compatible = bool(item.get("compatible", False))
            families = set(item.get("families") or [])
            if reload_mode == "restart_required":
                if bool(item.get("applied_enabled", False)) != desired_enabled:
                    restart_pending = True
                continue
            if not desired_enabled:
                item["applied_enabled"] = False
                item["health_status"] = "disabled"
                changed_plugin_ids.append(plugin_id)
                continue
            if not compatible:
                item["applied_enabled"] = False
                item["health_status"] = "error"
                changed_plugin_ids.append(plugin_id)
                continue
            try:
                bundle = self._load_contribution_bundle(item)
                if "llm_provider" in families:
                    llm_providers.update(bundle.llm_providers)
                if "stt_provider" in families:
                    stt_providers.update(bundle.stt_providers)
                if "tts_provider" in families:
                    tts_providers.update(bundle.tts_providers)
                item["applied_enabled"] = True
                item["health_status"] = "ready"
                item["last_error"] = ""
                changed_plugin_ids.append(plugin_id)
            except Exception as exc:
                load_errors[plugin_id] = str(exc)
                item["applied_enabled"] = False
                item["health_status"] = "error"
                item["last_error"] = str(exc)
                changed_plugin_ids.append(plugin_id)
                logger.error("Plugin load failed (%s): %s", plugin_id, exc, exc_info=True)

        await llm.replace_provider_registry(llm_providers)
        await voice_speech_service.replace_provider_registry(
            stt_providers=stt_providers,
            tts_providers=tts_providers,
        )

        await self._persist_catalog(catalog)
        run_id = f"pluginrun_{uuid4().hex[:12]}"
        await brain.create_plugin_runtime_run(
            plugin_runtime_run_id=run_id,
            trigger_kind=trigger_kind,
            action_kind="reload",
            status="completed" if not load_errors else "warning",
            changed_plugin_ids=changed_plugin_ids,
            restart_required=restart_pending,
            summary="Reloaded runtime-safe plugins." if not load_errors else "Reload completed with plugin errors.",
            error_text="; ".join(f"{key}: {value}" for key, value in load_errors.items()) or None,
            metadata={"errors": load_errors},
        )
        return {
            "run_id": run_id,
            "restart_required": restart_pending,
            "errors": load_errors,
        }

    async def initialize(self) -> None:
        if not bool(getattr(config, "PLUGIN_RUNTIME_ENABLED", True)):
            return
        catalog = self.prepare_startup_catalog(force=True)
        await self._persist_catalog(catalog, startup_apply_restart_required=True)
        if bool(getattr(config, "PLUGIN_DISCOVER_ON_START", True)):
            await self._apply_runtime_reload_plugins(catalog, trigger_kind="startup")

    async def _refresh_snapshot_state(self) -> None:
        if not bool(getattr(config, "PLUGIN_RUNTIME_ENABLED", True)):
            return
        catalog = self.prepare_startup_catalog(force=True)
        await self._persist_catalog(catalog)

    async def discover(self, *, trigger_kind: str = "manual") -> None:
        catalog = self.prepare_startup_catalog(force=True)
        await self._persist_catalog(catalog)
        await brain.create_plugin_runtime_run(
            plugin_runtime_run_id=f"pluginrun_{uuid4().hex[:12]}",
            trigger_kind=trigger_kind,
            action_kind="discover",
            status="completed",
            changed_plugin_ids=[str(item.get("plugin_id") or "") for item in catalog],
            restart_required=any(bool(item.get("restart_required", False)) for item in catalog),
            summary=f"Discovered {len(catalog)} plugins.",
            metadata={},
        )

    async def reload(self, *, trigger_kind: str = "manual") -> Dict[str, Any]:
        catalog = self.prepare_startup_catalog(force=True)
        await self._persist_catalog(catalog)
        return await self._apply_runtime_reload_plugins(catalog, trigger_kind=trigger_kind)

    async def list_plugins(self) -> List[Dict[str, Any]]:
        await self._refresh_snapshot_state()
        return await brain.list_plugin_runtime_plugins()

    async def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        await self._refresh_snapshot_state()
        return await brain.get_plugin_runtime_plugin(plugin_id=str(plugin_id or "").strip())

    async def set_desired_enabled(self, *, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        plugin = await self.get_plugin(str(plugin_id or "").strip())
        if not plugin:
            raise ValueError("plugin_not_found")
        if str(plugin.get("criticality") or "") == "required" and not enabled:
            raise ValueError("required_plugin_cannot_be_disabled")
        restart_required = str(plugin.get("reload_mode") or "") == "restart_required"
        await brain.update_plugin_runtime_plugin(
            plugin_id=str(plugin_id or "").strip(),
            desired_enabled=bool(enabled),
            health_status="restart_required" if restart_required else plugin.get("health_status") or "ready",
            restart_required=restart_required,
        )
        if not restart_required:
            await self.reload(trigger_kind="toggle")
        else:
            await brain.create_plugin_runtime_run(
                plugin_runtime_run_id=f"pluginrun_{uuid4().hex[:12]}",
                trigger_kind="toggle",
                action_kind="stage",
                status="completed",
                changed_plugin_ids=[str(plugin_id or "").strip()],
                restart_required=True,
                summary="Plugin change staged for restart.",
                metadata={"desired_enabled": bool(enabled)},
            )
        plugin = await brain.get_plugin_runtime_plugin(plugin_id=str(plugin_id or "").strip())
        return plugin or {}

    async def start_runtime_services(self, *, owner_user_id: Optional[int]) -> None:
        catalog = self.prepare_startup_catalog(force=False)
        for item in catalog:
            if "runtime_service" not in set(item.get("families") or []):
                continue
            if not bool(item.get("desired_enabled", False)) or not bool(item.get("compatible", False)):
                continue
            try:
                bundle = self._load_contribution_bundle(item)
                for service in bundle.runtime_services:
                    await service.start(owner_user_id=owner_user_id)
                    self._started_services[service.service_id] = service
            except Exception as exc:
                logger.error("Runtime service plugin failed to start (%s): %s", item.get("plugin_id"), exc, exc_info=True)
                await brain.update_plugin_runtime_plugin(
                    plugin_id=str(item.get("plugin_id") or ""),
                    health_status="error",
                    last_error=str(exc),
                )

    async def stop_runtime_services(self) -> None:
        for service_id, service in list(self._started_services.items()):
            if service.stop is None:
                continue
            try:
                await service.stop()
            except Exception as exc:
                logger.debug("Runtime service plugin failed to stop (%s): %s", service_id, exc)
        self._started_services.clear()

    def get_startup_router_contributions(self) -> List[PluginRouterContribution]:
        routers: List[PluginRouterContribution] = []
        for item in self.prepare_startup_catalog(force=False):
            if "api_router" not in set(item.get("families") or []):
                continue
            if not bool(item.get("desired_enabled", False)) or not bool(item.get("compatible", False)):
                continue
            try:
                bundle = self._load_contribution_bundle(item)
                routers.extend(bundle.api_routers)
            except Exception as exc:
                logger.error("Router plugin failed during startup (%s): %s", item.get("plugin_id"), exc, exc_info=True)
        return routers

    def get_startup_channel_integrations(self) -> List[PluginChannelContribution]:
        integrations: List[PluginChannelContribution] = []
        seen_channel_ids: set = set()

        # The doors are the `builtin.channel.*` entries in the startup
        # catalog. (The Phase 10 manifest-plugin path that ran beside them
        # had no plugins and was deleted in Batch 8, 2026-10-01.)
        for item in self.prepare_startup_catalog(force=False):
            if "channel_integration" not in set(item.get("families") or []):
                continue
            if not bool(item.get("desired_enabled", False)) or not bool(item.get("compatible", False)):
                continue
            try:
                bundle = self._load_contribution_bundle(item)
                for contribution in bundle.channel_integrations:
                    if contribution.channel_id in seen_channel_ids:
                        continue
                    seen_channel_ids.add(contribution.channel_id)
                    integrations.append(contribution)
            except Exception as exc:
                logger.error("Channel plugin failed during startup (%s): %s", item.get("plugin_id"), exc, exc_info=True)

        return integrations

    def build_health_snapshot(self) -> Dict[str, Any]:
        catalog = self.prepare_startup_catalog(force=True)
        total = len(catalog)
        enabled = sum(1 for item in catalog if bool(item.get("desired_enabled", False)))
        restart_pending = any(bool(item.get("restart_required", False)) for item in catalog)
        errors = [item for item in catalog if str(item.get("health_status") or "") == "error"]
        warnings = [item for item in catalog if str(item.get("compatibility_status") or "") in {"incompatible", "dependency_missing", "manifest_invalid"}]
        status = "ready"
        if errors:
            status = "degraded"
        elif warnings or restart_pending:
            status = "degraded"
        return {
            "status": status,
            "counts": {
                "total": total,
                "enabled": enabled,
                "errors": len(errors),
                "warnings": len(warnings),
            },
            "restart_pending": restart_pending,
            "items": [
                {
                    "plugin_id": str(item.get("plugin_id") or ""),
                    "display_name": str(item.get("display_name") or ""),
                    "source_kind": str(item.get("source_kind") or ""),
                    "families": list(item.get("families") or []),
                    "desired_enabled": bool(item.get("desired_enabled", False)),
                    "compatibility_status": str(item.get("compatibility_status") or ""),
                    "health_status": str(item.get("health_status") or ""),
                    "restart_required": bool(item.get("restart_required", False)),
                    "last_error": str(item.get("last_error") or ""),
                }
                for item in catalog
            ],
        }

    def doctor_checks(self) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []
        for item in self.prepare_startup_catalog(force=True):
            if not bool(item.get("desired_enabled", False)):
                continue
            compatibility_status = str(item.get("compatibility_status") or "compatible")
            if compatibility_status == "compatible":
                continue
            severity = "warning"
            if str(item.get("criticality") or "") == "required":
                severity = "core_blocker"
            checks.append(
                {
                    "code": f"plugin_{compatibility_status}",
                    "severity": severity,
                    "message": f"{item.get('display_name') or item.get('plugin_id')} is {compatibility_status.replace('_', ' ')}.",
                }
            )
        return checks


plugin_runtime = PluginRuntime()
