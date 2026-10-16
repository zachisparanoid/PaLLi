"""Phase B / B4 step 4 — reflect's user-fact writes redirect to a
structured ``preferences`` identity document.

Audit findings IM-CRIT-1, IM-CRIT-2, IM-HIGH-1: the legacy
``upsert_user_fact`` path stores LLM-emitted user facts as
``memory_type='user_fact'`` rows in ``user_memory``, which (a) competes
with the legacy ``analyze_and_store_facts`` pipeline that writes
``memory_type IN (preference, people, project, moment)`` to the same
table with an incompatible schema, (b) feeds the contradiction auditor
that only sees ONE pipeline's writes, and (c) overloads the decay-
ranked recall budget with what is structurally identity-document
material.

Decision 3 in the completion design (``2026-05-08-palli-completion-design.md``)
says identity Markdown documents are canonical for user preferences;
``user_memory`` becomes a derived index. B4 step 4 closes the producer
side: reflection's ``_apply_user_facts`` now writes each emitted fact
as a structured-document mutation on the new ``preferences`` doc rather
than as a row in ``user_memory``.

Tests cover:
  * Each ``ReflectionFactAdd`` lands in the ``preferences`` structured
    doc via ``identity_service.silently_maintain_document``.
  * ``ai.user_fact_memory.upsert_user_fact`` is NOT invoked from
    reflect anymore.
  * Preference keys are namespaced with a ``pref_`` prefix to avoid
    colliding with policy-managed frontmatter keys (``version``,
    ``platform``, ``user_id``, ``last_updated_by``, etc.).
  * Empty/whitespace-only fact lists are no-ops (no doc write fired).
  * Failures in ``silently_maintain_document`` don't propagate — the
    apply_reflection per-section ``try/except`` already guards, but
    defense-in-depth at the writer keeps a transient identity-store
    failure from interrupting other reflection work.

Spec-review safety net: tests use ``create_autospec(IdentityService,
instance=True)`` so a missing wrapper raises AttributeError instead of
being silently satisfied — same lesson as B2.2 / commit cd30ecd and
B4.2 / commit 7c4a1ce.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

import core.turn.reflect as reflect_module
from core.identity.service import IdentityService
from core.turn.types import ReflectionFactAdd


# ─── _apply_user_facts redirects to identity service ────────────────────


@pytest.mark.asyncio
async def test_apply_user_facts_writes_each_fact_to_preferences_document():
    """Every ``ReflectionFactAdd`` becomes a ``silently_maintain_document``
    call on the ``preferences`` doc, with a ``pref_<key>: <value>``
    update payload.

    The prefix is load-bearing: policy normalization (version,
    platform, last_updated_by, etc.) writes well-known frontmatter
    keys that we must not collide with. Prefixing every preference
    with ``pref_`` keeps the namespace clean and the read side
    debuggable.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    facts = [
        ReflectionFactAdd(key="favorite_color", value="blue", confidence=0.9),
        ReflectionFactAdd(key="preferred_editor", value="vim", confidence=0.85),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    # Two facts → two structured-doc writes, one per fact, all targeting
    # ``preferences``.
    assert autospec_service.silently_maintain_document.await_count == 2

    calls = autospec_service.silently_maintain_document.await_args_list
    doc_keys = {call.kwargs["doc_key"] for call in calls}
    assert doc_keys == {"preferences"}

    # Every call carries owner-platform routing so the doc lands in the
    # owner directory rather than under any other platform's userspace.
    for call in calls:
        assert call.kwargs["user_id"] == 42
        assert call.kwargs["platform"] == "owner"
        assert call.kwargs["identity_owner_id"] == 42
        assert call.kwargs["actor_id"] == "reflect"
        # Reason carries provenance for the changelog
        assert "reflect" in call.kwargs["reason"]

    # Updates payloads → one ``pref_<key>: value`` per fact.
    update_payloads = [call.kwargs["updates"] for call in calls]
    assert {"pref_favorite_color": "blue"} in update_payloads
    assert {"pref_preferred_editor": "vim"} in update_payloads


@pytest.mark.asyncio
async def test_apply_user_facts_works_without_legacy_user_fact_module():
    """The legacy ``user_memory`` write path must NOT fire from reflect.

    After B4 step 4, every ``ReflectionFactAdd`` lands in the structured
    ``preferences`` doc. Post-excision form (2026-07-09):
    ``ai.user_fact_memory`` was DELETED in Great Excision stage 3
    (tests/test_legacy_classifier_modules_deleted.py pins the deletion), so
    the guarantee is absolute — the fact path must complete with the legacy
    module not even importable.
    """
    with pytest.raises(ImportError):
        import ai.user_fact_memory  # noqa: F401 — must stay deleted

    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    facts = [
        ReflectionFactAdd(key="favorite_color", value="blue", confidence=0.9),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    autospec_service.silently_maintain_document.assert_awaited()


@pytest.mark.asyncio
async def test_apply_user_facts_empty_facts_list_is_a_noop():
    """Empty/zero-length fact lists never reach the identity service.

    ``apply_reflection`` already guards with ``if plan.user_facts_add:``
    but defense-in-depth in the writer keeps a future caller from
    accidentally hitting the doc store with no work to do.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=[])

    autospec_service.silently_maintain_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_user_facts_swallows_identity_service_failures():
    """If ``silently_maintain_document`` raises (transient I/O, schema
    race, missing template, etc.), ``_apply_user_facts`` must not
    propagate.

    The ``apply_reflection`` per-section try/except already guards, but
    defense-in-depth keeps the failure scoped to one fact rather than
    blowing up the whole batch.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(
        side_effect=RuntimeError("transient identity-store failure"),
    )

    facts = [
        ReflectionFactAdd(key="favorite_color", value="blue", confidence=0.9),
        ReflectionFactAdd(key="preferred_editor", value="vim", confidence=0.85),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        # No raise propagates out, even though every call would error.
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    # Both calls were attempted — one fact's failure doesn't short-circuit
    # the rest. (apply_reflection's outer guard catches per-section
    # errors, but per-fact resilience here is also valuable.)
    assert autospec_service.silently_maintain_document.await_count == 2


@pytest.mark.asyncio
async def test_apply_user_facts_skips_facts_with_blank_keys_or_values():
    """``ReflectionFactAdd`` values arrive after the parser has already
    enforced non-empty keys/values, but the writer should still defend
    against malformed entries: a fact with whitespace-only key or value
    must not produce a frontmatter entry of its own.

    The parser's ``_plan_from_payload`` enforces this contract upstream;
    pinning the writer-side defense keeps a regression in the parser
    from quietly polluting the preferences doc.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    # Construct facts directly — bypassing the parser's filter — so the
    # writer-side defense gets exercised. Confidence is irrelevant here.
    facts = [
        # Whitespace-only value
        ReflectionFactAdd(key="ok_key", value="   ", confidence=0.9),
        # Whitespace-only key
        ReflectionFactAdd(key="   ", value="ok value", confidence=0.9),
        # Real entry to confirm the writer still works for valid input
        ReflectionFactAdd(key="real_key", value="real value", confidence=0.9),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    # Only the real entry produces a write.
    assert autospec_service.silently_maintain_document.await_count == 1
    call = autospec_service.silently_maintain_document.await_args_list[0]
    assert call.kwargs["updates"] == {"pref_real_key": "real value"}


# ─── prefix discipline ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_user_facts_strips_double_pref_prefix():
    """If the LLM emits a key that already starts with ``pref_`` (a
    prompt regression, paste artifact, or model-error duplication), the
    writer must NOT double-prefix it.

    Without detect-and-strip, ``key='pref_favorite_color'`` would land
    as ``pref_pref_favorite_color`` in the frontmatter — a non-canonical
    name that round-trips stably and silently pollutes the preferences
    doc forever. The fix collapses any leading ``pref_`` so the doc's
    namespace stays canonical regardless of LLM emission shape.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    facts = [
        ReflectionFactAdd(key="pref_favorite_color", value="blue", confidence=0.9),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    assert autospec_service.silently_maintain_document.await_count == 1
    call = autospec_service.silently_maintain_document.await_args_list[0]
    # Single prefix only — NOT pref_pref_favorite_color.
    assert call.kwargs["updates"] == {"pref_favorite_color": "blue"}


@pytest.mark.asyncio
async def test_apply_user_facts_protects_policy_managed_keys():
    """The ``pref_`` prefix exists specifically to protect policy-managed
    frontmatter keys (``version``, ``platform``, ``user_id``,
    ``last_updated_by``, etc.) from accidental clobber by an LLM-emitted
    fact with the same key name.

    Concretely: if the LLM emits ``key='version', value='42'``, the
    writer must produce ``pref_version: 42`` rather than ``version:
    42``. The doc's policy-managed ``version`` (the changelog counter)
    must not be touched by user-fact writes.

    A future "let's drop the prefix" refactor would silently corrupt
    the changelog version counter without this pin.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    facts = [
        ReflectionFactAdd(key="version", value="42", confidence=0.9),
        ReflectionFactAdd(key="platform", value="discord", confidence=0.9),
        ReflectionFactAdd(key="user_id", value="hijacked", confidence=0.9),
        ReflectionFactAdd(key="last_updated_by", value="evil", confidence=0.9),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    update_keys = []
    for call in autospec_service.silently_maintain_document.await_args_list:
        update_keys.extend(call.kwargs["updates"].keys())

    # Every emitted key got prefixed; no bare policy-managed key
    # leaked into the updates payload.
    assert update_keys == [
        "pref_version",
        "pref_platform",
        "pref_user_id",
        "pref_last_updated_by",
    ]
    for bare_key in ("version", "platform", "user_id", "last_updated_by"):
        assert bare_key not in update_keys
