from __future__ import annotations

import asyncio

import pytest

from core.continuity_service import continuity_service


def _install_fake_thread_store(monkeypatch):
    threads: dict[str, dict] = {}

    async def fake_find_continuity_thread_by_scope(*, owner_user_id, thread_kind, scope_key, include_archived=False):
        for thread in threads.values():
            metadata = dict(thread.get("metadata") or {})
            if int(thread.get("owner_user_id") or 0) != int(owner_user_id):
                continue
            if str(thread.get("thread_kind") or "") != str(thread_kind or ""):
                continue
            if str(metadata.get("scope_key") or "") != str(scope_key or ""):
                continue
            if not include_archived and str(thread.get("status") or "") == "archived":
                continue
            return dict(thread)
        return None

    async def fake_upsert_continuity_thread(**kwargs):
        existing = dict(threads.get(str(kwargs.get("thread_id") or ""), {}))
        existing.update(kwargs)
        threads[str(kwargs.get("thread_id") or "")] = existing
        return None

    async def fake_get_continuity_thread(thread_id: str):
        thread = threads.get(str(thread_id or ""))
        return dict(thread) if thread else None

    async def fake_update_continuity_thread(*, thread_id: str, **kwargs):
        current = dict(threads.get(str(thread_id or ""), {}))
        current.update(kwargs)
        threads[str(thread_id or "")] = current
        return None

    monkeypatch.setattr("core.continuity_service.brain.find_continuity_thread_by_scope", fake_find_continuity_thread_by_scope)
    monkeypatch.setattr("core.continuity_service.brain.upsert_continuity_thread", fake_upsert_continuity_thread)
    monkeypatch.setattr("core.continuity_service.brain.get_continuity_thread", fake_get_continuity_thread)
    monkeypatch.setattr("core.continuity_service.brain.update_continuity_thread", fake_update_continuity_thread)
    async def fake_list_runtime_sessions(**kwargs):
        return []

    monkeypatch.setattr("core.continuity_service.brain.list_runtime_sessions", fake_list_runtime_sessions)
    return threads


def _reset_bootstrap_state() -> None:
    continuity_service._bootstrapped_owner_ids.clear()
    continuity_service._bootstrap_tasks.clear()


@pytest.mark.asyncio
async def test_resolve_surface_thread_shares_owner_main_thread_for_dm(monkeypatch):
    _reset_bootstrap_state()
    threads = _install_fake_thread_store(monkeypatch)

    thread = await continuity_service.resolve_surface_thread(
        owner_user_id=7,
        platform="discord",
        channel_id=123,
        is_dm=True,
    )

    assert thread["thread_id"] == "owner:7:thread:main"
    assert thread["thread_kind"] == "owner_main"
    assert threads["owner:7:thread:main"]["metadata"]["scope_key"] == "owner_main"


@pytest.mark.asyncio
async def test_resolve_surface_thread_isolates_probe_dm_from_owner_main(monkeypatch):
    _reset_bootstrap_state()
    threads = _install_fake_thread_store(monkeypatch)

    thread = await continuity_service.resolve_surface_thread(
        owner_user_id=7,
        platform="local",
        channel_id=90210,
        surface_id="probe:test",
        is_dm=True,
        metadata={"probe": True, "probe_isolated": True},
    )

    assert thread["thread_id"] == "owner:7:thread:local:channel:probe:test"
    assert thread["thread_kind"] == "channel_conversation"
    assert thread["label"] == "Local Probe probe:test"
    assert "owner:7:thread:main" not in threads
    assert threads["owner:7:thread:local:channel:probe:test"]["metadata"]["scope_key"] == "local:channel:probe:test"
    assert threads["owner:7:thread:local:channel:probe:test"]["metadata"]["probe"] is True
    assert threads["owner:7:thread:local:channel:probe:test"]["metadata"]["probe_isolated"] is True


@pytest.mark.asyncio
async def test_reset_thread_archives_current_and_creates_new_revision(monkeypatch):
    _reset_bootstrap_state()
    threads = _install_fake_thread_store(monkeypatch)
    threads["owner:7:thread:main"] = {
        "thread_id": "owner:7:thread:main",
        "owner_user_id": 7,
        "thread_kind": "owner_main",
        "status": "active",
        "label": "Main Thread",
        "workspace_root": "",
        "workspace_label": "",
        "reset_count": 0,
        "metadata": {"scope_key": "owner_main"},
    }

    new_thread = await continuity_service.reset_thread(
        owner_user_id=7,
        thread_id="owner:7:thread:main",
    )

    assert threads["owner:7:thread:main"]["status"] == "archived"
    assert new_thread is not None
    assert new_thread["thread_id"] == "owner:7:thread:main:r1"
    assert new_thread["reset_count"] == 1

