from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, List, Optional


class ConversationHistoryStore:
    """Persistence helper for conversation history and continuity-facing exports."""

    def __init__(self, brain: Any, *, now: Optional[Callable[[], float]] = None) -> None:
        self._brain = brain
        self._now = now or time.time

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        return self._brain._load_json(raw, fallback)

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _placeholders(self, count: int, *, start: int = 1) -> str:
        return self._brain._placeholders(count, start=start)

    def _pair_placeholders(self, count: int, *, start: int = 1) -> str:
        return self._brain._pair_placeholders(count, start=start)

    @staticmethod
    def _clean_optional_text(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _clean_optional_int(value: Any) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    def _conversation_record(self, row: Any, *, include_metadata: bool = False) -> Dict[str, Any]:
        record = {
            "id": row[0],
            "user_id": row[1],
            "platform": row[2],
            "channel_id": row[3],
            "surface_id": str(row[4] or row[3] or ""),
            "thread_id": str(row[5] or ""),
            "role": row[6],
            "username": row[7],
            "content": row[8],
            "timestamp": row[9],
        }
        if include_metadata:
            record["metadata"] = self._load_json(row[10], {})
        return record

    def _export_record(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row[0],
            "user_id": row[1],
            "platform": row[2],
            "channel_id": row[3],
            "role": row[4],
            "username": row[5],
            "content": row[6],
            "timestamp": row[7],
            "metadata": self._load_json(row[8], {}),
        }

    @staticmethod
    def _display_username(
        *,
        user_id: Optional[int],
        role: str,
        username: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        if role == "assistant":
            return "Me"
        if username:
            return username
        if user_id and metadata and metadata.get("username"):
            return str(metadata["username"])
        if user_id:
            return f"User{user_id}"
        return "Unknown"

    async def store_conversation(
        self,
        *,
        user_id: int,
        platform: str,
        channel_id: int,
        thread_id: Optional[str],
        role: str,
        content: str,
        username: str = None,
        metadata: Dict = None,
        surface_id: Optional[str] = None,
    ) -> int:
        conn = await self._brain.get_connection()
        try:
            conversation_id = await conn.fetchval(
                """
                INSERT INTO conversations
                (user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata)
                VALUES ($1, $2, $3, NULLIF($4, ''), $5, $6, $7, $8, $9, $10::jsonb)
                RETURNING id
                """,
                user_id,
                platform,
                channel_id,
                str(surface_id or "").strip() or None,
                self._clean_optional_text(thread_id),
                role,
                self._display_username(
                    user_id=user_id,
                    role=role,
                    username=username,
                    metadata=metadata,
                ),
                content,
                self._now(),
                self._dump_json(metadata) if metadata else None,
            )
            if user_id and role == "user":
                await self._brain._update_user_interaction_internal(conn, user_id, platform)
            return int(conversation_id)
        finally:
            await conn.close()

    async def export_user_conversations(self, user_ids: List[int]) -> Dict[str, Any]:
        unique_ids = sorted({uid for uid in user_ids if isinstance(uid, int)})
        exported_at = self._now()
        if not unique_ids:
            return {"exported_at": exported_at, "user_ids": [], "threads": []}

        conn = await self._brain.get_connection()
        try:
            id_placeholders = self._placeholders(len(unique_ids))
            safe_query = f"""
                SELECT platform, channel_id
                FROM conversations
                WHERE (platform, channel_id) IN (
                    SELECT DISTINCT platform, channel_id
                    FROM conversations
                    WHERE user_id IN ({id_placeholders})
                )
                GROUP BY platform, channel_id
                HAVING SUM(
                    CASE
                        WHEN user_id IS NOT NULL AND user_id NOT IN ({id_placeholders}) THEN 1
                        ELSE 0
                    END
                ) = 0
            """
            safe_rows = await conn.fetch(safe_query, *unique_ids, *unique_ids)
            safe_pairs = [(row[0], row[1]) for row in safe_rows]
            safe_set = set(safe_pairs)

            rows_by_id: Dict[int, Dict[str, Any]] = {}

            def _add_query_results(fetched: Iterable[Any]) -> None:
                for row in fetched:
                    record = self._export_record(row)
                    if record["id"] in rows_by_id:
                        continue
                    rows_by_id[record["id"]] = record

            if safe_pairs:
                pair_placeholders = self._pair_placeholders(len(safe_pairs))
                pair_params: List[Any] = []
                for platform, channel_id in safe_pairs:
                    pair_params.extend([platform, channel_id])
                full_thread_rows = await conn.fetch(
                    f"""
                    SELECT id, user_id, platform, channel_id, role, username, content, timestamp, metadata
                    FROM conversations
                    WHERE (platform, channel_id) IN ({pair_placeholders})
                    ORDER BY timestamp ASC
                    """,
                    *pair_params,
                )
                _add_query_results(full_thread_rows)

            owned_rows = await conn.fetch(
                f"""
                SELECT id, user_id, platform, channel_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE user_id IN ({id_placeholders})
                ORDER BY timestamp ASC
                """,
                *unique_ids,
            )
            _add_query_results(owned_rows)

            legacy_rows = await conn.fetch(
                f"""
                SELECT id, user_id, platform, channel_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE user_id IS NULL
                  AND metadata IS NOT NULL
                  AND CAST(json_extract(metadata, '$.target_user_id') AS INTEGER) IN ({id_placeholders})
                ORDER BY timestamp ASC
                """,
                *unique_ids,
            )
            _add_query_results(legacy_rows)

            threads: Dict[tuple[Any, Any], Dict[str, Any]] = {}
            for row in sorted(rows_by_id.values(), key=lambda item: item["timestamp"]):
                key = (row["platform"], row["channel_id"])
                thread = threads.get(key)
                if not thread:
                    thread = {
                        "platform": row["platform"],
                        "channel_id": row["channel_id"],
                        "full_transcript": key in safe_set,
                        "messages": [],
                        "first_timestamp": row["timestamp"],
                        "last_timestamp": row["timestamp"],
                    }
                    threads[key] = thread
                thread["messages"].append(
                    {
                        "id": row["id"],
                        "role": row["role"],
                        "username": row["username"],
                        "user_id": row["user_id"],
                        "content": row["content"],
                        "timestamp": row["timestamp"],
                        "metadata": row["metadata"],
                    }
                )
                thread["last_timestamp"] = row["timestamp"]

            ordered_threads = sorted(
                threads.values(),
                key=lambda thread: (
                    thread.get("last_timestamp", 0),
                    thread.get("platform", ""),
                    thread.get("channel_id", 0),
                ),
                reverse=True,
            )
            return {"exported_at": exported_at, "user_ids": unique_ids, "threads": ordered_threads}
        finally:
            await conn.close()

    async def get_conversation_history(
        self,
        *,
        user_id: int = None,
        platform: str = None,
        channel_id: int = None,
        surface_id: str = None,
        thread_id: str = None,
        limit: int = 50,
        include_metadata: bool = False,
    ) -> List[Dict]:
        conn = await self._brain.get_connection()
        try:
            query = """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE 1=1
                  AND platform != 'reminder_fire'
            """
            params: List[Any] = []

            if user_id is not None:
                params.append(user_id)
                query += f" AND user_id = ${len(params)}"
            if platform:
                params.append(platform)
                query += f" AND platform = ${len(params)}"
            if channel_id is not None:
                params.append(channel_id)
                query += f" AND channel_id = ${len(params)}"
            if surface_id:
                params.append(str(surface_id))
                query += f" AND surface_id = ${len(params)}"
            if thread_id:
                params.append(str(thread_id))
                query += f" AND thread_id = ${len(params)}"

            params.append(limit)
            query += f" ORDER BY timestamp DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            conversations = [
                self._conversation_record(row, include_metadata=include_metadata)
                for row in rows
            ]
            conversations.reverse()
            return conversations
        finally:
            await conn.close()

    async def get_conversations_for_day(
        self,
        *,
        day_start_ts: float,
        day_end_ts: float,
        exclude_platforms: tuple = ("reminder_fire",),
        limit: int = 400,
    ) -> List[Dict]:
        """One lived day's conversation rows, oldest first — Wave 1a
        consolidation input. READ-only; reminder fires are machinery, not
        lived conversation (the recall exclusion, 1d024e2)."""
        conn = await self._brain.get_connection()
        try:
            params: List[Any] = [float(day_start_ts), float(day_end_ts)]
            query = """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE timestamp >= $1 AND timestamp < $2
            """
            for platform in exclude_platforms or ():
                params.append(str(platform))
                query += f" AND platform != ${len(params)}"
            clean_limit = max(1, int(limit))
            head_params = list(params) + [clean_limit]
            rows = await conn.fetch(
                query + f" ORDER BY timestamp ASC LIMIT ${len(head_params)}", *head_params
            )
            if len(rows) == clean_limit:
                # Review fix: a day bigger than the cap used to lose its END —
                # the exact part the peak-and-ending law preserves. Merge in
                # the day's tail so the evening always survives.
                tail_params = list(params) + [max(1, clean_limit // 4)]
                tail = await conn.fetch(
                    query + f" ORDER BY timestamp DESC LIMIT ${len(tail_params)}", *tail_params
                )
                seen_ids = {row[0] for row in rows}
                merged = list(rows) + [row for row in tail if row[0] not in seen_ids]
                merged.sort(key=lambda row: (float(row[9] or 0.0), int(row[0] or 0)))
                rows = merged
            return [self._conversation_record(row) for row in rows]
        finally:
            await conn.close()

    async def search_conversation_content(
        self,
        *,
        terms: List[str],
        days_back: int,
        limit: int = 6,
        exclude_platforms: tuple = ("reminder_fire",),
        match_all: bool = True,
    ) -> List[Dict]:
        """Windowed verbatim search over conversation content — the introspect
        exact-mode source (Wave 1a). Plain LIKE over ~2k rows is milliseconds;
        newest first, capped. match_all=True ANDs the terms (a record-check
        for specific words); False ORs them (recall fallback)."""
        clean_terms = [str(t).strip().lower() for t in terms or [] if str(t).strip()]
        if not clean_terms:
            return []
        conn = await self._brain.get_connection()
        try:
            cutoff = self._now() - max(1, int(days_back)) * 86400.0
            params: List[Any] = [float(cutoff)]
            query = """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE timestamp >= $1
            """
            for platform in exclude_platforms or ():
                params.append(str(platform))
                query += f" AND platform != ${len(params)}"
            likes: List[str] = []
            for term in clean_terms:
                # Literal match: escape LIKE metacharacters so 'day_gist'
                # can't wildcard-match 'dayXgist' in a verbatim record-check.
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                params.append(f"%{escaped}%")
                likes.append(f"LOWER(content) LIKE ${len(params)} ESCAPE '\\'")
            if match_all:
                query += f" AND ({' AND '.join(likes)})"
                params.append(max(1, int(limit)))
                query += f" ORDER BY timestamp DESC LIMIT ${len(params)}"
            else:
                # OR mode ranks by HOW MANY terms matched, then recency —
                # never "the newest row containing any filler word".
                query += f" AND ({' OR '.join(likes)})"
                score = " + ".join(f"(CASE WHEN {like} THEN 1 ELSE 0 END)" for like in likes)
                params.append(max(1, int(limit)))
                query += f" ORDER BY ({score}) DESC, timestamp DESC LIMIT ${len(params)}"
            rows = await conn.fetch(query, *params)
            return [self._conversation_record(row) for row in rows]
        finally:
            await conn.close()

    async def get_thread_conversation_history(
        self,
        *,
        thread_id: str,
        limit: int = 50,
        include_metadata: bool = False,
        around_message_id: Optional[int] = None,
    ) -> List[Dict]:
        clean_thread_id = self._clean_optional_text(thread_id)
        clean_around_message_id = self._clean_optional_int(around_message_id)
        clean_limit = max(int(limit or 50), 1)
        if clean_around_message_id is None:
            return await self.get_conversation_history(
                thread_id=clean_thread_id,
                limit=clean_limit,
                include_metadata=include_metadata,
            )

        if not clean_thread_id:
            return []

        conn = await self._brain.get_connection()
        try:
            anchor_row = await conn.fetchrow(
                """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE thread_id = $1 AND id = $2
                LIMIT 1
                """,
                clean_thread_id,
                clean_around_message_id,
            )
            if anchor_row is None:
                return []

            anchor_record = self._conversation_record(anchor_row, include_metadata=include_metadata)
            if clean_limit == 1:
                return [anchor_record]

            context_limit = max(clean_limit - 1, 0)
            fetch_limit = context_limit + 1
            anchor_timestamp = anchor_record.get("timestamp")
            anchor_id = int(anchor_record.get("id") or clean_around_message_id)

            older_rows = await conn.fetch(
                """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE thread_id = $1
                  AND (
                    timestamp < $2
                    OR (timestamp = $2 AND id < $3)
                  )
                ORDER BY timestamp DESC, id DESC
                LIMIT $4
                """,
                clean_thread_id,
                anchor_timestamp,
                anchor_id,
                fetch_limit,
            )
            newer_rows = await conn.fetch(
                """
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE thread_id = $1
                  AND (
                    timestamp > $2
                    OR (timestamp = $2 AND id > $3)
                  )
                ORDER BY timestamp ASC, id ASC
                LIMIT $4
                """,
                clean_thread_id,
                anchor_timestamp,
                anchor_id,
                fetch_limit,
            )

            older_records_desc = [
                self._conversation_record(row, include_metadata=include_metadata)
                for row in older_rows
            ]
            newer_records_asc = [
                self._conversation_record(row, include_metadata=include_metadata)
                for row in newer_rows
            ]

            older_target = context_limit // 2
            newer_target = context_limit - older_target
            selected_older_desc = older_records_desc[:older_target]
            selected_newer_asc = newer_records_asc[:newer_target]

            remaining = context_limit - len(selected_older_desc) - len(selected_newer_asc)
            if remaining > 0:
                extra_newer = newer_records_asc[newer_target : newer_target + remaining]
                selected_newer_asc.extend(extra_newer)
                remaining -= len(extra_newer)
            if remaining > 0:
                extra_older = older_records_desc[older_target : older_target + remaining]
                selected_older_desc.extend(extra_older)

            return list(reversed(selected_older_desc)) + [anchor_record] + selected_newer_asc
        finally:
            await conn.close()

    async def search_thread_conversation_history(
        self,
        *,
        thread_ids: List[str],
        query: str = "",
        after: Optional[float] = None,
        before: Optional[float] = None,
        limit: int = 50,
        include_metadata: bool = False,
    ) -> List[Dict]:
        clean_thread_ids: List[str] = []
        seen_thread_ids: set[str] = set()
        for raw_thread_id in thread_ids:
            clean_thread_id = self._clean_optional_text(raw_thread_id)
            if not clean_thread_id or clean_thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(clean_thread_id)
            clean_thread_ids.append(clean_thread_id)
        if not clean_thread_ids:
            return []

        search_terms = [
            term
            for term in " ".join(str(query or "").lower().split()).split(" ")
            if term
        ]
        conn = await self._brain.get_connection()
        try:
            placeholders = self._placeholders(len(clean_thread_ids))
            query_text = f"""
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE thread_id IN ({placeholders})
            """
            params: List[Any] = list(clean_thread_ids)

            for term in search_terms:
                params.append(f"%{term}%")
                query_text += (
                    f" AND LOWER(COALESCE(content, '') || ' ' || COALESCE(username, '')) LIKE ${len(params)}"
                )

            if after is not None:
                params.append(float(after))
                query_text += f" AND timestamp >= ${len(params)}"
            if before is not None:
                params.append(float(before))
                query_text += f" AND timestamp <= ${len(params)}"

            params.append(max(int(limit or 50), 1))
            query_text += f" ORDER BY timestamp DESC LIMIT ${len(params)}"

            rows = await conn.fetch(query_text, *params)
            return [
                self._conversation_record(row, include_metadata=include_metadata)
                for row in rows
            ]
        finally:
            await conn.close()

    async def backfill_conversation_thread_id(
        self,
        *,
        platform: str,
        channel_id: int,
        surface_id: Optional[str] = None,
        thread_id: str,
    ) -> None:
        conn = await self._brain.get_connection()
        try:
            clean_surface_id = str(surface_id or "").strip()
            if clean_surface_id:
                await conn.execute(
                    """
                    UPDATE conversations
                    SET thread_id = $3
                    WHERE platform = $1
                      AND surface_id = $2
                      AND (thread_id IS NULL OR thread_id = '')
                    """,
                    str(platform or "").strip(),
                    clean_surface_id,
                    str(thread_id or "").strip(),
                )
                return
            await conn.execute(
                """
                UPDATE conversations
                SET thread_id = $3
                WHERE platform = $1
                  AND channel_id = $2
                  AND (thread_id IS NULL OR thread_id = '')
                """,
                str(platform or "").strip(),
                int(channel_id),
                str(thread_id or "").strip(),
            )
        finally:
            await conn.close()

    async def get_unified_dm_conversation_history(
        self,
        *,
        account_user_id: int,
        current_user_id: Optional[int] = None,
        limit: int = 50,
        include_metadata: bool = False,
    ) -> List[Dict]:
        conn = await self._brain.get_connection()
        try:
            ids: set[int] = set()
            # Seed the owner's own account id. In local mode the owner's rows
            # are stored under user_id = account_user_id (= palli_user_id), with
            # no raw Discord snowflake and no verified platform_connection —
            # without this seed a caller passing only account_user_id would get
            # 0 rows (same wrong-key blindness fixed elsewhere 2026-07-23).
            # Dormant today (no live callers); defensive so it can't regress.
            try:
                ids.add(int(account_user_id))
            except (TypeError, ValueError):
                pass

            try:
                discord_id = await conn.fetchval(
                    "SELECT discord_user_id FROM users WHERE id = $1",
                    account_user_id,
                )
            except Exception:
                discord_id = None
            if discord_id:
                try:
                    ids.add(int(discord_id))
                except (TypeError, ValueError):
                    pass

            try:
                rows = await conn.fetch(
                    """
                    SELECT platform, platform_user_id
                    FROM platform_connections
                    WHERE user_id = $1 AND verified = TRUE
                    """,
                    account_user_id,
                )
            except Exception:
                rows = []

            for row in rows:
                platform_name = str(row[0] or "").strip().lower()
                platform_user_id = row[1]
                if not platform_user_id:
                    continue
                try:
                    ids.add(int(platform_user_id))
                except (TypeError, ValueError):
                    try:
                        subject_row = await conn.fetchrow(
                            """
                            SELECT id
                            FROM platform_subjects
                            WHERE platform = $1 AND platform_user_id = $2
                            LIMIT 1
                            """,
                            platform_name,
                            str(platform_user_id),
                        )
                    except Exception:
                        subject_row = None
                    if subject_row:
                        ids.add(int(subject_row[0]))

            if current_user_id is not None:
                try:
                    ids.add(int(current_user_id))
                except (TypeError, ValueError):
                    pass

            user_ids = sorted(ids)
            if not user_ids:
                return []

            id_placeholders = self._placeholders(len(user_ids))
            query = f"""
                WITH dm_threads AS (
                    SELECT DISTINCT platform, COALESCE(surface_id, CAST(channel_id AS TEXT)) AS surface_key
                    FROM conversations
                    WHERE user_id IN ({id_placeholders})
                      AND role = 'user'
                      AND COALESCE(json_extract(metadata, '$.is_dm'), FALSE) = TRUE
                )
                SELECT id, user_id, platform, channel_id, surface_id, thread_id, role, username, content, timestamp, metadata
                FROM conversations
                WHERE user_id IN ({id_placeholders})
                  AND (platform, COALESCE(surface_id, CAST(channel_id AS TEXT))) IN (SELECT platform, surface_key FROM dm_threads)
                ORDER BY timestamp DESC
                LIMIT ${len(user_ids) * 2 + 1}
            """
            rows = await conn.fetch(query, *user_ids, *user_ids, limit)
            conversations = [
                self._conversation_record(row, include_metadata=include_metadata)
                for row in rows
            ]
            conversations.reverse()
            return conversations
        finally:
            await conn.close()
