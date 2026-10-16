"""Tests for the DashboardChannelAdapter owner-id resolution.

Decision 2 of the 2026-05-08 completion refactor: when the dashboard
adapter cannot extract owner_id from envelope metadata, it must fall
back to the canonical `core.local_mode.get_local_owner_refs()`
resolver — never to a hardcoded literal.
"""
from __future__ import annotations

import pytest

from bots.dashboard.channel_adapter import DashboardChannelAdapter
from core.channel_runtime import ChannelDeliveryEnvelope


@pytest.mark.asyncio
async def test_extract_owner_id_uses_metadata_when_present(monkeypatch):
    """When the envelope's metadata carries an owner key, it wins —
    the local_mode resolver must NOT be called."""
    from bots.dashboard import channel_adapter as adapter_mod

    async def _should_not_be_called():
        raise AssertionError(
            "get_local_owner_refs must not be called when metadata "
            "already supplies an owner_id"
        )

    monkeypatch.setattr(adapter_mod, "get_local_owner_refs", _should_not_be_called)

    adapter = DashboardChannelAdapter()
    envelope = ChannelDeliveryEnvelope(
        text="hi",
        metadata={"owner_id": 42},
    )

    result = await adapter._extract_owner_id(envelope)
    assert result == 42


@pytest.mark.asyncio
async def test_extract_owner_id_falls_back_to_local_mode_when_metadata_sparse(
    monkeypatch,
):
    """When metadata lacks every owner key, the adapter resolves via
    local_mode rather than silently using a hardcoded fallback."""
    from bots.dashboard import channel_adapter as adapter_mod

    async def _fake_refs():
        return {
            "app_user_id": 42,
            "palli_user_id": 42,
            "discord_user_id": None,
        }

    monkeypatch.setattr(adapter_mod, "get_local_owner_refs", _fake_refs)

    adapter = DashboardChannelAdapter()
    envelope = ChannelDeliveryEnvelope(text="hi", metadata={})

    result = await adapter._extract_owner_id(envelope)
    assert result == 42


@pytest.mark.asyncio
async def test_extract_owner_id_raises_when_unresolved(monkeypatch):
    """When metadata is sparse AND local_mode returns None, the adapter
    must raise rather than silently buffering envelopes under user_id=1."""
    from bots.dashboard import channel_adapter as adapter_mod

    async def _fake_refs_unresolved():
        return {
            "app_user_id": None,
            "palli_user_id": None,
            "discord_user_id": None,
        }

    monkeypatch.setattr(adapter_mod, "get_local_owner_refs", _fake_refs_unresolved)

    adapter = DashboardChannelAdapter()
    envelope = ChannelDeliveryEnvelope(text="hi", metadata={})

    with pytest.raises(RuntimeError, match="cannot extract owner_id"):
        await adapter._extract_owner_id(envelope)
