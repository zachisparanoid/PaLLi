"""Week 2, task 6 — "forget that" forgets it for good.

Deleting a memory row that was materialized from the ``preferences``
identity document must ALSO erase the ``pref_<key>`` entry the row came
from. Otherwise ``rebuild_user_memory_from_documents`` puts the fact
straight back the next time it runs, and "I've forgotten that" was a
lie with a timer on it.

These tests drive the REAL stores: the temp sqlite database and the
temp identity root that ``tests/conftest.py`` installs for the whole
session. The sibling
``tests/core/test_memory_service_rebuild_user_memory.py`` mocks the
brain, which is the right shape for its unit contract but the wrong
shape here — the whole point of this task is that the document and the
row agree on disk, so the row under test is built by the real rebuild
from a real document.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.database import brain
from core.identity import identity_service
from core.memory_service import (
    ForgetRowDeleteError,
    ForgetSourceWriteError,
    forget_user_memory,
    rebuild_user_memory_from_documents,
)


# Fictional owner ids, far outside anything the rest of the suite uses,
# so rows and identity workspaces never collide.
_DOC_OWNER_ID = 9_100_601
_PLAIN_OWNER_ID = 9_100_602
_OTHER_WRITER_OWNER_ID = 9_100_603
_FAILED_WRITE_OWNER_ID = 9_100_604
_NEIGHBOUR_OWNER_ID = 9_100_605
_FAILED_DELETE_OWNER_ID = 9_100_606


async def _purge_owner(owner_id: int) -> None:
    """Drop every memory row and the identity workspace for one owner."""
    rows = await brain.get_user_memories(
        user_id=owner_id, min_importance=0.0, include_superseded=True
    )
    for row in rows:
        await brain.delete_user_memory(user_id=owner_id, memory_id=int(row["id"]))
    workspace = Path(identity_service.document_store.identity_root) / "owners" / str(owner_id)
    shutil.rmtree(workspace, ignore_errors=True)


def _pref_frontmatter(owner_id: int) -> dict:
    bundle = identity_service.load_identity_bundle_sync(
        user_id=owner_id, platform="owner", identity_owner_id=owner_id
    )
    doc = bundle.user_documents.get("preferences")
    return dict(doc.frontmatter or {}) if doc is not None else {}


def _coffee_rows(rows: list) -> list:
    return [
        row
        for row in rows
        if (row.get("source_detail") or {}).get("fact_key") == "coffee"
    ]


async def _seed_coffee_and_rebuild(owner_id: int) -> int:
    """``pref_coffee: black`` in the doc, materialized by the REAL
    rebuild. Returns the id of the row it wrote."""
    written = await identity_service.silently_maintain_document(
        user_id=owner_id,
        platform="owner",
        doc_key="preferences",
        updates={"pref_coffee": "black"},
        actor_id="test_memory_forget",
        reason="seed_pref_for_forget_test",
        identity_owner_id=owner_id,
    )
    assert written is not None
    assert _pref_frontmatter(owner_id).get("pref_coffee") == "black"

    status = await rebuild_user_memory_from_documents(owner_user_id=owner_id)
    assert status["status"] == "ok", status
    seeded = _coffee_rows(
        await brain.get_user_memories(user_id=owner_id, min_importance=0.0)
    )
    assert len(seeded) == 1, seeded
    return int(seeded[0]["id"])


@pytest.mark.asyncio
async def test_forgetting_a_doc_seeded_row_erases_the_document_entry_so_a_rebuild_cannot_resurrect_it():
    """(a) The law: erase the derived row AND its source of truth.

    Seed ``pref_coffee: black`` into the preferences document, let the
    real rebuild materialize the row, forget that row, then run the
    rebuild again. The fact must not come back, and the document must
    no longer carry the key.
    """
    owner_id = _DOC_OWNER_ID
    await _purge_owner(owner_id)
    try:
        row_id = await _seed_coffee_and_rebuild(owner_id)

        result = await forget_user_memory(user_id=owner_id, memory_id=row_id)
        assert result == {"deleted_row": True, "source_removed": "pref_coffee"}

        # The source of truth is gone...
        assert "pref_coffee" not in _pref_frontmatter(owner_id)

        # ...so the rebuild cannot bring the row back.
        second = await rebuild_user_memory_from_documents(owner_user_id=owner_id)
        assert second["status"] == "ok", second
        assert (
            _coffee_rows(
                await brain.get_user_memories(user_id=owner_id, min_importance=0.0)
            )
            == []
        )
    finally:
        await _purge_owner(owner_id)


@pytest.mark.asyncio
async def test_forgetting_a_row_with_no_document_source_deletes_as_before():
    """(b) A row nobody seeded from a document deletes as today, and
    reports that no source was removed. An id that does not exist is
    not a deletion at all."""
    owner_id = _PLAIN_OWNER_ID
    await _purge_owner(owner_id)
    try:
        memory_id = await brain.store_user_memory(
            user_id=owner_id,
            memory_type="user_fact",
            content="hand_written: a row with no document behind it",
            importance=0.5,
            tags=["hand_written"],
        )
        assert memory_id is not None

        result = await forget_user_memory(user_id=owner_id, memory_id=int(memory_id))
        assert result == {"deleted_row": True, "source_removed": None}
        assert (
            await brain.get_user_memories(user_id=owner_id, min_importance=0.0) == []
        )

        missing = await forget_user_memory(user_id=owner_id, memory_id=int(memory_id))
        assert missing == {"deleted_row": False, "source_removed": None}
    finally:
        await _purge_owner(owner_id)


@pytest.mark.asyncio
async def test_a_row_from_a_different_writer_never_touches_the_document():
    """A row can look like a seeded one — same ``fact_key``, a document
    entry of that name sitting right there — and still not be one. Only
    the rebuilder's own ``source`` marker authorizes erasing the source;
    another writer's row deletes and leaves the document alone.
    """
    owner_id = _OTHER_WRITER_OWNER_ID
    await _purge_owner(owner_id)
    try:
        written = await identity_service.silently_maintain_document(
            user_id=owner_id,
            platform="owner",
            doc_key="preferences",
            updates={"pref_coffee": "black"},
            actor_id="test_memory_forget",
            reason="seed_pref_for_forget_test",
            identity_owner_id=owner_id,
        )
        assert written is not None

        memory_id = await brain.store_user_memory(
            user_id=owner_id,
            memory_type="user_fact",
            content="coffee: black",
            importance=0.5,
            tags=["coffee"],
            source_detail={"source": "something_else", "fact_key": "coffee"},
        )
        assert memory_id is not None

        result = await forget_user_memory(user_id=owner_id, memory_id=int(memory_id))
        assert result == {"deleted_row": True, "source_removed": None}
        assert (
            await brain.get_user_memory(user_id=owner_id, memory_id=int(memory_id))
            is None
        )
        # The document entry was never this row's source, so it stands.
        assert _pref_frontmatter(owner_id).get("pref_coffee") == "black"
    finally:
        await _purge_owner(owner_id)


@pytest.mark.asyncio
async def test_one_owner_cannot_forget_another_owners_memory():
    """The scoping is the row's owner, not the id alone: a neighbour
    asking to forget A's row is told nothing was deleted, and neither
    A's row nor A's document entry moves."""
    owner_a = _DOC_OWNER_ID
    owner_b = _NEIGHBOUR_OWNER_ID
    await _purge_owner(owner_a)
    await _purge_owner(owner_b)
    try:
        row_id = await _seed_coffee_and_rebuild(owner_a)

        result = await forget_user_memory(user_id=owner_b, memory_id=row_id)
        assert result == {"deleted_row": False, "source_removed": None}

        assert await brain.get_user_memory(user_id=owner_a, memory_id=row_id) is not None
        assert _pref_frontmatter(owner_a).get("pref_coffee") == "black"
    finally:
        await _purge_owner(owner_a)
        await _purge_owner(owner_b)


@pytest.mark.asyncio
async def test_a_failed_document_write_is_a_failed_forget_and_leaves_the_row(monkeypatch):
    """Review fix: the source is erased FIRST, so a document write that
    raises means nothing has been deleted. The exception propagates (the
    route turns it into a 500) and the owner's memory is exactly as it
    was — never a 200 "ok" for a fact the next rebuild hands back.
    """
    owner_id = _FAILED_WRITE_OWNER_ID
    await _purge_owner(owner_id)
    try:
        row_id = await _seed_coffee_and_rebuild(owner_id)

        async def explode(**kwargs):
            raise RuntimeError("the identity root is read-only")

        monkeypatch.setattr(
            "core.memory_service.identity_service.update_structured_document",
            explode,
        )

        # Named for what it was, and the cause is carried in the message.
        with pytest.raises(ForgetSourceWriteError, match="the identity root is read-only"):
            await forget_user_memory(user_id=owner_id, memory_id=row_id)

        assert await brain.get_user_memory(user_id=owner_id, memory_id=row_id) is not None
        assert _pref_frontmatter(owner_id).get("pref_coffee") == "black"
    finally:
        await _purge_owner(owner_id)


@pytest.mark.asyncio
async def test_a_row_delete_that_raises_after_the_source_is_erased_says_exactly_that(monkeypatch):
    """Review fix 2: the source went, the row did not. That is a real
    partial mutation, and it must NOT be reported as a failed document
    write — the document write succeeded. Its own error class carries
    that difference out to the route.
    """
    owner_id = _FAILED_DELETE_OWNER_ID
    await _purge_owner(owner_id)
    try:
        row_id = await _seed_coffee_and_rebuild(owner_id)

        async def explode(**kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr("core.memory_service.brain.delete_user_memory", explode)

        with pytest.raises(ForgetRowDeleteError, match="the database went away"):
            await forget_user_memory(user_id=owner_id, memory_id=row_id)

        # Undo before the purge below, which deletes rows for real.
        monkeypatch.undo()

        # The source IS gone — the fact cannot be rebuilt — even though
        # the stale row is still sitting there.
        assert "pref_coffee" not in _pref_frontmatter(owner_id)
        assert await brain.get_user_memory(user_id=owner_id, memory_id=row_id) is not None
    finally:
        monkeypatch.undo()
        await _purge_owner(owner_id)
