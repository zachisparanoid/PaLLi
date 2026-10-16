"""Tests for the canonical owner-id resolver in api.chat.

Decision 2 of the 2026-05-08 completion refactor: every surface that
needs an owner_id must go through `core.local_mode.get_local_owner_refs`.
The dashboard chat endpoint's `_resolve_local_owner_id` helper enforces
this — and surfaces an error rather than silently falling back to 1
when the resolver returns None.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_local_owner_id_returns_resolved_app_user_id(monkeypatch):
    """When local_mode resolves an app_user_id, the helper returns it."""
    from api import chat as chat_mod
    from api.chat import _resolve_local_owner_id

    async def _fake_refs():
        return {
            "app_user_id": 42,
            "palli_user_id": 42,
            "discord_user_id": None,
        }

    monkeypatch.setattr(chat_mod, "get_local_owner_refs", _fake_refs)

    result = await _resolve_local_owner_id()
    assert result == 42


@pytest.mark.asyncio
async def test_resolve_local_owner_id_raises_when_unresolved(monkeypatch):
    """When local_mode returns None, the helper raises rather than
    silently attributing the request to user_id=1."""
    from api import chat as chat_mod
    from api.chat import _resolve_local_owner_id

    async def _fake_refs_unresolved():
        return {
            "app_user_id": None,
            "palli_user_id": None,
            "discord_user_id": None,
        }

    monkeypatch.setattr(chat_mod, "get_local_owner_refs", _fake_refs_unresolved)

    with pytest.raises(RuntimeError, match="local owner"):
        await _resolve_local_owner_id()
