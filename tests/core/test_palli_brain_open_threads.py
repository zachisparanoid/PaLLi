"""PalliBrain has wrapper methods for OpenThreadsService."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from core.database import PalliBrain
from core.open_threads import ThreadKind, ThreadStatus


@pytest.fixture
def brain():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        b = PalliBrain(db_path=str(db_path))
        try:
            yield b
        finally:
            asyncio.run(b.close())


def test_brain_add_open_thread_persists(brain):
    thread_id = asyncio.run(
        brain.add_open_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind=ThreadKind.WORK,
            urgency="time-sensitive",
        )
    )
    rows = asyncio.run(brain.list_active_open_threads(owner_user_id=1))
    assert len(rows) == 1
    assert rows[0].id == thread_id


def test_brain_close_open_thread_flips_status(brain):
    thread_id = asyncio.run(
        brain.add_open_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind=ThreadKind.WORK,
            urgency="z",
        )
    )
    asyncio.run(
        brain.close_open_thread(
            thread_id=thread_id,
            resolution_reason="done",
            status=ThreadStatus.RESOLVED,
        )
    )
    active = asyncio.run(brain.list_active_open_threads(owner_user_id=1))
    assert active == []


def test_brain_mark_open_thread_surfaced(brain):
    thread_id = asyncio.run(
        brain.add_open_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind=ThreadKind.WORK,
            urgency="z",
        )
    )
    asyncio.run(brain.mark_open_thread_surfaced(thread_id=thread_id))
    rows = asyncio.run(brain.list_active_open_threads(owner_user_id=1))
    assert rows[0].surface_count == 1
    assert rows[0].last_surfaced_at is not None
