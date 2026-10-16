from __future__ import annotations

from core.reminder_continuity import (
    build_followthrough_memory_text,
    build_reminder_continuity_view,
    enrich_followthrough_record,
    enrich_reminder_record,
)


def test_build_reminder_continuity_view_prefers_variant_metadata_and_nested_refs():
    reminder = {
        "metadata": {
            "origin": "workflow_followthrough",
            "workflow_id": "wf_variant",
            "outcome_id": "out_variant",
            "followthrough_id": "wff_variant",
            "continuity_thread_id": "thread-variant",
            "continuity_lane_id": "lane-variant",
            "thread": {
                "id": "thread-nested",
                "kind": "workflow_thread",
                "label": "Nested Thread",
            },
            "lane": {
                "id": "lane-nested",
                "kind": "operator_lane",
                "name": "operator:nested",
                "label": "Nested Lane",
            },
            "continuity_refs": {
                "a": "workflow:wf_variant",
                "b": "thread:thread-legacy",
            },
            "related_refs": "outcome:out_variant, followthrough:wff_variant",
        },
    }

    view = build_reminder_continuity_view(reminder)

    assert view["continuity_kind"] == "workflow_followthrough"
    assert view["continuity_origin"] == "workflow_followthrough"
    assert view["thread_id"] == "thread-variant"
    assert view["lane_id"] == "lane-variant"
    assert view["thread_label"] == "Nested Thread"
    assert view["lane_kind"] == "operator_lane"
    assert set(view["continuity_refs"]) == {
        "workflow:wf_variant",
        "thread:thread-legacy",
        "outcome:out_variant",
        "followthrough:wff_variant",
    }
    assert view["continuity_summary"] == (
        "Workflow followthrough: workflow wf_variant, followthrough wff_variant, "
        "thread Nested Thread, lane lane-variant"
    )
    assert view["continuity_bound"] is True


def test_build_reminder_continuity_view_infers_thread_lane_from_refs_when_explicit_missing():
    reminder = {
        "workflow_id": "wf_inferred",
        "metadata": {
            "origin": "workflow",
            "continuity_refs": [
                "thread:thread-from-refs",
                "lane:operator:workflow",
            ],
        },
        "related_refs": "workflow:wf_inferred, outcome:out_inferred",
    }

    view = build_reminder_continuity_view(reminder)

    assert view["continuity_kind"] == "workflow"
    assert view["thread_id"] == "thread-from-refs"
    assert view["lane_id"] == "operator:workflow"
    assert view["continuity_refs"] == [
        "workflow:wf_inferred",
        "outcome:out_inferred",
        "thread:thread-from-refs",
        "lane:operator:workflow",
    ]
    assert view["continuity_summary"] == (
        "Workflow continuity: workflow wf_inferred, thread thread-from-refs, "
        "lane operator:workflow"
    )


def test_build_reminder_continuity_view_supports_operator_alias_and_last_fallbacks():
    reminder = {
        "operator_workflow_id": "wf_alias",
        "operator_outcome_id": "out_alias",
        "operator_followthrough_id": "wff_alias",
        "continuity_refs": {
            "lane": "lane:operator:alias",
        },
        "metadata": {
            "last_workflow_id": "wf_last",
            "last_thread_id": "thread-last",
            "last_lane_id": "lane:operator:last",
            "thread": {
                "value": "thread-last",
            },
            "lane": {
                "id": "lane:operator:last",
            },
        },
    }

    view = build_reminder_continuity_view(reminder)

    assert view["workflow_id"] == "wf_alias"
    assert view["outcome_id"] == "out_alias"
    assert view["followthrough_id"] == "wff_alias"
    assert view["thread_id"] == "thread-last"
    assert view["lane_id"] == "lane:operator:last"
    assert set(view["continuity_refs"]) == {"lane:operator:alias"}
    assert view["continuity_summary"] == (
        "Workflow followthrough: workflow wf_alias, followthrough wff_alias, "
        "thread thread-last, lane lane:operator:last"
    )


def test_enrich_followthrough_record_infers_missing_fields_from_linked_reminder():
    followthrough = {
        "summary": "Resume the workflow followthrough."
    }
    linked_reminder = {
        "id": 321,
        "metadata": {
            "origin": "workflow_followthrough",
            "workflow_id": "wf_linked",
            "followthrough_id": "wff_linked",
            "continuity_thread_id": "thread-linked",
            "continuity_lane_id": "lane-linked",
            "thread_label": "Linked thread",
            "continuity_refs": {
                "thread": "thread:thread-linked",
                "lane": "lane:lane-linked",
            },
        },
    }

    enriched = enrich_followthrough_record(
        followthrough,
        linked_reminder=linked_reminder,
    )

    assert enriched["thread_id"] == "thread-linked"
    assert enriched["lane_id"] == "lane-linked"
    assert enriched["thread_label"] == "Linked thread"
    assert enriched["continuity_origin"] == "workflow_followthrough"
    assert enriched["continuity_kind"] == "workflow_followthrough"
    assert isinstance(enriched["linked_reminder"], dict)
    assert enriched["linked_reminder"]["id"] == 321


def test_build_followthrough_memory_text_uses_linked_reminder_view():
    followthrough = {
        "status": "converted_to_reminder",
        "summary": "Old summary should be ignored when reminder is present.",
        "linked_reminder": {
            "metadata": {
                "thread_label": "Reminder Thread",
                "thread_id": "thread-reminder",
                "lane_id": "operator:reminder",
                "scheduled_time": 0,
            },
            "reminder_text": "Run the build checks.",
        },
    }
    text = build_followthrough_memory_text(followthrough)
    lines = text.splitlines()
    assert lines[0] == (
        'Reminder: "Run the build checks.". '
        "Thread: Reminder Thread. Continuity lane: operator:reminder."
    )
    assert lines[1] == "Old summary should be ignored when reminder is present."


def test_enrich_reminder_record_preserves_existing_and_builds_summary():
    reminder = {
        "reminder_text": "Prepare the environment.",
        "scheduled_time": 0,
        "metadata": {"continuity_thread_id": "thread-preserve"},
    }
    reminder_view = enrich_reminder_record(reminder)
    assert reminder_view["thread_id"] == "thread-preserve"
    assert reminder_view["continuity_kind"] == "continuity"
    assert reminder_view["continuity_summary"] == "Continuity: thread thread-preserve"
    assert reminder_view["metadata"] is not reminder["metadata"]
    assert reminder_view["metadata"].get("continuity_thread_id") == "thread-preserve"
