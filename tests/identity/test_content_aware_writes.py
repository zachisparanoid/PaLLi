"""
Wave 1: assert silently_maintain_document and update_structured_document
are content-aware — writing the same content twice produces exactly one
file write, one version bump, one CHANGELOG entry.

Root-cause fix for the 15-minute memory_summary refresh loop that has
been spinning the CHANGELOG with template content for days.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from core.identity.document_store import IdentityDocumentStore


@pytest.fixture
def tmp_identity_root(tmp_path: Path, monkeypatch) -> str:
    """Materialize a minimal owner directory for the test, plus mock the
    identity-document version DB recorder (which would otherwise hit the
    real palli-local.sqlite3 via the brain singleton)."""
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "999").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)

    seed = """---
doc_type: memory_summary
scope: user
owner: system
ownership_class: agent_maintained_evolving
mutability: system_evolving
visibility: user_visible
editable_via: ["raw_markdown", "silent_maintenance"]
review_requirement: safe_self_maintenance
platform: owner
user_id: 999
version: 1
last_updated_by: system
memory_summary_text: "Initial seed."
owner_user_id: 999
---
# Memory Summary
seed body
"""
    (root / "owners" / "999" / "MEMORY_SUMMARY.md").write_text(seed, encoding="utf-8")

    # Stub the brain version recorder so the test doesn't hit the real DB.
    from core import database as database_module

    async def _noop_record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        database_module.brain,
        "create_identity_document_version",
        _noop_record,
    )
    yield str(root)


def _read_version(store: IdentityDocumentStore) -> int:
    doc = store.load_storage_document(
        user_id=999, platform="owner", doc_key="memory_summary",
    )
    assert doc is not None
    return int(doc.frontmatter.get("version", 0) or 0)


def _read_changelog_size(tmp_identity_root: str) -> int:
    path = os.path.join(tmp_identity_root, "owners", "999", "CHANGELOG.md")
    if not os.path.exists(path):
        return 0
    return os.path.getsize(path)


def test_writing_same_content_twice_is_a_no_op(tmp_identity_root: str) -> None:
    """Second call with identical updates does NOT bump version."""
    store = IdentityDocumentStore(identity_root=tmp_identity_root)

    async def go() -> None:
        doc1 = store.load_storage_document(
            user_id=999, platform="owner", doc_key="memory_summary",
        )
        await store.update_structured_document(
            user_id=999,
            platform="owner",
            doc_key="memory_summary",
            current_document=doc1,
            updates={"memory_summary_text": "First real summary."},
            actor_type="system",
            actor_id="test",
            reason="test_first_write",
            owner_user_id=999,
        )
        v_after_first = _read_version(store)
        assert v_after_first > 1, "first write should bump version"

        doc2 = store.load_storage_document(
            user_id=999, platform="owner", doc_key="memory_summary",
        )
        await store.update_structured_document(
            user_id=999,
            platform="owner",
            doc_key="memory_summary",
            current_document=doc2,
            updates={"memory_summary_text": "First real summary."},
            actor_type="system",
            actor_id="test",
            reason="test_second_write_should_be_noop",
            owner_user_id=999,
        )
        v_after_second = _read_version(store)
        assert v_after_first == v_after_second, (
            f"version bumped on no-op write: {v_after_first} -> {v_after_second}"
        )

    asyncio.run(go())


def test_changelog_does_not_grow_on_noop_writes(tmp_identity_root: str) -> None:
    """Five identical updates in a row produce exactly one CHANGELOG entry."""
    store = IdentityDocumentStore(identity_root=tmp_identity_root)

    async def go() -> None:
        # First real update.
        doc = store.load_storage_document(
            user_id=999, platform="owner", doc_key="memory_summary",
        )
        await store.update_structured_document(
            user_id=999,
            platform="owner",
            doc_key="memory_summary",
            current_document=doc,
            updates={"memory_summary_text": "Stable summary."},
            actor_type="system",
            actor_id="test",
            reason="test_first",
            owner_user_id=999,
        )
        size_after_first = _read_changelog_size(tmp_identity_root)
        assert size_after_first > 0, "first write should produce CHANGELOG entry"

        # Five no-op writes.
        for _ in range(5):
            doc = store.load_storage_document(
                user_id=999, platform="owner", doc_key="memory_summary",
            )
            await store.update_structured_document(
                user_id=999,
                platform="owner",
                doc_key="memory_summary",
                current_document=doc,
                updates={"memory_summary_text": "Stable summary."},
                actor_type="system",
                actor_id="test",
                reason="test_repeat",
                owner_user_id=999,
            )
        size_after_repeats = _read_changelog_size(tmp_identity_root)
        assert size_after_repeats == size_after_first, (
            f"changelog grew on no-op writes: {size_after_first} -> {size_after_repeats}"
        )

    asyncio.run(go())


def test_actual_change_still_writes(tmp_identity_root: str) -> None:
    """Sanity: a real content change DOES write, version DOES bump."""
    store = IdentityDocumentStore(identity_root=tmp_identity_root)

    async def go() -> None:
        doc1 = store.load_storage_document(
            user_id=999, platform="owner", doc_key="memory_summary",
        )
        await store.update_structured_document(
            user_id=999,
            platform="owner",
            doc_key="memory_summary",
            current_document=doc1,
            updates={"memory_summary_text": "First."},
            actor_type="system",
            actor_id="test",
            reason="r1",
            owner_user_id=999,
        )
        v1 = _read_version(store)

        doc2 = store.load_storage_document(
            user_id=999, platform="owner", doc_key="memory_summary",
        )
        await store.update_structured_document(
            user_id=999,
            platform="owner",
            doc_key="memory_summary",
            current_document=doc2,
            updates={"memory_summary_text": "Second."},  # genuinely different
            actor_type="system",
            actor_id="test",
            reason="r2",
            owner_user_id=999,
        )
        v2 = _read_version(store)
        assert v2 > v1, f"genuine content change should bump version ({v1} -> {v2})"

    asyncio.run(go())
