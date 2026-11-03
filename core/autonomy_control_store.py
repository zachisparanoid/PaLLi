from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


# C1.5: identifying constants for the open_threads-as-canonical-store
# rewire. Module-level so consumers (and the migration script) share the
# same literals as the OpenThreadsService enums.
PROACTIVE_RECORD_KIND = "proactive_record"
LOGGED_STATUS = "logged"
DEFAULT_PROACTIVE_THREAD_KIND = "other"
DEFAULT_PROACTIVE_URGENCY = "historical"
PROACTIVE_WHY_IT_MATTERS = "Proactive outreach record (rumination guard / daily-cap source)"

# C8: identifying constants for the standing_orders absorb (B5 Decision 5).
# Mirrors the literals in scripts/migrate_standing_orders_to_open_threads.py.
RECURRING_KIND = "recurring"
DEFAULT_RECURRING_URGENCY = "recurring"
ACTIVE_STATUS = "active"
DISMISSED_STATUS = "dismissed"

# Per B5 Q6 — derive the topic-axis kind from the program_kind.
_PROGRAM_KIND_TO_THREAD_KIND = {
    "companion_checkin": "personal",
    "followthrough_review": "followup",
    "operator_program": "work",
    "research_watch": "work",
    "alert_watch": "work",
    "email_triage": "work",
}

# Per B5 Q5 — translate the standing_orders status to open_threads status.
# Round-trip distinction (paused vs disabled) lives in
# source_payload.original_status.
_STANDING_ORDER_STATUS_TRANSLATION = {
    "active": ACTIVE_STATUS,
    "paused": DISMISSED_STATUS,
    "disabled": DISMISSED_STATUS,
}


def _derive_thread_kind_for_recurring(program_kind: Any) -> str:
    if not program_kind:
        return "other"
    return _PROGRAM_KIND_TO_THREAD_KIND.get(
        str(program_kind).strip().lower(), "other"
    )


def _truncate_for_why_it_matters(instruction: Any) -> str:
    text = str(instruction or "").strip()
    if not text:
        return "Recurring program (instruction blank)"
    if len(text) <= 240:
        return text
    return text[:237].rstrip() + "..."


class AutonomyControlStore:
    """Persistence helper for proactive-action records (post-excision).

    Great Excision (2026-07-09): the heartbeat-run and standing-order
    methods were deleted with the operator engine — condition-watching
    returns as the Fears organ (PALLI_SELFHOOD_DESIGN.md §4.6). What
    survives is the LIVE proactive-record pair used by the proactive rail:
    create_proactive_action (core/turn/proactive.py) and
    list_recent_proactive_actions_for_user (ai/proactive_candidate_source.py),
    both open_threads-backed per C1.5 below.

    C1.5 (2026-05-12): proactive_actions writes/reads now route through
    ``open_threads`` with ``source_kind='proactive_record'`` per the B5
    Decision-5 design doc
    (``docs/superpowers/plans/2026-05-08-decision-5-queue-collapse-schema.md``).
    The legacy table is dropped in C1.5 step 5; this store no longer
    issues ``INSERT INTO proactive_actions`` or
    ``SELECT … FROM proactive_actions``. Record-shape returned by
    ``list_recent_proactive_actions_for_user`` is preserved so existing
    readers (rumination guard, daily-cap, bounded-reflection) continue
    to work without per-row format changes.
    """

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _clean_text(self, value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        text = self._clean_text(value)
        return text or None

    def _clean_text_list(self, values: Optional[List[Any]]) -> List[str]:
        clean_values: List[str] = []
        for value in values or []:
            text = self._clean_text(value)
            if text:
                clean_values.append(text)
        return clean_values

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    @staticmethod
    def _proactive_record_from_open_thread(thread: Any) -> Dict[str, Any]:
        """Render an OpenThread (source_kind='proactive_record') as the
        legacy proactive_actions dict shape.

        Preserves the same key set readers (rumination guard, daily-cap,
        bounded-reflection) consume today: ``id, user_id, runtime_job_id,
        runtime_session_key, action_type, trigger_reason, content,
        scheduled_time, executed_time, success, user_response,
        effectiveness_score``.

        The integer ``id`` is derived from the source row's stable PK so
        callers that use ``int(row["id"])`` as a dedup key (e.g.,
        ``ai/bounded_reflection.py``) keep getting unique values per row.
        When ``source_id`` text-renders cleanly to int (B5 Q1: migrated
        legacy rows do this), we use that. Otherwise (post-C1.5
        runtime-inserted rows whose source_id is NULL because the brain
        doesn't surface its text PK back) we hash the open_thread.id text
        PK into a stable int — same trick ``create_proactive_action``
        uses for its return value, so dedup works end-to-end.
        """
        payload = thread.source_payload or {}
        id_int = 0
        if thread.source_id is not None:
            try:
                id_int = int(thread.source_id)
            except (TypeError, ValueError):
                # Non-numeric source_id (future kind) — fall through to
                # hashing the thread's own text PK below.
                pass
        if id_int == 0 and thread.id:
            try:
                id_int = int(str(thread.id)[:15], 16)
            except (TypeError, ValueError):
                id_int = 0
        scheduled_time = thread.created_at
        executed_time = thread.last_surfaced_at
        return {
            "id": id_int,
            "user_id": int(thread.owner_user_id),
            "runtime_job_id": str(payload.get("runtime_job_id") or ""),
            "runtime_session_key": str(payload.get("runtime_session_key") or ""),
            "action_type": str(payload.get("action_type") or ""),
            "trigger_reason": payload.get("original_trigger_reason"),
            "content": thread.topic_summary or "",
            "scheduled_time": scheduled_time,
            "executed_time": executed_time,
            "success": payload.get("success"),
            "user_response": payload.get("user_response"),
            "effectiveness_score": payload.get("effectiveness_score"),
        }

    async def create_proactive_action(
        self,
        *,
        user_id: int,
        action_type: str,
        content: str,
        trigger_reason: Optional[str] = None,
        scheduled_time: Optional[float] = None,
        executed_time: Optional[float] = None,
        success: Optional[bool] = None,
        user_response: Optional[str] = None,
        effectiveness_score: Optional[float] = None,
        runtime_job_id: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
    ) -> int:
        """Insert a proactive_actions row — now stored on open_threads.

        Returns the legacy integer-id contract for backward compat. The
        integer is derived from the new TEXT thread id by hashing the
        first 15 hex chars (gives ~1.1e18 distinct values, well above
        the legacy auto-increment range). Callers that just track
        "did the insert happen" don't care about the value; the few
        callers that store the id alongside other rows use it as an
        opaque key.

        See B5 Q1 + C1.5 step 3 commit for the rationale.
        """
        # JSON-decode trigger_reason if it parses; otherwise preserve raw
        # string. Mirrors the migration script's _normalize_trigger_reason.
        decoded_trigger: Any = trigger_reason
        if isinstance(trigger_reason, str):
            t = trigger_reason.strip()
            if t.startswith("{") or t.startswith("["):
                try:
                    decoded_trigger = json.loads(t)
                except (TypeError, ValueError):
                    decoded_trigger = trigger_reason

        payload: Dict[str, Any] = {
            "runtime_job_id": self._clean_optional_text(runtime_job_id),
            "runtime_session_key": self._clean_optional_text(runtime_session_key),
            "action_type": self._clean_text(action_type),
            "original_trigger_reason": decoded_trigger,
            "success": (bool(success) if success is not None else None),
            "user_response": user_response,
            "effectiveness_score": (
                float(effectiveness_score) if effectiveness_score is not None else None
            ),
            # Reserved keys for parallelism with future RECURRING /
            # FOLLOWTHROUGH absorbs (B5 Q3); proactive_actions has no
            # 'instruction' or 'why_now' fields.
            "original_instruction": None,
            "original_why_now": None,
        }
        scheduled = float(scheduled_time) if scheduled_time is not None else self._now()
        executed = float(executed_time) if executed_time is not None else None
        if executed is not None:
            updated = executed
            last_surfaced = executed
            resolved_at: Optional[float] = executed
        else:
            updated = scheduled
            last_surfaced = None
            resolved_at = None
        if success is True:
            resolution_reason: Optional[str] = "executed"
        elif success is False:
            resolution_reason = "failed"
        else:
            resolution_reason = None

        thread_id = await self._brain.add_source_open_thread(
            owner_user_id=int(user_id),
            topic_summary=str(content or "").strip() or "(no content)",
            why_it_matters=PROACTIVE_WHY_IT_MATTERS,
            kind=DEFAULT_PROACTIVE_THREAD_KIND,
            urgency=DEFAULT_PROACTIVE_URGENCY,
            source_kind=PROACTIVE_RECORD_KIND,
            status=LOGGED_STATUS,
            source_id=None,  # set below from the assigned thread_id (text PK)
            source_payload=payload,
            created_at=scheduled,
            updated_at=updated,
            last_surfaced_at=last_surfaced,
            surface_count=1 if executed is not None else 0,
            resolved_at=resolved_at,
            resolution_reason=resolution_reason,
        )
        # Map TEXT thread_id → integer for the legacy return-contract.
        # The first 15 hex chars are guaranteed-unique by uuid4 (so
        # collisions are negligible at the row counts a single PaLLi
        # install hits). We do NOT roundtrip-store this integer; the
        # canonical id is the TEXT open_threads.id. Migration-time rows
        # store source_id explicitly via the script.
        try:
            return int(thread_id[:15], 16)
        except (TypeError, ValueError):
            return 0

    async def list_recent_proactive_actions_for_user(
        self,
        user_id: int,
        *,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return recent proactive-record rows in the legacy dict shape.

        C1.5: backed by open_threads filtered by source_kind. Sort key is
        the unified COALESCE(last_surfaced_at, created_at) — mirrors the
        legacy ORDER BY COALESCE(executed_time, scheduled_time).
        """
        threads = await self._brain.list_open_threads_by_source_kind(
            owner_user_id=int(user_id),
            source_kind=PROACTIVE_RECORD_KIND,
            limit=max(1, int(limit)),
        )
        return [self._proactive_record_from_open_thread(t) for t in threads]

