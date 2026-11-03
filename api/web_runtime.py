"""
Slim runtime introspection endpoints for the dashboard (restored 2026-06-11).

The original api/web_runtime.py (2,352 lines) was deleted in W5.2 along with
its runtime_authority assembler, leaving the dashboard's RuntimeMissionCard
and the admin UnderHoodObservatory (incl. the second Memory Galaxy view)
fetching 404s. This is the slim rebuild: three read-only GET surfaces,
reassembled against TODAY's services only:

  GET /runtime/mission                -> RuntimeMissionCard (dashboard)
  GET /runtime/under-hood             -> UnderHoodObservatory + Memory Galaxy
  GET /runtime/cognitive-architecture -> honest "unobserved" console payload

Mounted by core/plugin_runtime.py (builtin.api.web) under "/api/web" and
"/web". Every assembler degrades gracefully: a failing subsystem yields empty
values, never a 500. Payload regions backed by W5-deleted subsystems
(operator workflows, ambient, proactive/heartbeat loops, behavior traces)
are omitted or returned as honest empties; the frontend hides those panels.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Mapping, Optional

from fastapi import APIRouter, Request

from api.web_auth import _require_user
from core.backend_bootstrap import backend_bootstrap
from core.config import config
from core.database import brain
from core.identity.service import identity_service
from core.llm_runtime_activity import llm_runtime_activity_service
from core.local_mode import local_mode_enabled
from core.plugin_runtime import plugin_runtime
from core.request_traffic import api_request_traffic_service
from core.runtime_control import runtime_control

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_from_any(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, default: str = "") -> str:
    text = " ".join(str(value or "").strip().split())
    return text or default


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not parsed == parsed:  # NaN
        return None
    return parsed


def _enum_value(value: Any, default: str = "") -> str:
    raw = getattr(value, "value", value)
    return _clean_text(raw, default)


def _provider_label(provider: Any) -> str:
    clean = _clean_text(provider).lower()
    overrides = {
        "openai": "OpenAI",
        "local_openai": "Local OpenAI",
        "deepseek": "DeepSeek",
        "ollama": "Ollama",
    }
    if clean in overrides:
        return overrides[clean]
    return clean.replace("_", " ").title() or "Unknown"


def _owner_root_key(owner_user_id: int) -> str:
    return f"owner:{int(owner_user_id)}"


def _settings_snapshot() -> Any:
    try:
        from core.settings_boot import settings_service

        return settings_service.snapshot()
    except Exception:
        logger.debug("settings snapshot unavailable for runtime payload", exc_info=True)
        return None


async def _safe_await(awaitable: Any, fallback: Any) -> Any:
    try:
        return await awaitable
    except Exception:
        logger.debug("runtime mission source failed", exc_info=True)
        return fallback


# --------------------------------------------------------------------------
# Mission snapshot (slim rebuild of the deleted runtime_authority assembler)
# --------------------------------------------------------------------------

def _build_llm_block(settings: Any, owner_user_id: int) -> Dict[str, Any]:
    providers: list[Dict[str, Any]] = []
    task_routing: Dict[str, Any] = {}
    default_provider = ""
    default_model_reference = ""
    default_provider_configured = False
    if settings is not None:
        try:
            default_provider = _clean_text(settings.llm.default_provider)
            for name, provider_cfg in dict(settings.providers or {}).items():
                providers.append(
                    {
                        "provider": str(name),
                        "label": _provider_label(name),
                        "configured": bool(getattr(provider_cfg, "enabled", False)),
                    }
                )
            configured_names = {p["provider"] for p in providers if p["configured"]}
            default_provider_configured = default_provider in configured_names
            routing = settings.llm.routing
            for task in (
                "response", "decision", "analysis", "memory",
                "proactive", "reflection", "web_search", "vision",
            ):
                entry = getattr(routing, task, None)
                if entry is None:
                    continue
                provider = _clean_text(entry.provider)
                model = _clean_text(entry.model)
                reference = f"{provider}::{model}" if provider and model else model
                task_routing[task] = {
                    "provider": provider,
                    "provider_label": _provider_label(provider),
                    "provider_known": True,
                    "provider_configured": provider in configured_names,
                    "model": model,
                    "requested_provider": provider,
                    "requested_provider_label": _provider_label(provider),
                    "requested_model": model,
                    "requested_model_reference": reference,
                    "effective_provider": provider,
                    "effective_provider_label": _provider_label(provider),
                    "effective_model": model,
                    "effective_model_reference": reference,
                    "route_changed": False,
                    "resolution_reason": "settings_routing",
                    "issue": None,
                }
            response_route = task_routing.get("response") or {}
            default_model_reference = _clean_text(response_route.get("effective_model_reference"))
        except Exception:
            logger.debug("llm settings block failed", exc_info=True)
    try:
        recent_activity = llm_runtime_activity_service.get_summary(
            owner_user_id=int(owner_user_id), limit=8
        )
    except Exception:
        logger.debug("llm activity summary failed", exc_info=True)
        recent_activity = {
            "scope": "unavailable", "call_count": 0, "success_count": 0,
            "failure_count": 0, "last_call": None, "last_response": None,
            "response_lanes": [], "recent_calls": [],
        }
    return {
        "default_provider": default_provider,
        "default_provider_label": _provider_label(default_provider),
        "default_model_reference": default_model_reference,
        "default_provider_configured": default_provider_configured,
        "providers": providers,
        "recent_activity": recent_activity,
        "task_routing": task_routing,
        "reply_surfaces": [],
        "route_issues": [],
        # Required unguarded fields in RuntimeMissionCard:
        "routing_ready": bool(task_routing) and default_provider_configured,
        "routing_issue_count": 0,
        "critical_route_failure_count": 0,
    }


def _build_voice_runtime_block(settings: Any) -> Dict[str, Any]:
    voice_cfg = getattr(settings, "voice", None) if settings is not None else None
    engine = _enum_value(getattr(voice_cfg, "engine", None), "classic")
    interaction_mode = _enum_value(getattr(voice_cfg, "interaction_mode", None), "direct_address")
    return {
        "bootstrap_ok": voice_cfg is not None,
        "bootstrap_messages": [],
        # Required unguarded fields:
        "stt_provider": _enum_value(getattr(voice_cfg, "stt_provider", None), "unknown"),
        "tts_provider": _enum_value(getattr(voice_cfg, "tts_provider", None), "unknown"),
        "voice_engine_default": engine,
        "voice_engine_default_label": engine.replace("_", " ").title(),
        "saved_defaults": {
            "voice_engine": engine,
            "interaction_mode": interaction_mode,
            "presence_profile": "ambient",
            "focus_state": "available",
            "local_transport_mode": "auto",
        },
        "surface_engines": [],
        "browser_paths": [],
    }


def _build_features_block(settings: Any) -> Dict[str, Any]:
    channels = getattr(settings, "channels", None) if settings is not None else None

    def _channel_enabled(name: str) -> bool:
        entry = getattr(channels, name, None)
        return bool(getattr(entry, "enabled", False))

    return {
        "discord_enabled": _channel_enabled("discord"),
        "telegram_enabled": _channel_enabled("telegram"),
        "slack_enabled": _channel_enabled("slack"),
        "voice_enabled": getattr(settings, "voice", None) is not None,
        "agentic_enabled": bool(getattr(config, "AGENTIC_ENABLED", False)),
        "agentic_mode": _clean_text(getattr(config, "AGENTIC_MODE", ""), "off"),
        "agentic_rollout_mode": _clean_text(getattr(config, "AGENTIC_ROLLOUT_MODE", ""), "off"),
        "privileged_autonomy_enabled": False,
    }


async def _build_plugins_section() -> Dict[str, Any]:
    items = await _safe_await(plugin_runtime.list_plugins(), [])
    items = _list_from_any(items)
    enabled = sum(
        1 for item in items
        if bool(_mapping(item).get("applied_enabled") or _mapping(item).get("desired_enabled"))
    )
    errors = sum(
        1 for item in items
        if _clean_text(_mapping(item).get("health_status")).lower() in {"error", "failed"}
    )
    warnings = sum(
        1 for item in items
        if _clean_text(_mapping(item).get("health_status")).lower() in {"degraded", "restart_required"}
    )
    restart_pending = any(bool(_mapping(item).get("restart_required")) for item in items)
    return {
        "status": "degraded" if errors else ("ready" if items else "quiet"),
        "counts": {"total": len(items), "enabled": enabled, "errors": errors, "warnings": warnings},
        "restart_pending": restart_pending,
        "items": items,
        "recent_runs": [],
    }


async def _build_sessions_section(owner_user_id: int) -> Dict[str, Any]:
    rows = await _safe_await(
        runtime_control.list_sessions(owner_user_id=int(owner_user_id), limit=24), []
    )
    rows = _list_from_any(rows)
    active = sum(1 for row in rows if _clean_text(_mapping(row).get("status")).lower() == "active")
    return {
        "status": "ready" if rows else "quiet",
        "counts": {"total": len(rows), "active": active, "threads": 0},
        # Old payload items were thread-shaped; raw surface-session rows would
        # mislabel, so the list stays empty (the card tolerates []).
        "items": [],
        "current_thread": None,
        "surface_sessions": [],
        "recent_workspaces": [],
    }


async def _build_jobs_section(owner_user_id: int) -> Dict[str, Any]:
    rows = await _safe_await(
        runtime_control.list_jobs(owner_user_id=int(owner_user_id), limit=24), []
    )
    rows = _list_from_any(rows)

    def _count(statuses: set[str]) -> int:
        return sum(1 for row in rows if _clean_text(_mapping(row).get("status")).lower() in statuses)

    return {
        "status": "ready" if rows else "quiet",
        "counts": {
            "total": len(rows),
            "active": _count({"running", "in_progress", "waiting"}),
            "scheduled": _count({"scheduled", "queued", "pending"}),
            "failed": _count({"failed", "timed_out", "errored"}),
        },
        "items": rows,
        "next_due": None,
    }


def _build_health_section(
    user: Dict[str, Any], settings: Any, owner_user_id: int,
    plugins_section: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        startup = backend_bootstrap.snapshot()
        raw_status = _clean_text(backend_bootstrap.health_status(), "unknown")
    except Exception:
        startup, raw_status = None, "unknown"
    status = {"healthy": "ready", "unhealthy": "failed"}.get(raw_status, raw_status)

    db_path = ""
    db_writable = False
    try:
        db_path = str(brain.db_path)
        db_writable = os.access(db_path, os.W_OK)
    except Exception:
        pass

    return {
        "status": status,
        "backend": {"url": "", "status": status, "startup": startup},
        "database": {"absolute_path": db_path, "writable": db_writable},
        "plugins": {
            "status": _clean_text(plugins_section.get("status"), "quiet"),
            "counts": dict(_mapping(plugins_section.get("counts"))),
            "restart_pending": bool(plugins_section.get("restart_pending")),
        },
        "warnings": [],
        "runtime": {
            "deployment": {
                "mode": _clean_text(getattr(config, "PALLI_DEPLOYMENT_MODE", ""), "local"),
                "local_mode": bool(local_mode_enabled()),
                "dashboard_no_auth": bool(getattr(config, "LOCAL_DASHBOARD_NO_AUTH", False)),
                "owner_display_name": _clean_text(
                    getattr(config, "LOCAL_OWNER_DISPLAY_NAME", ""),
                    _clean_text(user.get("email"), "Owner"),
                ),
            },
            "operator": {
                "id": int(user.get("id") or 0),
                "email": _clean_text(user.get("email")),
                "discord_user_id": _clean_text(user.get("discord_user_id")),
            },
            "features": _build_features_block(settings),
            "llm": _build_llm_block(settings, owner_user_id),
            "voice": _build_voice_runtime_block(settings),
        },
    }


async def _build_mission_snapshot(user: Dict[str, Any]) -> Dict[str, Any]:
    owner_user_id = int(user.get("id") or 0)
    settings = _settings_snapshot()
    plugins_section = await _build_plugins_section()
    sessions_section = await _build_sessions_section(owner_user_id)
    jobs_section = await _build_jobs_section(owner_user_id)
    try:
        warnings = list(backend_bootstrap.warning_items())
    except Exception:
        warnings = []
    return {
        "owner_root_key": _owner_root_key(owner_user_id),
        "owner_user_id": owner_user_id,
        "generated_at": time.time(),
        "warnings": warnings,
        # Sections backed by W5-deleted subsystems (runs, agent, voice runtime,
        # context, ambient, operator workflows, reminders, heartbeat/proactive,
        # standing_orders, channels, nodes) are intentionally omitted: the
        # dashboard hides those panels when the key is absent.
        "sections": {
            "health": _build_health_section(user, settings, owner_user_id, plugins_section),
            "sessions": sessions_section,
            "jobs": jobs_section,
            "plugins": plugins_section,
        },
    }


# --------------------------------------------------------------------------
# Under-hood observatory (restored minus the deleted behavior-trace inputs)
# --------------------------------------------------------------------------

def _empty_tier(tier_id: str, label: str, headline: str) -> Dict[str, Any]:
    return {"id": tier_id, "label": label, "headline": headline,
            "status": "empty", "signals": [], "last_seen_at": None}


def _build_cognitive_payload(*, owner_user_id: int, now: float) -> Dict[str, Any]:
    # Behavior traces and behavior controls were deleted in Wave 5, and the
    # six inert cognitive feature flags in the 2026-07-16 inert-flag cut;
    # this is the honest "unobserved" rendering of the old cognitive payload.
    return {
        "section": "cognitive_architecture",
        "owner_user_id": int(owner_user_id),
        "updated_at": now,
        "live": {
            "status": "unobserved", "latest_trace_id": None, "age_seconds": None,
            "mode": "unknown", "strategy_action": "unknown", "delivery_shape": "unknown",
            "channel": "unknown", "message_id": "", "last_seen_at": None,
        },
        "tiers": [
            _empty_tier("perception", "Tier 1: Perception and situation modeling", "No recent intake frame"),
            _empty_tier("state_engine", "Tier 2: Memory and state engine", "No recent state-engine frame"),
            _empty_tier("strategy", "Tier 3: Interaction strategy and discourse", "No recent strategy frame"),
            _empty_tier("delivery", "Tier 4: Linguistic execution and delivery", "No recent delivery frame"),
        ],
        "control_plane": {"active_count": 0, "active_controls": [], "supported_controls": []},
        "feature_flags": [],
        "stats": {
            "trace_count": 0, "mode_counts": {}, "strategy_counts": {},
            "delivery_counts": {}, "active_control_count": 0,
        },
        "recent_traces": [],
    }


_UNDER_HOOD_SECTIONS: tuple[str, ...] = (
    "traffic", "context", "jobs", "voice", "operator", "heartbeat", "nodes",
)


async def _under_hood_source(
    label: str, awaitable: Any, *, timeout_seconds: float = 1.25
) -> tuple[str, Dict[str, Any]]:
    try:
        payload = await asyncio.wait_for(awaitable, timeout=max(float(timeout_seconds), 0.1))
    except Exception as exc:
        return label, {
            "status": "error",
            "error": _clean_text(str(exc), exc.__class__.__name__),
            "payload": {},
        }
    return label, {
        "status": "ready", "error": "",
        "payload": payload if isinstance(payload, dict) else {},
    }


async def _static_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload


def _clip_under_hood_text(value: Any, *, limit: int = 220) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _under_hood_string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, str):
        return [_clean_text(item) for item in value.split(",") if _clean_text(item)]
    return []


async def _build_under_hood_memory_overview(*, owner_user_id: int) -> Dict[str, Any]:
    # NOTE: reads brain.list_memory_index_items directly instead of
    # memory_workspace_service.build_overview — the latter calls
    # rebuild_index() and the observatory polls every 6 seconds.
    items = await brain.list_memory_index_items(owner_user_id=int(owner_user_id), limit=96)
    counts: Dict[str, int] = {}
    recent: list[Dict[str, Any]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        item_map = _mapping(item)
        source_kind = _clean_text(item_map.get("source_kind"), "unknown")
        counts[source_kind] = counts.get(source_kind, 0) + 1
        if index >= 24:
            continue
        recent.append(
            {
                "item_id": _clean_text(item_map.get("item_id"), f"memory:{index}"),
                "source_kind": source_kind,
                "source_id": _clean_text(item_map.get("source_id")),
                "title": _clean_text(item_map.get("title"), source_kind.replace("_", " ").title()),
                "snippet": _clip_under_hood_text(item_map.get("snippet") or item_map.get("content")),
                "content": _clip_under_hood_text(item_map.get("content"), limit=360),
                "salience": _clean_text(item_map.get("salience"), "low"),
                "tags": _under_hood_string_list(item_map.get("tags")),
                "related_refs": _under_hood_string_list(item_map.get("related_refs")),
                "updated_at": _float_or_none(item_map.get("updated_at")),
                "score": 0.0,
                "score_explanation": "indexed_preview",
            }
        )
    return {
        "counts": counts,
        "capability": {"status": "ready" if recent else "empty", "mode": "indexed_preview"},
        "recent_captures": recent,
    }


def _source_payload(sources: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    return _mapping(_mapping(sources.get(label)).get("payload"))


def _count_section_items(section: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = section.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, Mapping):
            return len(value)
    counts = _mapping(section.get("counts"))
    for value in counts.values():
        parsed = _float_or_none(value)
        if parsed is not None:
            return int(parsed)
    return 0


def _first_number(section: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        parsed = _float_or_none(section.get(key))
        if parsed is not None:
            return int(parsed)
    return 0


def _sum_numeric_counts(counts: Mapping[str, Any]) -> int:
    total = 0
    for value in counts.values():
        parsed = _float_or_none(value)
        if parsed is not None:
            total += int(parsed)
    return total


def _section_status(section: Mapping[str, Any], fallback: str = "quiet") -> str:
    return _clean_text(section.get("status")) or fallback


def _activity_item(
    *, kind: str, title: str, detail: str = "", created_at: Any = None,
    status: str = "", source: str = "",
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "title": _clean_text(title, "Untitled signal"),
        "detail": _clean_text(detail),
        "created_at": _float_or_none(created_at),
        "status": _clean_text(status, "observed"),
        "source": _clean_text(source, kind),
    }


def _build_under_hood_activity(
    *, sections: Mapping[str, Mapping[str, Any]],
    memory_overview: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    activity: list[Dict[str, Any]] = []
    for capture in _list_from_any(memory_overview.get("recent_captures"))[:8]:
        capture_map = _mapping(capture)
        activity.append(_activity_item(
            kind="memory_capture",
            title=_clean_text(capture_map.get("title"), "Memory capture"),
            detail=_clean_text(capture_map.get("snippet") or capture_map.get("source_kind")),
            created_at=capture_map.get("updated_at"),
            status=_clean_text(capture_map.get("salience"), "memory"),
            source="memory",
        ))
    jobs = _mapping(sections.get("jobs"))
    for job in _list_from_any(jobs.get("items"))[:6]:
        job_map = _mapping(job)
        activity.append(_activity_item(
            kind="runtime_job",
            title=_clean_text(job_map.get("title") or job_map.get("job_id"), "Runtime job"),
            detail=_clean_text(job_map.get("job_type") or job_map.get("kind") or job_map.get("source")),
            created_at=job_map.get("updated_at") or job_map.get("created_at"),
            status=_clean_text(job_map.get("status"), "job"),
            source="jobs",
        ))
    traffic = _mapping(sections.get("traffic"))
    if traffic:
        in_flight = _first_number(traffic, "in_flight")
        activity.append(_activity_item(
            kind="traffic_window",
            title=f"{in_flight} request{'s' if in_flight != 1 else ''} in flight",
            detail="HTTP and dashboard traffic moving through the runtime",
            created_at=traffic.get("updated_at") or time.time(),
            status=_section_status(traffic, "traffic"),
            source="traffic",
        ))

    def sort_key(item: Dict[str, Any]) -> float:
        created_at = _float_or_none(item.get("created_at"))
        return created_at if created_at is not None else -1.0

    return sorted(activity, key=sort_key, reverse=True)[:32]


def _memory_weight(salience: Any) -> float:
    clean = _clean_text(salience).lower()
    if clean == "high":
        return 1.0
    if clean == "medium":
        return 0.72
    if clean:
        return 0.48
    return 0.38


def _add_galaxy_node(nodes: list[Dict[str, Any]], seen: set[str], node: Dict[str, Any]) -> None:
    node_id = _clean_text(node.get("id"))
    if not node_id or node_id in seen:
        return
    seen.add(node_id)
    nodes.append(node)


def _build_memory_galaxy(memory_overview: Mapping[str, Any]) -> Dict[str, Any]:
    nodes: list[Dict[str, Any]] = []
    links: list[Dict[str, Any]] = []
    seen_nodes: set[str] = set()
    counts = _mapping(memory_overview.get("counts"))

    for index, capture in enumerate(_list_from_any(memory_overview.get("recent_captures"))[:36]):
        capture_map = _mapping(capture)
        node_id = _clean_text(capture_map.get("item_id"), f"memory:{index}")
        source_kind = _clean_text(capture_map.get("source_kind"), "memory")
        tags = [
            _clean_text(tag)
            for tag in _list_from_any(capture_map.get("tags")) if _clean_text(tag)
        ]
        related_refs = [
            _clean_text(ref)
            for ref in _list_from_any(capture_map.get("related_refs")) if _clean_text(ref)
        ]
        _add_galaxy_node(nodes, seen_nodes, {
            "id": node_id,
            "label": _clean_text(capture_map.get("title"), node_id),
            "summary": _clean_text(capture_map.get("snippet") or capture_map.get("content")),
            "group": "memory",
            "source_kind": source_kind,
            "source_id": _clean_text(capture_map.get("source_id")),
            "salience": _clean_text(capture_map.get("salience"), "low"),
            "weight": _memory_weight(capture_map.get("salience")),
            "tags": tags,
            "related_refs": related_refs,
            "updated_at": _float_or_none(capture_map.get("updated_at")),
        })
        if source_kind:
            kind_count = _float_or_none(counts.get(source_kind)) or 0.0
            _add_galaxy_node(nodes, seen_nodes, {
                "id": f"kind:{source_kind}",
                "label": source_kind.replace("_", " ").title(),
                "group": "source_kind",
                "weight": max(0.42, min(1.2, 0.35 + kind_count / 10.0)),
                "count": int(kind_count),
            })
            links.append({
                "source": node_id, "target": f"kind:{source_kind}",
                "kind": "source_kind", "strength": 0.62,
            })
        for tag in tags[:8]:
            tag_id = f"tag:{tag}"
            _add_galaxy_node(nodes, seen_nodes, {"id": tag_id, "label": tag, "group": "tag", "weight": 0.46})
            links.append({"source": node_id, "target": tag_id, "kind": "tag", "strength": 0.78})
        for ref in related_refs[:6]:
            ref_id = f"ref:{ref}"
            _add_galaxy_node(nodes, seen_nodes, {
                "id": ref_id, "label": ref, "group": "continuity_ref", "weight": 0.36,
            })
            links.append({
                "source": node_id, "target": ref_id,
                "kind": "continuity_ref", "strength": 0.42,
            })

    for source_kind, count in counts.items():
        kind_label = _clean_text(source_kind, "memory")
        _add_galaxy_node(nodes, seen_nodes, {
            "id": f"kind:{kind_label}",
            "label": kind_label.replace("_", " ").title(),
            "group": "source_kind",
            "weight": max(0.42, min(1.2, 0.35 + (_float_or_none(count) or 0.0) / 10.0)),
            "count": int(_float_or_none(count) or 0),
        })

    return {
        "nodes": nodes,
        "links": links,
        "counts": {
            "nodes": len(nodes),
            "links": len(links),
            "captures": len(_list_from_any(memory_overview.get("recent_captures"))),
            "indexed_items": _sum_numeric_counts(counts),
        },
    }


def _build_under_hood_subsystems(
    *, cognitive: Mapping[str, Any],
    sections: Mapping[str, Mapping[str, Any]],
    memory_overview: Mapping[str, Any],
    runtime_status: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    stats = _mapping(cognitive.get("stats"))
    memory_total = _sum_numeric_counts(_mapping(memory_overview.get("counts")))
    traffic = _mapping(sections.get("traffic"))
    context = _mapping(sections.get("context"))
    jobs = _mapping(sections.get("jobs"))
    voice = _mapping(sections.get("voice"))
    operator = _mapping(sections.get("operator"))
    heartbeat = _mapping(sections.get("heartbeat"))
    nodes = _mapping(sections.get("nodes"))
    return [
        {"id": "runtime", "label": "Runtime Core", "kind": "control",
         "status": _clean_text(runtime_status.get("status"), "online"),
         "metric": len(_mapping(runtime_status.get("features"))),
         "detail": "Feature switches and owner runtime state"},
        {"id": "cognitive", "label": "Cognitive Stack", "kind": "reasoning",
         "status": _mapping(cognitive.get("live")).get("status") or "unobserved",
         "metric": int(_float_or_none(stats.get("trace_count")) or 0),
         "detail": "Perception, state, strategy, and delivery traces"},
        {"id": "memory", "label": "Memory Galaxy", "kind": "memory",
         "status": _clean_text(_mapping(memory_overview.get("capability")).get("status"), "ready"),
         "metric": memory_total,
         "detail": "Indexed captures, topic tags, and continuity refs"},
        {"id": "traffic", "label": "Traffic", "kind": "io",
         "status": _section_status(traffic, "quiet"),
         "metric": _first_number(traffic, "in_flight"),
         "detail": "Live HTTP and dashboard request flow"},
        {"id": "context", "label": "Context", "kind": "situation",
         "status": _section_status(context, "quiet"),
         "metric": _count_section_items(context, "events", "items"),
         "detail": "Ambient context and recent runtime events"},
        {"id": "operator", "label": "Operator", "kind": "agency",
         "status": _section_status(operator, "idle"),
         "metric": _count_section_items(operator, "active_workflows", "items", "actions"),
         "detail": "Actions, workflows, and follow-through state"},
        {"id": "jobs", "label": "Jobs", "kind": "work",
         "status": _section_status(jobs, "idle"),
         "metric": _count_section_items(jobs, "items", "jobs"),
         "detail": "Deferred and currently running runtime work"},
        {"id": "voice", "label": "Voice", "kind": "presence",
         "status": _section_status(voice, "idle"),
         "metric": _count_section_items(voice, "open_sessions", "items", "sessions"),
         "detail": "Voice sessions and presence lane state"},
        {"id": "heartbeat", "label": "Heartbeat", "kind": "initiative",
         "status": _section_status(heartbeat, "quiet"),
         "metric": 1 if _section_status(heartbeat, "quiet") in {"active", "ready"} else 0,
         "detail": "Background initiative and scheduled checks"},
        {"id": "nodes", "label": "Devices", "kind": "surface",
         "status": _section_status(nodes, "quiet"),
         "metric": _count_section_items(nodes, "items", "nodes"),
         "detail": "Connected local surfaces and device capabilities"},
        {"id": "delivery", "label": "Delivery", "kind": "language",
         "status": _clean_text(_mapping(cognitive.get("live")).get("delivery_shape"), "unknown"),
         "metric": len(_mapping(stats.get("delivery_counts"))),
         "detail": "Reply posture, burst shape, and linguistic execution"},
    ]


def _under_hood_relations() -> list[Dict[str, Any]]:
    return [
        {"source": "traffic", "target": "runtime", "label": "requests", "strength": 0.72},
        {"source": "runtime", "target": "context", "label": "surface state", "strength": 0.62},
        {"source": "context", "target": "cognitive", "label": "situational cues", "strength": 0.82},
        {"source": "memory", "target": "cognitive", "label": "recall context", "strength": 0.86},
        {"source": "cognitive", "target": "operator", "label": "delegation", "strength": 0.74},
        {"source": "cognitive", "target": "delivery", "label": "reply posture", "strength": 0.88},
        {"source": "voice", "target": "delivery", "label": "timing and pacing", "strength": 0.68},
        {"source": "heartbeat", "target": "operator", "label": "initiative", "strength": 0.7},
        {"source": "jobs", "target": "operator", "label": "work queue", "strength": 0.64},
        {"source": "nodes", "target": "runtime", "label": "surfaces", "strength": 0.58},
    ]


async def _resolve_memory_owner_user_id(user: Dict[str, Any]) -> int:
    # Memories are written under the identity-resolved local owner id, which
    # is not always the account id (see commit 6e236ca).
    try:
        owner_user_id = await identity_service.resolve_local_owner_user_id(
            account_user_id=int(user["id"])
        )
        if owner_user_id is not None:
            return int(owner_user_id)
    except Exception:
        logger.debug("identity owner resolution failed", exc_info=True)
    return int(user.get("id") or 0)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("/runtime/mission")
async def get_runtime_mission(request: Request):
    user = await _require_user(request)
    return await _build_mission_snapshot(user)


@router.get("/runtime/cognitive-architecture")
async def get_runtime_cognitive_architecture(request: Request, limit: int = 48):
    # Behavior traces were deleted in Wave 5 — this returns the honest
    # "unobserved" payload so the admin console renders instead of erroring.
    user = await _require_user(request)
    return _build_cognitive_payload(
        owner_user_id=int(user.get("id") or 0),
        now=time.time(),
    )


@router.get("/runtime/under-hood")
async def get_runtime_under_hood(
    request: Request, owner_user_id: Optional[int] = None, limit: int = 32
):
    user = await _require_user(request)
    account_user_id = int(owner_user_id or user["id"])
    memory_owner_user_id = await _resolve_memory_owner_user_id(user)
    now = time.time()

    runtime_status_payload = {
        "status": "online",
        "features": {},
    }
    try:
        traffic_payload = api_request_traffic_service.get_snapshot()
    except Exception:
        traffic_payload = {}

    source_calls = [
        _under_hood_source("runtime_status", _static_source(runtime_status_payload)),
        _under_hood_source("mission", _build_mission_snapshot(user), timeout_seconds=2.5),
        _under_hood_source(
            "memory",
            _build_under_hood_memory_overview(owner_user_id=memory_owner_user_id),
            timeout_seconds=1.5,
        ),
        _under_hood_source("traffic", _static_source(dict(_mapping(traffic_payload)))),
        _under_hood_source("jobs", _build_jobs_section(account_user_id)),
    ]
    # Sections whose backing subsystems were deleted in Wave 5 stay as honest
    # empty sources so the observatory shows them as quiet rather than erroring.
    for section in ("context", "voice", "operator", "heartbeat", "nodes"):
        source_calls.append(_under_hood_source(section, _static_source({"status": "quiet"})))

    source_pairs = await asyncio.gather(*source_calls)
    sources = dict(source_pairs)

    cognitive = _build_cognitive_payload(owner_user_id=account_user_id, now=now)
    sections = {section: _source_payload(sources, section) for section in _UNDER_HOOD_SECTIONS}
    memory_overview = _source_payload(sources, "memory")
    runtime_status = _source_payload(sources, "runtime_status")
    mission_snapshot = _source_payload(sources, "mission")

    return {
        "section": "under_hood_observatory",
        "owner_user_id": account_user_id,
        "updated_at": now,
        "sources": {
            label: {
                "status": _clean_text(_mapping(source).get("status"), "unknown"),
                "error": _clean_text(_mapping(source).get("error")),
            }
            for label, source in sources.items()
        },
        "health": {
            "runtime_status": _clean_text(runtime_status.get("status"), "unknown"),
            "mission_sections": len(_mapping(mission_snapshot.get("sections"))),
            "degraded_sources": [
                label for label, source in sources.items()
                if _clean_text(_mapping(source).get("status")) == "error"
            ],
        },
        "subsystems": _build_under_hood_subsystems(
            cognitive=cognitive, sections=sections,
            memory_overview=memory_overview, runtime_status=runtime_status,
        ),
        "relations": _under_hood_relations(),
        "activity": _build_under_hood_activity(sections=sections, memory_overview=memory_overview),
        "memory_galaxy": _build_memory_galaxy(memory_overview),
        "cognitive": cognitive,
        "raw": {
            "runtime_status": runtime_status,
            "sections": sections,
            "memory": memory_overview,
        },
    }
