from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class PluginRuntimeStore:
    """Persistence helper for plugin runtime catalog state and run history."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _clean_text(self, value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        text = self._clean_text(value)
        return text or None

    def _clean_text_list(self, values: Optional[List[Any]]) -> List[str]:
        items: List[str] = []
        for value in values or []:
            text = self._clean_text(value)
            if text:
                items.append(text)
        return items

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _plugin_runtime_plugin_record(self, row: Any) -> Dict[str, Any]:
        return {
            "plugin_id": self._clean_text(row[0]),
            "display_name": self._clean_text(row[1]),
            "version": self._clean_text(row[2]),
            "plugin_api_version": self._clean_text(row[3]),
            "description": self._clean_text(row[4]),
            "entrypoint": self._clean_text(row[5]),
            "source_kind": self._clean_text(row[6]),
            "source_path": self._clean_text(row[7]),
            "families": self._load_json(row[8], []),
            "criticality": self._clean_text(row[9]),
            "reload_mode": self._clean_text(row[10]),
            "host_support": self._load_json(row[11], []),
            "requires_json": self._load_json(row[12], {}),
            "manifest": self._load_json(row[13], {}),
            "desired_enabled": bool(row[14]),
            "applied_enabled": bool(row[15]),
            "compatibility_status": self._clean_text(row[16]),
            "health_status": self._clean_text(row[17]),
            "restart_required": bool(row[18]),
            "last_error": self._clean_text(row[19]),
            "last_discovered_at": float(row[20]) if row[20] is not None else None,
            "last_applied_at": float(row[21]) if row[21] is not None else None,
            "metadata": self._load_json(row[22], {}),
            "created_at": float(row[23] or 0.0),
            "updated_at": float(row[24] or 0.0),
        }

    def _plugin_runtime_run_record(self, row: Any) -> Dict[str, Any]:
        return {
            "plugin_runtime_run_id": self._clean_text(row[0]),
            "trigger_kind": self._clean_text(row[1]),
            "action_kind": self._clean_text(row[2]),
            "status": self._clean_text(row[3]),
            "changed_plugin_ids": self._load_json(row[4], []),
            "restart_required": bool(row[5]),
            "summary": self._clean_text(row[6]),
            "error_text": self._clean_text(row[7]),
            "metadata": self._load_json(row[8], {}),
            "created_at": float(row[9] or 0.0),
            "updated_at": float(row[10] or 0.0),
        }

    async def upsert_plugin_runtime_plugin(
        self,
        *,
        plugin_id: str,
        display_name: str,
        version: Optional[str] = None,
        plugin_api_version: Optional[str] = None,
        description: Optional[str] = None,
        entrypoint: Optional[str] = None,
        source_kind: str,
        source_path: Optional[str] = None,
        families: Optional[List[str]] = None,
        criticality: str,
        reload_mode: str,
        host_support: Optional[List[str]] = None,
        requires_json: Optional[Dict[str, Any]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        desired_enabled: bool = False,
        applied_enabled: bool = False,
        compatibility_status: str = "unknown",
        health_status: str = "unknown",
        restart_required: bool = False,
        last_error: Optional[str] = None,
        last_discovered_at: Optional[float] = None,
        last_applied_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = float(created_at) if created_at is not None else self._now()
        try:
            await conn.execute(
                """
                INSERT INTO plugin_runtime_plugins
                (
                    plugin_id, display_name, version, plugin_api_version, description,
                    entrypoint, source_kind, source_path, families, criticality,
                    reload_mode, host_support, requires_json, manifest, desired_enabled,
                    applied_enabled, compatibility_status, health_status, restart_required,
                    last_error, last_discovered_at, last_applied_at, metadata, created_at, updated_at
                )
                VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12::jsonb, $13::jsonb, $14::jsonb, $15, $16, $17, $18, $19, $20, $21, $22, $23::jsonb, $24, $24)
                ON CONFLICT (plugin_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    version = COALESCE(EXCLUDED.version, plugin_runtime_plugins.version),
                    plugin_api_version = COALESCE(EXCLUDED.plugin_api_version, plugin_runtime_plugins.plugin_api_version),
                    description = COALESCE(EXCLUDED.description, plugin_runtime_plugins.description),
                    entrypoint = COALESCE(EXCLUDED.entrypoint, plugin_runtime_plugins.entrypoint),
                    source_kind = EXCLUDED.source_kind,
                    source_path = COALESCE(EXCLUDED.source_path, plugin_runtime_plugins.source_path),
                    families = EXCLUDED.families,
                    criticality = EXCLUDED.criticality,
                    reload_mode = EXCLUDED.reload_mode,
                    host_support = EXCLUDED.host_support,
                    requires_json = EXCLUDED.requires_json,
                    manifest = EXCLUDED.manifest,
                    desired_enabled = EXCLUDED.desired_enabled,
                    applied_enabled = EXCLUDED.applied_enabled,
                    compatibility_status = EXCLUDED.compatibility_status,
                    health_status = EXCLUDED.health_status,
                    restart_required = EXCLUDED.restart_required,
                    last_error = EXCLUDED.last_error,
                    last_discovered_at = EXCLUDED.last_discovered_at,
                    last_applied_at = COALESCE(EXCLUDED.last_applied_at, plugin_runtime_plugins.last_applied_at),
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                self._clean_text(plugin_id),
                self._clean_text(display_name),
                self._clean_optional_text(version),
                self._clean_optional_text(plugin_api_version),
                self._clean_optional_text(description),
                self._clean_optional_text(entrypoint),
                self._clean_text(source_kind),
                self._clean_optional_text(source_path),
                self._dump_json(self._clean_text_list(families)),
                self._clean_text(criticality),
                self._clean_text(reload_mode),
                self._dump_json(self._clean_text_list(host_support)),
                self._dump_json(dict(requires_json or {})),
                self._dump_json(dict(manifest or {})),
                bool(desired_enabled),
                bool(applied_enabled),
                self._clean_text(compatibility_status),
                self._clean_text(health_status),
                bool(restart_required),
                self._clean_optional_text(last_error),
                float(last_discovered_at) if last_discovered_at is not None else now,
                float(last_applied_at) if last_applied_at is not None else None,
                self._dump_json(dict(metadata or {})),
                now,
            )
        finally:
            await conn.close()

    async def get_plugin_runtime_plugin(self, *, plugin_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT plugin_id, display_name, version, plugin_api_version, description,
                       entrypoint, source_kind, source_path, families, criticality,
                       reload_mode, host_support, requires_json, manifest, desired_enabled,
                       applied_enabled, compatibility_status, health_status, restart_required,
                       last_error, last_discovered_at, last_applied_at, metadata, created_at, updated_at
                FROM plugin_runtime_plugins
                WHERE plugin_id = $1
                LIMIT 1
                """,
                self._clean_text(plugin_id),
            )
            return self._plugin_runtime_plugin_record(row) if row else None
        finally:
            await conn.close()

    async def list_plugin_runtime_plugins(self) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT plugin_id, display_name, version, plugin_api_version, description,
                       entrypoint, source_kind, source_path, families, criticality,
                       reload_mode, host_support, requires_json, manifest, desired_enabled,
                       applied_enabled, compatibility_status, health_status, restart_required,
                       last_error, last_discovered_at, last_applied_at, metadata, created_at, updated_at
                FROM plugin_runtime_plugins
                ORDER BY source_kind ASC, display_name COLLATE NOCASE ASC, plugin_id ASC
                """
            )
            return [self._plugin_runtime_plugin_record(row) for row in rows]
        finally:
            await conn.close()

    async def update_plugin_runtime_plugin(
        self,
        *,
        plugin_id: str,
        desired_enabled: Optional[bool] = None,
        applied_enabled: Optional[bool] = None,
        compatibility_status: Optional[str] = None,
        health_status: Optional[str] = None,
        restart_required: Optional[bool] = None,
        last_error: Optional[str] = None,
        last_discovered_at: Optional[float] = None,
        last_applied_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        clean_plugin_id = self._clean_text(plugin_id)
        if not clean_plugin_id:
            return
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            current = await conn.fetchrow(
                """
                SELECT metadata
                FROM plugin_runtime_plugins
                WHERE plugin_id = $1
                LIMIT 1
                """,
                clean_plugin_id,
            )
            if not current:
                return
            merged_metadata = self._load_json(current[0], {})
            if metadata:
                merged_metadata.update(metadata)
            await conn.execute(
                """
                UPDATE plugin_runtime_plugins
                SET desired_enabled = COALESCE($2, desired_enabled),
                    applied_enabled = COALESCE($3, applied_enabled),
                    compatibility_status = COALESCE($4, compatibility_status),
                    health_status = COALESCE($5, health_status),
                    restart_required = COALESCE($6, restart_required),
                    last_error = COALESCE($7, last_error),
                    last_discovered_at = COALESCE($8, last_discovered_at),
                    last_applied_at = COALESCE($9, last_applied_at),
                    metadata = $10::jsonb,
                    updated_at = $11
                WHERE plugin_id = $1
                """,
                clean_plugin_id,
                desired_enabled,
                applied_enabled,
                self._clean_optional_text(compatibility_status),
                self._clean_optional_text(health_status),
                restart_required,
                self._clean_optional_text(last_error),
                float(last_discovered_at) if last_discovered_at is not None else None,
                float(last_applied_at) if last_applied_at is not None else None,
                self._dump_json(merged_metadata),
                now,
            )
        finally:
            await conn.close()

    async def create_plugin_runtime_run(
        self,
        *,
        plugin_runtime_run_id: str,
        trigger_kind: str,
        action_kind: str,
        status: str,
        changed_plugin_ids: Optional[List[str]] = None,
        restart_required: bool = False,
        summary: Optional[str] = None,
        error_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        now = float(created_at) if created_at is not None else self._now()
        try:
            await conn.execute(
                """
                INSERT INTO plugin_runtime_runs
                (
                    plugin_runtime_run_id, trigger_kind, action_kind, status,
                    changed_plugin_ids, restart_required, summary, error_text,
                    metadata, created_at, updated_at
                )
                VALUES
                ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::jsonb, $10, $10)
                """,
                self._clean_text(plugin_runtime_run_id),
                self._clean_text(trigger_kind),
                self._clean_text(action_kind),
                self._clean_text(status),
                self._dump_json(self._clean_text_list(changed_plugin_ids)),
                bool(restart_required),
                self._clean_optional_text(summary),
                self._clean_optional_text(error_text),
                self._dump_json(dict(metadata or {})),
                now,
            )
        finally:
            await conn.close()

    async def list_plugin_runtime_runs(self, *, limit: int = 12) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT plugin_runtime_run_id, trigger_kind, action_kind, status,
                       changed_plugin_ids, restart_required, summary, error_text,
                       metadata, created_at, updated_at
                FROM plugin_runtime_runs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                max(int(limit or 12), 1),
            )
            return [self._plugin_runtime_run_record(row) for row in rows]
        finally:
            await conn.close()
