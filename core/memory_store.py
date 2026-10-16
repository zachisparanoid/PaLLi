from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

# The today slot (R15, memory that reaches, T12): what the owner said today,
# kept as user_memory rows of this type until the nightly pass retires them.
TODAY_TYPE = "today"
TODAY_DAILY_CAP = 8


class MemoryStore:
    """Persistence helper for legacy fact memory and the indexed memory plane."""

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

    def _clean_fts_query(self, value: Any) -> str:
        text = self._clean_text(value)
        if not text:
            return ""
        text = re.sub(r"[^\w\s]", " ", text)
        return " ".join(part for part in text.split() if part)

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _user_memory_record(self, row: Any) -> Dict[str, Any]:
        record = {
            "id": row[0],
            "user_id": row[1],
            "memory_type": row[2],
            "content": row[3],
            "importance_score": row[4],
            "created_at": row[5],
            "last_accessed": row[6],
            "access_count": row[7],
            "tags": self._load_json(row[8], []),
        }
        if len(row) > 9:
            record.update(
                {
                    "last_referenced_at": row[9],
                    "reference_count": row[10],
                    "salience_weight": row[11],
                    "status": row[12] or "active",
                    "superseded_by_fact_id": row[13],
                    "source_detail": self._load_json(row[14], {}),
                }
            )
        else:
            record.update(
                {
                    "last_referenced_at": record["created_at"],
                    "reference_count": int(record.get("access_count") or 0),
                    "salience_weight": 1.0,
                    "status": "active",
                    "superseded_by_fact_id": None,
                    "source_detail": {},
                }
            )
        return record

    def _memory_index_item_record(self, row: Any) -> Dict[str, Any]:
        return {
            "item_id": str(row[0] or ""),
            "owner_user_id": int(row[1] or 0),
            "source_kind": str(row[2] or ""),
            "source_id": str(row[3] or ""),
            "title": str(row[4] or ""),
            "content": str(row[5] or ""),
            "snippet": str(row[6] or ""),
            "salience": str(row[7] or "low"),
            "tags": self._load_json(row[8], []),
            "related_refs": self._load_json(row[9], []),
            "content_hash": str(row[10] or ""),
            "metadata": self._load_json(row[11], {}),
            "created_at": float(row[12]) if row[12] is not None else None,
            "updated_at": float(row[13]) if row[13] is not None else None,
        }

    def _memory_index_embedding_record(self, row: Any) -> Dict[str, Any]:
        return {
            "item_id": str(row[0] or ""),
            "owner_user_id": int(row[1] or 0),
            "provider": str(row[2] or ""),
            "model": str(row[3] or ""),
            "vector": self._load_json(row[4], []),
            "content_hash": str(row[5] or ""),
            "created_at": float(row[6]) if row[6] is not None else None,
            "updated_at": float(row[7]) if row[7] is not None else None,
        }

    def _memory_bullet_ref_record(self, row: Any) -> Dict[str, Any]:
        return {
            "item_id": str(row[0] or ""),
            "owner_user_id": int(row[1] or 0),
            "last_referenced_at": float(row[2]) if row[2] is not None else 0.0,
            "reference_count": int(row[3] or 0),
        }

    async def store_user_memory(
        self,
        user_id: int,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        salience_weight: float = 1.0,
        source_detail: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Store important user memories."""
        conn = await self._brain.get_connection()
        now = self._now()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO user_memory
                (user_id, memory_type, content, importance_score, created_at, last_accessed, tags,
                 last_referenced_at, reference_count, salience_weight, status, source_detail)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 0, $9, 'active', $10)
                RETURNING id
            """,
                int(user_id),
                self._clean_text(memory_type),
                str(content or ""),
                float(importance),
                now,
                now,
                self._dump_json(tags) if tags else None,
                now,
                max(0.1, float(salience_weight or 1.0)),
                self._dump_json(source_detail or {}),
            )
            return int(row[0]) if row else None
        finally:
            await conn.close()

    async def note_today_fact(self, *, user_id: int, content: str, source: str) -> Optional[int]:
        """Keep one line in the owner's today slot (R15, T12): a ``user_memory``
        row with ``memory_type`` TODAY_TYPE, written by the after-reply
        reflection the moment it learns something, read into the reply under
        its own heading, retired by the nightly pass. Deduped on the exact
        content among the slot's active rows (the existing row's id comes
        back); None for an empty line or once the slot holds
        TODAY_DAILY_CAP active rows. The check and the write share one
        transaction (BEGIN IMMEDIATE), so two reflections cannot both slip
        past the cap or the dedup."""
        text = self._clean_text(content)
        if not text:
            return None
        conn = await self._brain.get_connection()
        try:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, content FROM user_memory
                    WHERE user_id = $1 AND memory_type = $2
                      AND COALESCE(status, 'active') = 'active'
                    """,
                    int(user_id),
                    TODAY_TYPE,
                )
                for existing in rows:
                    if str(existing[1] or "") == text:
                        return int(existing[0])
                if len(rows) >= TODAY_DAILY_CAP:
                    return None
                now = self._now()
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_memory
                    (user_id, memory_type, content, importance_score, created_at, last_accessed, tags,
                     last_referenced_at, reference_count, salience_weight, status, source_detail)
                    VALUES ($1, $2, $3, $4, $5, $6, NULL, $7, 0, $8, 'active', $9)
                    RETURNING id
                    """,
                    int(user_id),
                    TODAY_TYPE,
                    text,
                    0.5,
                    now,
                    now,
                    now,
                    1.0,
                    self._dump_json({"source": self._clean_text(source)}),
                )
                return int(row[0]) if row else None
        finally:
            await conn.close()

    async def active_today_facts(self, *, user_id: int) -> List[Dict[str, Any]]:
        """The owner's today slot: its active rows, newest first (R15, T12)."""
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT * FROM user_memory
                WHERE user_id = $1 AND memory_type = $2
                  AND COALESCE(status, 'active') = 'active'
                ORDER BY created_at DESC, id DESC
                """,
                int(user_id),
                TODAY_TYPE,
            )
            return [self._user_memory_record(row) for row in rows]
        finally:
            await conn.close()

    async def retire_today_facts(self, *, user_id: int, before_ts: float) -> int:
        """The night's end of the today slot (R15, T12): every active today row
        written before ``before_ts`` is set to status 'retired' (kept, never
        deleted; every reader of active facts then passes it by). Returns how
        many rows it retired."""
        conn = await self._brain.get_connection()
        try:
            status = await conn.execute(
                """
                UPDATE user_memory
                SET status = 'retired'
                WHERE user_id = $1
                  AND memory_type = $2
                  AND COALESCE(status, 'active') = 'active'
                  AND created_at < $3
                """,
                int(user_id),
                TODAY_TYPE,
                float(before_ts),
            )
            return self._brain._rowcount(status)
        finally:
            await conn.close()

    async def prune_retired_today_facts(self, *, before_ts: float) -> int:
        """The housekeeping's end of the today slot (R15, T12; fix round 1):
        retired today rows written before ``before_ts`` are deleted, for every
        user. Only rows the night already retired — an active today row, or
        any other type, is never touched. Returns how many rows went."""
        conn = await self._brain.get_connection()
        try:
            status = await conn.execute(
                """
                DELETE FROM user_memory
                WHERE memory_type = $1
                  AND status = 'retired'
                  AND created_at < $2
                """,
                TODAY_TYPE,
                float(before_ts),
            )
            return self._brain._rowcount(status)
        finally:
            await conn.close()

    async def supersede_user_memory_with_gist(
        self,
        *,
        user_id: int,
        memory_id: int,
        gist_content: str,
        source_detail: Optional[Dict[str, Any]] = None,
        salience_factor: float = 0.5,
        new_salience_weight: Optional[float] = None,
        inherit_timestamps: bool = True,
    ) -> Optional[int]:
        """Wave 1a supersede mechanics in ONE transaction: INSERT the gist as
        a NEW fact (type/importance/tags inherited) and mark the original
        superseded → new id with salience decayed. The original's content is
        NEVER touched (owner decision #1, 2026-07-15 — nothing she lived is
        ever altered). Returns the new fact id; None when the row is missing
        or already superseded (re-aging is a no-op).

        inherit_timestamps=True (aging): the successor carries the ORIGINAL's
        created_at/last_referenced_at so decay continuity holds — aging fades,
        it must never rejuvenate (review fix 2026-07-15: a fresh-stamped gist
        scored recency 1.0 and outranked yesterday's facts). Corrections pass
        False: a correction is genuinely new information."""
        clean_gist = self._clean_text(gist_content)
        if not clean_gist:
            return None
        conn = await self._brain.get_connection()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT memory_type, importance_score, tags, salience_weight, status,
                           created_at, last_referenced_at
                    FROM user_memory WHERE id = $1 AND user_id = $2
                    """,
                    int(memory_id),
                    int(user_id),
                )
                if row is None or str(row[4] or "active") != "active":
                    return None
                now = self._now()
                old_salience = max(0.1, float(row[3] or 1.0))
                if inherit_timestamps:
                    new_created = float(row[5] or now)
                    new_referenced = float(row[6] or 0.0) or new_created
                else:
                    new_created = now
                    new_referenced = now
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO user_memory
                    (user_id, memory_type, content, importance_score, created_at, last_accessed, tags,
                     last_referenced_at, reference_count, salience_weight, status, source_detail)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 0, $9, 'active', $10)
                    RETURNING id
                    """,
                    int(user_id),
                    str(row[0] or ""),
                    clean_gist,
                    float(row[1] or 0.5),
                    new_created,
                    now,
                    row[2],
                    new_referenced,
                    max(0.1, float(new_salience_weight if new_salience_weight is not None else old_salience)),
                    self._dump_json(source_detail or {}),
                )
                new_id = int(new_row[0])
                await conn.execute(
                    """
                    UPDATE user_memory
                    SET status = 'superseded',
                        superseded_by_fact_id = $1,
                        salience_weight = $2
                    WHERE id = $3 AND user_id = $4
                    """,
                    str(new_id),
                    max(0.1, old_salience * float(salience_factor)),
                    int(memory_id),
                    int(user_id),
                )
                return new_id
        finally:
            await conn.close()

    async def get_user_memories(
        self,
        user_id: int,
        memory_type: Optional[str] = None,
        min_importance: float = 0.3,
        include_superseded: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieve user memories by type and importance.

        Default excludes superseded originals (Wave 1a review fix: every
        ambient consumer — Discord /insights included — showed original +
        gist pairs after aging). The deliberate raw doors opt in:
        get_user_facts_raw, introspect exact mode, and the web account
        listing (the owner's only view/delete surface for superseded rows).
        """
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT * FROM user_memory
                WHERE user_id = $1 AND importance_score >= $2
            """
            if not include_superseded:
                query += " AND COALESCE(status, 'active') = 'active'"
            params: List[Any] = [int(user_id), float(min_importance)]

            clean_memory_type = self._clean_text(memory_type)
            if clean_memory_type:
                params.append(clean_memory_type)
                query += f" AND memory_type = ${len(params)}"

            query += " ORDER BY importance_score DESC, last_accessed DESC"

            rows = await conn.fetch(query, *params)
            return [self._user_memory_record(row) for row in rows]
        finally:
            await conn.close()

    async def bump_user_memory_references(self, *, user_id: int, memory_ids: List[int]) -> None:
        """Mark surfaced facts as referenced without changing their content."""
        cleaned = [int(item) for item in memory_ids or [] if item is not None]
        if not cleaned:
            return
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            placeholders = self._brain._placeholders(len(cleaned), start=2)
            await conn.execute(
                f"""
                UPDATE user_memory
                SET last_referenced_at = $1,
                    reference_count = COALESCE(reference_count, 0) + 1,
                    last_accessed = $1,
                    access_count = COALESCE(access_count, 0) + 1
                WHERE user_id = ${len(cleaned) + 2}
                  AND id IN ({placeholders})
                """,
                now,
                *cleaned,
                int(user_id),
            )
        finally:
            await conn.close()

    async def get_user_memory(
        self, user_id: int, memory_id: int
    ) -> Optional[Dict[str, Any]]:
        """Read ONE stored memory row, scoped to its owner.

        Week 2 task 6 (2026-09-10) — the forget path has to know what a
        row was made of BEFORE it deletes it (``source_detail`` says
        whether an identity document is the row's source of truth), and
        ``get_user_memories`` is a list read over the whole owner. Same
        ``id = $1 AND user_id = $2`` scoping as ``delete_user_memory``:
        another owner's row reads as missing, never as someone else's.
        """
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM user_memory WHERE id = $1 AND user_id = $2",
                int(memory_id),
                int(user_id),
            )
            return self._user_memory_record(row) if row is not None else None
        finally:
            await conn.close()

    async def delete_user_memory(self, user_id: int, memory_id: int) -> bool:
        """Delete a specific user memory."""
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                "DELETE FROM user_memory WHERE id = $1 AND user_id = $2 RETURNING id",
                int(memory_id),
                int(user_id),
            )
            return row is not None
        finally:
            await conn.close()

    async def delete_user_memory_by_source(
        self,
        *,
        user_id: int,
        source: str,
    ) -> int:
        """Delete every ``user_memory`` row for a user whose
        ``source_detail.source`` matches the given marker.

        Phase B / B4 step 5 (2026-05-09) — used by
        ``core.memory_service.rebuild_user_memory_from_documents`` to
        scrub the prior set of derived rows before writing a fresh
        materialization. Scoping via ``source_detail->>'source'`` keeps
        the scrub from touching pre-derived rows that were written by a
        different writer (e.g. legacy ``upsert_user_fact`` calls before
        B4 step 4 redirected reflection to the preferences doc).

        The runtime SQL adapter (``core/database.py::_adapt_sql``) maps
        postgres ``->>`` accessor syntax to sqlite ``json_extract`` so
        the same query string runs unchanged on both backends. Returns
        the count of deleted rows.
        """
        clean_source = self._clean_text(source)
        if not clean_source:
            return 0
        conn = await self._brain.get_connection()
        try:
            cursor_status = await conn.execute(
                """
                DELETE FROM user_memory
                WHERE user_id = $1
                  AND source_detail->>'source' = $2
                """,
                int(user_id),
                clean_source,
            )
            return self._brain._rowcount(cursor_status)
        finally:
            await conn.close()

    async def upsert_memory_index_item(
        self,
        *,
        item_id: str,
        owner_user_id: int,
        source_kind: str,
        source_id: str,
        title: Optional[str] = None,
        content: str,
        snippet: Optional[str] = None,
        salience: str = "low",
        tags: Optional[List[str]] = None,
        related_refs: Optional[List[str]] = None,
        content_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            await conn.execute(
                """
                INSERT INTO memory_index_items
                (item_id, owner_user_id, source_kind, source_id, title, content, snippet,
                 salience, tags, related_refs, content_hash, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12::jsonb, $13, $14)
                ON CONFLICT (item_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    source_kind = EXCLUDED.source_kind,
                    source_id = EXCLUDED.source_id,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    snippet = EXCLUDED.snippet,
                    salience = EXCLUDED.salience,
                    tags = EXCLUDED.tags,
                    related_refs = EXCLUDED.related_refs,
                    content_hash = EXCLUDED.content_hash,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                self._clean_text(item_id),
                int(owner_user_id),
                self._clean_text(source_kind),
                self._clean_text(source_id),
                self._clean_optional_text(title),
                self._clean_text(content),
                self._clean_optional_text(snippet),
                self._clean_text(salience, default="low"),
                self._dump_json(tags or []),
                self._dump_json(related_refs or []),
                self._clean_optional_text(content_hash),
                self._dump_json(metadata or {}),
                float(created_at or now),
                float(updated_at or now),
            )
        finally:
            await conn.close()

    async def get_memory_index_item(self, *, item_id: str) -> Optional[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            row = await conn.fetchrow(
                """
                SELECT item_id, owner_user_id, source_kind, source_id, title, content, snippet,
                       salience, tags, related_refs, content_hash, metadata, created_at, updated_at
                FROM memory_index_items
                WHERE item_id = $1
                """,
                self._clean_text(item_id),
            )
            return self._memory_index_item_record(row) if row else None
        finally:
            await conn.close()

    async def list_memory_index_items(
        self,
        *,
        owner_user_id: int,
        source_kinds: Optional[List[str]] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            params: List[Any] = [int(owner_user_id)]
            query = (
                "SELECT item_id, owner_user_id, source_kind, source_id, title, content, snippet, "
                "salience, tags, related_refs, content_hash, metadata, created_at, updated_at "
                "FROM memory_index_items WHERE owner_user_id = $1"
            )
            clean_source_kinds = self._clean_text_list(source_kinds)
            if clean_source_kinds:
                placeholders = []
                for item in clean_source_kinds:
                    params.append(item)
                    placeholders.append(f"${len(params)}")
                query += f" AND source_kind IN ({', '.join(placeholders)})"
            params.append(max(1, int(limit)))
            query += f" ORDER BY updated_at DESC, created_at DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._memory_index_item_record(row) for row in rows]
        finally:
            await conn.close()

    async def delete_memory_index_items(self, *, item_ids: List[str]) -> None:
        cleaned = self._clean_text_list(item_ids)
        if not cleaned:
            return
        conn = await self._brain.get_connection()
        try:
            placeholders = self._brain._placeholders(len(cleaned))
            await conn.execute(f"DELETE FROM memory_index_items WHERE item_id IN ({placeholders})", *cleaned)
            await conn.execute(f"DELETE FROM memory_index_embeddings WHERE item_id IN ({placeholders})", *cleaned)
            await conn.execute(f"DELETE FROM memory_index_fts WHERE item_id IN ({placeholders})", *cleaned)
        finally:
            await conn.close()

    async def clear_memory_index_fts(self, *, owner_user_id: int) -> None:
        conn = await self._brain.get_connection()
        try:
            # IM-CRIT-4 + code-review issue #1: dual-bind for self-heal.
            # FTS5 columns have no type affinity, so a TEXT row written by
            # a pre-fix deployment ('7') is not matched by an int=7
            # WHERE clause and vice versa. Binding both forms lets the
            # next rebuild_index sweep up legacy rows instead of
            # accumulating orphans alongside fresh int rows.
            owner_id_int = int(owner_user_id)
            await conn.execute(
                "DELETE FROM memory_index_fts WHERE owner_user_id = $1 OR owner_user_id = $2",
                owner_id_int,
                str(owner_id_int),
            )
        finally:
            await conn.close()

    async def upsert_memory_index_fts(
        self,
        *,
        item_id: str,
        owner_user_id: int,
        source_kind: str,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            await conn.execute(
                """
                INSERT INTO memory_index_fts (owner_user_id, item_id, source_kind, title, content, tags)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                # IM-CRIT-4: bind as int to match canonical memory_index_items.owner_user_id (BIGINT).
                int(owner_user_id),
                self._clean_text(item_id),
                self._clean_text(source_kind),
                self._clean_text(title),
                self._clean_text(content),
                " ".join(str(tag).strip() for tag in (tags or []) if str(tag).strip()),
            )
        finally:
            await conn.close()

    async def search_memory_index_fts(
        self,
        *,
        owner_user_id: int,
        query: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        clean_query = self._clean_fts_query(query)
        if not clean_query:
            return []
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT i.item_id, i.owner_user_id, i.source_kind, i.source_id, i.title, i.content, i.snippet,
                       i.salience, i.tags, i.related_refs, i.content_hash, i.metadata, i.created_at, i.updated_at,
                       bm25(memory_index_fts) AS lexical_score
                FROM memory_index_fts
                JOIN memory_index_items AS i ON i.item_id = memory_index_fts.item_id
                WHERE memory_index_fts.owner_user_id = $1
                  AND memory_index_fts MATCH $2
                ORDER BY lexical_score
                LIMIT $3
                """,
                # IM-CRIT-4: bind as int to match canonical memory_index_items.owner_user_id (BIGINT).
                int(owner_user_id),
                clean_query,
                max(1, int(limit)),
            )
            results: List[Dict[str, Any]] = []
            for row in rows:
                item = self._memory_index_item_record(row)
                item["lexical_score"] = float(row[14]) if row[14] is not None else 0.0
                results.append(item)
            return results
        finally:
            await conn.close()

    async def upsert_memory_index_embedding(
        self,
        *,
        item_id: str,
        owner_user_id: int,
        provider: str,
        model: str,
        vector: List[float],
        content_hash: str,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            now = self._now()
            await conn.execute(
                """
                INSERT INTO memory_index_embeddings
                (item_id, owner_user_id, provider, model, vector_json, content_hash, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
                ON CONFLICT (item_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    vector_json = EXCLUDED.vector_json,
                    content_hash = EXCLUDED.content_hash,
                    updated_at = EXCLUDED.updated_at
                """,
                self._clean_text(item_id),
                int(owner_user_id),
                self._clean_text(provider),
                self._clean_text(model),
                self._dump_json(list(vector or [])),
                self._clean_text(content_hash),
                now,
                now,
            )
        finally:
            await conn.close()

    async def list_memory_index_embeddings(
        self,
        *,
        owner_user_id: int,
        limit: int = 4096,
    ) -> List[Dict[str, Any]]:
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT item_id, owner_user_id, provider, model, vector_json, content_hash, created_at, updated_at
                FROM memory_index_embeddings
                WHERE owner_user_id = $1
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                int(owner_user_id),
                max(1, int(limit)),
            )
            return [self._memory_index_embedding_record(row) for row in rows]
        finally:
            await conn.close()

    async def load_memory_bullet_refs(self, *, owner_user_id: int) -> List[Dict[str, Any]]:
        """Every reference stamp of the owner's memory bullets (Batch 5, task 3,
        2026-09-28): core.memory_refs builds its per-owner snapshot from these
        rows."""
        conn = await self._brain.get_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT item_id, owner_user_id, last_referenced_at, reference_count
                FROM memory_bullet_refs
                WHERE owner_user_id = $1
                ORDER BY item_id
                """,
                int(owner_user_id),
            )
            return [self._memory_bullet_ref_record(row) for row in rows]
        finally:
            await conn.close()

    async def bump_memory_bullet_refs(
        self,
        *,
        owner_user_id: int,
        item_ids: List[str],
        now: Optional[float] = None,
    ) -> None:
        """Mark memory bullets as referenced: a new row starts at one, an existing
        row takes the new time and counts up. Blank and repeated ids in one call
        are one bump each; nothing to bump is a no-op. One transaction, so a
        turn's bumps land together."""
        cleaned: List[str] = []
        for item in item_ids or []:
            text = self._clean_text(item)
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            return
        stamp = float(now) if now is not None else float(self._now())
        conn = await self._brain.get_connection()
        try:
            async with conn.transaction():
                for item_id in cleaned:
                    await conn.execute(
                        """
                        INSERT INTO memory_bullet_refs
                        (item_id, owner_user_id, last_referenced_at, reference_count)
                        VALUES ($1, $2, $3, 1)
                        ON CONFLICT (item_id, owner_user_id) DO UPDATE SET
                            last_referenced_at = EXCLUDED.last_referenced_at,
                            reference_count = reference_count + 1
                        """,
                        item_id,
                        int(owner_user_id),
                        stamp,
                    )
        finally:
            await conn.close()
