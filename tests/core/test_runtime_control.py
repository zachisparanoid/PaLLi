from __future__ import annotations

import asyncio

import pytest

from core.runtime_control import RuntimeControlPlane


@pytest.mark.asyncio
async def test_backfill_open_reminders_creates_missing_jobs_and_updates_runtime_links(monkeypatch) -> None:
    ensured_sessions: list[dict[str, object]] = []
    created_jobs: list[dict[str, object]] = []
    updated_links: list[dict[str, object]] = []

    async def fake_list_open_scheduled_reminders(*, limit: int) -> list[dict[str, object]]:
        assert limit == 256
        return [
            {
                "id": 91,
                "user_id": 7,
                "platform": "discord",
                "channel_id": 1234,
                "reminder_text": "Check the draft",
                "scheduled_time": 1500.0,
                "kind": "followthrough",
                "runtime_job_id": "",
                "runtime_session_key": "",
                "metadata": {
                    "account_user_id": 11,
                    "source_is_dm": True,
                    "delivery_preference": "dm",
                    "workflow_id": "wf_follow",
                    "followthrough_id": "wff_follow",
                    "thread_id": "thread-repo",
                    "thread_kind": "continuity",
                    "thread_label": "Repo Fix",
                    "lane_id": "operator:repo",
                    "continuity_refs": [
                        "workflow:wf_follow",
                        "thread:thread-repo",
                        "lane:operator:repo",
                    ],
                },
            },
            {
                "id": 92,
                "user_id": 7,
                "platform": "local",
                "channel_id": 1,
                "reminder_text": "Review tomorrow",
                "scheduled_time": 2500.0,
                "kind": "reminder",
                "runtime_job_id": "job_existing",
                "runtime_session_key": "owner:7:surface:local:chat",
                "metadata": {},
            },
        ]

    async def fake_ensure_surface_session(**kwargs):
        ensured_sessions.append(dict(kwargs))
        return {"session_key": f"owner:{kwargs['owner_user_id']}:surface:{kwargs['platform']}:session"}

    async def fake_create_job(**kwargs):
        created_jobs.append(dict(kwargs))
        return {"job_id": "job_created"}

    async def fake_update_scheduled_reminder_runtime_link(**kwargs):
        updated_links.append(dict(kwargs))

    monkeypatch.setattr("core.runtime_control.brain.list_open_scheduled_reminders", fake_list_open_scheduled_reminders)
    monkeypatch.setattr("core.runtime_control.brain.update_scheduled_reminder_runtime_link", fake_update_scheduled_reminder_runtime_link)
    monkeypatch.setattr("core.runtime_control.time.time", lambda: 2000.0)

    control = RuntimeControlPlane()
    monkeypatch.setattr(control, "ensure_surface_session", fake_ensure_surface_session)
    monkeypatch.setattr(control, "create_job", fake_create_job)

    await control._backfill_open_reminders()

    assert ensured_sessions == [
        {
            "owner_user_id": 11,
            "platform": "discord",
            "channel_id": 1234,
            "is_dm": True,
            "status": "ready",
            "metadata": {
                "origin": "deferred_action",
                "kind": "followthrough",
                "workflow_id": "wf_follow",
                "followthrough_id": "wff_follow",
                "lane_id": "operator:repo",
                "continuity_refs": [
                    "workflow:wf_follow",
                    "thread:thread-repo",
                    "lane:operator:repo",
                ],
            },
            "thread_id": "thread-repo",
            "thread_kind": "continuity",
            "thread_label": "Repo Fix",
        },
        {
            "owner_user_id": 7,
            "platform": "local",
            "channel_id": 1,
            "is_dm": False,
            "status": "ready",
            "metadata": {"origin": "deferred_action", "kind": "reminder"},
        },
    ]
    assert created_jobs == [
        {
            "owner_user_id": 11,
            "runtime_session_key": "owner:11:surface:discord:session",
            "job_type": "deferred_action",
            "subtype": "followthrough",
            "status": "queued",
            "request_text": "Check the draft",
            "scheduled_for": 1500.0,
            "source_table": "scheduled_reminders",
            "source_id": "91",
            "metadata": {
                "delivery_preference": "dm",
                "platform": "discord",
                "channel_id": 1234,
                "surface_id": "1234",
                "workflow_id": "wf_follow",
                "followthrough_id": "wff_follow",
                "thread_id": "thread-repo",
                "thread_kind": "continuity",
                "thread_label": "Repo Fix",
                "lane_id": "operator:repo",
                "continuity_refs": [
                    "workflow:wf_follow",
                    "thread:thread-repo",
                    "lane:operator:repo",
                ],
            },
        }
    ]
    assert updated_links == [
        {
            "reminder_id": 91,
            "runtime_job_id": "job_created",
            "runtime_session_key": "owner:11:surface:discord:session",
        },
        {
            "reminder_id": 92,
            "runtime_job_id": "job_existing",
            "runtime_session_key": "owner:7:surface:local:chat",
        },
    ]


@pytest.mark.asyncio
async def test_ensure_bootstrap_allows_reentrant_surface_session_calls(monkeypatch) -> None:
    control = RuntimeControlPlane()
    events: list[object] = []

    async def fake_backfill_open_reminders() -> None:
        events.append("open_reminders")
        session = await control.ensure_surface_session(
            owner_user_id=7,
            platform="local",
            is_dm=True,
        )
        events.append(("session", session["session_key"]))

    async def fake_upsert_runtime_session(**kwargs):
        events.append(("upsert", kwargs["session_key"]))

    async def fake_get_runtime_session(session_key: str):
        return {"session_key": session_key}

    monkeypatch.setattr(control, "_backfill_open_reminders", fake_backfill_open_reminders)
    monkeypatch.setattr("core.runtime_control.brain.upsert_runtime_session", fake_upsert_runtime_session)
    monkeypatch.setattr("core.runtime_control.brain.get_runtime_session", fake_get_runtime_session)

    await asyncio.wait_for(control.ensure_bootstrap(), timeout=1.0)

    assert control._bootstrap_complete is True
    assert events == [
        "open_reminders",
        ("upsert", "owner:7:surface:local:chat"),
        ("session", "owner:7:surface:local:chat"),
    ]
