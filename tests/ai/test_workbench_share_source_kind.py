"""Workbench SHARE rows get a structural origin marker (arbiter spec §3, as
amended in-build: source_id, not source_kind — the live table's CHECK pins
the source_kind vocabulary)."""
import pytest

from ai.workbench import _file_share_thread


@pytest.mark.asyncio
async def test_share_row_carries_workbench_share_source_id(monkeypatch):
    captured = {}

    async def _fake_add(**kwargs):
        captured.update(kwargs)
        return "tid"

    import core.database as db

    monkeypatch.setattr(db.brain, "add_open_thread", _fake_add)
    await _file_share_thread("I finished the mirror-notes read", 1)
    assert captured["source_id"] == "workbench_share"
    assert captured["kind"] == "personal"
    assert captured["topic_summary"].startswith("I finished")
