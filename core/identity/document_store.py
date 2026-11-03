from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, Optional

from ai.profile_preferences import get_safe_preferred_name
from core.database import brain

from .constants import (
    ACTION_PREFERENCE_BOOL_FIELDS,
    ACTION_PREFERENCE_ENUM_FIELDS,
    CORE_DOCS,
    PALLI_OWNED_CORE_DOCS,
    DEFAULT_ACTION_SETTINGS,
    DEFAULT_CURATED_MEMORY_SETTINGS,
    DEFAULT_MEMORY_ARCHIVE_SETTINGS,
    DEFAULT_MEMORY_SETTINGS,
    DEFAULT_PERSONALITY_SETTINGS,
    DEFAULT_PREFERENCES_SETTINGS,
    DEFAULT_RELATIONSHIP_SETTINGS,
    DEFAULT_ROUTINE_SETTINGS,
    DEFAULT_STANDING_ORDER_SETTINGS,
    DEFAULT_VOICE_PREFERENCES,
    LEGACY_ACTION_SETTINGS,
    USER_DOCS,
    VOICE_PREFERENCE_ENUM_FIELDS,
)
from .document_policy import (
    apply_document_policy,
    assert_document_edit_allowed,
    document_policy_view,
    get_document_policy,
    user_facing_raw_markdown_editable,
)
from .frontmatter import dump_frontmatter, parse_frontmatter
from .models import IdentityBundle, IdentityDocument

OWNER_PLATFORM = "owner"


class IdentityDocumentStore:
    def __init__(self, *, identity_root: str) -> None:
        self.identity_root = os.path.abspath(identity_root)
        self.core_root = os.path.join(self.identity_root, "core")
        self.palli_root = os.path.join(self.identity_root, "palli")
        self.template_root = os.path.join(self.identity_root, "templates")
        self.user_root = os.path.join(self.identity_root, "users")
        self.owner_root = os.path.join(self.identity_root, "owners")

    def user_dir(self, user_id: int, platform: str) -> str:
        return os.path.join(self.user_root, str(platform), str(user_id))

    def owner_dir(self, owner_user_id: int) -> str:
        return os.path.join(self.owner_root, str(owner_user_id))

    def core_doc_path(self, doc_key: str) -> str:
        # 2026-08-13 — manifesto §2.8: voice_identity is HERS now. Resolution
        # is palli-first with core fallback so the handover has no breakage
        # window: until her first authorship writes identity/palli/
        # VOICE_IDENTITY.md, readers keep getting the creator's page; the
        # moment hers exists, every consumer reads it instead. The stale core
        # copy is removed in a later cleanup once nothing resolves to it.
        if doc_key in PALLI_OWNED_CORE_DOCS:
            palli_path = os.path.join(self.palli_root, CORE_DOCS[doc_key])
            if os.path.isfile(palli_path):
                return palli_path
        return os.path.join(self.core_root, CORE_DOCS[doc_key])

    def user_doc_path(self, user_id: int, platform: str, doc_key: str) -> str:
        return os.path.join(self.user_dir(user_id, platform), USER_DOCS[doc_key])

    def owner_doc_path(self, owner_user_id: int, doc_key: str) -> str:
        return os.path.join(self.owner_dir(owner_user_id), USER_DOCS[doc_key])

    def template_path(self, doc_key: str) -> str:
        return os.path.join(self.template_root, USER_DOCS[doc_key])

    def storage_doc_path(self, *, user_id: int, platform: str, doc_key: str) -> str:
        policy = get_document_policy(doc_key)
        if policy.scope == "core":
            return self.core_doc_path(doc_key)
        if platform == OWNER_PLATFORM:
            return self.owner_doc_path(user_id, doc_key)
        return self.user_doc_path(user_id, platform, doc_key)

    def load_storage_document(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
    ) -> Optional[IdentityDocument]:
        path = self.storage_doc_path(user_id=user_id, platform=platform, doc_key=doc_key)
        if not os.path.exists(path):
            return None

        policy = get_document_policy(doc_key)
        if policy.scope == "core":
            record_platform = "global"
            record_user_id = 0
            owner_user_id = None
        else:
            record_platform = platform
            record_user_id = int(user_id)
            owner_user_id = record_user_id if platform == OWNER_PLATFORM else None

        return self.load_document(
            doc_key=doc_key,
            path=path,
            platform=record_platform,
            user_id=record_user_id,
            owner_user_id=owner_user_id,
        )

    def load_bundle(self, *, user_id: int, platform: str) -> IdentityBundle:
        bundle = IdentityBundle(user_id=int(user_id), platform=platform)

        for doc_key in CORE_DOCS:
            doc = self.load_storage_document(user_id=user_id, platform=platform, doc_key=doc_key)
            if doc is not None:
                bundle.core_documents[doc_key] = doc

        for doc_key in USER_DOCS:
            doc = self.load_storage_document(user_id=user_id, platform=platform, doc_key=doc_key)
            if doc is not None:
                bundle.user_documents[doc_key] = doc

        return bundle

    def load_document(
        self,
        doc_key: str,
        path: str,
        *,
        platform: str,
        user_id: int,
        owner_user_id: Optional[int] = None,
    ) -> IdentityDocument:
        with open(path, "r", encoding="utf-8") as handle:
            markdown = handle.read()
        frontmatter, content = parse_frontmatter(markdown)
        frontmatter = self.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=frontmatter,
            user_id=user_id,
            platform=platform,
            actor_type=str(frontmatter.get("last_updated_by") or "system"),
            owner_user_id=owner_user_id,
            increment_version=False,
        )
        if doc_key == "action_preferences":
            frontmatter = self._normalize_action_preference_values(frontmatter)
        elif doc_key == "voice_preferences":
            frontmatter = self._normalize_voice_preference_values(frontmatter)
        return IdentityDocument(
            doc_key=doc_key,
            path=path,
            frontmatter=frontmatter,
            content=content,
            policy=document_policy_view(doc_key),
            editable=user_facing_raw_markdown_editable(doc_key),
        )

    def render_user_template(
        self,
        *,
        doc_key: str,
        user_id: int,
        platform: str,
        profile: Optional[Dict[str, Any]],
    ) -> str:
        with open(self.template_path(doc_key), "r", encoding="utf-8") as handle:
            template = handle.read()
        context = self._build_template_context(
            doc_key=doc_key,
            user_id=user_id,
            platform=platform,
            profile=profile or {},
        )
        rendered_template = template.format_map(SafeDict(context))
        template_frontmatter, template_content = parse_frontmatter(rendered_template)
        normalized = self.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=template_frontmatter,
            user_id=user_id,
            platform=platform,
            actor_type="system",
            owner_user_id=(user_id if platform == OWNER_PLATFORM else None),
        )
        return dump_frontmatter(normalized, template_content)

    def normalize_frontmatter(
        self,
        *,
        doc_key: str,
        frontmatter: Dict[str, Any],
        user_id: int,
        platform: str,
        actor_type: str,
        owner_user_id: Optional[int] = None,
        increment_version: bool = True,
    ) -> Dict[str, Any]:
        normalized = apply_document_policy(
            doc_key=doc_key,
            frontmatter=frontmatter,
            user_id=user_id,
            platform=platform,
            actor_type=actor_type,
            owner_user_id=owner_user_id,
            increment_version=increment_version,
        )

        if doc_key == "personality_profile":
            for key, value in DEFAULT_PERSONALITY_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "action_preferences":
            normalized = self._normalize_action_preference_values(normalized)
        elif doc_key == "voice_preferences":
            normalized = self._normalize_voice_preference_values(normalized)
        elif doc_key == "relationship_state":
            for key, value in DEFAULT_RELATIONSHIP_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "memory_summary":
            for key, value in DEFAULT_MEMORY_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "memory":
            for key, value in DEFAULT_CURATED_MEMORY_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "memory_archive":
            for key, value in DEFAULT_MEMORY_ARCHIVE_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "routines":
            for key, value in DEFAULT_ROUTINE_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "standing_orders":
            for key, value in DEFAULT_STANDING_ORDER_SETTINGS.items():
                normalized.setdefault(key, value)
        elif doc_key == "preferences":
            # Phase B / B4 step 4 (2026-05-09): preferences doc holds an
            # open-ended set of ``pref_<key>: <value>`` frontmatter
            # entries. Only the ``preferences_summary`` description has a
            # default; the actual preference rows are written by reflect
            # over time. No coercion of pref_* values — they are stored
            # verbatim from the LLM emission.
            for key, value in DEFAULT_PREFERENCES_SETTINGS.items():
                normalized.setdefault(key, value)
        return normalized

    async def persist_document_markdown(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        markdown: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        file_path: Optional[str] = None,
    ) -> IdentityDocument:
        path = file_path or self.storage_doc_path(user_id=user_id, platform=platform, doc_key=doc_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(markdown)

        frontmatter, _ = parse_frontmatter(markdown)
        record_user_id = self._required_int(frontmatter.get("user_id"), default=user_id)
        record_platform = str(frontmatter.get("platform") or platform)
        await self._record_version(
            user_id=record_user_id,
            platform=record_platform,
            doc_key=doc_key,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            markdown=markdown,
            file_path=path,
        )
        return self.load_document(
            doc_key=doc_key,
            path=path,
            platform=record_platform,
            user_id=record_user_id,
            owner_user_id=(
                record_user_id
                if record_platform == OWNER_PLATFORM
                else self._optional_int(frontmatter.get("owner_user_id"))
            ),
        )

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
        owner_user_id: Optional[int] = None,
    ) -> IdentityDocument:
        assert_document_edit_allowed(
            doc_key=doc_key,
            actor_type=actor_type,
            operation="raw_markdown",
            allow_admin_override=allow_admin,
        )
        frontmatter, content = parse_frontmatter(markdown)
        frontmatter = self.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=frontmatter,
            user_id=user_id,
            platform=platform,
            actor_type=actor_type,
            owner_user_id=owner_user_id,
        )
        rendered = dump_frontmatter(frontmatter, content)
        doc = await self.persist_document_markdown(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            markdown=rendered,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        if get_document_policy(doc_key).scope == "user" and doc_key != "changelog":
            await self.append_changelog(
                user_id=user_id,
                platform=platform,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason,
                changed_doc=doc_key,
            )
        return doc

    async def update_structured_document(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        current_document: IdentityDocument,
        updates: Dict[str, Any],
        actor_type: str,
        actor_id: str,
        reason: str,
        owner_user_id: Optional[int] = None,
        remove_keys: Iterable[str] = (),
    ) -> IdentityDocument:
        assert_document_edit_allowed(
            doc_key=doc_key,
            actor_type=actor_type,
            operation="structured",
        )
        merged = dict(current_document.frontmatter)
        merged.update(updates)
        # Week 2 task 6 (2026-09-10) — deletion needs its own verb. A
        # ``{key: None}`` update does NOT drop a key: normalize_frontmatter /
        # apply_document_policy keep None values, so the entry survives as an
        # empty one and the rebuilder would still see it. ``remove_keys`` pops
        # before the unchanged-check, so removing a key that isn't there stays
        # the no-op the short-circuit already handles.
        for removable in remove_keys or ():
            merged.pop(removable, None)

        # Wave 1 — content-aware write. Compute the prospective frontmatter
        # WITHOUT bumping the version; if the merge produces no functional
        # change, short-circuit. Stops the 15-min memory_summary refresh
        # loop from accreting CHANGELOG entries on identical writes.
        merged_preview = self.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=merged,
            user_id=user_id,
            platform=platform,
            actor_type=actor_type,
            owner_user_id=owner_user_id,
            increment_version=False,
        )
        if self._frontmatter_unchanged(current_document.frontmatter, merged_preview):
            return current_document

        merged = self.normalize_frontmatter(
            doc_key=doc_key,
            frontmatter=merged,
            user_id=user_id,
            platform=platform,
            actor_type=actor_type,
            owner_user_id=owner_user_id,
        )
        rendered = dump_frontmatter(merged, current_document.content)
        doc = await self.persist_document_markdown(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            markdown=rendered,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        await self.append_changelog(
            user_id=user_id,
            platform=platform,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            changed_doc=doc_key,
        )
        return doc

    @staticmethod
    def _frontmatter_unchanged(
        current: Dict[str, Any],
        merged: Dict[str, Any],
    ) -> bool:
        """True iff every content-bearing field is unchanged between current
        and merged. Ignores `version` and `last_updated_by` since those bump
        on every write regardless of content change.
        """
        meta_keys = {"version", "last_updated_by"}
        merged_keys = set(merged.keys()) - meta_keys
        current_keys = set(current.keys()) - meta_keys
        if merged_keys != current_keys:
            return False
        for key in merged_keys:
            if current.get(key) != merged.get(key):
                return False
        return True

    async def append_changelog(
        self,
        *,
        user_id: int,
        platform: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        changed_doc: str,
    ) -> None:
        changelog_path = self.storage_doc_path(
            user_id=user_id,
            platform=platform,
            doc_key="changelog",
        )
        if os.path.exists(changelog_path):
            doc = self.load_document(
                "changelog",
                changelog_path,
                platform=platform,
                user_id=user_id,
                owner_user_id=(user_id if platform == OWNER_PLATFORM else None),
            )
            content = doc.content.rstrip()
            frontmatter = dict(doc.frontmatter)
        else:
            frontmatter = self.normalize_frontmatter(
                doc_key="changelog",
                frontmatter={},
                user_id=user_id,
                platform=platform,
                actor_type="system",
                owner_user_id=(user_id if platform == OWNER_PLATFORM else None),
            )
            content = "# Identity Changelog\n\n"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        content = (
            f"{content}\n"
            f"- {timestamp} | doc=`{changed_doc}` | actor=`{actor_type}:{actor_id}` | reason=`{reason}`"
        ).rstrip()
        frontmatter["version"] = int(frontmatter.get("version", 0) or 0) + 1
        frontmatter["last_updated_by"] = actor_type
        rendered = dump_frontmatter(frontmatter, content)
        await self.persist_document_markdown(
            user_id=user_id,
            platform=platform,
            doc_key="changelog",
            markdown=rendered,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=f"changelog:{changed_doc}:{reason}",
            file_path=changelog_path,
        )

    async def list_history(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: Optional[str] = None,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        return await brain.list_identity_document_versions(
            user_id=user_id,
            platform=platform,
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
    ) -> Optional[Dict[str, Any]]:
        return await brain.get_identity_document_version(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            version=version,
        )

    def _build_template_context(
        self,
        *,
        doc_key: str,
        user_id: int,
        platform: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        preferences = profile.get("preferences", {}) if isinstance(profile, dict) else {}
        safe_preferred_name = get_safe_preferred_name(profile)
        display_name = (
            profile.get("display_name")
            or profile.get("username")
            or safe_preferred_name
            or f"user_{user_id}"
        )
        interests = preferences.get("interests") or []
        if not isinstance(interests, list):
            interests = []

        context = {
            "platform": platform,
            "user_id": user_id,
            "display_name": display_name,
            "interests": ", ".join(interests[:8]) if interests else "No explicit interest list yet.",
            "preferred_name": safe_preferred_name or display_name,
            # Week 1, task 4: the pronouns field is born EMPTY (never the env
            # default) so the first-contact target stays open until she learns
            # the answer; the fallback is applied at read time, in the prompt.
            "pronouns": "",
            "humor_style": preferences.get("humor_style") or DEFAULT_PERSONALITY_SETTINGS["humor"],
            "response_length": preferences.get("response_length") or DEFAULT_PERSONALITY_SETTINGS["response_length"],
            "voice_enabled": str(bool(preferences.get("voice_enabled", True))).lower(),
            "proactive_enabled": str(
                bool((preferences.get("proactive_consent") or {}).get("enabled", True))
            ).lower(),
        }
        context.update(DEFAULT_PERSONALITY_SETTINGS)
        context.update(self._build_action_settings(profile))
        context.update(DEFAULT_VOICE_PREFERENCES)
        context.update(DEFAULT_RELATIONSHIP_SETTINGS)
        context.update(DEFAULT_CURATED_MEMORY_SETTINGS)
        context.update(DEFAULT_MEMORY_SETTINGS)
        context.update(DEFAULT_ROUTINE_SETTINGS)
        context.update(DEFAULT_STANDING_ORDER_SETTINGS)
        context.update(DEFAULT_PREFERENCES_SETTINGS)
        return context

    async def _record_version(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        markdown: str,
        file_path: Optional[str] = None,
    ) -> None:
        frontmatter, _ = parse_frontmatter(markdown)
        await brain.create_identity_document_version(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            file_path=file_path or self.storage_doc_path(user_id=user_id, platform=platform, doc_key=doc_key),
            version=int(frontmatter.get("version", 1) or 1),
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            content=markdown,
            frontmatter=frontmatter,
        )

    def _build_action_settings(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        settings = dict(DEFAULT_ACTION_SETTINGS)
        if self._is_existing_user_profile(profile):
            settings = dict(LEGACY_ACTION_SETTINGS)
            legacy_proactive_enabled = self._legacy_proactive_enabled(profile)
            settings["allow_proactive_followups"] = legacy_proactive_enabled
            settings["background_watch_mode"] = "enabled" if legacy_proactive_enabled else "off"
        return settings

    def _is_existing_user_profile(self, profile: Dict[str, Any]) -> bool:
        try:
            return int(profile.get("total_interactions") or 0) > 0
        except Exception:
            return False

    def _legacy_proactive_enabled(self, profile: Dict[str, Any]) -> bool:
        preferences = profile.get("preferences", {}) if isinstance(profile, dict) else {}
        consent = preferences.get("proactive_consent")
        if isinstance(consent, dict):
            enabled = consent.get("enabled")
            if isinstance(enabled, bool):
                return enabled
        if isinstance(consent, bool):
            return consent
        reminder_prefs = preferences.get("reminder_preferences") or {}
        if isinstance(reminder_prefs, dict) and reminder_prefs.get("allow_proactive_reminders") is False:
            return False
        return False

    def _normalize_action_preference_values(self, frontmatter: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(frontmatter)
        action_defaults = dict(LEGACY_ACTION_SETTINGS)
        for key, default_value in action_defaults.items():
            raw_value = normalized.get(key, default_value)
            if key in ACTION_PREFERENCE_BOOL_FIELDS:
                normalized[key] = self._coerce_bool(raw_value, bool(default_value))
                continue
            choices = ACTION_PREFERENCE_ENUM_FIELDS.get(key)
            if choices:
                normalized[key] = self._normalize_choice(raw_value, choices, str(default_value))
                continue
            normalized[key] = raw_value if raw_value is not None else default_value
        return normalized

    def _normalize_voice_preference_values(self, frontmatter: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(frontmatter)
        for key, choices in VOICE_PREFERENCE_ENUM_FIELDS.items():
            default_value = DEFAULT_VOICE_PREFERENCES[key]
            raw_value = normalized.get(key, default_value)
            normalized[key] = self._normalize_choice(raw_value, choices, default_value)
        return normalized

    def _normalize_choice(self, value: Any, choices: Iterable[str], default: str) -> str:
        text = str(value or "").strip().lower()
        if text in set(choices):
            return text
        return default

    def _coerce_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off", ""}:
            return False
        return default

    def _optional_int(self, value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    def _required_int(self, value: Any, *, default: int) -> int:
        try:
            if value is None:
                return int(default)
            return int(value)
        except Exception:
            return int(default)


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
