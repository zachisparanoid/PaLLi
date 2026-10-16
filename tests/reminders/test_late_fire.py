"""Late reminders become one honest message (Batch 4 task 2, 5.7).

After downtime every overdue row used to fire on its own, one LLM turn and one
message each — a storm of stale nudges, none saying it was late. Now anything
more than thirty minutes past due is gathered per chat into ONE
"while I was down" message; rows within the grace, and rows whose due time is
unknown, keep today's per-row path.

Custody: a late row is marked completed (and stamped ``late_fired``) only
after the digest was actually delivered. A failed digest leaves every row open
and counts toward the same dead-letter cap the per-row path uses.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pytest

import core.reminders.scheduler as scheduler
from core.database import PalliBrain
from core.reminders.binding import REMINDER_MAX_DELIVERY_ATTEMPTS
from core.reminders.scheduler import partition_due, render_late_digest


# ── the plan's two tests (verbatim) ─────────────────────────────────────────


def test_grace_splits_on_time_from_late():
    now = 10_000.0
    rows = [{"id": 1, "scheduled_time": now - 29 * 60}, {"id": 2, "scheduled_time": now - 31 * 60}, {"id": 3, "scheduled_time": now - 11 * 3600}]
    on_time, late = partition_due(rows, now=now)
    assert [r["id"] for r in on_time] == [1] and [r["id"] for r in late] == [2, 3]


def test_digest_reads_naturally_for_one_and_for_many():
    one = render_late_digest([{"reminder_text": "call the dentist", "scheduled_time": 0.0}])
    assert one.startswith("This one came due while I was down")
    many = render_late_digest([{"reminder_text": "a", "scheduled_time": 0.0}, {"reminder_text": "b", "scheduled_time": 60.0}])
    assert many.startswith("While I was down, these came due:") and "\n- " in many


# ── partition_due ───────────────────────────────────────────────────────────


def test_unknown_lateness_is_not_lateness():
    """A row with no due time (absent, None, unreadable, or the store's 0.0
    stand-in) cannot be proven late, so it keeps today's per-row path."""
    now = 1_000_000.0
    rows = [
        {"id": 1},
        {"id": 2, "scheduled_time": None},
        {"id": 3, "scheduled_time": 0.0},
        {"id": 4, "scheduled_time": "not-a-time"},
    ]
    on_time, late = partition_due(rows, now=now)
    assert [r["id"] for r in on_time] == [1, 2, 3, 4]
    assert late == []


def test_exactly_the_grace_is_still_on_time_and_the_grace_is_a_knob():
    now = 1_000_000.0
    on_time, late = partition_due([{"id": 1, "scheduled_time": now - 1800}], now=now)
    assert [r["id"] for r in on_time] == [1] and late == []
    on_time, late = partition_due([{"id": 1, "scheduled_time": now - 120}], now=now, grace_seconds=60)
    assert on_time == [] and [r["id"] for r in late] == [1]


def test_tuple_rows_partition_like_dict_rows():
    """The store's raw row shape (a tuple in column order) reads the same."""
    now = 1_000_000.0
    late_row = (5, 1, "discord", 111, "111", "stretch", now - 7200, 0.0, False, "reminder", "", "", None)
    fresh_row = (6, 1, "discord", 111, "111", "water", now - 60, 0.0, False, "reminder", "", "", None)
    on_time, late = partition_due([late_row, fresh_row], now=now)
    assert on_time == [fresh_row] and late == [late_row]


# ── render_late_digest ──────────────────────────────────────────────────────


def _local(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


def test_digest_lines_show_the_clock_for_today_and_the_date_for_other_days():
    now = _local(2026, 9, 28, 12, 0)
    many = render_late_digest(
        [
            {"reminder_text": "take the bins out", "scheduled_time": _local(2026, 9, 27, 23, 15)},
            {"reminder_text": "call the dentist", "scheduled_time": _local(2026, 9, 28, 9, 0)},
        ],
        now=now,
    )
    assert many == (
        "While I was down, these came due:\n"
        "- 2026-09-27 23:15: take the bins out\n"
        "- 09:00: call the dentist"
    )
    one = render_late_digest(
        [{"reminder_text": "call the dentist", "scheduled_time": _local(2026, 9, 28, 9, 0)}],
        now=now,
    )
    assert one == "This one came due while I was down (09:00): call the dentist"


# ── the digest path inside the scheduler ────────────────────────────────────


class _Store:
    """Stands in for the brain's reminder methods; records what was done."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.completed: list[int] = []
        self.merged: list[tuple[int, dict]] = []
        self.fail_complete: set[int] = set()   # ids whose completion raises
        self.fail_merge: set[int] = set()      # ids whose metadata stamp raises

    async def list_due(self, *, as_of, limit=None):
        return [r for r in self.rows if r["id"] not in self.completed]

    async def mark_completed(self, *, reminder_id):
        if int(reminder_id) in self.fail_complete:
            raise RuntimeError("database is locked")
        self.completed.append(int(reminder_id))

    async def merge_metadata(self, *, reminder_id, patch):
        if int(reminder_id) in self.fail_merge:
            raise RuntimeError("database is locked")
        self.merged.append((int(reminder_id), dict(patch)))


@pytest.fixture
def store(monkeypatch):
    def _install(rows):
        s = _Store(rows)
        monkeypatch.setattr(scheduler.brain, "list_due_scheduled_reminders", s.list_due)
        monkeypatch.setattr(scheduler.brain, "mark_scheduled_reminder_completed", s.mark_completed)
        monkeypatch.setattr(scheduler.brain, "merge_scheduled_reminder_metadata", s.merge_metadata)
        return s
    return _install


def _target(monkeypatch, *, result=True):
    target = scheduler.ReminderScheduler()
    sent: list[dict] = []

    async def _send(row):
        sent.append(dict(row))
        return result

    monkeypatch.setattr(target, "_send_reminder", _send)
    return target, sent


def _row(rid, *, late_by, platform="discord", channel_id=111, text=None, now=None,
         user_id=1, kind="reminder"):
    now = time.time() if now is None else now
    return {
        "id": rid,
        "user_id": user_id,
        "platform": platform,
        "channel_id": channel_id,
        "surface_id": str(channel_id),
        "reminder_text": text or f"reminder {rid}",
        "scheduled_time": now - late_by,
        "kind": kind,
        "runtime_job_id": f"job_{rid}",
        "metadata": {"delivery_preference": "channel"},
    }


@pytest.mark.asyncio
async def test_a_late_group_is_sent_once_per_chat_and_on_time_rows_keep_their_path(store, monkeypatch):
    s = store([
        _row(1, late_by=3 * 3600, text="take the bins out"),
        _row(2, late_by=2 * 3600, text="call the dentist"),
        _row(3, late_by=90 * 60, platform="telegram", channel_id=-100555, text="stretch"),
        _row(4, late_by=60, text="drink water"),
    ])
    target, sent = _target(monkeypatch)

    await target._check_due_reminders()

    # The on-time row goes out as itself, unchanged.
    assert sent[0]["id"] == 4 and sent[0]["reminder_text"] == "drink water"
    assert sent[0]["runtime_job_id"] == "job_4"
    # One digest per chat — never one fire per late row.
    digests = sent[1:]
    assert len(digests) == 2
    discord = next(d for d in digests if d["platform"] == "discord")
    telegram = next(d for d in digests if d["platform"] == "telegram")
    assert discord["id"] == 1 and discord["channel_id"] == 111 and discord["surface_id"] == "111"
    assert discord["user_id"] == 1 and discord["runtime_job_id"] == ""
    assert discord["reminder_text"].startswith("While I was down, these came due:")
    assert "take the bins out" in discord["reminder_text"]
    assert "call the dentist" in discord["reminder_text"]
    assert "drink water" not in discord["reminder_text"]
    assert telegram["id"] == 3 and telegram["channel_id"] == -100555
    assert telegram["reminder_text"].startswith("This one came due while I was down (")
    assert telegram["reminder_text"].endswith("): stretch")

    # Delivered, so every row is retired; only the late ones say they were late.
    assert sorted(s.completed) == [1, 2, 3, 4]
    assert sorted(s.merged) == [(1, {"late_fired": True}), (2, {"late_fired": True}), (3, {"late_fired": True})]


@pytest.mark.asyncio
async def test_a_failed_digest_leaves_every_row_open_and_counts(store, monkeypatch):
    s = store([_row(1, late_by=3 * 3600), _row(2, late_by=2 * 3600)])
    target, sent = _target(monkeypatch, result=False)

    await target._check_due_reminders()

    assert len(sent) == 1                        # one digest attempt, not two fires
    assert s.completed == [] and s.merged == []  # nothing claims it was said
    # Keyed by the digest's own door, never by its first row's id (final
    # review, minor 5): that row may carry on-time failures of its own.
    assert target._delivery_failures == {"digest:1:discord:111": 1}


@pytest.mark.asyncio
async def test_a_digest_that_never_lands_is_dead_lettered_at_the_cap(store, monkeypatch, caplog):
    s = store([_row(1, late_by=3 * 3600), _row(2, late_by=2 * 3600)])
    target, sent = _target(monkeypatch, result=False)

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS - 1):
        await target._check_due_reminders()
    assert s.completed == []                     # below the cap: still retried

    with caplog.at_level(logging.WARNING, logger=scheduler.logger.name):
        await target._check_due_reminders()

    assert len(sent) == REMINDER_MAX_DELIVERY_ATTEMPTS
    assert sorted(s.completed) == [1, 2]
    assert sorted(s.merged) == [(1, {"dead_lettered": True}), (2, {"dead_lettered": True})]
    assert not any(patch.get("late_fired") for _, patch in s.merged)
    assert target._delivery_failures == {}
    assert any("dead-letter" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert any(
        e.get("kind") == "dead_lettered" and e.get("reminder_id") == 1
        for e in target.recent_events
    )

    await target._check_due_reminders()          # retired rows are not listed again
    assert len(sent) == REMINDER_MAX_DELIVERY_ATTEMPTS



@pytest.mark.asyncio
async def test_a_digest_gets_its_own_three_tries_whatever_its_first_row_carried(store, monkeypatch):
    """Final review, minor 5: the digest counted on its first row's id, and
    that row may already carry failed on-time tries from before it turned
    late — so the digest could dead-letter on its first try. The digest now
    has a key of its own, and the cap means three digest attempts."""
    s = store([_row(1, late_by=3 * 3600), _row(2, late_by=2 * 3600)])
    target, sent = _target(monkeypatch, result=False)
    target._delivery_failures = {1: REMINDER_MAX_DELIVERY_ATTEMPTS - 1}   # row 1's on-time failures

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS - 1):
        await target._check_due_reminders()
    assert s.completed == [] and s.merged == []          # still owed after two digest tries

    await target._check_due_reminders()

    assert len(sent) == REMINDER_MAX_DELIVERY_ATTEMPTS
    assert sorted(s.completed) == [1, 2]
    assert sorted(s.merged) == [(1, {"dead_lettered": True}), (2, {"dead_lettered": True})]
    assert target._delivery_failures == {}              # the digest's count and row 1's both go

@pytest.mark.asyncio
async def test_the_digest_record_rides_the_real_delivery_request(store, monkeypatch):
    """The synthetic digest record goes through the real ``_send_reminder`` →
    ``prepare_reminder_delivery_request``: the digest is the reminder text,
    the door is the group's, and no runtime job is begun or completed."""
    from core.reminders.binding import ReminderDeliveryResult

    s = store([
        _row(1, late_by=3 * 3600, text="take the bins out"),
        _row(2, late_by=2 * 3600, text="call the dentist"),
    ])
    target = scheduler.ReminderScheduler()
    requests = []

    async def _execute(request):
        requests.append(request)
        return ReminderDeliveryResult(
            sent=True, delivered_in="channel", delivery_channel_id=111,
            delivery_surface_id="111", delivered_text="her words",
        )

    async def _policy(**_kw):
        return {}

    async def _no_softening(**_kw):
        return False

    monkeypatch.setattr(target, "_execute_reminder_delivery", _execute)
    monkeypatch.setattr(target, "_get_reminder_action_policy", _policy)
    monkeypatch.setattr(target, "_should_reduce_reminder_interruptions", _no_softening)

    await target._check_due_reminders()

    assert len(requests) == 1
    request = requests[0]
    assert request.reminder_id == 1 and request.user_id == 1
    assert request.platform == "discord" and request.channel_id == 111
    assert request.delivery_surface_id == "111"
    assert request.reminder_text.startswith("While I was down, these came due:")
    assert request.kind == "reminder" and request.runtime_job_id == ""
    # The flag survives the request and reaches the fire turn's payload.
    from core.reminders.binding import _reminder_payload_from_request
    assert request.metadata.get("late_digest") is True
    assert _reminder_payload_from_request(request).late_digest is True
    assert sorted(s.completed) == [1, 2]


@pytest.mark.asyncio
async def test_a_row_with_no_due_time_takes_the_per_row_path(store, monkeypatch):
    s = store([{"id": 7, "user_id": 1, "platform": "discord", "reminder_text": "call the shop"}])
    target, sent = _target(monkeypatch)

    await target._check_due_reminders()

    assert sent == [{"id": 7, "user_id": 1, "platform": "discord", "reminder_text": "call the shop"}]
    assert s.completed == [7] and s.merged == []


# ── fix round 1: the digest names every reminder; rows retire safely ────────


@pytest.mark.asyncio
async def test_the_digest_record_is_flagged_and_the_rows_metadata_is_not_touched(store, monkeypatch):
    first = _row(1, late_by=3 * 3600)
    s = store([first, _row(2, late_by=2 * 3600)])
    target, sent = _target(monkeypatch)

    await target._check_due_reminders()

    assert sent[0]["metadata"] == {"delivery_preference": "channel", "late_digest": True}
    assert first["metadata"] == {"delivery_preference": "channel"}   # the row's own dict
    assert sorted(s.completed) == [1, 2]


@pytest.mark.asyncio
async def test_a_group_led_by_a_timer_is_still_a_reminder_digest(store, monkeypatch):
    """A timer's fallback text is "Timer finished." — it would drop every
    other line of the digest, so a digest is always kind "reminder"."""
    store([
        _row(1, late_by=3 * 3600, kind="timer", text="Timer finished."),
        _row(2, late_by=2 * 3600, text="call the dentist"),
    ])
    target, sent = _target(monkeypatch)

    await target._check_due_reminders()

    assert len(sent) == 1 and sent[0]["kind"] == "reminder"
    assert "Timer finished." in sent[0]["reminder_text"]
    assert "call the dentist" in sent[0]["reminder_text"]


@pytest.mark.asyncio
async def test_two_owners_in_one_chat_get_two_digests(store, monkeypatch):
    """One user's reminder text never rides another user's digest."""
    store([
        _row(1, late_by=3 * 3600, user_id=1, text="his dentist"),
        _row(2, late_by=2 * 3600, user_id=2, text="her train"),
        _row(3, late_by=2 * 3600, user_id=1, text="his bins"),
    ])
    target, sent = _target(monkeypatch)

    await target._check_due_reminders()

    assert len(sent) == 2
    by_user = {d["user_id"]: d for d in sent}
    assert by_user[1]["id"] == 1
    assert "his dentist" in by_user[1]["reminder_text"] and "his bins" in by_user[1]["reminder_text"]
    assert "her train" not in by_user[1]["reminder_text"]
    assert by_user[2]["id"] == 2
    assert by_user[2]["reminder_text"].endswith("): her train")


@pytest.mark.asyncio
async def test_a_completion_failing_partway_still_retires_the_rest(store, monkeypatch, caplog):
    s = store([_row(1, late_by=3 * 3600), _row(2, late_by=2 * 3600), _row(3, late_by=90 * 60)])
    s.fail_complete = {2}
    target, sent = _target(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=scheduler.logger.name):
        await target._check_due_reminders()

    assert len(sent) == 1
    assert sorted(s.completed) == [1, 3]
    assert sorted(s.merged) == [(1, {"late_fired": True}), (3, {"late_fired": True})]
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("could not retire reminder 2" in m for m in messages)
    # The digest WAS delivered; nothing may call it a send error or count it.
    assert not any("Error sending late reminder digest" in m for m in messages)
    assert getattr(target, "_delivery_failures", {}) == {}


@pytest.mark.asyncio
async def test_a_failed_dead_letter_stamp_leaves_that_row_open(store, monkeypatch):
    """Dead-letter stamps BEFORE it completes: a row whose stamp failed stays
    open to retry, and never looks delivered."""
    s = store([_row(1, late_by=3 * 3600), _row(2, late_by=2 * 3600)])
    s.fail_merge = {2}
    target, sent = _target(monkeypatch, result=False)

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()

    assert s.completed == [1]
    assert s.merged == [(1, {"dead_lettered": True})]

    await target._check_due_reminders()          # row 2 is still due and tried again
    assert len(sent) == REMINDER_MAX_DELIVERY_ATTEMPTS + 1
    assert sent[-1]["id"] == 2


def _fire_capture(monkeypatch):
    seen: dict = {}

    async def fake_dispatch(turn):
        seen["turn"] = turn
        return None

    async def no_history(**_kw):
        return []

    import core.agent.dispatch as dispatch_mod
    monkeypatch.setattr(dispatch_mod, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(scheduler.brain, "get_conversation_history", no_history)
    return seen


@pytest.mark.asyncio
async def test_a_digest_fire_turn_is_flagged_and_told_to_name_every_line(monkeypatch):
    from types import SimpleNamespace

    seen = _fire_capture(monkeypatch)
    digest = "While I was down, these came due:\n- 09:00: a\n- 09:30: b"
    await scheduler.fire_reminder(
        SimpleNamespace(id=1, what=digest, platform="discord", channel_id=111, late_digest=True),
        owner_id=1,
    )
    turn = seen["turn"]
    assert turn.metadata.extra.get("late_digest") is True
    assert turn.content.startswith(f"[REMINDER FIRE] {digest}")
    assert "Name every one" in turn.content
    assert "leave none out" in turn.content

    # With recent lines, "already handled" is said per line, never by skipping.
    async def some_history(**_kw):
        return [{"role": "user", "content": "did a already", "timestamp": 1.0}]
    monkeypatch.setattr(scheduler.brain, "get_conversation_history", some_history)
    await scheduler.fire_reminder(
        SimpleNamespace(id=1, what=digest, platform="discord", channel_id=111, late_digest=True),
        owner_id=1,
    )
    content = seen["turn"].content
    assert "they: did a already" in content
    assert "say so for that one, but still name it" in content
    assert "instead of repeating it" not in content


@pytest.mark.asyncio
async def test_an_ordinary_fire_turn_is_not_flagged_and_keeps_its_wording(monkeypatch):
    from types import SimpleNamespace

    seen = _fire_capture(monkeypatch)
    await scheduler.fire_reminder(SimpleNamespace(id=1, what="call the shop"), owner_id=1)
    turn = seen["turn"]
    assert "late_digest" not in turn.metadata.extra
    assert turn.content == "[REMINDER FIRE] call the shop"


def test_the_fire_payload_carries_the_digest_flag_from_the_request():
    from core.reminders.binding import ReminderDeliveryRequest, _reminder_payload_from_request

    def _request(metadata):
        return ReminderDeliveryRequest(
            reminder_id=1, user_id=1, platform="discord", channel_id=111,
            delivery_surface_id="111", reminder_text="digest",
            reminder_message="Reminder: digest", scheduled_time=1.0,
            kind="reminder", metadata=metadata, delivery_preference="channel",
            runtime_job_id="", account_user_id=None,
        )

    assert _reminder_payload_from_request(_request({"late_digest": True})).late_digest is True
    assert _reminder_payload_from_request(_request({})).late_digest is False


def _wire_dispatch(monkeypatch):
    """Fake dispatch's edges (no LLM, no DB); the real dispatch body runs.
    Mirrors tests/agent/test_workbench_dispatch_seam.py::_wire."""
    from types import SimpleNamespace
    import core.agent.dispatch as d
    import core.agent.runner as runner_mod
    import core.turn.persistence as persistence_mod
    import ai.present_moment as pm
    import ai.stake_ledger as ledger

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")
    seen: dict = {"deps": None}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        seen["deps"] = deps
        return SimpleNamespace(responded=True, response_text="ok",
                               image_descriptions=(), text_attachment_contents=(),
                               attachments=(), error=None)

    async def fake_persist(**_kw):
        return None

    async def fake_user_persist(turn, content):
        return None

    async def fake_client():
        return object()

    async def fake_cadence():
        async def _c(prompt):
            return ""
        return _c

    async def fake_is_owner(turn):
        return True

    monkeypatch.setattr(runner_mod, "run_turn", fake_run_turn)
    monkeypatch.setattr(persistence_mod, "_persist_assistant_side_to_history", fake_persist)
    monkeypatch.setattr(persistence_mod, "_persist_user_side_with_content", fake_user_persist)
    monkeypatch.setattr(d, "_get_openai_compat_client", fake_client)
    monkeypatch.setattr(d, "_build_cadence_llm_callable", fake_cadence)
    monkeypatch.setattr(d, "_resolve_is_owner_for_turn", fake_is_owner)
    monkeypatch.setattr(pm, "notice", lambda kind, **kw: None)
    monkeypatch.setattr(ledger, "ledger_enabled", lambda: False)
    return seen


def _fire_turn(*, late_digest):
    from core.turn.types import Turn, TurnMetadata

    extra = {"late_digest": True} if late_digest else {}
    return Turn(surface="reminder_fire", owner_id=1, content="[REMINDER FIRE] x", images=[],
                metadata=TurnMetadata(origin="reminder", reminder_id=1, extra=extra))


@pytest.mark.asyncio
async def test_the_length_governor_skips_a_digest_and_still_shapes_a_plain_reminder(monkeypatch):
    from core.agent.dispatch import dispatch_turn

    seen = _wire_dispatch(monkeypatch)
    await dispatch_turn(_fire_turn(late_digest=True))
    assert seen["deps"].govern_replies is False

    seen["deps"] = None
    await dispatch_turn(_fire_turn(late_digest=False))
    assert seen["deps"].govern_replies is True


# ── the metadata writer, on a real store ────────────────────────────────────


async def _insert(brain: PalliBrain, *, metadata_json):
    conn = await brain.get_connection()
    try:
        return int(await conn.fetchval(
            """
            INSERT INTO scheduled_reminders
            (user_id, platform, channel_id, surface_id, reminder_text, scheduled_time, created_time, is_completed, kind, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, $8, $9::jsonb)
            RETURNING id
            """,
            1, "discord", 111, "111", "stretch", 1000.0, 900.0, "reminder", metadata_json,
        ))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_merge_scheduled_reminder_metadata_merges_into_the_stored_json(tmp_path):
    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    try:
        with_meta = await _insert(brain, metadata_json='{"delivery_preference": "dm"}')
        without_meta = await _insert(brain, metadata_json=None)

        await brain.merge_scheduled_reminder_metadata(reminder_id=with_meta, patch={"late_fired": True})
        await brain.merge_scheduled_reminder_metadata(reminder_id=without_meta, patch={"late_fired": True})
        await brain.merge_scheduled_reminder_metadata(reminder_id=999_999, patch={"late_fired": True})

        first = await brain.get_scheduled_reminder_record(with_meta)
        second = await brain.get_scheduled_reminder_record(without_meta)
        assert first["metadata"] == {"delivery_preference": "dm", "late_fired": True}
        assert second["metadata"] == {"late_fired": True}
        # A metadata stamp is not a completion.
        assert first["is_completed"] is False and second["is_completed"] is False
        assert await brain.get_scheduled_reminder_record(999_999) is None
    finally:
        await brain.close()
