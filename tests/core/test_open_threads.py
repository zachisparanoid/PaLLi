"""Service-level CRUD for the open_threads table."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.open_threads import (
    OpenThread,
    OpenThreadsService,
    ThreadKind,
    ThreadSourceKind,
    ThreadStatus,
)
from core.sqlite_schema_catalog import ensure_catalog_schema_sync


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        # Service no longer commits internally; put the test connection in
        # autocommit mode so writes are durable without explicit commit().
        conn.isolation_level = None
        try:
            ensure_catalog_schema_sync(conn)
            yield OpenThreadsService(conn)
        finally:
            conn.close()


def test_add_thread_returns_id_and_persists_active_row(service):
    thread_id = service.add_thread(
        owner_user_id=1,
        topic_summary="user thinking about workshops",
        why_it_matters="casual interest, no commitment",
        kind=ThreadKind.CURIOSITY,
        urgency="no urgency",
    )
    assert thread_id
    rows = service.list_active(owner_user_id=1)
    assert len(rows) == 1
    assert rows[0].id == thread_id
    assert rows[0].status == ThreadStatus.ACTIVE
    assert rows[0].surface_count == 0
    assert rows[0].last_surfaced_at is None


def test_close_thread_flips_status_and_records_reason(service):
    thread_id = service.add_thread(
        owner_user_id=1,
        topic_summary="email vendor",
        why_it_matters="committed to send this week",
        kind=ThreadKind.WORK,
        urgency="time-sensitive",
    )
    service.close_thread(
        thread_id=thread_id,
        resolution_reason="user confirmed sent",
        status=ThreadStatus.RESOLVED,
    )
    active = service.list_active(owner_user_id=1)
    assert active == []


def test_close_thread_with_dismissed_status(service):
    thread_id = service.add_thread(
        owner_user_id=1,
        topic_summary="workshop thing",
        why_it_matters="changed mind",
        kind=ThreadKind.CURIOSITY,
        urgency="no urgency",
    )
    service.close_thread(
        thread_id=thread_id,
        resolution_reason="user said stop bringing it up",
        status=ThreadStatus.DISMISSED,
    )
    active = service.list_active(owner_user_id=1)
    assert active == []


def test_mark_surfaced_increments_count_and_sets_timestamp(service):
    thread_id = service.add_thread(
        owner_user_id=1,
        topic_summary="x",
        why_it_matters="y",
        kind=ThreadKind.OTHER,
        urgency="z",
    )
    service.mark_surfaced(thread_id=thread_id)
    rows = service.list_active(owner_user_id=1)
    assert len(rows) == 1
    assert rows[0].surface_count == 1
    assert rows[0].last_surfaced_at is not None


def test_list_active_filters_by_owner(service):
    service.add_thread(
        owner_user_id=1,
        topic_summary="owner1",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="x",
    )
    service.add_thread(
        owner_user_id=2,
        topic_summary="owner2",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="x",
    )
    assert len(service.list_active(owner_user_id=1)) == 1
    assert len(service.list_active(owner_user_id=2)) == 1


def test_close_unknown_thread_id_is_noop(service):
    # Should not raise. Just no-op.
    service.close_thread(
        thread_id="nonexistent",
        resolution_reason="x",
        status=ThreadStatus.RESOLVED,
    )


def test_kind_values_constrained_to_enum(service):
    with pytest.raises(ValueError):
        service.add_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind="not_a_valid_kind",  # type: ignore[arg-type]
            urgency="z",
        )


# ── C1.5: ThreadStatus.LOGGED + source_kind plumbing (B5 Decision 5) ────


def test_thread_status_includes_logged_for_proactive_record():
    """LOGGED is the new status for append-only history rows (B5 Q4).

    Surfaces in the enum so the migration script and absorbed-table writers
    can reference it via ``ThreadStatus.LOGGED`` without a stringly-typed
    literal.
    """
    assert ThreadStatus.LOGGED.value == "logged"
    assert ThreadStatus("logged") is ThreadStatus.LOGGED


def test_thread_source_kind_enum_covers_b5_kinds():
    """The four B5-defined source_kinds must all be in the enum."""
    assert ThreadSourceKind.TOPIC.value == "topic"
    assert ThreadSourceKind.RECURRING.value == "recurring"
    assert ThreadSourceKind.FOLLOWTHROUGH.value == "followthrough"
    assert ThreadSourceKind.PROACTIVE_RECORD.value == "proactive_record"


def test_add_thread_defaults_to_topic_source(service):
    """Pre-existing add_thread keeps writing source_kind='topic'."""
    thread_id = service.add_thread(
        owner_user_id=1,
        topic_summary="x",
        why_it_matters="y",
        kind=ThreadKind.WORK,
        urgency="z",
    )
    rows = service.list_active(owner_user_id=1)
    assert len(rows) == 1
    assert rows[0].id == thread_id
    assert rows[0].source_kind == ThreadSourceKind.TOPIC
    assert rows[0].source_id is None
    assert rows[0].source_payload is None


def test_add_source_thread_persists_proactive_record_with_logged_status(service):
    """Absorbed proactive_actions row → LOGGED status, full payload preserved."""
    thread_id = service.add_source_thread(
        owner_user_id=1,
        topic_summary="hey",
        why_it_matters="Proactive outreach record",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="42",
        source_payload={
            "action_type": "proactive_message",
            "original_trigger_reason": "review_due",
            "success": True,
            "user_response": "thanks",
        },
        created_at=1800.0,
        updated_at=1810.0,
        last_surfaced_at=1810.0,
        surface_count=1,
        resolved_at=1810.0,
        resolution_reason="executed",
    )
    assert thread_id
    # LOGGED rows MUST NOT show up under list_active (active-only filter).
    assert service.list_active(owner_user_id=1) == []
    rows = service.list_by_source_kind(
        owner_user_id=1,
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        limit=10,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.id == thread_id
    assert row.source_kind == ThreadSourceKind.PROACTIVE_RECORD
    assert row.source_id == "42"
    assert row.status == ThreadStatus.LOGGED
    assert row.source_payload is not None
    assert row.source_payload["action_type"] == "proactive_message"
    assert row.source_payload["original_trigger_reason"] == "review_due"
    assert row.source_payload["success"] is True
    assert row.surface_count == 1
    assert row.last_surfaced_at == 1810.0


def test_list_by_source_kind_filters_out_other_kinds(service):
    """list_by_source_kind ONLY returns matching rows."""
    service.add_thread(
        owner_user_id=1,
        topic_summary="topic-thread",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="z",
    )
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="proactive-record",
        why_it_matters="y",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="7",
        source_payload={"action_type": "proactive_message"},
    )
    proactive_rows = service.list_by_source_kind(
        owner_user_id=1,
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
    )
    assert len(proactive_rows) == 1
    assert proactive_rows[0].topic_summary == "proactive-record"


def test_count_by_source_kind_since_filters_by_payload_action_type(service):
    """Daily-cap query: counts by source_kind + action_type + cutoff."""
    import time as _t
    now = _t.time()
    # In-window proactive_message
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg1",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="1",
        source_payload={"action_type": "proactive_message"},
        created_at=now - 3600,
        last_surfaced_at=now - 3600,
    )
    # In-window followup_message
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg2",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="2",
        source_payload={"action_type": "followup_message"},
        created_at=now - 1800,
        last_surfaced_at=now - 1800,
    )
    # In-window OTHER action_type — must not be counted
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg3",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="3",
        source_payload={"action_type": "system_event"},
        created_at=now - 60,
        last_surfaced_at=now - 60,
    )
    # Out-of-window proactive_message — must not be counted
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg4",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="4",
        source_payload={"action_type": "proactive_message"},
        created_at=now - 100000,
        last_surfaced_at=now - 100000,
    )
    count = service.count_by_source_kind_since(
        owner_user_id=1,
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        since=now - (24 * 3600),
        payload_action_types=["proactive_message", "followup_message"],
    )
    assert count == 2


def test_max_last_surfaced_by_source_kind_returns_none_when_empty(service):
    """No rows → None (matches MAX-of-empty-set semantics)."""
    import time as _t
    assert service.max_last_surfaced_by_source_kind(
        owner_user_id=1,
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        since=_t.time() - 86400,
    ) is None


def test_max_last_surfaced_by_source_kind_returns_most_recent(service):
    """MAX over the qualifying rows; respects payload action_type filter."""
    import time as _t
    now = _t.time()
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg1",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="1",
        source_payload={"action_type": "proactive_message"},
        created_at=now - 3600,
        last_surfaced_at=now - 3600,
    )
    service.add_source_thread(
        owner_user_id=1,
        topic_summary="msg2",
        why_it_matters="x",
        kind=ThreadKind.OTHER,
        urgency="historical",
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        source_id="2",
        source_payload={"action_type": "proactive_message"},
        created_at=now - 1800,
        last_surfaced_at=now - 1800,
    )
    max_ts = service.max_last_surfaced_by_source_kind(
        owner_user_id=1,
        source_kind=ThreadSourceKind.PROACTIVE_RECORD,
        since=now - (7 * 24 * 3600),
        payload_action_types=["proactive_message"],
    )
    assert max_ts is not None
    # 1800 second offset should be the most recent
    assert abs(max_ts - (now - 1800)) < 1.0


def test_add_source_thread_validates_source_kind(service):
    with pytest.raises(ValueError):
        service.add_source_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind=ThreadKind.OTHER,
            urgency="z",
            source_kind="bogus_source_kind",  # type: ignore[arg-type]
        )


def test_add_source_thread_validates_status(service):
    with pytest.raises(ValueError):
        service.add_source_thread(
            owner_user_id=1,
            topic_summary="x",
            why_it_matters="y",
            kind=ThreadKind.OTHER,
            urgency="z",
            source_kind=ThreadSourceKind.PROACTIVE_RECORD,
            status="not_a_status",  # type: ignore[arg-type]
        )
