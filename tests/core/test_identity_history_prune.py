"""Identity history keeps what's useful (Batch 8, task 3, item 2.11; owner
call B8-3; rulings T3-1..T3-6).

Every change to one of her identity documents saves a full copy in
``identity_document_versions`` — 65% of the database, nearly all of it
CHANGELOG and MEMORY copies. A nightly prune keeps, per (user, platform,
doc_key) stream: the newest ten copies, the newest of each day for the last
30 days, the newest of each ISO week before that, and ALWAYS the oldest row —
first contact reads ``MIN(created_at)``. The live documents are never
touched; only old copies go.

``plan_prune`` is pure (ids in, ids to delete out); ``prune_history`` runs it
against a database and deletes in one transaction. Everything here runs on a
temp database, never hers.
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import datetime

import pytest
import pytest_asyncio

import core.retention as retention
from core.identity.history_prune import plan_prune, prune_history
from core.retention import _prune_identity_history as _REAL_PRUNE_STEP

DAY = 86400.0


def _row(i, day, *, user_id=1, platform="owner", doc_key="memory"):
    return {"id": i, "doc_key": doc_key, "user_id": user_id, "platform": platform,
            "created_at": day * DAY}


# ── the plan (pure) ──────────────────────────────────────────────────────

def test_keeps_newest_ten_daily_for_thirty_days_weekly_before_and_the_first_ever():
    """The brief's test with unique ids (ruling T3-2: ``d*3+i``)."""
    rows = [_row(d * 3 + i, d) for d in range(1, 101) for i in (1, 2, 3)]   # 3 saves a day for 100 days
    doomed = plan_prune(rows, keep_last=10, daily_days=30, now=101 * DAY)
    kept = {r["id"] for r in rows} - doomed
    first = min(rows, key=lambda r: (r["created_at"], r["id"]))
    assert first["id"] == 4 and 4 in kept                 # the first ever: first contact reads it
    newest = sorted(rows, key=lambda r: (r["created_at"], r["id"]))[-10:]
    assert {r["id"] for r in newest} <= kept              # the newest 10
    for day in range(71, 101):                            # one per day for the last 30 days
        assert any(r["id"] in kept for r in rows if int(r["created_at"] // DAY) == day), day
    assert len(kept) <= 10 + 30 + 11 + 1                   # + about one per ISO week before
    assert doomed                                          # and the rest go


def test_keep_last_zero_prunes_nothing():
    assert plan_prune([_row(1, 1), _row(2, 2)], keep_last=0, now=3 * DAY) == set()


def test_daily_days_defaults_to_thirty():
    rows = [_row(d * 3 + i, d) for d in range(1, 101) for i in (1, 2, 3)]
    assert plan_prune(rows, keep_last=10, now=101 * DAY) == plan_prune(
        rows, keep_last=10, daily_days=30, now=101 * DAY)


def test_a_tie_on_created_at_is_broken_by_id():
    """Two copies saved in the same instant: the higher id is the newer one,
    the lower id the older one — both ends of the stream stay."""
    rows = [_row(7, 1), _row(5, 1), {**_row(6, 1), "created_at": 1.5 * DAY}, _row(9, 50), _row(8, 50)]
    doomed = plan_prune(rows, keep_last=1, daily_days=0, now=400 * DAY)
    assert 5 not in doomed            # the oldest: lowest id at the earliest instant
    assert 9 not in doomed            # the newest: highest id at the latest instant
    assert 6 not in doomed            # the newest of the first week
    assert doomed == {7, 8}           # each shares its week with a newer copy


def test_the_weekly_tier_keeps_the_newest_copy_of_each_iso_week():
    # one save a day for 60 days, all before the daily window
    rows = [_row(d, d) for d in range(1, 61)]
    doomed = plan_prune(rows, keep_last=1, daily_days=0, now=1000 * DAY)
    kept = [r for r in rows if r["id"] not in doomed]
    weeks = defaultdict(list)
    for r in rows:
        weeks[datetime.fromtimestamp(r["created_at"]).isocalendar()[:2]].append(r)
    for week, members in weeks.items():
        newest = max(members, key=lambda r: (r["created_at"], r["id"]))
        assert newest["id"] not in doomed, week
    # one per week, plus the first ever (the newest is its week's newest)
    assert len(kept) == len(weeks) + 1


def test_every_stream_is_planned_on_its_own():
    """A busy MEMORY stream never costs a quiet stream — another document,
    another platform, another user — its copies."""
    busy = [_row(d * 3 + i, d) for d in range(1, 101) for i in (1, 2, 3)]
    quiet_doc = [_row(10_000 + d, d, doc_key="preferences") for d in (1, 2, 3)]
    other_platform = [_row(20_000 + d, d, platform="discord") for d in (1, 2, 3)]
    other_user = [_row(30_000 + d, d, user_id=2) for d in (1, 2, 3)]
    doomed = plan_prune(busy + quiet_doc + other_platform + other_user,
                        keep_last=10, daily_days=30, now=101 * DAY)
    for row in quiet_doc + other_platform + other_user:
        assert row["id"] not in doomed
    assert doomed == plan_prune(busy, keep_last=10, daily_days=30, now=101 * DAY)


@pytest.mark.parametrize("seed", range(300))
def test_the_newest_and_the_oldest_of_every_stream_are_never_doomed(seed):
    """Review Focus 3 on random data (ruling T3-2): whatever the rows, the
    clock and the knobs, each stream keeps its newest and its oldest copy,
    its newest ``keep_last``, one per day inside the window and one per ISO
    week before it — and each user's first contact (MIN(created_at)) reads
    the same before and after."""
    rnd = random.Random(seed)
    now = 1_790_000_000.0 + rnd.uniform(0, 400 * DAY)
    ids = rnd.sample(range(1, 1_000_000), 600)
    rows = []
    for _ in range(rnd.randint(1, 6)):
        user, platform, doc = rnd.choice((1, 2, 3)), rnd.choice(("owner", "discord")), rnd.choice(
            ("memory", "changelog", "preferences"))
        instants = [now - rnd.uniform(0, 400 * DAY) for _ in range(rnd.randint(1, 8))]
        for _ in range(rnd.randint(1, 80)):
            # ties on purpose: some copies share an instant
            at = rnd.choice(instants) if rnd.random() < 0.3 else now - rnd.uniform(-DAY, 400 * DAY)
            rows.append({"id": ids.pop(), "user_id": user, "platform": platform, "doc_key": doc,
                         "created_at": at})
    keep_last = rnd.randint(1, 15)
    daily_days = rnd.randint(0, 40)
    doomed = plan_prune(rows, keep_last=keep_last, daily_days=daily_days, now=now)

    assert doomed <= {r["id"] for r in rows}
    streams = defaultdict(list)
    for r in rows:
        streams[(r["user_id"], r["platform"], r["doc_key"])].append(r)
    cutoff = now - daily_days * DAY
    for stream, members in streams.items():
        ordered = sorted(members, key=lambda r: (r["created_at"], r["id"]))
        assert ordered[0]["id"] not in doomed, ("oldest", stream)
        assert ordered[-1]["id"] not in doomed, ("newest", stream)
        assert not {r["id"] for r in ordered[-keep_last:]} & doomed, ("newest keep_last", stream)
        buckets = defaultdict(list)
        for r in ordered:
            when = datetime.fromtimestamp(r["created_at"])
            key = ("day", when.date()) if r["created_at"] >= cutoff else ("week", when.isocalendar()[:2])
            buckets[key].append(r)
        for key, bucket in buckets.items():
            assert any(r["id"] not in doomed for r in bucket), (stream, key)
    for user in {r["user_id"] for r in rows}:
        everything = [r["created_at"] for r in rows if r["user_id"] == user]
        kept = [r["created_at"] for r in rows if r["user_id"] == user and r["id"] not in doomed]
        assert min(kept) == min(everything)


# ── the prune (a temp database) ──────────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "history.sqlite3"))
    await brain.initialize()
    try:
        yield brain
    finally:
        await brain.close()


_INSERT = (
    "INSERT INTO identity_document_versions (user_id, platform, doc_key, file_path, version, "
    "actor_type, actor_id, reason, content, frontmatter, created_at) "
    "VALUES ($1, $2, $3, $4, $5, 'system', 'test', 'save', $6, '{}', $7)"
)


async def _insert_many(db, rows):
    """(user_id, platform, doc_key, version, created_at) rows, 100 bytes of
    content and a 2-byte frontmatter each — one transaction, one commit."""
    conn = await db.get_connection()
    try:
        async with conn.transaction():
            for user_id, platform, doc_key, version, created_at in rows:
                await conn.execute(_INSERT, user_id, platform, doc_key, f"{doc_key.upper()}.md",
                                   version, "x" * 100, created_at)
    finally:
        await conn.close()


async def _ids(db):
    conn = await db.get_connection()
    try:
        rows = await conn.fetch("SELECT id FROM identity_document_versions ORDER BY id")
    finally:
        await conn.close()
    return [int(r[0]) for r in rows]


async def _seed_hundred_days(db, *, user_id=1, doc_key="memory"):
    rows = []
    for d in range(1, 101):
        for i in range(3):
            rows.append((user_id, "owner", doc_key, len(rows) + 1, d * DAY + i))
    await _insert_many(db, rows)


@pytest.mark.asyncio
async def test_prune_history_deletes_what_the_plan_dooms_and_logs_it(db, caplog):
    await _seed_hundred_days(db)
    before = await _ids(db)
    caplog.set_level(logging.INFO, logger="core.identity.history_prune")

    result = await prune_history(db, keep_last=10, daily_days=30, now=101 * DAY)

    after = await _ids(db)
    assert result.removed == len(before) - len(after) > 0
    assert result.kept == len(after)
    assert result.bytes == result.removed * (100 + 2)          # content + frontmatter, in bytes
    assert f"history_prune: kept {result.kept}, removed {result.removed}, bytes {result.bytes}" in caplog.text
    assert min(before) in after and max(before) in after       # the first ever and the newest
    assert len(result.streams) == 1
    stream = result.streams[0]
    assert (stream.user_id, stream.platform, stream.doc_key) == (1, "owner", "memory")
    assert (stream.kept, stream.removed) == (result.kept, result.removed)


@pytest.mark.asyncio
async def test_a_dry_run_removes_nothing_and_says_what_it_would(db):
    await _seed_hundred_days(db)
    before = await _ids(db)
    planned = await prune_history(db, keep_last=10, daily_days=30, now=101 * DAY, dry_run=True)
    assert await _ids(db) == before
    assert planned.dry_run and planned.removed > 0
    real = await prune_history(db, keep_last=10, daily_days=30, now=101 * DAY)
    assert (real.kept, real.removed, real.bytes) == (planned.kept, planned.removed, planned.bytes)


@pytest.mark.asyncio
async def test_keep_last_zero_touches_nothing(db):
    await _seed_hundred_days(db)
    before = await _ids(db)
    result = await prune_history(db, keep_last=0, daily_days=30, now=101 * DAY)
    assert await _ids(db) == before
    assert result.removed == 0 and result.kept == len(before)


@pytest.mark.asyncio
async def test_the_deletes_are_one_transaction(db, monkeypatch):
    """A batch that fails part-way leaves every copy in place: the deletes
    run in batches inside one transaction, and a failure rolls them all back."""
    import core.identity.history_prune as hp

    await _seed_hundred_days(db)
    before = await _ids(db)
    monkeypatch.setattr(hp, "DELETE_BATCH", 7)
    real_get = db.get_connection
    calls = {"deletes": 0}

    async def failing_connection():
        conn = await real_get()
        real_execute = conn.execute

        async def execute(query, *args):
            if query.lstrip().upper().startswith("DELETE"):
                calls["deletes"] += 1
                if calls["deletes"] == 3:
                    raise RuntimeError("the disk said no")
            return await real_execute(query, *args)

        conn.execute = execute
        return conn

    monkeypatch.setattr(db, "get_connection", failing_connection)
    with pytest.raises(RuntimeError, match="the disk said no"):
        await prune_history(db, keep_last=10, daily_days=30, now=101 * DAY)
    monkeypatch.setattr(db, "get_connection", real_get)
    assert await _ids(db) == before
    assert calls["deletes"] == 3


# (first contact reading the same date after the prune: tests/core/
# test_first_contact_preferences.py, beside the brain's MIN(created_at) test)


# ── the night's housekeeping (ruling T3-3) ────────────────────────────────

def test_the_night_step_is_pinned_in_tests():
    """G3: the seven night-cycle tests run _run_once for real; the prune
    step inside the night's housekeeping is a no-op for every test unless
    the test puts the real one back."""
    assert retention._prune_identity_history is not _REAL_PRUNE_STEP


@pytest.mark.asyncio
async def test_the_housekeeping_runs_the_prune_on_her_database(db, monkeypatch):
    import core.database as database

    await _seed_hundred_days(db)
    before = await _ids(db)
    monkeypatch.setattr(retention, "_prune_identity_history", _REAL_PRUNE_STEP)
    monkeypatch.setattr(database, "brain", db)
    memory = type("Memory", (), {"history_keep_last": 10, "history_daily_days": 30})()
    settings = type("Settings", (), {"memory": memory})()

    out = await retention.run_nightly_housekeeping(now=101 * DAY, settings=settings)

    after = await _ids(db)
    # Batch 8, task 5: the log sweep is the housekeeping's second key (pinned
    # to a no-op here, tests/conftest.py); this test is the prune's.
    assert out["history_prune"] == {"kept": len(after), "removed": len(before) - len(after),
                                    "bytes": (len(before) - len(after)) * 102}
    assert set(out) == {"history_prune", "retention"}


@pytest.mark.asyncio
async def test_the_housekeeping_prune_is_off_at_zero(db, monkeypatch):
    import core.database as database

    await _seed_hundred_days(db)
    before = await _ids(db)
    monkeypatch.setattr(retention, "_prune_identity_history", _REAL_PRUNE_STEP)
    monkeypatch.setattr(database, "brain", db)
    memory = type("Memory", (), {"history_keep_last": 0, "history_daily_days": 30})()
    out = await retention.run_nightly_housekeeping(
        now=101 * DAY, settings=type("Settings", (), {"memory": memory})())
    assert out["history_prune"] == {"skipped": "flag_off"}
    assert await _ids(db) == before


@pytest.mark.asyncio
async def test_a_broken_prune_is_an_error_in_the_record_never_a_raise(monkeypatch):
    import core.database as database
    import core.identity.history_prune as hp

    async def boom(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(retention, "_prune_identity_history", _REAL_PRUNE_STEP)
    monkeypatch.setattr(hp, "prune_history", boom)
    monkeypatch.setattr(database, "brain", object())
    memory = type("Memory", (), {"history_keep_last": 10, "history_daily_days": 30})()
    out = await retention.run_nightly_housekeeping(
        now=101 * DAY, settings=type("Settings", (), {"memory": memory})())
    assert out["history_prune"] == {"error": "database is locked"}


def test_the_knobs_come_from_the_memory_settings_with_the_schema_defaults():
    from core.identity.history_prune import history_prune_settings
    from core.settings_schema import MemoryConfig

    assert history_prune_settings(MemoryConfig(history_keep_last=10, history_daily_days=30)) == (10, 30)
    assert history_prune_settings(MemoryConfig(history_keep_last=0)) == (0, 30)
    defaults = MemoryConfig()
    assert history_prune_settings(defaults) == (defaults.history_keep_last, defaults.history_daily_days)
    assert history_prune_settings(object()) == (defaults.history_keep_last, defaults.history_daily_days)


def test_the_settings_ship_dark_and_refuse_negatives():
    """G5: a new setting's schema default keeps today's behaviour (no prune)
    until the wrap writes the owner's rows (B8-3: keep the last 10, daily
    for 30 days)."""
    from pydantic import ValidationError

    from core.settings_schema import MemoryConfig

    fields = MemoryConfig.model_fields
    assert fields["history_keep_last"].default == 0
    assert fields["history_daily_days"].default == 30
    assert MemoryConfig(history_keep_last=10).history_keep_last == 10
    for bad in ({"history_keep_last": -1}, {"history_daily_days": -1}):
        with pytest.raises(ValidationError):
            MemoryConfig(**bad)
    assert "0" in (fields["history_keep_last"].description or "")
