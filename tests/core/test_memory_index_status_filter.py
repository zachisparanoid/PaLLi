"""Wave 1a hinge: superseded facts leave EVERY ambient surface. The decay
path already filters (pinned in test_fact_retrieval); this pins the other
choke point — the workspace index (legacy_fact items feed recall's semantic
lane and introspect's memory notes)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_collect_index_items_skips_superseded_facts():
    from core.database import brain
    from core.memory_service import memory_workspace_service as svc

    active_id = await brain.store_user_memory(
        user_id=1, memory_type="moment", content="gist: early May was build-planning", importance=0.6)
    old_id = await brain.store_user_memory(
        user_id=1, memory_type="moment", content="2026-05-02: three quotes compared line by line", importance=0.6)
    conn = await brain.get_connection()
    try:
        await conn.execute(
            "UPDATE user_memory SET status='superseded', superseded_by_fact_id=$1 WHERE id=$2",
            str(active_id), old_id)
    finally:
        await conn.close()

    items = await svc._collect_index_items(owner_user_id=1)
    legacy_ids = {i["item_id"] for i in items if i["source_kind"] == "legacy_fact"}
    assert f"lfm_{active_id}" in legacy_ids
    assert f"lfm_{old_id}" not in legacy_ids
