"""Expiry backstop (arbiter spec §7, as amended in-build): tiered TTLs, a
REVERSIBLE status flip with a recorded 'backstop_ttl' reason — expressed as
status='dismissed' because the live table's CHECK constraint pins the
status vocabulary (widening it needs a table rebuild; deviation recorded in
the spec). Only judgment-owned rows (source_kind='topic') age out —
recurring, followthrough, and record rows are never touched. Runs against
the REAL catalog DDL so the live CHECK constraints are exercised."""
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from core.open_threads import (
    OpenThreadsService,
    ThreadSourceKind,
    ThreadStatus,
)
from core.sqlite_schema_catalog import ensure_catalog_schema_sync

DAY = 86400.0


@pytest.fixture
def svc():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.isolation_level = None
        try:
            ensure_catalog_schema_sync(conn)
            yield OpenThreadsService(conn)
        finally:
            conn.close()


def _add(svc, *, kind, age_days, source_id=None):
    tid = svc.add_thread(
        owner_user_id=1, topic_summary=f"t-{kind}-{age_days}",
        why_it_matters="w", kind=kind, urgency="low", source_id=source_id,
    )
    then = time.time() - age_days * DAY
    svc._conn.execute(
        "UPDATE open_threads SET created_at = ?, updated_at = ? WHERE id = ?",
        (then, then, tid),
    )
    return tid


def test_add_thread_takes_source_id(svc):
    tid = svc.add_thread(
        owner_user_id=1, topic_summary="s", why_it_matters="w",
        kind="personal", urgency="low", source_id="workbench_share",
    )
    row = svc._conn.execute(
        "SELECT source_id, source_kind, status FROM open_threads WHERE id = ?",
        (tid,),
    ).fetchone()
    assert row == ("workbench_share", "topic", "active")


def test_tiered_ttls_flip_to_dismissed_with_backstop_reason(svc):
    old_followup = _add(svc, kind="followup", age_days=8)
    young_followup = _add(svc, kind="followup", age_days=6)
    old_topic = _add(svc, kind="curiosity", age_days=46)
    young_topic = _add(svc, kind="curiosity", age_days=44)
    expired = svc.expire_stale(
        owner_user_id=1, now=time.time(),
        followup_ttl_days=7, thread_ttl_days=45,
    )
    ids = {e[0] for e in expired}
    assert ids == {old_followup, old_topic}
    row = svc._conn.execute(
        "SELECT status, resolution_reason FROM open_threads WHERE id = ?",
        (old_topic,),
    ).fetchone()
    assert row[0] == "dismissed"          # live-CHECK-compatible terminal state
    assert row[1].startswith("backstop_ttl")
    assert "45" in row[1]
    active = {t.id for t in svc.list_active(owner_user_id=1)}
    assert active == {young_followup, young_topic}


def test_zero_ttl_disables_that_tier(svc):
    _add(svc, kind="followup", age_days=400)
    old_topic = _add(svc, kind="work", age_days=400)
    expired = svc.expire_stale(
        owner_user_id=1, now=time.time(), followup_ttl_days=0, thread_ttl_days=45,
    )
    assert {e[0] for e in expired} == {old_topic}


def test_shares_age_but_structural_source_kinds_never_expire(svc):
    recurring = svc.add_source_thread(
        owner_user_id=1, topic_summary="standing order", why_it_matters="w",
        kind="followup", urgency="low",
        source_kind=ThreadSourceKind.RECURRING, status=ThreadStatus.ACTIVE,
    )
    then = time.time() - 400 * DAY
    svc._conn.execute(
        "UPDATE open_threads SET created_at = ? WHERE id = ?", (then, recurring)
    )
    share = _add(svc, kind="personal", age_days=46, source_id="workbench_share")
    expired = svc.expire_stale(
        owner_user_id=1, now=time.time(), followup_ttl_days=7, thread_ttl_days=45,
    )
    assert {e[0] for e in expired} == {share}   # shares age; recurring never


def test_expiry_is_reversible(svc):
    tid = _add(svc, kind="curiosity", age_days=100)
    svc.expire_stale(
        owner_user_id=1, now=time.time(), followup_ttl_days=7, thread_ttl_days=45,
    )
    svc._conn.execute(
        "UPDATE open_threads SET status = 'active', resolution_reason = NULL, "
        "resolved_at = NULL WHERE id = ?",
        (tid,),
    )
    assert {t.id for t in svc.list_active(owner_user_id=1)} == {tid}
