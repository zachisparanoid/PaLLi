"""Batch 5, task 8 (memory that picks, 2026-09-28; rulings R14, R50): a
thread that was never surfaced and has been held longer than
``memory.thread_archive_days`` retires — REVERSIBLY. The live table's CHECK
pins the status vocabulary (active/resolved/dismissed/logged), so, like the
arbiter's backstop, the terminal state is status='dismissed' with a
``resolution_reason`` prefixed 'retired_unsurfaced'; ``unretire`` returns
ANY dismissed thread (a backstop_ttl row too) to 'active' with the reason
and resolved_at cleared. Recurring rows are structural and never retire.
Runs against the REAL catalog DDL, and the async brain wrappers against
the tmp test database.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from core.open_threads import OpenThreadsService, ThreadSourceKind, ThreadStatus
from core.sqlite_schema_catalog import ensure_catalog_schema_sync

DAY = 86400.0
NOW = 1_800_000_000.0


@pytest.fixture
def svc():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.isolation_level = None
        # A throwaway file: no fsync per autocommitted DDL statement. The
        # expiry fixture this mirrors pays ~20 s of setup per test for it.
        conn.execute("PRAGMA synchronous=OFF")
        try:
            ensure_catalog_schema_sync(conn)
            yield OpenThreadsService(conn)
        finally:
            conn.close()


def _add(svc, *, age_days, surfaced_days_ago=None, source_kind="topic", owner=1, summary=None):
    tid = svc.add_thread(
        owner_user_id=owner, topic_summary=summary or f"t-{age_days}",
        why_it_matters="w", kind="personal", urgency="when-the-moment-fits",
    )
    then = NOW - age_days * DAY
    svc._conn.execute(
        "UPDATE open_threads SET created_at = ?, updated_at = ?, source_kind = ? WHERE id = ?",
        (then, then, source_kind, tid),
    )
    if surfaced_days_ago is not None:
        svc._conn.execute(
            "UPDATE open_threads SET last_surfaced_at = ?, surface_count = 1 WHERE id = ?",
            (NOW - surfaced_days_ago * DAY, tid),
        )
    return tid


def _row(svc, tid):
    return svc._conn.execute(
        "SELECT status, resolved_at, resolution_reason, last_surfaced_at FROM open_threads WHERE id = ?",
        (tid,),
    ).fetchone()


def test_only_the_never_surfaced_old_thread_retires_with_the_reason_prefix(svc):
    surfaced = _add(svc, age_days=50, surfaced_days_ago=1)
    never = _add(svc, age_days=50)
    young_never = _add(svc, age_days=44)

    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW) == 1

    assert _row(svc, surfaced)[0] == "active"
    assert _row(svc, young_never)[0] == "active"
    status, resolved_at, reason, _ = _row(svc, never)
    assert status == "dismissed"                       # the live CHECK's vocabulary
    assert resolved_at == NOW
    assert reason == "retired_unsurfaced (never surfaced, held 50d > 45d)"
    # it left the board
    assert {t.id for t in svc.list_active(owner_user_id=1)} == {surfaced, young_never}


def test_unretire_brings_it_back_active_with_the_reason_cleared(svc):
    never = _add(svc, age_days=50)
    svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW)
    assert _row(svc, never)[0] == "dismissed"

    assert svc.unretire(thread_id=never) is True
    status, resolved_at, reason, last_surfaced = _row(svc, never)
    assert (status, resolved_at, reason, last_surfaced) == ("active", None, None, None)
    assert [t.id for t in svc.list_active(owner_user_id=1)] == [never]
    # a second unretire finds nothing dismissed
    assert svc.unretire(thread_id=never) is False


def test_unretire_gives_a_fresh_window_so_the_next_night_does_not_retire_it_again(svc):
    """Fix round 1 (review Important, ruling R55): unretire used to leave
    created_at and last_surfaced_at as they were, so the row matched the
    retirement's filter again the next night and "Back on the board" lasted
    a day. The age is max(created_at, updated_at) now, and unretire stamps
    updated_at, so the window restarts at the unretire."""
    never = _add(svc, age_days=50)
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW) == 1
    assert svc.unretire(thread_id=never, now=NOW) is True
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW + DAY) == 0
    assert _row(svc, never)[0] == "active"
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW + 44 * DAY) == 0
    # the new window runs out 45 days after the unretire, and the reason counts from it
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW + 46 * DAY) == 1
    assert _row(svc, never)[2] == "retired_unsurfaced (never surfaced, held 46d > 45d)"


def test_expire_stale_spares_the_unretired_row_and_still_dismisses_an_untouched_old_one(svc):
    """R55's other half: the arbiter's daily backstop keyed on created_at
    too, so live it re-dismissed nearly every unretired row within a day.
    It ages by max(created_at, updated_at) now; an untouched row has
    updated_at == created_at, so today's selections do not change."""
    never = _add(svc, age_days=50)
    untouched = _add(svc, age_days=46, surfaced_days_ago=10)   # surfaced, never updated since
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW) == 1
    assert svc.unretire(thread_id=never, now=NOW) is True
    expired = svc.expire_stale(owner_user_id=1, now=NOW + DAY, followup_ttl_days=7, thread_ttl_days=45)
    assert [e[0] for e in expired] == [untouched]
    assert _row(svc, never)[0] == "active"
    assert _row(svc, untouched)[0] == "dismissed"
    assert _row(svc, untouched)[2] == "backstop_ttl (held 47d > 45d)"
    # a backstop row unretired gets the same fresh window
    assert svc.unretire(thread_id=untouched, now=NOW + DAY) is True
    assert svc.expire_stale(owner_user_id=1, now=NOW + 2 * DAY, followup_ttl_days=7, thread_ttl_days=45) == []
    assert {t.id for t in svc.list_active(owner_user_id=1)} == {never, untouched}


def test_a_recently_updated_row_is_not_expired_by_the_backstop(svc):
    """The same rule read the other way: a thread touched (updated_at) 10
    days ago is 10 days old to the backstop, however long ago it was
    created; a followup keeps its own 7-day tier."""
    touched = _add(svc, age_days=100)
    svc._conn.execute("UPDATE open_threads SET updated_at = ? WHERE id = ?", (NOW - 10 * DAY, touched))
    followup = _add(svc, age_days=100)
    svc._conn.execute("UPDATE open_threads SET kind = 'followup', updated_at = ? WHERE id = ?", (NOW - 8 * DAY, followup))
    expired = svc.expire_stale(owner_user_id=1, now=NOW, followup_ttl_days=7, thread_ttl_days=45)
    assert [e[0] for e in expired] == [followup]
    assert _row(svc, followup)[2] == "backstop_ttl (held 8d > 7d)"
    assert _row(svc, touched)[0] == "active"


def test_unretire_reaches_the_arbiters_backstop_rows_too(svc):
    old = _add(svc, age_days=46, surfaced_days_ago=10)
    expired = svc.expire_stale(owner_user_id=1, now=NOW, followup_ttl_days=7, thread_ttl_days=45)
    assert [e[0] for e in expired] == [old]
    assert _row(svc, old)[2].startswith("backstop_ttl")
    assert svc.unretire(thread_id=old) is True
    assert _row(svc, old)[0] == "active" and _row(svc, old)[2] is None


def test_unretire_leaves_resolved_and_active_threads_alone(svc):
    active = _add(svc, age_days=3)
    resolved = _add(svc, age_days=3)
    svc.close_thread(thread_id=resolved, resolution_reason="they said it was done", status=ThreadStatus.RESOLVED)
    assert svc.unretire(thread_id=active) is False
    assert svc.unretire(thread_id=resolved) is False
    assert svc.unretire(thread_id="no-such-id") is False
    assert _row(svc, resolved)[0] == "resolved" and _row(svc, resolved)[2] == "they said it was done"


def test_recurring_never_surfaced_old_threads_are_untouched(svc):
    recurring = _add(svc, age_days=200, source_kind="recurring")
    followthrough = _add(svc, age_days=200, source_kind="followthrough")
    topic = _add(svc, age_days=200)
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW) == 2
    assert _row(svc, recurring)[0] == "active"
    assert _row(svc, followthrough)[0] == "dismissed"
    assert _row(svc, topic)[0] == "dismissed"


def test_already_dismissed_and_other_owners_threads_are_untouched(svc):
    dismissed = _add(svc, age_days=50)
    svc.close_thread(thread_id=dismissed, resolution_reason="let go", status=ThreadStatus.DISMISSED)
    other_owner = _add(svc, age_days=50, owner=2)
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW) == 0
    assert _row(svc, dismissed)[2] == "let go"            # the reason it already had
    assert _row(svc, other_owner)[0] == "active"
    assert svc.retire_unsurfaced(owner_user_id=2, older_than_days=45, now=NOW) == 1


def test_zero_days_disables_retirement(svc):
    _add(svc, age_days=500)
    assert svc.retire_unsurfaced(owner_user_id=1, older_than_days=0, now=NOW) == 0
    assert len(svc.list_active(owner_user_id=1)) == 1


def test_list_dismissed_is_the_retired_board_newest_retirement_first(svc):
    first = _add(svc, age_days=60, summary="first")
    second = _add(svc, age_days=50, summary="second")
    active = _add(svc, age_days=50, surfaced_days_ago=1, summary="active")
    svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW - DAY)
    svc.close_thread(thread_id=active, resolution_reason="done", status=ThreadStatus.RESOLVED)
    later = _add(svc, age_days=50, summary="later")
    svc.retire_unsurfaced(owner_user_id=1, older_than_days=45, now=NOW)
    listed = svc.list_dismissed(owner_user_id=1)
    assert [t.topic_summary for t in listed] == ["later", "first", "second"] or \
        [t.topic_summary for t in listed] == ["later", "second", "first"]
    assert all(t.status is ThreadStatus.DISMISSED for t in listed)
    assert listed[0].id == later and {t.id for t in listed} == {first, second, later}


# ── the async brain wrappers, against the tmp test database ─────────────

_OWNER = 918_273


async def _seed(brain, *, age_days, surfaced=False):
    def _do(svc):
        tid = svc.add_thread(
            owner_user_id=_OWNER, topic_summary=f"wrap-{age_days}", why_it_matters="w",
            kind="curiosity", urgency="no urgency",
        )
        then = time.time() - age_days * DAY
        svc._conn.execute("UPDATE open_threads SET created_at = ?, updated_at = ? WHERE id = ?", (then, then, tid))
        if surfaced:
            svc._conn.execute("UPDATE open_threads SET last_surfaced_at = ? WHERE id = ?", (time.time(), tid))
        return tid

    return brain._run_open_threads_sync(_do)


async def _purge(brain):
    brain._run_open_threads_sync(
        lambda svc: svc._conn.execute("DELETE FROM open_threads WHERE owner_user_id = ?", (_OWNER,))
    )


@pytest.mark.asyncio
async def test_brain_wrappers_retire_list_and_unretire():
    from core.database import brain

    await brain.initialize()
    await _purge(brain)
    try:
        never = await _seed(brain, age_days=50)
        surfaced = await _seed(brain, age_days=50, surfaced=True)
        assert await brain.retire_unsurfaced_open_threads(owner_user_id=_OWNER, older_than_days=45) == 1
        active_ids = {t.id for t in await brain.list_active_open_threads(owner_user_id=_OWNER)}
        assert active_ids == {surfaced}
        dismissed = await brain.list_dismissed_open_threads(owner_user_id=_OWNER)
        assert [t.id for t in dismissed] == [never]
        assert dismissed[0].resolution_reason == "retired_unsurfaced (never surfaced, held 50d > 45d)"
        assert dismissed[0].source_kind is ThreadSourceKind.TOPIC
        assert await brain.unretire_open_thread(thread_id=never) is True
        assert await brain.unretire_open_thread(thread_id=never) is False
        active_ids = {t.id for t in await brain.list_active_open_threads(owner_user_id=_OWNER)}
        assert active_ids == {never, surfaced}
        assert await brain.list_dismissed_open_threads(owner_user_id=_OWNER) == []
        # R55: the next night's retirement and the backstop both leave the unretired row
        # alone; the backstop still takes the untouched 50-day row (surfaced, never updated)
        assert await brain.retire_unsurfaced_open_threads(owner_user_id=_OWNER, older_than_days=45) == 0
        expired = await brain.expire_stale_open_threads(owner_user_id=_OWNER, followup_ttl_days=7, thread_ttl_days=45)
        assert [(tid, kind) for tid, kind, _age in expired] == [(surfaced, "curiosity")]
        active_ids = {t.id for t in await brain.list_active_open_threads(owner_user_id=_OWNER)}
        assert active_ids == {never}
    finally:
        await _purge(brain)
