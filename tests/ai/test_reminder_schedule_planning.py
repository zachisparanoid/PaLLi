from __future__ import annotations

import core.reminders.binding as reminder_schedule_planning
from core.reminders.parser import ReminderTimeParseResult


def test_prepare_deferred_schedule_request_defaults_timer_delivery_and_content():
    prepared = reminder_schedule_planning.prepare_deferred_schedule_request(
        {
            "is_deferred_request": True,
            "kind": "timer",
            "confidence": 0.95,
            "content_text": "",
            "time_expression": "4 and a half minutes",
        },
        message="set a timer for 4 and a half minutes",
        confidence_threshold=0.6,
        source_is_dm=True,
        account_user_id=17,
        default_content=lambda kind: f"default:{kind}",
    )

    assert prepared is not None
    assert prepared.kind == "timer"
    assert prepared.reminder_text == "default:timer"
    assert prepared.time_expression == "4 and a half minutes"
    assert prepared.metadata["delivery_preference"] == "channel"
    assert prepared.metadata["source_is_dm"] is True
    assert prepared.metadata["account_user_id"] == 17


def test_prepare_deferred_schedule_request_uses_analysis_source_is_dm():
    prepared = reminder_schedule_planning.prepare_deferred_schedule_request(
        {
            "is_reminder_request": True,
            "kind": "reminder",
            "confidence": 0.91,
            "reminder_text": "Call mom",
            "time_expression": "tomorrow morning",
            "source_is_dm": False,
            "delivery_preference": "somewhere-else",
        },
        message="remind me tomorrow morning to call mom",
        confidence_threshold=0.6,
        source_is_dm=None,
        account_user_id=None,
        default_content=lambda kind: f"default:{kind}",
    )

    assert prepared is not None
    assert prepared.reminder_text == "Call mom"
    assert prepared.metadata["source_is_dm"] is False
    assert prepared.metadata["delivery_preference"] == "dm"


def test_prepare_deferred_schedule_request_rejects_low_confidence():
    prepared = reminder_schedule_planning.prepare_deferred_schedule_request(
        {
            "is_deferred_request": True,
            "kind": "reminder",
            "confidence": 0.2,
            "reminder_text": "Call mom",
            "time_expression": "tomorrow morning",
        },
        message="remind me tomorrow morning to call mom",
        confidence_threshold=0.8,
        source_is_dm=False,
        account_user_id=None,
        default_content=lambda kind: f"default:{kind}",
    )

    assert prepared is None


def test_apply_time_parse_result_records_parse_metadata_and_timer_duration():
    prepared = reminder_schedule_planning.PreparedDeferredScheduleRequest(
        kind="timer",
        reminder_text="Timer finished.",
        time_expression="4 and a half minutes",
        metadata={},
    )

    scheduled_time = reminder_schedule_planning.apply_time_parse_result(
        prepared,
        parse_result=ReminderTimeParseResult(
            status="ok",
            timestamp=123.0,
            reason_code="",
        ),
        parse_relative_duration_seconds=lambda text: 270.0 if text else None,
    )

    assert scheduled_time == 123.0
    assert prepared.metadata["parse_status"] == "ok"
    assert prepared.metadata["parse_reason_code"] == ""
    assert prepared.metadata["normalized_scheduled_time"] == 123.0
    assert prepared.metadata["duration_seconds"] == 270.0


def test_build_runtime_binding_seed_shapes_session_and_job_payloads():
    seed = reminder_schedule_planning.build_runtime_binding_seed(
        reminder_id=77,
        user_id=8,
        account_user_id=19,
        platform="discord",
        channel_id=11,
        source_is_dm=False,
        surface_id="voice:test",
        scheduled_time=250.0,
        reminder_text="Call mom",
        kind="reminder",
        delivery_preference="dm",
        metadata={
            "workflow_id": "wf_wait",
            "outcome_id": "wfo_wait",
            "followthrough_id": "wff_wait",
            "thread_id": "thread-repo",
            "thread_kind": "continuity",
            "thread_label": "Repo Fix",
            "lane_id": "operator:repo",
            "continuity_refs": [
                "workflow:wf_wait",
                "thread:thread-repo",
                "lane:operator:repo",
            ],
        },
        current_time=100.0,
    )

    assert seed.owner_user_id == 19
    assert seed.session_kwargs == {
        "owner_user_id": 19,
        "platform": "discord",
        "channel_id": 11,
        "is_dm": False,
        "status": "ready",
        "metadata": {
            "origin": "deferred_action",
            "kind": "reminder",
            "workflow_id": "wf_wait",
            "outcome_id": "wfo_wait",
            "followthrough_id": "wff_wait",
            "lane_id": "operator:repo",
            "continuity_refs": [
                "workflow:wf_wait",
                "thread:thread-repo",
                "lane:operator:repo",
            ],
        },
        "surface_id": "voice:test",
        "thread_id": "thread-repo",
        "thread_kind": "continuity",
        "thread_label": "Repo Fix",
    }
    assert seed.job_kwargs == {
        "owner_user_id": 19,
        "job_type": "deferred_action",
        "subtype": "reminder",
        "status": "scheduled",
        "request_text": "Call mom",
        "scheduled_for": 250.0,
        "source_table": "scheduled_reminders",
        "source_id": "77",
        "metadata": {
            "platform": "discord",
            "channel_id": 11,
            "surface_id": "voice:test",
            "delivery_preference": "dm",
            "workflow_id": "wf_wait",
            "outcome_id": "wfo_wait",
            "followthrough_id": "wff_wait",
            "thread_id": "thread-repo",
            "thread_kind": "continuity",
            "thread_label": "Repo Fix",
            "lane_id": "operator:repo",
            "continuity_refs": [
                "workflow:wf_wait",
                "thread:thread-repo",
                "lane:operator:repo",
            ],
        },
    }


def test_build_runtime_binding_seed_supports_nested_metadata_variants():
    seed = reminder_schedule_planning.build_runtime_binding_seed(
        reminder_id=88,
        user_id=8,
        account_user_id=19,
        platform="discord",
        channel_id=11,
        source_is_dm=False,
        surface_id="voice:variant",
        scheduled_time=250.0,
        reminder_text="Follow up repo work",
        kind="reminder",
        delivery_preference="dm",
        metadata={
            "workflow_id": "wf_wait",
            "continuity_refs": [
                "workflow:wf_wait",
                "thread:thread-label-first",
            ],
            "thread": {
                "id": "thread-nested",
                "label": "Nested continuity thread",
                "refs": ["thread:thread-nested", "lane:lane-nested-fallback"],
                "kind": "continuity",
            },
                "lane": {
                    "id": "lane:repo-review",
                    "label": "Repo Review",
                },
            "continuity_thread_id": "",
            "continuity_lane_id": "",
        },
        current_time=100.0,
    )

    assert seed.session_kwargs["thread_id"] == "thread-nested"
    assert seed.session_kwargs["thread_label"] == "Nested continuity thread"
    assert seed.session_kwargs["thread_kind"] == "continuity"
    assert seed.session_kwargs["metadata"]["lane_id"] == "lane:repo-review"
    assert seed.session_kwargs["metadata"]["workflow_id"] == "wf_wait"
    assert set(seed.session_kwargs["metadata"]["continuity_refs"]) == {
        "workflow:wf_wait",
        "thread:thread-label-first",
        "thread:thread-nested",
        "lane:lane-nested-fallback",
    }
    assert "thread_id" not in seed.session_kwargs["metadata"]

    assert seed.job_kwargs["metadata"]["thread_id"] == "thread-nested"
    assert seed.job_kwargs["metadata"]["thread_kind"] == "continuity"
    assert seed.job_kwargs["metadata"]["thread_label"] == "Nested continuity thread"
    assert seed.job_kwargs["metadata"]["lane_id"] == "lane:repo-review"
    assert set(seed.job_kwargs["metadata"]["continuity_refs"]) == {
        "workflow:wf_wait",
        "thread:thread-label-first",
        "thread:thread-nested",
        "lane:lane-nested-fallback",
    }
