"""Open-Threads agenda service — durable store of topics PaLLi is tracking.

Replaces the deleted core/companion_agenda.py + core/companion_agenda_store.py
pair (per plan 2026-05-10-agenda-rebuild and spec
2026-05-10-agenda-rebuild-design). LLM-judged capture/surface/resolve; no
regex, no salience math, no cooldown timers in this layer.

Capture and resolution are dispatched from core/turn/reflect.py. Surface
selection rides the arbiter (step 19, 2026-08-19 — the old
surface_decision lane is deleted). This module
provides only the CRUD primitives — all judgment lives in the LLM calls.

C1.5 (2026-05-12): added ``source_kind``/``source_id``/``source_payload``
origin discriminator + ``ThreadStatus.LOGGED`` per B5 design doc
(``docs/superpowers/plans/2026-05-08-decision-5-queue-collapse-schema.md``)
to absorb the legacy ``proactive_actions`` table. Rows with
``source_kind='proactive_record'`` are append-only history (status='logged'),
not active work — the rumination guard / daily-cap queries consult them.
"""
from __future__ import annotations

import enum
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any


class ThreadStatus(str, enum.Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    # C1.5: append-only "I did this" history (e.g., a proactive_actions
    # record absorbed via source_kind='proactive_record'). Never transitions
    # to ACTIVE. Active-thread queries filter `status='active'` and exclude
    # LOGGED automatically.
    LOGGED = "logged"


class ThreadKind(str, enum.Enum):
    WORK = "work"
    PERSONAL = "personal"
    HEALTH = "health"
    CURIOSITY = "curiosity"
    FOLLOWUP = "followup"
    OTHER = "other"


class ThreadSourceKind(str, enum.Enum):
    """Origin discriminator for unified-queue rows (B5 Decision 5).

    - ``TOPIC`` — pre-existing LLM-judged conversational thread (default).
    - ``PROACTIVE_RECORD`` — C1.5: absorbed proactive_actions row;
      append-only history. Used by the rumination guard and daily-cap
      queries.

    Future kinds (Phase C6/C8): ``RECURRING`` (standing_orders) and
    ``FOLLOWTHROUGH`` (operator_followthrough_items). Defined per the B5
    design doc; this enum is the SoT for the discriminator literals.
    """

    TOPIC = "topic"
    RECURRING = "recurring"
    FOLLOWTHROUGH = "followthrough"
    PROACTIVE_RECORD = "proactive_record"


@dataclass(frozen=True)
class OpenThread:
    id: str
    owner_user_id: int
    status: ThreadStatus
    created_at: float
    updated_at: float
    topic_summary: str
    why_it_matters: str
    kind: ThreadKind
    urgency: str
    last_surfaced_at: float | None
    surface_count: int
    resolved_at: float | None
    resolution_reason: str | None
    # C1.5: origin-discriminator fields (B5 Decision 5).
    source_kind: ThreadSourceKind = ThreadSourceKind.TOPIC
    source_id: str | None = None
    source_payload: dict[str, Any] | None = None


def _row_to_thread(row: sqlite3.Row) -> OpenThread:
    d = dict(row)
    # source_* are optional (added in C1.5 ALTER); pre-C1.5 connections
    # opened before the migration ran may not surface them. Treat missing
    # keys as the default 'topic' / NULL state.
    raw_source_kind = d.get("source_kind") if "source_kind" in d.keys() else None
    if raw_source_kind is None:
        source_kind = ThreadSourceKind.TOPIC
    else:
        source_kind = ThreadSourceKind(raw_source_kind)
    raw_source_id = d.get("source_id") if "source_id" in d.keys() else None
    raw_source_payload = d.get("source_payload") if "source_payload" in d.keys() else None
    if raw_source_payload is None:
        source_payload: dict[str, Any] | None = None
    elif isinstance(raw_source_payload, (dict, list)):
        source_payload = raw_source_payload  # type: ignore[assignment]
    else:
        try:
            source_payload = json.loads(raw_source_payload)
        except (TypeError, ValueError):
            source_payload = None
    return OpenThread(
        id=d["id"],
        owner_user_id=int(d["owner_user_id"]),
        status=ThreadStatus(d["status"]),
        created_at=float(d["created_at"]),
        updated_at=float(d["updated_at"]),
        topic_summary=d["topic_summary"],
        why_it_matters=d["why_it_matters"],
        kind=ThreadKind(d["kind"]),
        urgency=d["urgency"],
        last_surfaced_at=(None if d["last_surfaced_at"] is None else float(d["last_surfaced_at"])),
        surface_count=int(d["surface_count"]),
        resolved_at=(None if d["resolved_at"] is None else float(d["resolved_at"])),
        resolution_reason=d["resolution_reason"],
        source_kind=source_kind,
        source_id=raw_source_id,
        source_payload=source_payload,
    )


_SELECT_FIELDS = (
    "id, owner_user_id, status, created_at, updated_at, "
    "topic_summary, why_it_matters, kind, urgency, "
    "last_surfaced_at, surface_count, resolved_at, resolution_reason, "
    "source_kind, source_id, source_payload"
)

# A thread's age for the two age rules (retire_unsurfaced, expire_stale)
# counts from its last touch — max(created_at, updated_at) — not from its
# birth (Batch 5 fix round 1, ruling R55). unretire stamps updated_at, so a
# thread the owner put back on the board gets a fresh window instead of
# being set down again by the next sweep, which keyed on created_at and
# undid "Back on the board" within a day. An untouched row has updated_at
# == created_at, so nothing else's selection changes.
_AGED_AT = "MAX(created_at, COALESCE(updated_at, created_at))"


class OpenThreadsService:
    """CRUD over the open_threads table.

    Caller owns the connection's lifecycle. The service does not commit —
    callers commit (or rely on the connection's autocommit mode).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_thread(
        self,
        *,
        owner_user_id: int,
        topic_summary: str,
        why_it_matters: str,
        kind: ThreadKind | str,
        urgency: str,
        source_id: str | None = None,
    ) -> str:
        # source_id (2026-08-06, arbiter build): a free-text origin marker on
        # the UNCONSTRAINED column — the live table's source_kind CHECK pins
        # that vocabulary, so structural labels that arrived after the DDL
        # (e.g. 'workbench_share') live here instead of in source_kind.
        if isinstance(kind, str):
            try:
                kind = ThreadKind(kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid kind {kind!r}; must be one of "
                    f"{[k.value for k in ThreadKind]}"
                ) from exc
        thread_id = uuid.uuid4().hex
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO open_threads (
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                source_kind, source_id, source_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, ?, ?, NULL)
            """,
            (
                thread_id,
                int(owner_user_id),
                ThreadStatus.ACTIVE.value,
                now,
                now,
                topic_summary,
                why_it_matters,
                kind.value,
                urgency,
                ThreadSourceKind.TOPIC.value,
                source_id,
            ),
        )
        return thread_id

    def close_thread(
        self,
        *,
        thread_id: str,
        resolution_reason: str,
        status: ThreadStatus,
    ) -> None:
        if status not in (ThreadStatus.RESOLVED, ThreadStatus.DISMISSED):
            raise ValueError(
                f"close_thread status must be ThreadStatus.RESOLVED or "
                f"ThreadStatus.DISMISSED, got {status!r}"
            )
        now = time.time()
        self._conn.execute(
            """
            UPDATE open_threads
            SET status = ?, updated_at = ?, resolved_at = ?, resolution_reason = ?
            WHERE id = ?
            """,
            (status.value, now, now, resolution_reason, thread_id),
        )

    def list_active(self, *, owner_user_id: int) -> list[OpenThread]:
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE owner_user_id = ? AND status = ?
            ORDER BY created_at ASC
            """,
            (int(owner_user_id), ThreadStatus.ACTIVE.value),
        )
        return [_row_to_thread(row) for row in cur.fetchall()]

    def mark_surfaced(self, *, thread_id: str) -> None:
        now = time.time()
        self._conn.execute(
            """
            UPDATE open_threads
            SET last_surfaced_at = ?, surface_count = surface_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (now, now, thread_id),
        )

    def expire_stale(
        self,
        *,
        owner_user_id: int,
        now: float,
        followup_ttl_days: float,
        thread_ttl_days: float,
    ) -> list[tuple[str, str, int]]:
        """Backstop expiry (arbiter spec §7). Judgment owns lifespan; this
        only catches what judgment never touched. Tier 1: kind='followup'
        (the transient class — openclaw's 72h insight, owner-tuned to 7d).
        Tier 2: everything else at thread_ttl_days. A TTL of 0 disables
        that tier (the 0-means-unlimited convention). Only judgment-owned
        rows age (source_kind='topic', which includes workbench shares via
        their source_id marker) — recurring, followthrough and
        proactive_record rows are structural, never expired here.

        The terminal state is status='dismissed' with a resolution_reason
        prefixed 'backstop_ttl' — the live table's CHECK constraint pins
        the status vocabulary (a new 'expired' value needs a table
        rebuild; deviation recorded in the spec). The prefix is the
        discriminator, and the flip is reversible by construction — and
        since ruling R55 the age counts from the last touch
        (max(created_at, updated_at); see _AGED_AT), so an unretired row
        is not expired again the next day.
        Returns [(id, kind, age_days)] for the caller to log."""
        out: list[tuple[str, str, int]] = []
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        for ttl_days, followup_tier in (
            (float(followup_ttl_days or 0), True),
            (float(thread_ttl_days or 0), False),
        ):
            if ttl_days <= 0:
                continue
            cutoff = float(now) - ttl_days * 86400.0
            kind_clause = "kind = ?" if followup_tier else "kind != ?"
            rows = cur.execute(
                f"""
                SELECT id, kind, {_AGED_AT} AS aged_at FROM open_threads
                WHERE owner_user_id = ? AND status = ? AND {kind_clause}
                  AND {_AGED_AT} < ? AND source_kind = ?
                """,
                (
                    int(owner_user_id), ThreadStatus.ACTIVE.value,
                    ThreadKind.FOLLOWUP.value, cutoff,
                    ThreadSourceKind.TOPIC.value,
                ),
            ).fetchall()
            for r in rows:
                age_days = int((float(now) - float(r["aged_at"])) // 86400)
                reason = f"backstop_ttl (held {age_days}d > {int(ttl_days)}d)"
                self._conn.execute(
                    """
                    UPDATE open_threads
                    SET status = ?, updated_at = ?, resolved_at = ?,
                        resolution_reason = ?
                    WHERE id = ?
                    """,
                    (ThreadStatus.DISMISSED.value, float(now), float(now),
                     reason, r["id"]),
                )
                out.append((str(r["id"]), str(r["kind"]), age_days))
        return out

    def retire_unsurfaced(
        self,
        *,
        owner_user_id: int,
        older_than_days: float,
        now: float,
    ) -> int:
        """Retire the threads her judgment never surfaced (Batch 5, task 8,
        2026-09-28, rulings R14/R50): ACTIVE rows with ``last_surfaced_at``
        NULL, last touched (max(created_at, updated_at) — _AGED_AT, ruling
        R55) more than ``older_than_days`` ago, and not recurring (standing
        orders are structural). Like expire_stale, the terminal state is
        status='dismissed' — the live table's CHECK pins the vocabulary —
        with ``resolution_reason`` prefixed 'retired_unsurfaced' as the
        discriminator, ``resolved_at`` = now; :meth:`unretire` reverses it
        and stamps updated_at, so the window restarts. 0 days disables (the
        0-means-unlimited convention). Returns how many rows were retired."""
        days = float(older_than_days or 0)
        if days <= 0:
            return 0
        cutoff = float(now) - days * 86400.0
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        rows = cur.execute(
            f"""
            SELECT id, {_AGED_AT} AS aged_at FROM open_threads
            WHERE owner_user_id = ? AND status = ? AND last_surfaced_at IS NULL
              AND {_AGED_AT} < ? AND source_kind != ?
            """,
            (
                int(owner_user_id), ThreadStatus.ACTIVE.value, cutoff,
                ThreadSourceKind.RECURRING.value,
            ),
        ).fetchall()
        for r in rows:
            held_d = int((float(now) - float(r["aged_at"])) // 86400)
            reason = f"retired_unsurfaced (never surfaced, held {held_d}d > {int(days)}d)"
            self._conn.execute(
                """
                UPDATE open_threads
                SET status = ?, updated_at = ?, resolved_at = ?, resolution_reason = ?
                WHERE id = ?
                """,
                (ThreadStatus.DISMISSED.value, float(now), float(now), reason, r["id"]),
            )
        return len(rows)

    def unretire(self, *, thread_id: str, now: float | None = None) -> bool:
        """Return ANY dismissed thread to 'active' with ``resolved_at`` and
        ``resolution_reason`` cleared — the owner's lever over a
        'retired_unsurfaced' row and over the arbiter's 'backstop_ttl' rows
        alike (Batch 5, task 8). ``updated_at`` is stamped ``now`` (the
        clock unless given), which is what gives the row its fresh window
        under _AGED_AT (ruling R55). Resolved and active rows are left
        alone. True when a row flipped."""
        stamp = time.time() if now is None else float(now)
        cur = self._conn.execute(
            """
            UPDATE open_threads
            SET status = ?, updated_at = ?, resolved_at = NULL, resolution_reason = NULL
            WHERE id = ? AND status = ?
            """,
            (ThreadStatus.ACTIVE.value, stamp, str(thread_id), ThreadStatus.DISMISSED.value),
        )
        return cur.rowcount == 1

    def list_dismissed(self, *, owner_user_id: int) -> list[OpenThread]:
        """The retired board: this owner's dismissed threads, newest
        retirement first — what `palli_ctl threads retired` lists so the
        owner can find an id to unretire."""
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE owner_user_id = ? AND status = ?
            ORDER BY resolved_at DESC, created_at DESC
            """,
            (int(owner_user_id), ThreadStatus.DISMISSED.value),
        )
        return [_row_to_thread(row) for row in cur.fetchall()]

    def list_recently_surfaced(
        self,
        *,
        owner_user_id: int,
        since: float,
    ) -> list[OpenThread]:
        """Threads surfaced after ``since`` (unix ts) for this owner — any status.

        Used by the surface-decision prompt to show "recent surface history."
        """
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE owner_user_id = ?
              AND last_surfaced_at IS NOT NULL
              AND last_surfaced_at >= ?
            ORDER BY last_surfaced_at DESC
            """,
            (int(owner_user_id), float(since)),
        )
        return [_row_to_thread(row) for row in cur.fetchall()]

    # ── C1.5: source-kind-aware methods (B5 Decision 5) ─────────────────
    #
    # These power the absorbed-table use cases. The proactive_actions
    # rumination guard and daily-cap queries are the immediate consumers
    # (C1.5). Future kinds (RECURRING, FOLLOWTHROUGH) will reuse the same
    # primitives.

    def add_source_thread(
        self,
        *,
        owner_user_id: int,
        topic_summary: str,
        why_it_matters: str,
        kind: ThreadKind | str,
        urgency: str,
        source_kind: ThreadSourceKind | str,
        status: ThreadStatus | str = ThreadStatus.LOGGED,
        source_id: str | None = None,
        source_payload: dict[str, Any] | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        last_surfaced_at: float | None = None,
        surface_count: int = 0,
        resolved_at: float | None = None,
        resolution_reason: str | None = None,
    ) -> str:
        """Insert a row with a non-default ``source_kind``.

        Used by absorbed-table writers (proactive_actions in C1.5,
        standing_orders + operator_followthrough in future phases). For
        a plain topic-classified LLM-judged thread, use ``add_thread``.

        ``status`` defaults to ``LOGGED`` because the first absorbed kind
        (``proactive_record``) is append-only history. Callers handling
        active work (recurring/followthrough) pass an explicit
        ``ThreadStatus.ACTIVE``.
        """
        if isinstance(kind, str):
            try:
                kind = ThreadKind(kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid kind {kind!r}; must be one of "
                    f"{[k.value for k in ThreadKind]}"
                ) from exc
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        if isinstance(status, str):
            try:
                status = ThreadStatus(status)
            except ValueError as exc:
                raise ValueError(
                    f"invalid status {status!r}; must be one of "
                    f"{[s.value for s in ThreadStatus]}"
                ) from exc
        thread_id = uuid.uuid4().hex
        now = time.time()
        created = float(created_at) if created_at is not None else now
        updated = float(updated_at) if updated_at is not None else created
        payload_json = json.dumps(source_payload) if source_payload is not None else None
        self._conn.execute(
            """
            INSERT INTO open_threads (
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                source_kind, source_id, source_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                int(owner_user_id),
                status.value,
                created,
                updated,
                topic_summary,
                why_it_matters,
                kind.value,
                urgency,
                float(last_surfaced_at) if last_surfaced_at is not None else None,
                int(surface_count),
                float(resolved_at) if resolved_at is not None else None,
                resolution_reason,
                source_kind.value,
                source_id,
                payload_json,
            ),
        )
        return thread_id

    def list_by_source_kind(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        limit: int = 20,
    ) -> list[OpenThread]:
        """Return rows of a given ``source_kind`` for this owner, newest-first.

        Used by the rumination guard (``ai/proactive_candidate_source.py``)
        and the daily-cap / frequency-limit reads
        (``ai/proactive_policy.py``). Sort key matches the legacy
        ``proactive_actions`` ORDER BY: most-recent by executed/scheduled,
        which we approximate via the unified ``last_surfaced_at`` and
        ``created_at`` columns.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE owner_user_id = ? AND source_kind = ?
            ORDER BY COALESCE(last_surfaced_at, created_at) DESC
            LIMIT ?
            """,
            (int(owner_user_id), source_kind.value, max(1, int(limit))),
        )
        return [_row_to_thread(row) for row in cur.fetchall()]

    def count_by_source_kind_since(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        since: float,
        payload_action_types: list[str] | None = None,
    ) -> int:
        """Count rows of ``source_kind`` for this owner with
        ``COALESCE(last_surfaced_at, created_at) > since``.

        Used by the daily-cap query in ``ai/proactive_policy.py``. Pass
        ``payload_action_types`` to filter by
        ``source_payload->>'action_type'`` IN (...). This preserves the
        legacy
        ``WHERE action_type IN ('proactive_message', 'followup_message')``
        filter from the source table.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        params: list[Any] = [int(owner_user_id), source_kind.value, float(since)]
        action_filter = ""
        if payload_action_types:
            cleaned = [str(t).strip() for t in payload_action_types if t]
            if cleaned:
                placeholders = ",".join("?" for _ in cleaned)
                action_filter = (
                    f" AND json_extract(source_payload, '$.action_type') IN ({placeholders})"
                )
                params.extend(cleaned)
        cur = self._conn.execute(
            f"""
            SELECT COUNT(*)
            FROM open_threads
            WHERE owner_user_id = ?
              AND source_kind = ?
              AND COALESCE(last_surfaced_at, created_at) > ?
              {action_filter}
            """,
            tuple(params),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)

    def max_last_surfaced_by_source_kind(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        since: float,
        payload_action_types: list[str] | None = None,
    ) -> float | None:
        """Return the maximum ``COALESCE(last_surfaced_at, created_at)``
        among rows of ``source_kind`` since ``since`` for this owner.

        Replaces the legacy
        ``SELECT MAX(executed_time) FROM proactive_actions WHERE ...``
        pattern used by frequency-limit and timing-guard reads.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        params: list[Any] = [int(owner_user_id), source_kind.value, float(since)]
        action_filter = ""
        if payload_action_types:
            cleaned = [str(t).strip() for t in payload_action_types if t]
            if cleaned:
                placeholders = ",".join("?" for _ in cleaned)
                action_filter = (
                    f" AND json_extract(source_payload, '$.action_type') IN ({placeholders})"
                )
                params.extend(cleaned)
        cur = self._conn.execute(
            f"""
            SELECT MAX(COALESCE(last_surfaced_at, created_at))
            FROM open_threads
            WHERE owner_user_id = ?
              AND source_kind = ?
              AND COALESCE(last_surfaced_at, created_at) > ?
              {action_filter}
            """,
            tuple(params),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    # ── C8: source-id-keyed CRUD for absorbed kinds (B5 Decision 5) ─────
    #
    # The standing_orders rewire (C8 step 3) needs to update a specific
    # absorbed row by its stable source_id. These helpers generalize the
    # operations the legacy autonomy_control_store SQL did directly on
    # the standing_orders table so they keep working post-absorb without
    # exposing the open_threads PK to callers.

    def get_by_source_id(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        source_id: str,
    ) -> OpenThread | None:
        """Return the (single) open_thread for a source_id under one owner.

        ``source_id`` is the stable PK from the absorbed source table
        (e.g., ``standing_order_id`` for RECURRING). Pairing with
        ``owner_user_id`` matches the security model of the legacy
        get_standing_order which scoped by owner.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE owner_user_id = ?
              AND source_kind = ?
              AND source_id = ?
            LIMIT 1
            """,
            (int(owner_user_id), source_kind.value, str(source_id)),
        )
        row = cur.fetchone()
        return _row_to_thread(row) if row else None

    def get_by_source_id_unscoped(
        self,
        *,
        source_kind: ThreadSourceKind | str,
        source_id: str,
    ) -> OpenThread | None:
        """Return the (single) open_thread for a source_id, ignoring owner.

        For absorbed kinds whose PK was globally unique pre-absorb (e.g.,
        standing_order_id is a uuid-prefixed text PK with collision-resistant
        randomness), the legacy unscoped lookup pattern continues to work.
        Use ``get_by_source_id`` when the owner is known — it adds a layer
        of access control that this helper omits.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE source_kind = ? AND source_id = ?
            LIMIT 1
            """,
            (source_kind.value, str(source_id)),
        )
        row = cur.fetchone()
        return _row_to_thread(row) if row else None

    def update_source_thread(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        source_id: str,
        status: ThreadStatus | str | None = None,
        topic_summary: str | None = None,
        why_it_matters: str | None = None,
        kind: ThreadKind | str | None = None,
        urgency: str | None = None,
        last_surfaced_at: float | None = None,
        resolved_at: float | None = None,
        resolution_reason: str | None = None,
        source_payload_updates: dict[str, Any] | None = None,
        source_payload_replace: dict[str, Any] | None = None,
    ) -> bool:
        """UPDATE a source-keyed row. None args mean "leave unchanged".

        ``source_payload_updates`` does a shallow merge into the existing
        payload (legacy ``UPDATE … SET metadata = $X`` semantics — most
        callers want partial updates). Pass ``source_payload_replace``
        to replace the entire payload (exclusive with ``_updates``).

        Returns True if a row was matched and updated; False otherwise.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        if source_payload_updates is not None and source_payload_replace is not None:
            raise ValueError(
                "pass at most one of source_payload_updates / source_payload_replace"
            )
        if isinstance(status, str):
            try:
                status = ThreadStatus(status)
            except ValueError as exc:
                raise ValueError(
                    f"invalid status {status!r}; must be one of "
                    f"{[s.value for s in ThreadStatus]}"
                ) from exc
        if isinstance(kind, str):
            try:
                kind = ThreadKind(kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid kind {kind!r}; must be one of "
                    f"{[k.value for k in ThreadKind]}"
                ) from exc

        # Resolve source_payload first because we may need to merge.
        existing = self.get_by_source_id(
            owner_user_id=int(owner_user_id),
            source_kind=source_kind,
            source_id=str(source_id),
        )
        if existing is None:
            return False
        if source_payload_replace is not None:
            new_payload: dict[str, Any] | None = source_payload_replace
        elif source_payload_updates is not None:
            base = dict(existing.source_payload or {})
            base.update(source_payload_updates)
            new_payload = base
        else:
            new_payload = None  # don't touch
        now = time.time()
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if topic_summary is not None:
            sets.append("topic_summary = ?")
            params.append(topic_summary)
        if why_it_matters is not None:
            sets.append("why_it_matters = ?")
            params.append(why_it_matters)
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind.value)
        if urgency is not None:
            sets.append("urgency = ?")
            params.append(urgency)
        if last_surfaced_at is not None:
            sets.append("last_surfaced_at = ?")
            params.append(float(last_surfaced_at))
        if resolved_at is not None:
            sets.append("resolved_at = ?")
            params.append(float(resolved_at))
        if resolution_reason is not None:
            sets.append("resolution_reason = ?")
            params.append(resolution_reason)
        if new_payload is not None:
            sets.append("source_payload = ?")
            params.append(json.dumps(new_payload))
        params.extend(
            [int(owner_user_id), source_kind.value, str(source_id)]
        )
        self._conn.execute(
            f"""
            UPDATE open_threads
            SET {', '.join(sets)}
            WHERE owner_user_id = ?
              AND source_kind = ?
              AND source_id = ?
            """,
            tuple(params),
        )
        return True

    def list_due_by_source_kind(
        self,
        *,
        owner_user_id: int,
        source_kind: ThreadSourceKind | str,
        statuses: list[ThreadStatus | str] | None = None,
        due_before: float | None = None,
        limit: int = 32,
    ) -> list[OpenThread]:
        """List rows of ``source_kind`` for this owner, optionally filtered.

        Designed for the heartbeat due-poll: ``statuses=['active']`` plus
        ``due_before=time.time()`` returns the recurring rows whose
        ``source_payload.next_run_at`` is at or before ``due_before``.
        Sort matches the legacy ``list_standing_orders`` ORDER BY:
        ``COALESCE(next_run_at, updated_at, created_at) ASC, updated_at DESC``.
        """
        if isinstance(source_kind, str):
            try:
                source_kind = ThreadSourceKind(source_kind)
            except ValueError as exc:
                raise ValueError(
                    f"invalid source_kind {source_kind!r}; must be one of "
                    f"{[k.value for k in ThreadSourceKind]}"
                ) from exc
        clean_statuses: list[str] = []
        for s in statuses or []:
            if isinstance(s, ThreadStatus):
                clean_statuses.append(s.value)
            else:
                clean_statuses.append(str(s).strip())
        clean_statuses = [s for s in clean_statuses if s]
        params: list[Any] = [int(owner_user_id), source_kind.value]
        clauses: list[str] = [
            "owner_user_id = ?",
            "source_kind = ?",
        ]
        if clean_statuses:
            placeholders = ",".join("?" for _ in clean_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(clean_statuses)
        if due_before is not None:
            clauses.append(
                "CAST(json_extract(source_payload, '$.next_run_at') AS REAL) > 0"
            )
            clauses.append(
                "CAST(json_extract(source_payload, '$.next_run_at') AS REAL) <= ?"
            )
            params.append(float(due_before))
        params.append(max(1, int(limit)))
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            f"""
            SELECT {_SELECT_FIELDS}
            FROM open_threads
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(
                CAST(json_extract(source_payload, '$.next_run_at') AS REAL),
                updated_at,
                created_at
            ) ASC, updated_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [_row_to_thread(row) for row in cur.fetchall()]
