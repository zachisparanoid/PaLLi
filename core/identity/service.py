from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from core.config import config
from core.local_mode import ensure_local_owner, local_mode_enabled

from .compiler import compile_identity as _compile_identity_from_bundle
from .document_policy import supports_silent_maintenance
from .document_store import IdentityDocumentStore, OWNER_PLATFORM
from .materialization import IdentityBundleMaterializer
from .models import (
    ActionIdentitySnapshot,
    CompiledActionPolicy,
    CompiledIdentity,
    IdentityBundle,
    IdentityDocument,
    TextIdentitySnapshot,
    VoiceIdentitySnapshot,
)

logger = logging.getLogger(__name__)


class IdentityService:
    def __init__(self) -> None:
        self.document_store = IdentityDocumentStore(
            identity_root=getattr(config, "IDENTITY_ROOT_PATH", "identity"),
        )
        self.materializer = IdentityBundleMaterializer(document_store=self.document_store)
        self.identity_root = self.document_store.identity_root
        self.core_root = self.document_store.core_root
        self.template_root = self.document_store.template_root
        self.user_root = self.document_store.user_root
        self.owner_root = self.document_store.owner_root

    def identity_enabled(self) -> bool:
        return bool(getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True))

    async def resolve_local_owner_user_id(self, *, account_user_id: Optional[int] = None) -> Optional[int]:
        if not local_mode_enabled():
            return None

        try:
            owner = await ensure_local_owner()
        except Exception as exc:
            logger.debug("Failed to resolve local owner identity: %s", exc)
            return None

        owner_user_id = int(owner.get("id") or owner.get("palli_user_id") or 0)
        if owner_user_id <= 0:
            return None

        owner_candidates = {owner_user_id}
        try:
            owner_candidates.add(int(owner.get("palli_user_id") or owner_user_id))
        except Exception:
            pass

        if account_user_id is None:
            return owner_user_id

        try:
            candidate = int(account_user_id)
        except Exception:
            return None

        if candidate in owner_candidates:
            return owner_user_id
        return None

    def user_dir(self, user_id: int, platform: str) -> str:
        return self.document_store.user_dir(user_id, platform)

    def owner_dir(self, owner_user_id: int) -> str:
        return self.document_store.owner_dir(owner_user_id)

    def core_doc_path(self, doc_key: str) -> str:
        return self.document_store.core_doc_path(doc_key)

    def user_doc_path(self, user_id: int, platform: str, doc_key: str) -> str:
        return self.document_store.user_doc_path(user_id, platform, doc_key)

    def owner_doc_path(self, owner_user_id: int, doc_key: str) -> str:
        return self.document_store.owner_doc_path(owner_user_id, doc_key)

    def template_path(self, doc_key: str) -> str:
        return self.document_store.template_path(doc_key)

    def _resolve_storage_target(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> tuple[int, str]:
        normalized_platform = str(platform or "discord").strip().lower() or "discord"
        if normalized_platform == OWNER_PLATFORM:
            return int(user_id), OWNER_PLATFORM
        if identity_owner_id is not None:
            return int(identity_owner_id), OWNER_PLATFORM
        return int(user_id), normalized_platform

    async def ensure_user_identity_bundle(
        self,
        *,
        user_id: int,
        platform: str,
        profile: Optional[Dict[str, Any]] = None,
        actor_type: str = "system",
        actor_id: str = "identity_service",
        reason: str = "materialize_if_missing",
        identity_owner_id: Optional[int] = None,
    ) -> IdentityBundle:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )

        if not self.identity_enabled():
            return IdentityBundle(user_id=storage_user_id, platform=storage_platform)

        await self.materializer.ensure_materialized_documents(
            user_id=storage_user_id,
            platform=storage_platform,
            profile=profile,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )

        return self.load_identity_bundle_sync(user_id=storage_user_id, platform=storage_platform)

    def load_identity_bundle_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> IdentityBundle:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        bundle = IdentityBundle(user_id=storage_user_id, platform=storage_platform)

        if not self.identity_enabled():
            return bundle

        return self.document_store.load_bundle(
            user_id=storage_user_id,
            platform=storage_platform,
        )

    async def load_identity_bundle(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = False,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> IdentityBundle:
        if ensure_materialized:
            return await self.ensure_user_identity_bundle(
                user_id=user_id,
                platform=platform,
                profile=profile,
                identity_owner_id=identity_owner_id,
            )
        return self.load_identity_bundle_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )

    async def compose_prompt_context(
        self,
        *,
        user_id: int,
        platform: str,
        modality: str = "text",
        task_type: str = "response",
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> str:
        compiled = await self.compile_identity(
            user_id=user_id,
            platform=platform,
            ensure_materialized=True,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        if modality == "voice":
            return compiled.voice_snapshot.prompt_context
        elif task_type == "action":
            return compiled.action_snapshot.prompt_context
        return compiled.text_snapshot.prompt_context

    async def compile_identity(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = True,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> CompiledIdentity:
        if ensure_materialized:
            bundle = await self.ensure_user_identity_bundle(
                user_id=user_id,
                platform=platform,
                profile=profile,
                identity_owner_id=identity_owner_id,
            )
        else:
            bundle = self.load_identity_bundle_sync(
                user_id=user_id,
                platform=platform,
                identity_owner_id=identity_owner_id,
            )
        return _compile_identity_from_bundle(bundle)

    def compile_identity_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> CompiledIdentity:
        bundle = self.load_identity_bundle_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return _compile_identity_from_bundle(bundle)

    def compile_for_owner(self, *, owner_id: int) -> CompiledIdentity:
        """E.4 Wave 3 (2026-05-13): build a CompiledIdentity for the given
        owner with file-mtime-based caching. Cache key is
        ``(owner_id, max_mtime_of_identity_files)``; on change of any
        markdown file the cache invalidates.

        Pre-E.4 this constructed a stripped-down ``CompiledIdentity`` with
        just ``core``/``owner``/``action_policy`` while a sibling call to
        ``compile_identity_bundle`` produced the full snapshot triple.
        Now both shapes live on the unified ``CompiledIdentity`` and a
        single ``_compile_identity_from_bundle`` call gives us everything,
        so the cached object is the same shape every other producer
        returns.
        """
        max_mtime = self._max_identity_mtime(owner_id=owner_id)
        cached = getattr(self, "_compiled_cache", {}).get(owner_id)
        if cached is not None and cached.cache_key == (owner_id, max_mtime):
            return cached

        raw_bundle = self.load_identity_bundle_sync(
            user_id=owner_id, platform="owner",
        )
        compiled = _compile_identity_from_bundle(raw_bundle)
        compiled.cache_key = (owner_id, max_mtime)
        if not hasattr(self, "_compiled_cache"):
            self._compiled_cache = {}
        self._compiled_cache[owner_id] = compiled
        return compiled

    def _max_identity_mtime(self, *, owner_id: int) -> float:
        """Walk the core/ and owners/<id>/ directories; return the max mtime
        across all .md files. Used as the cache-invalidation key."""
        import os
        roots = [
            self.core_root,
            os.path.join(self.owner_root, str(owner_id)),
        ]
        max_m: float = 0.0
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                for entry in os.scandir(root):
                    if entry.is_file() and entry.name.endswith(".md"):
                        try:
                            max_m = max(max_m, entry.stat().st_mtime)
                        except OSError:
                            continue
            except OSError:
                continue
        return max_m

    async def get_text_identity_snapshot(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = True,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> TextIdentitySnapshot:
        compiled = await self.compile_identity(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        return compiled.text_snapshot

    async def get_voice_identity_snapshot(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = True,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> VoiceIdentitySnapshot:
        compiled = await self.compile_identity(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        return compiled.voice_snapshot

    def get_voice_identity_snapshot_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> VoiceIdentitySnapshot:
        compiled = self.compile_identity_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return compiled.voice_snapshot

    async def get_action_identity_snapshot(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = True,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> ActionIdentitySnapshot:
        compiled = await self.compile_identity(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        return compiled.action_snapshot

    def get_action_identity_snapshot_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> ActionIdentitySnapshot:
        compiled = self.compile_identity_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return compiled.action_snapshot

    async def get_compiled_action_policy(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = True,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> CompiledActionPolicy:
        compiled = await self.compile_identity(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        if compiled.action_policy is None:
            raise RuntimeError("Compiled identity is missing an action policy.")
        return compiled.action_policy

    def get_compiled_action_policy_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> CompiledActionPolicy:
        compiled = self.compile_identity_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        if compiled.action_policy is None:
            raise RuntimeError("Compiled identity is missing an action policy.")
        return compiled.action_policy

    async def get_action_preferences(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = False,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        policy = await self.get_compiled_action_policy(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        return policy.as_preferences()

    def get_action_preferences_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        policy = self.get_compiled_action_policy_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return policy.as_preferences()

    async def get_voice_preferences(
        self,
        *,
        user_id: int,
        platform: str,
        ensure_materialized: bool = False,
        profile: Optional[Dict[str, Any]] = None,
        identity_owner_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        snapshot = await self.get_voice_identity_snapshot(
            user_id=user_id,
            platform=platform,
            ensure_materialized=ensure_materialized,
            profile=profile,
            identity_owner_id=identity_owner_id,
        )
        prefs = dict(snapshot.preferences)
        prefs["_materialized"] = bool(snapshot.materialized)
        return prefs

    def get_voice_preferences_sync(
        self,
        *,
        user_id: int,
        platform: str,
        identity_owner_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        snapshot = self.get_voice_identity_snapshot_sync(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        prefs = dict(snapshot.preferences)
        prefs["_materialized"] = bool(snapshot.materialized)
        return prefs

    async def update_raw_document(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        markdown: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        allow_admin: bool = False,
        identity_owner_id: Optional[int] = None,
    ) -> IdentityDocument:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return await self.document_store.update_raw_document(
            user_id=storage_user_id,
            platform=storage_platform,
            doc_key=doc_key,
            markdown=markdown,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            allow_admin=allow_admin,
            owner_user_id=(storage_user_id if storage_platform == OWNER_PLATFORM else None),
        )

    async def update_structured_document(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        updates: Dict[str, Any],
        actor_type: str,
        actor_id: str,
        reason: str,
        identity_owner_id: Optional[int] = None,
        remove_keys: Iterable[str] = (),
    ) -> IdentityDocument:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        await self.ensure_user_identity_bundle(
            user_id=storage_user_id,
            platform=storage_platform,
        )
        doc = self.document_store.load_storage_document(
            user_id=storage_user_id,
            platform=storage_platform,
            doc_key=doc_key,
        )
        if not doc:
            raise FileNotFoundError(f"Unknown identity document: {doc_key}")
        return await self.document_store.update_structured_document(
            user_id=storage_user_id,
            platform=storage_platform,
            doc_key=doc_key,
            current_document=doc,
            updates=updates,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            owner_user_id=(storage_user_id if storage_platform == OWNER_PLATFORM else None),
            remove_keys=remove_keys,
        )

    async def list_history(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: Optional[str] = None,
        limit: int = 50,
        identity_owner_id: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return await self.document_store.list_history(
            user_id=storage_user_id,
            platform=storage_platform,
            doc_key=doc_key,
            limit=limit,
        )

    async def get_history_entry(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        version: int,
        identity_owner_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        storage_user_id, storage_platform = self._resolve_storage_target(
            user_id=user_id,
            platform=platform,
            identity_owner_id=identity_owner_id,
        )
        return await self.document_store.get_history_entry(
            user_id=storage_user_id,
            platform=storage_platform,
            doc_key=doc_key,
            version=version,
        )

    async def silently_maintain_document(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        updates: Dict[str, Any],
        actor_id: str,
        reason: str,
        identity_owner_id: Optional[int] = None,
    ) -> Optional[IdentityDocument]:
        if not getattr(config, "IDENTITY_ENABLE_SILENT_SELF_MAINTENANCE", True):
            return None
        if not supports_silent_maintenance(doc_key):
            return None
        return await self.update_structured_document(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            updates=updates,
            actor_type="system",
            actor_id=actor_id,
            reason=reason,
            identity_owner_id=identity_owner_id,
        )

identity_service = IdentityService()
