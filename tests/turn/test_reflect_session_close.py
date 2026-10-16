"""Phase B / B4 step 2 — reflect's session-close hook redirects to users column.

Audit finding IM-HIGH-2: the session-close hook in
``core/turn/reflect.py`` previously stored a ``last_session_closed_at``
fact in the ``user_memory`` table (a generic decay-ranked store),
polluting recall budgets with bookkeeping data. B4 step 1 (commit
``ea277b2``) shipped the new home — column ``users.last_session_closed_at``
plus a migration script. This step (B4 step 2) redirects the writer.

Tests cover:
  * ``_record_session_close`` calls the new brain wrapper
    ``set_user_last_session_closed_at`` with the owner_id and a numeric
    timestamp.
  * ``_record_session_close`` does NOT write to ``user_memory`` — i.e.
    ``upsert_user_fact`` is no longer invoked from this code path.
  * Failure of the brain wrapper does not propagate (the
    ``apply_reflection`` per-section try/except already guards, but the
    hook itself should not raise either).
  * ``_apply_user_facts`` (the OTHER fact-write path) is independent of
    the session-close hook. After B4 step 4 it routes through
    ``identity_service.silently_maintain_document`` on the new
    ``preferences`` doc; this file pins that ``last_session_closed_at``
    is NOT among the fact keys it processes — that key still belongs
    exclusively to the dedicated session-close hook.

Spec-review safety net: tests use ``create_autospec(PalliBrain,
instance=True)`` so a missing wrapper raises AttributeError instead of
being silently satisfied — the same lesson learned in B2.2 / commit
cd30ecd.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

import core.turn.reflect as reflect_module
from core.database import PalliBrain
from core.identity.service import IdentityService
from core.turn.types import ReflectionFactAdd


# ─── _record_session_close redirect ─────────────────────────────────────


@pytest.mark.asyncio
async def test_record_session_close_calls_brain_set_user_last_session_closed_at_wrapper():
    """The session-close hook lands the timestamp in
    ``users.last_session_closed_at`` via the brain wrapper, not in
    ``user_memory``.

    Before B4 step 2: the hook called
    ``ai.user_fact_memory.upsert_user_fact(key='last_session_closed_at',
    value=f'{ts:.0f}|{reason}', source='reflect.session_close')``.
    After B4 step 2: the hook calls
    ``brain.set_user_last_session_closed_at(owner_user_id=..., timestamp=...)``.

    ``_record_session_close`` resolves ``brain`` at call-time via
    ``from core.database import brain``, which means patching
    ``core.database.brain`` substitutes the autospec'd instance for the
    duration of the test.
    """
    autospec_brain = create_autospec(PalliBrain, instance=True)

    with patch("time.time", return_value=1778197025.5), patch(
        "core.database.brain", autospec_brain,
    ):
        await reflect_module._record_session_close(owner_id=42, reason="user-said-goodnight")

    autospec_brain.set_user_last_session_closed_at.assert_awaited_once_with(
        owner_user_id=42, timestamp=1778197025.5,
    )


@pytest.mark.asyncio
async def test_record_session_close_works_without_legacy_user_fact_module():
    """The legacy ``user_memory`` write path must NOT fire on session close.

    Post-excision form (2026-07-09): ``ai.user_fact_memory`` was DELETED in
    Great Excision stage 3 (tests/test_legacy_classifier_modules_deleted.py
    pins the deletion), so the guarantee is now absolute — session close
    must complete with the legacy module not even importable.
    """
    with pytest.raises(ImportError):
        import ai.user_fact_memory  # noqa: F401 — must stay deleted

    autospec_brain = create_autospec(PalliBrain, instance=True)

    with patch("core.database.brain", autospec_brain):
        await reflect_module._record_session_close(owner_id=42, reason="user-said-goodnight")

    autospec_brain.set_user_last_session_closed_at.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_session_close_swallows_brain_failures():
    """If the brain wrapper raises (transient DB, schema race, etc.),
    ``_record_session_close`` must not propagate — the apply_reflection
    per-section try/except already guards, but defense-in-depth at the
    hook keeps the failure isolated.
    """
    autospec_brain = create_autospec(PalliBrain, instance=True)
    autospec_brain.set_user_last_session_closed_at.side_effect = RuntimeError(
        "transient DB failure",
    )

    with patch("core.database.brain", autospec_brain):
        # No raise propagates out.
        await reflect_module._record_session_close(owner_id=42, reason="x")


# ─── _apply_user_facts is independent of the session-close hook ────────


@pytest.mark.asyncio
async def test_apply_user_facts_is_distinct_from_session_close_hook():
    """The general ``_apply_user_facts`` writer (which the LLM uses to
    surface user-fact additions like 'favorite_color', 'has_pet') is
    architecturally distinct from the session-close hook.

    After B4 step 4, ``_apply_user_facts`` redirects to the new
    ``preferences`` structured identity document via
    ``identity_service.silently_maintain_document`` — see
    ``tests/turn/test_reflect_preferences.py`` for the full pinning of
    that contract.

    What this test pins for the session-close path is that
    ``last_session_closed_at`` is NEVER routed through ``_apply_user_facts``
    (it has its own dedicated hook, ``_record_session_close``, that
    writes to ``users.last_session_closed_at`` directly). Even if a
    LLM-emitted fact accidentally used that key, the session-close
    semantics live elsewhere and the preferences doc is the only thing
    ``_apply_user_facts`` touches.
    """
    autospec_service = create_autospec(IdentityService, instance=True)
    autospec_service.silently_maintain_document = AsyncMock(return_value=MagicMock())

    facts = [
        ReflectionFactAdd(key="favorite_color", value="blue", confidence=0.9),
        ReflectionFactAdd(key="has_pet", value="true", confidence=0.95),
    ]

    with patch("core.turn.reflect.identity_service", autospec_service):
        await reflect_module._apply_user_facts(owner_id=42, facts=facts)

    # Both facts land in the preferences doc.
    assert autospec_service.silently_maintain_document.await_count == 2
    keys_seen = []
    for call in autospec_service.silently_maintain_document.await_args_list:
        keys_seen.extend(call.kwargs["updates"].keys())
    assert keys_seen == ["pref_favorite_color", "pref_has_pet"]
    # Most importantly: ``last_session_closed_at`` is NOT among them — the
    # session-close path uses the dedicated brain wrapper, never reflect's
    # general fact path.
    assert "pref_last_session_closed_at" not in keys_seen
    assert "last_session_closed_at" not in keys_seen
