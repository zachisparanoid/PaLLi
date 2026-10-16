"""Thread-loop scenario regression test — the open-threads LIFECYCLE.

User mentions topic X → reflection captures → the raise happens → user
resolves → reflection closes → the topic never comes back on a later
pass. The historic thread-question loop bug was: PaLLi asked about
something the user owned/decided, the user answered, and PaLLi then
re-asked the same topic on the next tick because the system kept no
durable record of "this topic was answered."

Step 19 (2026-08-19): the decide_surface weave this test used to carry
retired with the deleted lane — the arbiter's thread-board digest is how
active threads are raised now, and it reads list_active_open_threads
exactly as this test does. What this file pins is the STORE lifecycle
that both worlds share: capture → active → mark-surfaced → close →
gone-from-active. If the last assertion fails, the thread-loop bug has
regressed at the storage layer, whatever sits above it.

T5 fixture-fix lesson: ``brain.close()`` is async; calling it without
``await`` leaves the coroutine unawaited and pytest emits a warning.
The fixture below wraps it in ``asyncio.run`` since the fixture itself
is sync. Same pattern as other tmp-brain fixtures across the suite.
"""
import asyncio
import tempfile
from pathlib import Path

import pytest

from core.database import PalliBrain
from core.open_threads import ThreadStatus


@pytest.fixture
def brain_fixture():
    """Tmp-DB-backed PalliBrain. Closes asynchronously on teardown."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        b = PalliBrain(db_path=str(db_path))
        try:
            yield b
        finally:
            asyncio.run(b.close())


@pytest.mark.asyncio
async def test_thread_loop_fix_via_open_threads(brain_fixture):
    """Full lifecycle: capture → active → surfaced → closed → not active.

    In production, reflect-LLM emits ``open_threads_to_add`` /
    ``open_threads_to_close`` and ``apply_reflection`` dispatches into
    the brain wrappers (T8/T9 pin those paths); the arbiter marks a
    thread surfaced when a say serving it releases. Here we drive the
    wrappers directly so the lifecycle itself is unambiguous.
    """
    brain = brain_fixture

    # ── Turn 1: capture ─────────────────────────────────────────────
    thread_id = await brain.add_open_thread(
        owner_user_id=1,
        topic_summary="user is considering buying a Bridgeport mill",
        why_it_matters="casual interest, no commitment",
        kind="curiosity",
        urgency="no urgency",
    )

    # Sanity: the thread is active and visible to the digest.
    active_before = await brain.list_active_open_threads(owner_user_id=1)
    assert len(active_before) == 1
    assert active_before[0].id == thread_id

    # ── The raise delivered → mark surfaced ─────────────────────────
    await brain.mark_open_thread_surfaced(thread_id=thread_id)

    # ── Turn 2: user reply "yeah I bought one" → reflect closes ─────
    await brain.close_open_thread(
        thread_id=thread_id,
        resolution_reason="user said they bought one",
        status=ThreadStatus.RESOLVED,
    )

    # ── The thread-loop fix: thread is gone from the active list ──────
    # If the lifecycle is broken anywhere — capture, close, or the
    # active-filter SQL — the resolved thread leaks back into
    # ``list_active_open_threads`` and a future pass re-raises it.
    # That IS the thread-loop bug.
    active_after = await brain.list_active_open_threads(owner_user_id=1)
    assert active_after == [], (
        "the thread should not be active after resolution; this is the "
        "thread-loop fix — failing here means the lifecycle is broken"
    )
