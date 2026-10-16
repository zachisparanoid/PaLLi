"""Phase B / B4 step 5 — ``rebuild_user_memory_from_documents`` materializes
the ``user_memory`` cache from identity documents on demand.

Audit findings IM-CRIT-1, IM-CRIT-2, IM-HIGH-8: B4 step 4 closed the
producer side by routing reflect's user-fact writes to the structured
``preferences`` identity document. This step closes the consumer side:
``user_memory`` becomes a derived index that gets rebuilt from those
documents on demand.

The plan (``2026-05-08-phase-B-cognitive-lifecycle-layer.md`` task B4
step 6, verbatim: "user_memory shrinks to a query cache. Add a
``core/memory_service.rebuild_user_memory_from_documents()`` that
materializes the cache from the identity docs on demand"). The user-
facing brief calls this "step 5 of 6" — the difference is whether the
"decide where last_session_closed_at goes" decision counts as a
discrete step. Both numberings refer to the same task.

Tests cover:
  * Each ``pref_<key>: <value>`` frontmatter entry on the ``preferences``
    doc lands as a ``memory_type='user_fact'`` row in user_memory with
    the ``pref_`` prefix stripped.
  * Idempotent: running twice doesn't multiply rows or leave torn state.
  * Failure-isolated: a transient DB error mid-rebuild stops the rebuild
    AFTER the prior-rows DELETE (the caller can retry; user_memory may
    end up empty for the user but never inconsistent — half-old half-new).
  * Empty preferences doc → no writes; doesn't raise.
  * Policy-managed frontmatter keys (``version``, ``platform``, etc.)
    DON'T get written into user_memory — only ``pref_`` keys are
    eligible.
  * Returns a status summary so callers / probes can assert the work.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from core.identity.models import IdentityBundle, IdentityDocument
from core.identity.service import IdentityService
from core.memory_service import rebuild_user_memory_from_documents


def _preferences_doc(frontmatter: dict[str, Any]) -> IdentityDocument:
    """Build an IdentityDocument matching the shape document_store
    produces for the ``preferences`` doc — frontmatter is the open-ended
    ``pref_<key>: <value>`` namespace plus policy-managed metadata.
    """
    return IdentityDocument(
        doc_key="preferences",
        path="/tmp/owners/42/PREFERENCES.md",
        frontmatter=dict(frontmatter),
        content="# User Preferences\n",
        policy={},
        editable=False,
    )


def _identity_bundle_with_preferences(
    *,
    user_id: int,
    pref_entries: dict[str, Any] | None = None,
    extra_frontmatter: dict[str, Any] | None = None,
) -> IdentityBundle:
    fm: dict[str, Any] = {
        "doc_type": "preferences",
        "scope": "user",
        "platform": "owner",
        "user_id": user_id,
        "version": 1,
        "last_updated_by": "system",
    }
    if extra_frontmatter:
        fm.update(extra_frontmatter)
    for key, value in (pref_entries or {}).items():
        # Caller passes either prefixed (``pref_foo``) or bare (``foo``);
        # the doc stores them prefixed so write what was passed unless
        # the caller explicitly asked for a bare name.
        fm[key] = value
    bundle = IdentityBundle(user_id=user_id, platform="owner")
    bundle.user_documents["preferences"] = _preferences_doc(fm)
    return bundle


# ─── basic happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_writes_one_row_per_pref_frontmatter_entry(monkeypatch):
    """Each ``pref_<key>: <value>`` in the preferences doc lands as a
    ``memory_type='user_fact'`` row in user_memory.

    The ``pref_`` prefix is stripped so readers see the bare key. The
    content shape matches what ``ai.user_fact_memory.upsert_user_fact``
    would have written before B4 step 4 — readers depend on
    ``"<key>: <value>"`` as the content payload.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={
            "pref_favorite_color": "blue",
            "pref_preferred_editor": "vim",
        },
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    # Two pref_* entries → two store_user_memory calls.
    assert fake_brain.store_user_memory.await_count == 2
    calls = fake_brain.store_user_memory.await_args_list

    # Each call is keyed under user_id=42 with memory_type='user_fact'.
    user_ids = {call.kwargs.get("user_id") for call in calls}
    memory_types = {call.kwargs.get("memory_type") for call in calls}
    assert user_ids == {42}
    assert memory_types == {"user_fact"}

    # Content is "<bare-key>: <value>" — pref_ prefix stripped.
    contents = {call.kwargs.get("content") for call in calls}
    assert contents == {"favorite_color: blue", "preferred_editor: vim"}

    # Result advertises the rebuild count for caller / probe assertion.
    assert result["status"] == "ok"
    assert result["written"] == 2
    assert result["owner_user_id"] == 42


# ─── prefix discipline ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_strips_pref_prefix_in_keys_and_content(monkeypatch):
    """The ``pref_`` namespace prefix on the doc's frontmatter must be
    stripped before reaching user_memory — readers expect bare keys.

    This pins the contract that rebuild's strip mirrors reflect's prefix
    (``core/turn/reflect.py`` ``_apply_user_facts``). If reflect's prefix
    discipline ever changes, this test catches the rebuild side.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_user_name": "Sam"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=99)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    await rebuild_user_memory_from_documents(owner_user_id=42)

    call = fake_brain.store_user_memory.await_args_list[0]
    # Bare key in content — no ``pref_`` substring leaks through.
    assert call.kwargs["content"] == "user_name: Sam"
    # Tags contain the bare key for the same readability reason; the
    # ``pref_`` prefix is a doc-frontmatter namespace concern, not a
    # user_memory concern.
    tags = call.kwargs.get("tags") or []
    assert "user_name" in tags
    assert not any(str(tag).startswith("pref_") for tag in tags)


@pytest.mark.asyncio
async def test_rebuild_skips_policy_managed_frontmatter(monkeypatch):
    """The preferences doc's frontmatter has both ``pref_*`` user
    entries AND policy-managed metadata (``version``, ``platform``,
    ``user_id``, ``last_updated_by``, ``doc_type``, etc.).

    Only ``pref_*`` keys are eligible for user_memory rows. Writing the
    policy keys would pollute recall with bookkeeping that has the same
    failure mode as IM-HIGH-2 (last_session_closed_at in user_memory).
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_favorite_color": "blue"},
        extra_frontmatter={
            # Policy-managed keys that must NOT become user_memory rows.
            "preferences_summary": "User likes blue.",
            "review_requirement": "safe_self_maintenance",
        },
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    await rebuild_user_memory_from_documents(owner_user_id=42)

    # Only the single ``pref_favorite_color`` entry produces a write.
    assert fake_brain.store_user_memory.await_count == 1
    call = fake_brain.store_user_memory.await_args_list[0]
    assert call.kwargs["content"] == "favorite_color: blue"


# ─── full-rebuild semantics: clear before write ────────────────────────


@pytest.mark.asyncio
async def test_rebuild_clears_prior_rows_before_writing_fresh_ones(monkeypatch):
    """Rebuild is a full DELETE-then-INSERT, not an incremental upsert.

    The prior derived rows (from a previous rebuild) get cleared first,
    then the fresh set lands. A user who deleted ``pref_foo`` from their
    doc must see the corresponding user_memory row vanish — not stick
    around because the rebuild only added new rows.

    The DELETE is scoped to the derived-row tag so only previously-
    rebuilt rows go away — pre-B4 ``user_fact`` rows that landed via
    direct ``upsert_user_fact`` (legacy reflect path) are NOT in scope
    of this scrub.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_favorite_color": "blue"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    # Single shared call log so we can assert ordering: every DELETE
    # entry must appear before every store entry.
    call_log: list[tuple] = []

    async def fake_delete(*, user_id: int, source: str) -> int:
        call_log.append(("delete", user_id, source))
        return 0

    async def fake_store(**kwargs: Any) -> int:
        call_log.append(("store", kwargs.get("content")))
        return 1

    fake_brain.delete_user_memory_by_source = fake_delete
    fake_brain.store_user_memory = fake_store

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    await rebuild_user_memory_from_documents(owner_user_id=42)

    # First call is a delete, then a store. If the rebuilder skipped
    # the prior-rows clear or interleaved them, this assertion fails.
    assert call_log[0][0] == "delete", (
        "rebuild must clear prior rows before any store_user_memory call"
    )
    assert any(entry[0] == "store" for entry in call_log[1:]), (
        "rebuild must write at least one fresh row after the clear"
    )
    # Source marker matches what the writer uses, so subsequent
    # rebuilds are scoped correctly.
    delete_entry = call_log[0]
    assert delete_entry[2] == "preferences_doc"
    assert delete_entry[1] == 42  # correct user


# ─── idempotence ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_is_idempotent_two_runs_produce_same_state(monkeypatch):
    """Running rebuild twice with the same input produces the same
    user_memory state — no duplicate rows, no torn state.

    The full-rebuild semantics (DELETE + INSERT) are what make this
    idempotent. If the implementation switched to incremental upsert
    without dedup, this test catches it: the second rebuild would see
    twice as many rows.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={
            "pref_favorite_color": "blue",
            "pref_preferred_editor": "vim",
        },
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    # Track every store call across both rebuilds.
    store_calls: list[dict] = []

    async def fake_store(**kwargs: Any) -> int:
        store_calls.append(dict(kwargs))
        return len(store_calls)

    # Simulate the prior-derived-rows count growing across rebuilds:
    # before the first run there are 0 derived rows; before the second
    # run there are 2 (from the first run). The DELETE is scoped, so
    # both runs scrub them.
    delete_call_count = [0]

    async def fake_delete(*, user_id: int, source: str) -> int:
        delete_call_count[0] += 1
        # First rebuild: no prior rows; second: 2 derived rows present.
        return 0 if delete_call_count[0] == 1 else 2

    fake_brain.delete_user_memory_by_source = fake_delete
    fake_brain.store_user_memory = fake_store

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    first = await rebuild_user_memory_from_documents(owner_user_id=42)
    second = await rebuild_user_memory_from_documents(owner_user_id=42)

    # Both runs wrote the same number of rows (2 each).
    assert first["written"] == 2
    assert second["written"] == 2

    # The second rebuild should have scrubbed the 2 derived rows from
    # the first rebuild before writing fresh — the result advertises
    # this so caller / probe can verify dedup happened.
    assert second.get("scrubbed", 0) >= 2

    # Total store calls is 2 + 2 = 4, but the DELETE between them
    # ensures user_memory only ever holds 2 derived rows at any moment.
    # The list above is what the brain SAW; the on-disk state is "2".
    assert len(store_calls) == 4


# ─── failure isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_failure_mid_writes_does_not_corrupt_prior_state(monkeypatch):
    """If ``store_user_memory`` raises mid-rebuild, the rebuilder must
    surface the failure (or count partial progress) without leaving
    user_memory inconsistent — half-old, half-new.

    The full-rebuild semantics already isolate this: prior rows are
    cleared FIRST. If a write fails mid-batch, user_memory ends up
    with FEWER derived rows than the doc has, but every row still
    accurately reflects the doc. The caller can retry the whole
    rebuild safely.

    This test pins that contract: a failure on the second of three
    writes still completes the first write, surfaces the failure in
    the result, and doesn't propagate an exception. The remaining
    writes are best-effort.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={
            "pref_alpha": "1",
            "pref_beta": "2",
            "pref_gamma": "3",
        },
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)

    write_count = [0]

    async def fake_store(**kwargs: Any) -> int:
        write_count[0] += 1
        if write_count[0] == 2:
            raise RuntimeError("transient db error mid-rebuild")
        return write_count[0]

    fake_brain.store_user_memory = fake_store

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    # No raise propagates — the rebuilder swallows per-row errors and
    # surfaces them in the result.
    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    # All three writes were attempted (the failure on #2 didn't
    # short-circuit #3).
    assert write_count[0] == 3

    # Result records the partial-success state.
    assert result["status"] == "partial"
    assert result["written"] == 2  # alpha and gamma succeeded
    assert result["failed"] == 1


# ─── DELETE failure isolation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_swallows_delete_failure_and_skips_writes(monkeypatch):
    """If the prior-rows DELETE fails, the rebuild must NOT proceed to
    write fresh rows — that would leave user_memory with a doubled set
    of derived rows (old + new). Better to fail loudly and not corrupt.

    The contract: DELETE failure aborts the rebuild and surfaces the
    failure, but doesn't propagate.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_favorite_color": "blue"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(
        side_effect=RuntimeError("delete failed"),
    )
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    # No writes happened — delete failure aborted before the write loop.
    fake_brain.store_user_memory.assert_not_awaited()
    assert result["status"] == "error"


# ─── degenerate inputs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_empty_preferences_doc_writes_nothing(monkeypatch):
    """A preferences doc with no ``pref_*`` entries (only policy
    metadata) is a valid empty cache — the rebuild scrubs prior rows
    and writes none.

    This is the "user has no preferences yet" case OR the "user removed
    all their preferences" case. Both must succeed without raising.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={},  # no pref_* entries
        extra_frontmatter={"preferences_summary": "no preferences yet"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    # Prior rows were still cleared (the "user removed all" case).
    fake_brain.delete_user_memory_by_source.assert_awaited_once()
    fake_brain.store_user_memory.assert_not_awaited()

    assert result["status"] == "ok"
    assert result["written"] == 0


@pytest.mark.asyncio
async def test_rebuild_missing_preferences_doc_writes_nothing(monkeypatch):
    """A bundle with NO preferences doc at all (e.g. the user's identity
    bundle was never materialized, or the doc was deleted) is treated
    as an empty cache. Don't raise; clear and exit.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = IdentityBundle(user_id=42, platform="owner")
    # No preferences doc registered in the bundle.
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    # Still clears prior rows (defensive — an old derived row from a
    # previous rebuild cycle should not survive a doc deletion).
    fake_brain.delete_user_memory_by_source.assert_awaited_once()
    fake_brain.store_user_memory.assert_not_awaited()

    assert result["status"] == "ok"
    assert result["written"] == 0


# ─── source_detail provenance ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_records_source_provenance_in_source_detail(monkeypatch):
    """Each derived user_memory row records its provenance in
    ``source_detail`` so downstream debugging / auditing knows which
    rows came from which doc.

    This mirrors how ``upsert_user_fact`` recorded ``source='reflect'``;
    rebuild records ``source='preferences_doc'`` so a query like
    ``SELECT id FROM user_memory WHERE source_detail->>'source' = 'preferences_doc'``
    can identify the derived rows for diagnostics.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_favorite_color": "blue"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    fake_brain = MagicMock()
    fake_brain.delete_user_memory_by_source = AsyncMock(return_value=0)
    fake_brain.store_user_memory = AsyncMock(return_value=1)

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", fake_brain)

    await rebuild_user_memory_from_documents(owner_user_id=42)

    call = fake_brain.store_user_memory.await_args_list[0]
    source_detail = call.kwargs.get("source_detail") or {}
    assert source_detail.get("source") == "preferences_doc"
    assert source_detail.get("fact_key") == "favorite_color"
    assert source_detail.get("fact_value") == "blue"


# ─── autospec safety net (mirrors B2.2 / B4.4 lesson) ──────────────────


def test_pallibrain_exposes_delete_user_memory_by_source():
    """Spec-net: PalliBrain must expose ``delete_user_memory_by_source``.

    ``create_autospec(PalliBrain, instance=True)`` raises AttributeError
    on access to attributes the spec class doesn't have. This test
    fails loudly if the brain wrapper for the new helper is missing
    even after the rebuilder lands — same lesson as B2.2 / commit
    cd30ecd and B4.2 / commit 7c4a1ce: a dependency the rebuilder
    silently no-ops on at runtime is harder to debug than a wrapper
    that fails at import.
    """
    from core.database import PalliBrain
    autospec_brain = create_autospec(PalliBrain, instance=True)
    # If the wrapper is missing this raises AttributeError; the test
    # asserts the wrapper EXISTS by simply touching it.
    assert callable(autospec_brain.delete_user_memory_by_source), (
        "PalliBrain must expose delete_user_memory_by_source — added in "
        "B4 step 5 (this commit) for the rebuild_user_memory_from_documents "
        "helper. Add it to core/database.py alongside delete_user_memory."
    )


@pytest.mark.asyncio
async def test_rebuild_against_autospecced_brain_calls_real_methods(monkeypatch):
    """Run the rebuilder against ``create_autospec(PalliBrain, instance=True)``.

    Stricter than the unspec'd MagicMock tests above: if any brain
    method the rebuilder uses doesn't exist on the spec class
    (delete_user_memory_by_source, store_user_memory), autospec raises
    AttributeError and the rebuilder's per-row try/except surfaces it
    as ``status='error'`` or partial results. The test pins the
    contract that all required wrappers are present.
    """
    from core.database import PalliBrain

    autospec_service = create_autospec(IdentityService, instance=True)
    bundle = _identity_bundle_with_preferences(
        user_id=42,
        pref_entries={"pref_favorite_color": "blue"},
    )
    autospec_service.load_identity_bundle = AsyncMock(return_value=bundle)

    autospec_brain = create_autospec(PalliBrain, instance=True)
    autospec_brain.delete_user_memory_by_source.return_value = 0
    autospec_brain.store_user_memory.return_value = 1

    monkeypatch.setattr("core.memory_service.identity_service", autospec_service)
    monkeypatch.setattr("core.memory_service.brain", autospec_brain)

    result = await rebuild_user_memory_from_documents(owner_user_id=42)

    assert result["status"] == "ok"
    assert result["written"] == 1
    autospec_brain.delete_user_memory_by_source.assert_awaited_once_with(
        user_id=42, source="preferences_doc",
    )
    autospec_brain.store_user_memory.assert_awaited_once()
