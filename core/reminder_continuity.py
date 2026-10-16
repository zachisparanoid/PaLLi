from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, Dict

from core.time_utils import format_month_day, format_time_12h


# Reminder.metadata["origin"] tag set by
# `OperatorWorkflowContinuityCoordinator.maybe_create_due_followthrough_reminder`
# whenever an operator workflow followthrough is converted into a scheduled
# reminder. Read by `ai/reminder_runtime.py:check_due_reminders_for_target`
# to decide whether a fired reminder should also flip the originating
# followthrough item to `delivered` (QUE-HIGH-1). Centralized in this neutral
# bridge module so writer (core/operator_continuity.py) and reader
# (ai/reminder_runtime.py) can never silently drift out of sync via a typo.
WORKFLOW_FOLLOWTHROUGH_REMINDER_ORIGIN = "workflow_followthrough"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _iter_ref_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Mapping):
        value = value.values()
    try:
        values = list(value)
    except TypeError:
        return [_normalize_text(value)]
    ref_values: list[str] = []
    for raw in values:
        if isinstance(raw, str):
            ref_values.extend([piece.strip() for piece in raw.split(",") if piece.strip()])
            continue
        normalized = _normalize_text(raw)
        if normalized:
            ref_values.append(normalized)
    return ref_values


def _normalize_refs(*groups: Iterable[Any]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in _iter_ref_values(group):
            text = _normalize_text(value)
            if not text:
                continue
            normalized = text.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            refs.append(text)
    return refs


def build_reminder_continuity_view(reminder: Mapping[str, Any] | None) -> Dict[str, Any]:
    item = reminder if isinstance(reminder, Mapping) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    thread_metadata = metadata.get("thread") if isinstance(metadata.get("thread"), Mapping) else {}
    lane_metadata = metadata.get("lane") if isinstance(metadata.get("lane"), Mapping) else {}

    continuity_origin = _normalize_text(
        item.get("continuity_origin") or metadata.get("origin")
    )
    workflow_id = _normalize_text(
        item.get("workflow_id")
        or item.get("operator_workflow_id")
        or metadata.get("workflow_id")
        or metadata.get("operator_workflow_id")
        or metadata.get("last_workflow_id")
    )
    outcome_id = _normalize_text(
        item.get("outcome_id")
        or item.get("operator_outcome_id")
        or metadata.get("outcome_id")
        or metadata.get("operator_outcome_id")
    )
    followthrough_id = _normalize_text(
        item.get("followthrough_id")
        or item.get("operator_followthrough_id")
        or metadata.get("followthrough_id")
        or metadata.get("operator_followthrough_id")
    )
    thread_id = _normalize_text(
        item.get("thread_id")
        or item.get("continuity_thread_id")
        or item.get("last_thread_id")
        or metadata.get("thread_id")
        or metadata.get("continuity_thread_id")
        or metadata.get("last_thread_id")
        or thread_metadata.get("id")
        or thread_metadata.get("thread_id")
        or thread_metadata.get("value")
    )
    thread_kind = _normalize_text(
        item.get("thread_kind")
        or metadata.get("thread_kind")
        or thread_metadata.get("kind")
        or thread_metadata.get("lane_kind")
    )
    thread_label = _normalize_text(
        item.get("thread_label")
        or metadata.get("thread_label")
        or thread_metadata.get("label")
        or thread_metadata.get("name")
    )
    lane_label = _normalize_text(
        item.get("lane_label")
        or metadata.get("lane_label")
        or lane_metadata.get("label")
        or lane_metadata.get("name")
    )
    lane_kind = _normalize_text(
        item.get("lane_kind")
        or metadata.get("lane_kind")
        or lane_metadata.get("kind")
        or lane_metadata.get("lane_kind")
    )
    lane_id = _normalize_text(
        item.get("lane_id")
        or item.get("continuity_lane_id")
        or item.get("last_lane_id")
        or metadata.get("lane_id")
        or metadata.get("continuity_lane_id")
        or metadata.get("last_lane_id")
        or lane_metadata.get("id")
        or lane_metadata.get("lane_id")
    )
    continuity_refs = _normalize_refs(
        item.get("continuity_refs") or [],
        item.get("related_refs") or [],
        metadata.get("continuity_refs") or [],
        metadata.get("related_refs") or [],
        thread_metadata.get("refs"),
        lane_metadata.get("refs"),
    )
    inferred_thread_id = ""
    inferred_lane_id = ""
    for ref in continuity_refs:
        if ":" not in ref:
            continue
        prefix, value = ref.split(":", 1)
        normalized_prefix = prefix.strip().lower()
        normalized_value = _normalize_text(value)
        if not normalized_value:
            continue
        if not inferred_thread_id and normalized_prefix == "thread":
            inferred_thread_id = normalized_value
        elif not inferred_lane_id and normalized_prefix == "lane":
            inferred_lane_id = normalized_value
    if not thread_id:
        thread_id = inferred_thread_id
    if not lane_id:
        lane_id = inferred_lane_id

    continuity_kind = ""
    if followthrough_id or continuity_origin == "workflow_followthrough":
        continuity_kind = "workflow_followthrough"
    elif workflow_id or outcome_id:
        continuity_kind = "workflow"
    elif thread_id or lane_id or continuity_refs:
        continuity_kind = "continuity"

    summary_parts: list[str] = []
    if workflow_id:
        summary_parts.append(f"workflow {workflow_id}")
    elif outcome_id:
        summary_parts.append(f"outcome {outcome_id}")
    if followthrough_id:
        summary_parts.append(f"followthrough {followthrough_id}")
    if thread_label:
        summary_parts.append(f"thread {thread_label}")
    elif thread_id:
        summary_parts.append(f"thread {thread_id}")
    if lane_id:
        summary_parts.append(f"lane {lane_id}")

    continuity_summary = ""
    if summary_parts:
        heading = {
            "workflow_followthrough": "Workflow followthrough",
            "workflow": "Workflow continuity",
            "continuity": "Continuity",
        }.get(continuity_kind, "Continuity")
        continuity_summary = f"{heading}: {', '.join(summary_parts)}"

    return {
        "continuity_bound": bool(continuity_kind or continuity_refs),
        "continuity_origin": continuity_origin,
        "continuity_kind": continuity_kind,
        "continuity_refs": continuity_refs,
        "continuity_summary": continuity_summary,
        "workflow_id": workflow_id,
        "outcome_id": outcome_id,
        "followthrough_id": followthrough_id,
        "thread_id": thread_id,
        "thread_kind": thread_kind,
        "thread_label": thread_label,
        "lane_id": lane_id,
        "lane_kind": lane_kind,
        "lane_label": lane_label,
    }


def enrich_reminder_record(reminder: Mapping[str, Any] | None) -> Dict[str, Any]:
    item = dict(reminder or {})
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    item["metadata"] = dict(metadata)
    item.update(build_reminder_continuity_view(item))
    return item


def _format_scheduled_time(value: Any) -> str:
    try:
        scheduled_time = float(value)
    except (TypeError, ValueError):
        return ""
    if scheduled_time <= 0.0:
        return ""
    dt = datetime.fromtimestamp(scheduled_time)
    return f"{format_month_day(dt, include_year=True)} at {format_time_12h(dt)}"


def describe_linked_reminder(reminder: Mapping[str, Any] | None) -> str:
    if not isinstance(reminder, Mapping):
        return ""
    item = enrich_reminder_record(reminder)
    parts: list[str] = []

    reminder_text = _normalize_text(item.get("reminder_text"))
    if reminder_text:
        parts.append(f'Reminder: "{reminder_text}".')
    else:
        parts.append("A linked reminder is scheduled.")

    scheduled_text = _format_scheduled_time(item.get("scheduled_time"))
    if scheduled_text:
        parts.append(f"Scheduled for {scheduled_text}.")

    thread_label = _normalize_text(item.get("thread_label"))
    thread_id = _normalize_text(item.get("thread_id"))
    lane_id = _normalize_text(item.get("lane_id"))
    if thread_label:
        parts.append(f"Thread: {thread_label}.")
    elif thread_id:
        parts.append(f"Thread: {thread_id}.")
    if lane_id:
        parts.append(f"Continuity lane: {lane_id}.")

    return " ".join(parts).strip()


def _append_unique_text(parts: list[str], value: Any) -> None:
    text = _normalize_text(value)
    if not text:
        return
    normalized = text.lower()
    for existing in parts:
        existing_normalized = existing.lower()
        if (
            normalized == existing_normalized
            or normalized in existing_normalized
            or existing_normalized in normalized
        ):
            return
    parts.append(text)


def describe_followthrough_resurfacing(
    followthrough: Mapping[str, Any] | None,
) -> str:
    item = enrich_followthrough_record(followthrough)
    summary = _normalize_text(
        item.get("summary") or item.get("why_now") or item.get("objective")
    )
    linked_reminder_note = describe_linked_reminder(item.get("linked_reminder"))
    if (
        str(item.get("status") or "").strip().lower() == "converted_to_reminder"
        and linked_reminder_note
    ):
        return linked_reminder_note
    return summary


def build_followthrough_memory_text(
    followthrough: Mapping[str, Any] | None,
) -> str:
    item = enrich_followthrough_record(followthrough)
    parts: list[str] = []
    _append_unique_text(parts, describe_followthrough_resurfacing(item))
    _append_unique_text(parts, item.get("summary"))
    _append_unique_text(parts, item.get("why_now"))
    _append_unique_text(parts, item.get("objective"))
    return "\n".join(parts).strip()


def linked_reminder_ids(items: Iterable[Mapping[str, Any] | None]) -> list[int]:
    reminder_ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_value = item.get("linked_reminder_id")
        if raw_value in {None, ""}:
            continue
        try:
            reminder_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if reminder_id <= 0 or reminder_id in seen:
            continue
        seen.add(reminder_id)
        reminder_ids.append(reminder_id)
    return reminder_ids


async def hydrate_followthrough_linked_reminders(
    items: Iterable[Mapping[str, Any] | None],
    *,
    reminder_loader: Callable[[int], Awaitable[Mapping[str, Any] | None]] | None,
) -> list[Dict[str, Any]]:
    reminder_records: dict[int, Dict[str, Any]] = {}
    if reminder_loader is not None:
        for reminder_id in linked_reminder_ids(items):
            try:
                reminder = await reminder_loader(int(reminder_id))
            except Exception:
                reminder = None
            if reminder:
                reminder_records[int(reminder_id)] = dict(reminder)

    enriched: list[Dict[str, Any]] = []
    for item in items:
        reminder = None
        raw_reminder_id = item.get("linked_reminder_id") if isinstance(item, Mapping) else None
        if raw_reminder_id not in {None, ""}:
            try:
                reminder = reminder_records.get(int(raw_reminder_id))
            except (TypeError, ValueError):
                reminder = None
        enriched.append(
            enrich_followthrough_record(
                item,
                linked_reminder=reminder,
            )
        )
    return enriched


def enrich_followthrough_record(
    followthrough: Mapping[str, Any] | None,
    *,
    linked_reminder: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    item = dict(followthrough or {})
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    item["metadata"] = dict(metadata)

    linked_source = linked_reminder
    if linked_source is None and isinstance(item.get("linked_reminder"), Mapping):
        linked_source = item.get("linked_reminder")
    linked_view = (
        enrich_reminder_record(linked_source)
        if isinstance(linked_source, Mapping)
        else None
    )
    if linked_view:
        for key in (
            "continuity_origin",
            "workflow_id",
            "outcome_id",
            "followthrough_id",
            "thread_id",
            "thread_kind",
            "thread_label",
            "lane_id",
        ):
            if not _normalize_text(item.get(key)):
                item[key] = linked_view.get(key)
        if not item.get("continuity_refs"):
            item["continuity_refs"] = list(linked_view.get("continuity_refs") or [])

    item.update(build_reminder_continuity_view(item))
    item["linked_reminder"] = linked_view
    return item
