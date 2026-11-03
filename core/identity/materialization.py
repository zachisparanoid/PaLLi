from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.database import brain

from .constants import USER_DOCS
from .document_store import IdentityDocumentStore, OWNER_PLATFORM
from .frontmatter import dump_frontmatter, parse_frontmatter

OWNER_MIGRATION_REASON = "owner_identity_migration"


class IdentityBundleMaterializer:
    def __init__(self, *, document_store: IdentityDocumentStore) -> None:
        self.document_store = document_store

    async def load_profile_for_storage(self, *, user_id: int, platform: str) -> Optional[Dict[str, Any]]:
        if platform == OWNER_PLATFORM:
            profile = await brain.get_user_profile(user_id, "discord")
            if profile:
                return profile
            return await brain.get_user_profile(user_id, "local")
        return await brain.get_user_profile(user_id, platform)

    async def ensure_materialized_documents(
        self,
        *,
        user_id: int,
        platform: str,
        profile: Optional[Dict[str, Any]],
        actor_type: str,
        actor_id: str,
        reason: str,
    ) -> None:
        if profile is None:
            profile = await self.load_profile_for_storage(user_id=user_id, platform=platform)

        if platform == OWNER_PLATFORM:
            await self._ensure_owner_documents(
                owner_user_id=user_id,
                profile=profile,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason,
            )
            return

        await self._ensure_platform_documents(
            user_id=user_id,
            platform=platform,
            profile=profile,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )

    async def _ensure_platform_documents(
        self,
        *,
        user_id: int,
        platform: str,
        profile: Optional[Dict[str, Any]],
        actor_type: str,
        actor_id: str,
        reason: str,
    ) -> None:
        user_dir = self.document_store.user_dir(user_id, platform)
        os.makedirs(user_dir, exist_ok=True)

        for doc_key in USER_DOCS:
            path = self.document_store.user_doc_path(user_id, platform, doc_key)
            if os.path.exists(path):
                continue

            rendered = self.document_store.render_user_template(
                doc_key=doc_key,
                user_id=user_id,
                platform=platform,
                profile=profile,
            )
            await self.document_store.persist_document_markdown(
                user_id=user_id,
                platform=platform,
                doc_key=doc_key,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason,
                markdown=rendered,
                file_path=path,
            )

    async def _ensure_owner_documents(
        self,
        *,
        owner_user_id: int,
        profile: Optional[Dict[str, Any]],
        actor_type: str,
        actor_id: str,
        reason: str,
    ) -> None:
        owner_dir = self.document_store.owner_dir(owner_user_id)
        os.makedirs(owner_dir, exist_ok=True)

        for doc_key in USER_DOCS:
            path = self.document_store.owner_doc_path(owner_user_id, doc_key)
            if os.path.exists(path):
                continue

            rendered = await self._select_owner_migration_markdown(
                owner_user_id=owner_user_id,
                doc_key=doc_key,
                actor_type=actor_type,
            )
            record_reason = OWNER_MIGRATION_REASON if rendered is not None else reason
            if rendered is None:
                rendered = self.document_store.render_user_template(
                    doc_key=doc_key,
                    user_id=owner_user_id,
                    platform=OWNER_PLATFORM,
                    profile=profile,
                )

            await self.document_store.persist_document_markdown(
                user_id=owner_user_id,
                platform=OWNER_PLATFORM,
                doc_key=doc_key,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=record_reason,
                markdown=rendered,
                file_path=path,
            )

    async def _select_owner_migration_markdown(
        self,
        *,
        owner_user_id: int,
        doc_key: str,
        actor_type: str,
    ) -> Optional[str]:
        preferred_platforms = ("local", "discord") if doc_key == "voice_preferences" else ("discord", "local")
        candidate_ids = await self._owner_legacy_ids(owner_user_id)

        best_markdown: Optional[str] = None
        best_frontmatter: Dict[str, Any] = {}
        best_content = ""
        best_version = -1
        best_priority = -1

        for priority, candidate_platform in enumerate(reversed(preferred_platforms)):
            for candidate_user_id in candidate_ids.get(candidate_platform, []):
                path = self.document_store.user_doc_path(candidate_user_id, candidate_platform, doc_key)
                if not os.path.exists(path):
                    continue

                with open(path, "r", encoding="utf-8") as handle:
                    markdown = handle.read()
                frontmatter, content = parse_frontmatter(markdown)
                version = int(frontmatter.get("version", 0) or 0)

                if version > best_version or (version == best_version and priority > best_priority):
                    best_markdown = markdown
                    best_frontmatter = frontmatter
                    best_content = content
                    best_version = version
                    best_priority = priority

        if best_markdown is None:
            return None

        normalized = self.document_store.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=best_frontmatter,
            user_id=owner_user_id,
            platform=OWNER_PLATFORM,
            actor_type=actor_type,
            owner_user_id=owner_user_id,
        )
        return dump_frontmatter(normalized, best_content)

    async def _owner_legacy_ids(self, owner_user_id: int) -> Dict[str, list[int]]:
        ids_by_platform: Dict[str, list[int]] = {
            "discord": [int(owner_user_id)],
            "local": [int(owner_user_id)],
        }

        app_user = await brain.get_app_user_by_id(int(owner_user_id))
        if not app_user:
            return ids_by_platform

        palli_user_id = app_user.get("palli_user_id")
        if palli_user_id is not None:
            try:
                palli_id = int(palli_user_id)
            except Exception:
                palli_id = None
            if palli_id is not None:
                for platform_name in ids_by_platform:
                    if palli_id not in ids_by_platform[platform_name]:
                        ids_by_platform[platform_name].append(palli_id)

        discord_user_id = app_user.get("discord_user_id")
        if discord_user_id:
            try:
                discord_id = int(discord_user_id)
            except Exception:
                discord_id = None
            if discord_id is not None and discord_id not in ids_by_platform["discord"]:
                ids_by_platform["discord"].insert(0, discord_id)

        return ids_by_platform
