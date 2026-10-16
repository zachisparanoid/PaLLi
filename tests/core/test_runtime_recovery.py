from __future__ import annotations

import pytest

from core.runtime_recovery import runtime_recovery


@pytest.mark.asyncio
async def test_recover_deferred_jobs_requeues_open_reminders_and_completes_missing_ones(monkeypatch):
    updated_jobs: list[dict] = []
    completed_jobs: list[dict] = []

    async def fake_list_jobs(*, statuses, job_types, limit):
        assert statuses == ["running", "waiting"]
        assert job_types == ["deferred_action"]
        assert limit == 256
        return [
            {
                "job_id": "job_future",
                "source_table": "scheduled_reminders",
                "source_id": "91",
                "metadata": {"recovery_attempt_count": 2},
            },
            {
                "job_id": "job_missing",
                "source_table": "scheduled_reminders",
                "source_id": "92",
                "metadata": {},
            },
        ]

    async def fake_get_scheduled_reminder_record(reminder_id: int):
        if reminder_id == 91:
            return {"id": 91, "scheduled_time": 2500.0, "is_completed": False}
        return None

    async def fake_update_job(**kwargs):
        updated_jobs.append(dict(kwargs))
        return {"job_id": kwargs["job_id"], "status": kwargs["status"]}

    async def fake_complete_job(**kwargs):
        completed_jobs.append(dict(kwargs))
        return {"job_id": kwargs["job_id"], "status": kwargs["status"]}

    monkeypatch.setattr(runtime_recovery, "_completed", False)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.list_jobs", fake_list_jobs)
    monkeypatch.setattr("core.runtime_recovery.brain.get_scheduled_reminder_record", fake_get_scheduled_reminder_record)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.update_job", fake_update_job)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.complete_job", fake_complete_job)
    monkeypatch.setattr("core.runtime_recovery.time.time", lambda: 2000.0)

    await runtime_recovery._recover_deferred_jobs()

    assert updated_jobs == [
        {
            "job_id": "job_future",
            "status": "scheduled",
            "completed_at": None,
            "last_error": "",
            "last_transition_reason": "recovered_after_restart",
            "metadata": {
                "recovered_after_restart": True,
                "recovery_source": "startup",
                "recovery_attempt_count": 2,
            },
        }
    ]
    assert completed_jobs == [
        {
            "job_id": "job_missing",
            "status": "completed",
            "last_transition_reason": "recovered_after_restart",
            "metadata": {
                "recovered_after_restart": True,
                "recovery_source": "startup",
                "recovery_attempt_count": 0,
            },
        }
    ]


@pytest.mark.asyncio
async def test_recover_internal_jobs_marks_maintenance_work_terminal(monkeypatch):
    touched_sessions: list[dict] = []
    completed_jobs: list[dict] = []

    async def fake_list_jobs(*, statuses, job_types, limit):
        assert statuses == ["running", "waiting"]
        return [
            {
                "job_id": "job_reflect",
                "job_type": "reflection",
                "runtime_session_key": "owner:7:surface:maintenance",
                "metadata": {},
            },
            {
                "job_id": "job_proactive",
                "job_type": "proactive_message",
                "runtime_session_key": "owner:7:surface:local:chat",
                "metadata": {},
            },
        ]

    async def fake_complete_job(**kwargs):
        completed_jobs.append(dict(kwargs))
        return {"job_id": kwargs["job_id"], "status": kwargs["status"]}

    async def fake_touch_session(**kwargs):
        touched_sessions.append(dict(kwargs))
        return {"session_key": kwargs["session_key"], "status": kwargs["status"]}

    monkeypatch.setattr(runtime_recovery, "_completed", False)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.list_jobs", fake_list_jobs)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.complete_job", fake_complete_job)
    monkeypatch.setattr("core.runtime_recovery.runtime_control.touch_session", fake_touch_session)

    await runtime_recovery._recover_internal_jobs()

    assert ("job_reflect", "skipped") in {(item["job_id"], item["status"]) for item in completed_jobs}
    assert ("job_proactive", "failed") in {(item["job_id"], item["status"]) for item in completed_jobs}
    assert touched_sessions[0]["status"] == "ready"


@pytest.mark.asyncio
async def test_recover_runtime_sessions_resets_stale_voice_and_maintenance(monkeypatch):
    touched_sessions: list[dict] = []

    async def fake_list_runtime_sessions(*, owner_user_id=None, statuses=None, session_types=None, limit=24, include_archived=False):
        assert owner_user_id is None
        assert set(statuses or []) == {"active", "running", "waiting", "listening"}
        assert session_types == ["maintenance", "local_voice", "discord_voice"]
        assert include_archived is False
        return [
            {
                "session_key": "owner:7:surface:maintenance",
                "session_type": "maintenance",
                "status": "running",
            },
            {
                "session_key": "owner:7:surface:local:voice",
                "session_type": "local_voice",
                "status": "active",
            },
            {
                "session_key": "owner:7:surface:discord:voice:123",
                "session_type": "discord_voice",
                "status": "active",
            },
        ]

    async def fake_touch_session(**kwargs):
        touched_sessions.append(dict(kwargs))
        return {"session_key": kwargs["session_key"], "status": kwargs["status"]}

    monkeypatch.setattr(runtime_recovery, "_completed", False)
    monkeypatch.setattr("core.runtime_recovery.brain.list_runtime_sessions", fake_list_runtime_sessions)
    monkeypatch.setattr(
        "core.runtime_recovery._voice_runtime_snapshots",
        lambda: [{"runtime_session_key": "owner:7:surface:discord:voice:123"}],
    )
    monkeypatch.setattr("core.runtime_recovery.runtime_control.touch_session", fake_touch_session)

    await runtime_recovery._recover_runtime_sessions()

    touched_keys = {item["session_key"] for item in touched_sessions}
    assert "owner:7:surface:maintenance" in touched_keys
    assert "owner:7:surface:local:voice" in touched_keys
    assert "owner:7:surface:discord:voice:123" not in touched_keys
    assert all(item["status"] == "ready" for item in touched_sessions)


