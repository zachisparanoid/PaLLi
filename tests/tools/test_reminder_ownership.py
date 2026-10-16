"""Only the owner of a reminder can cancel or move it (Batch 4 task 4, 5.9).

Before this fix ``cancel_reminder`` and ``reschedule_reminder`` trusted
whatever ``reminder_id`` the caller supplied and completed or moved that row
for anyone who asked — there was no check that ``ctx.turn_owner_id`` was the
row's ``user_id``. Guests have no tools today, so the only live caller is the
owner himself, but the store had no way to refuse a mismatched id at all.

Controller ruling R8 (2026-09-27, task 4) makes ``user_id`` on the two store
methods OPTIONAL rather than required, so the due-loop/snooze/dead-letter
system paths (which do not know an owner) and the pinned store tests keep
calling them with no ``user_id`` and are untouched. The two agent tools pass
``user_id=int(ctx.turn_owner_id)`` and treat anything that isn't a positive
rowcount (0, or a fake returning ``None``) as "not yours".
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from core.agent.tools import ToolContext
from core.database import PalliBrain


def _ctx(owner_id: int) -> ToolContext:
    return ToolContext(turn_owner_id=owner_id, is_owner=True, surface="dashboard")


class _FakeBrainReminders:
    """A small stateful double for the two mutating store methods.

    ``tests/agent/test_builtin_tools_failures.py`` patches single methods
    onto the live ``core.database.brain`` singleton rather than replacing the
    whole object; this borrows that idiom so the rest of ``brain`` (imports,
    other methods) stays real and untouched.
    """

    def __init__(self, rows: Dict[int, Dict[str, Any]]) -> None:
        self.rows = rows

    async def mark_scheduled_reminder_completed(
        self, *, reminder_id: int, user_id: Optional[int] = None,
    ) -> int:
        row = self.rows.get(reminder_id)
        if row is None:
            return 0
        if user_id is not None and row["user_id"] != user_id:
            return 0
        row["is_completed"] = True
        return 1

    async def update_scheduled_reminder_schedule(
        self, *, reminder_id: int, scheduled_time: float, user_id: Optional[int] = None,
    ) -> int:
        row = self.rows.get(reminder_id)
        if row is None:
            return 0
        if user_id is not None and row["user_id"] != user_id:
            return 0
        row["scheduled_time"] = scheduled_time
        return 1


def _rows() -> Dict[int, Dict[str, Any]]:
    # id 7 owned by user 2, per the plan's example.
    return {7: {"user_id": 2, "is_completed": False, "scheduled_time": 1000.0}}


# ── cancel_reminder ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_refuses_someone_elses_reminder(monkeypatch):
    from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
    from core.database import brain

    fake = _FakeBrainReminders(_rows())
    monkeypatch.setattr(
        brain, "mark_scheduled_reminder_completed", fake.mark_scheduled_reminder_completed,
    )

    out = await CancelReminderTool().execute({"reminder_id": 7}, _ctx(1))

    assert "isn't yours" in str(out)
    assert fake.rows[7]["is_completed"] is False


@pytest.mark.asyncio
async def test_cancel_succeeds_for_the_owner(monkeypatch):
    from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
    from core.database import brain

    fake = _FakeBrainReminders(_rows())
    monkeypatch.setattr(
        brain, "mark_scheduled_reminder_completed", fake.mark_scheduled_reminder_completed,
    )

    out = await CancelReminderTool().execute({"reminder_id": 7}, _ctx(2))

    assert "cancelled" in str(out)
    assert fake.rows[7]["is_completed"] is True


@pytest.mark.asyncio
async def test_cancel_refuses_an_id_that_does_not_exist(monkeypatch):
    from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
    from core.database import brain

    fake = _FakeBrainReminders(_rows())
    monkeypatch.setattr(
        brain, "mark_scheduled_reminder_completed", fake.mark_scheduled_reminder_completed,
    )

    out = await CancelReminderTool().execute({"reminder_id": 999}, _ctx(2))

    assert "isn't yours" in str(out)


# ── reschedule_reminder ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reschedule_refuses_someone_elses_reminder(monkeypatch):
    from core.agent.builtin_tools.reschedule_reminder import RescheduleReminderTool
    from core.database import brain

    fake = _FakeBrainReminders(_rows())
    monkeypatch.setattr(
        brain, "update_scheduled_reminder_schedule", fake.update_scheduled_reminder_schedule,
    )

    out = await RescheduleReminderTool().execute(
        {"reminder_id": 7, "new_time": "in 2 hours"}, _ctx(1),
    )

    assert "isn't yours" in str(out)
    assert fake.rows[7]["scheduled_time"] == 1000.0


@pytest.mark.asyncio
async def test_reschedule_succeeds_for_the_owner(monkeypatch):
    from core.agent.builtin_tools.reschedule_reminder import RescheduleReminderTool
    from core.database import brain

    fake = _FakeBrainReminders(_rows())
    monkeypatch.setattr(
        brain, "update_scheduled_reminder_schedule", fake.update_scheduled_reminder_schedule,
    )

    out = await RescheduleReminderTool().execute(
        {"reminder_id": 7, "new_time": "in 2 hours"}, _ctx(2),
    )

    assert "rescheduled" in str(out)
    assert fake.rows[7]["scheduled_time"] != 1000.0


# ── existing failure behaviour stays a ToolFailure ───────────────────────────


@pytest.mark.asyncio
async def test_cancel_store_exception_still_beats_the_ownership_check(monkeypatch):
    """A store that raises is a failure, not a refusal — ``_boom`` (the
    existing pin in tests/agent/test_builtin_tools_failures.py) must still
    surface as a ``ToolFailure``, ownership check or not."""
    from core.agent.tool_outcome import ToolFailure
    from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
    from core.database import brain

    async def _boom(*args, **kwargs):
        raise RuntimeError("store is down")

    monkeypatch.setattr(brain, "mark_scheduled_reminder_completed", _boom)

    out = await CancelReminderTool().execute({"reminder_id": 7}, _ctx(1))

    assert isinstance(out, ToolFailure)


# ── the real store, ownership enforced in SQL ────────────────────────────────


async def _insert_row(brain: PalliBrain, *, user_id: int) -> int:
    conn = await brain.get_connection()
    try:
        return int(await conn.fetchval(
            """
            INSERT INTO scheduled_reminders
            (user_id, platform, channel_id, surface_id, reminder_text, scheduled_time, created_time, is_completed, kind)
            VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, $8)
            RETURNING id
            """,
            user_id, "discord", 111, "111", "stretch", 1000.0, 900.0, "reminder",
        ))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_store_cancel_only_touches_the_owners_row(tmp_path):
    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    try:
        reminder_id = await _insert_row(brain, user_id=2)

        rowcount = await brain.mark_scheduled_reminder_completed(
            reminder_id=reminder_id, user_id=1,
        )
        assert rowcount == 0
        row = await brain.get_scheduled_reminder_record(reminder_id)
        assert row["is_completed"] is False

        rowcount = await brain.mark_scheduled_reminder_completed(
            reminder_id=reminder_id, user_id=2,
        )
        assert rowcount == 1
        row = await brain.get_scheduled_reminder_record(reminder_id)
        assert row["is_completed"] is True
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_store_reschedule_only_touches_the_owners_row(tmp_path):
    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    try:
        reminder_id = await _insert_row(brain, user_id=2)

        rowcount = await brain.update_scheduled_reminder_schedule(
            reminder_id=reminder_id, scheduled_time=5000.0, user_id=1,
        )
        assert rowcount == 0
        row = await brain.get_scheduled_reminder_record(reminder_id)
        assert row["scheduled_time"] == 1000.0

        rowcount = await brain.update_scheduled_reminder_schedule(
            reminder_id=reminder_id, scheduled_time=5000.0, user_id=2,
        )
        assert rowcount == 1
        row = await brain.get_scheduled_reminder_record(reminder_id)
        assert row["scheduled_time"] == 5000.0
    finally:
        await brain.close()
