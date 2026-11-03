"""
Advanced database management for user learning and memory
"""

import aiosqlite
import asyncio
import json
import sqlite3
import time
import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Sequence, Tuple
from datetime import datetime, timedelta
from core.account_purge_store import AccountPurgeStore
from core.app_user_store import AppUserStore
from core.account_security_store import AccountSecurityStore
from core.analytics_audit_store import AnalyticsAuditStore
from core.background_job_queue_store import BackgroundJobQueueStore
from core.account_integration_store import AccountIntegrationStore
from core.conversation_history_store import ConversationHistoryStore
from core.autonomy_control_store import AutonomyControlStore
from core.device_node_registry_store import DeviceNodeRegistryStore
from core.config import config
from core.activity_logger import activity_logger
from core.continuity_thread_store import ContinuityThreadStore
from core.media_history_store import MediaHistoryStore
from core.memory_store import MemoryStore
from core.operator_action_store import OperatorActionStore
from core.open_threads import (
    OpenThread,
    OpenThreadsService,
    ThreadKind,
    ThreadSourceKind,
    ThreadStatus,
)
from core.passive_listening_store import PassiveListeningStore
from core.platform_connection_store import PlatformConnectionStore
from core.plugin_runtime_store import PluginRuntimeStore
from core.identity.profile_store import ProfileReflectionStore
from core.runtime_artifact_store import RuntimeArtifactStore
from core.runtime_state_store import RuntimeStateStore
from core.scheduled_reminder_store import ScheduledReminderStore
from core.sqlite_database_bootstrap import (
    SQLITE_BUSY_TIMEOUT_MS,
    adapt_sqlite_schema_statement,
    configure_sqlite_connection,
    initialize_sqlite_schema,
    _sweep_stale_sqlite_probes,
)
from core.sqlite_schema_catalog import DEFAULT_SQLITE_BOOTSTRAP_PROGRAM
from core.voice_session_store import VoiceSessionStore

logger = logging.getLogger(__name__)

_JSON_COLUMNS = {
    "applied_docs",
    "action_input",
    "choices",
    "completed_steps",
    "connection_metadata",
    "frontmatter",
    "habits",
    "interpreted_answer",
    "last_completion",
    "metadata",
    "passive_context",
    "personality_data",
    "preferences",
    "communication_style",
    "emotional_patterns",
    "evidence",
    "summary",
    "scopes",
    "settings",
    "source_detail",
    "tags",
}
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def effective_database_path_setting(configured_path: Optional[str] = None) -> str:
    candidate = getattr(config, "DATABASE_PATH", "") if configured_path is None else configured_path
    clean = str(candidate or "").strip()
    return clean or "data/palli-local.sqlite3"


def resolve_database_path(configured_path: Optional[str] = None) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    resolved_path = Path(effective_database_path_setting(configured_path))
    if not resolved_path.is_absolute():
        resolved_path = repo_root / resolved_path
    return resolved_path


class SQLiteRecord:
    """Small asyncpg.Record-like wrapper around SQLite rows."""

    def __init__(self, column_names: Sequence[str], values: Sequence[Any]):
        self._column_names = tuple(column_names)
        self._values = tuple(values)
        self._mapping = {name: self._values[idx] for idx, name in enumerate(self._column_names)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, slice):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()

    def values(self):
        return self._mapping.values()

    def items(self):
        return self._mapping.items()

    def __repr__(self) -> str:
        return f"SQLiteRecord({self._mapping!r})"


class _SQLiteTransaction:
    """Transaction helper with a small subset of asyncpg-style behavior."""

    def __init__(self, conn: "_SQLiteConnection"):
        self._conn = conn
        self._savepoint_name: Optional[str] = None
        self._started = False

    async def start(self):
        if self._started:
            return
        if self._conn._transaction_depth == 0:
            await self._conn._raw.execute("BEGIN IMMEDIATE")
        else:
            self._conn._savepoint_counter += 1
            self._savepoint_name = f"sp_{self._conn._savepoint_counter}"
            await self._conn._raw.execute(f"SAVEPOINT {self._savepoint_name}")
        self._conn._transaction_depth += 1
        self._started = True

    async def commit(self):
        if not self._started:
            return
        if self._savepoint_name:
            await self._conn._raw.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
        else:
            await self._conn._raw.commit()
        self._conn._transaction_depth = max(0, self._conn._transaction_depth - 1)
        self._started = False

    async def rollback(self):
        if not self._started:
            return
        if self._savepoint_name:
            await self._conn._raw.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint_name}")
            await self._conn._raw.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
        else:
            await self._conn._raw.rollback()
        self._conn._transaction_depth = max(0, self._conn._transaction_depth - 1)
        self._started = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()


# Batch 8 Task 11 Extra-1: how long a cancelled or failed open waits for its
# aiosqlite worker. An open is a local file and a few PRAGMAs — milliseconds;
# the cap only bounds a worker stuck behind a lock, which then ends as before.
_WORKER_DRAIN_SECONDS = 1.0


async def _let_worker_finish(conn: Any) -> None:
    """Wait, while the loop is still alive, for a stopped connection's worker.

    aiosqlite's stop() queues the close and returns at once. When the open was
    cancelled because its loop is ending (asyncio.run or a test's loop
    cancelling a fire-and-forget task), the loop closed first and the worker
    then delivered the finished open to a closed loop: "Exception in thread …
    RuntimeError: Event loop is closed", blamed on whatever ran next (CI,
    09-17 to 10-01). Polling keeps the loop turning so every delivery lands
    while it is open; the thread is gone before the cancellation finishes.
    """
    thread = getattr(conn, "_thread", None)
    if thread is None or not thread.is_alive():
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WORKER_DRAIN_SECONDS
    while thread.is_alive() and loop.time() < deadline:
        await asyncio.sleep(0.005)


class _SQLiteConnection:
    """SQLite wrapper that exposes the asyncpg methods the app expects."""

    def __init__(self, brain: "PalliBrain", raw: aiosqlite.Connection):
        self._brain = brain
        self._raw = raw
        self._transaction_depth = 0
        self._savepoint_counter = 0

    @classmethod
    async def create(cls, brain: "PalliBrain") -> "_SQLiteConnection":
        pending = aiosqlite.connect(brain.db_path.as_posix())
        try:
            raw = await pending
        except BaseException:
            # Cancelled or failed inside aiosqlite's own open: it has queued
            # its stop already; wait for the worker while the loop is alive.
            await _let_worker_finish(pending)
            raise
        try:
            await configure_sqlite_connection(raw)
        except BaseException:
            # Cancelled or failed after aiosqlite started its worker thread:
            # stop that thread now. Left to the garbage collector, a stored
            # exception (a kept task's traceback) can keep the connection
            # alive forever, and its non-daemon thread then blocks the
            # process from exiting (the 2026-09-30 CI hang).
            raw.stop()
            await _let_worker_finish(raw)
            raise
        return cls(brain, raw)

    async def close(self):
        if self._raw is None:
            return
        await self._raw.close()
        self._raw = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    def transaction(self) -> _SQLiteTransaction:
        return _SQLiteTransaction(self)

    async def execute(self, query: str, *args):
        sql, params = self._brain._adapt_sql(query, args)
        cursor = await self._raw.execute(sql, params)
        if self._transaction_depth == 0:
            await self._raw.commit()
        op = self._brain._statement_type(sql)
        rowcount = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
        return f"{op} {rowcount}"

    async def fetch(self, query: str, *args) -> List[SQLiteRecord]:
        sql, params = self._brain._adapt_sql(query, args)
        cursor = await self._raw.execute(sql, params)
        rows = await cursor.fetchall()
        if self._transaction_depth == 0 and self._brain._is_write_statement(sql):
            await self._raw.commit()
        return self._brain._materialize_records(cursor.description, rows)

    async def fetchrow(self, query: str, *args) -> Optional[SQLiteRecord]:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args) -> Any:
        row = await self.fetchrow(query, *args)
        if not row:
            return None
        return row[0]


class PalliBrain:
    """Advanced database manager for PaLLi's memory and learning"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = resolve_database_path(db_path)
        self._pool = None
        self._schema_initialized = False
        # 2026-07-28 boot-race fix: a bare bool cannot serialize concurrent
        # initializers — the FastAPI lifespan's backend bootstrap and the
        # launcher's Discord component both saw False and both ran schema
        # init on separate connections (BEGIN IMMEDIATE vs BEGIN IMMEDIATE
        # -> "database is locked" -> component death -> full shutdown, the
        # 15:17 incident). Double-checked locking in initialize() below.
        self._init_lock = asyncio.Lock()
        self._account_purge_store = AccountPurgeStore(self)
        self._app_user_store = AppUserStore(self)
        self._account_security_store = AccountSecurityStore(self)
        self._analytics_audit_store = AnalyticsAuditStore(self)
        self._background_job_queue_store = BackgroundJobQueueStore(self)
        self._account_integration_store = AccountIntegrationStore(self)
        self._conversation_history_store = ConversationHistoryStore(self)
        self._continuity_thread_store = ContinuityThreadStore(self)
        self._media_history_store = MediaHistoryStore(self)
        self._memory_store = MemoryStore(self)
        self._operator_action_store = OperatorActionStore(self)
        self._autonomy_control_store = AutonomyControlStore(self)
        self._device_node_registry_store = DeviceNodeRegistryStore(self)
        self._passive_listening_store = PassiveListeningStore(self)
        self._platform_connection_store = PlatformConnectionStore(self)
        self._plugin_runtime_store = PluginRuntimeStore(self)
        self._profile_reflection_store = ProfileReflectionStore(self)
        self._runtime_artifact_store = RuntimeArtifactStore(self)
        self._runtime_state_store = RuntimeStateStore(self)
        self._scheduled_reminder_store = ScheduledReminderStore(self)
        self._voice_session_store = VoiceSessionStore(self)

    async def _open_connection(self):
        return await _SQLiteConnection.create(self)

    async def get_connection(self):
        """Get a database connection with proper handling"""
        if not self._schema_initialized:
            await self.initialize()
        return await self._open_connection()

    async def initialize(self):
        """Initialize SQLite database and ensure schema exists."""
        if not str(self.db_path).strip():
            raise ValueError("DATABASE_PATH is not set; cannot initialize database.")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if getattr(config, "DATABASE_URL", ""):
            logger.warning("DATABASE_URL is ignored in PaLLi Local; using SQLite at %s", self.db_path)

        if self._schema_initialized:
            return

        # 2026-07-28 boot-race fix: serialize concurrent initializers. Two
        # boot components (backend bootstrap + Discord client) racing this
        # path each ran schema init on its own connection — the second
        # BEGIN IMMEDIATE outlived busy_timeout and died "database is
        # locked", killing the component and (launcher policy) the whole
        # boot. Later callers now wait here, re-check, and return.
        async with self._init_lock:
            if self._schema_initialized:
                return

            # E.6 step 1: sweep any stale .palli-sqlite-probe-*.sqlite3* files left
            # behind by past inspect_sqlite_database_path() probes whose Windows WAL
            # cleanup failed silently. Runs once per bootstrap; tolerates failures.
            try:
                swept = _sweep_stale_sqlite_probes(self.db_path.parent)
                if swept:
                    logger.info(
                        "Swept %d stale .palli-sqlite-probe-* file(s) from %s",
                        swept,
                        self.db_path.parent,
                    )
            except Exception as exc:  # never block bootstrap on cleanup
                logger.debug("Stale SQLite probe sweep skipped: %s", exc)

            program = DEFAULT_SQLITE_BOOTSTRAP_PROGRAM

            conn = await self._open_connection()
            try:
                await initialize_sqlite_schema(
                    conn,
                    program=program,
                    logger=logger,
                )
            finally:
                await conn.close()

            self._schema_initialized = True
            logger.info("PaLLi Brain SQLite database initialized successfully at %s", self.db_path)

    async def close(self):
        self._pool = None

    @staticmethod
    def _statement_type(query: str) -> str:
        stripped = str(query or "").lstrip()
        if not stripped:
            return "UNKNOWN"
        return stripped.split(None, 1)[0].upper()

    def _is_write_statement(self, query: str) -> bool:
        return self._statement_type(query) in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "PRAGMA"}

    def _adapt_param(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def _materialize_records(self, description: Optional[Sequence[Tuple]], rows: Sequence[Sequence[Any]]) -> List[SQLiteRecord]:
        if not rows:
            return []
        column_names = [item[0] for item in (description or [])]
        records: List[SQLiteRecord] = []
        for row in rows:
            values: List[Any] = []
            for idx, value in enumerate(row):
                column_name = column_names[idx] if idx < len(column_names) else f"column_{idx}"
                if column_name in _JSON_COLUMNS and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except Exception:
                        pass
                values.append(value)
            records.append(SQLiteRecord(column_names, values))
        return records

    def _adapt_schema_statement(self, statement: str) -> str:
        return adapt_sqlite_schema_statement(statement)

    def _adapt_sql(self, query: str, args: Sequence[Any]) -> tuple[str, List[Any]]:
        sql = str(query or "")
        sql = sql.replace("FOR UPDATE SKIP LOCKED", "")
        sql = re.sub(r"\bFOR UPDATE\b", "", sql)
        sql = re.sub(r"::[A-Za-z_][A-Za-z0-9_\[\]]*", "", sql)
        sql = re.sub(r"([A-Za-z_][A-Za-z0-9_]*)\s*->>\s*'([^']+)'", r"json_extract(\1, '$.\2')", sql)

        translated_params: List[Any] = []

        def _replace_placeholder(match: re.Match[str]) -> str:
            param_index = int(match.group(1)) - 1
            translated_params.append(self._adapt_param(args[param_index]))
            return "?"

        sql = _PG_PLACEHOLDER_RE.sub(_replace_placeholder, sql)
        return sql, translated_params

    @staticmethod
    def _rowcount(status: str) -> int:
        if not status:
            return 0
        parts = status.split()
        if not parts:
            return 0
        last = parts[-1]
        return int(last) if last.isdigit() else 0

    @staticmethod
    def _placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"${i}" for i in range(start, start + count))

    @staticmethod
    def _pair_placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"(${i}, ${i + 1})" for i in range(start, start + count * 2, 2))

    @staticmethod
    def _load_json(raw: Any, fallback: Any):
        if raw is None:
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return fallback
    
    async def store_pairing_code(self, code: str, user_id: int, expires_at: float):
        """Store a temporary pairing code."""
        await self._device_node_registry_store.store_pairing_code(code, user_id, expires_at)

    # ── open threads (T5 — agenda rebuild 2026-05-10) ────────────────────
    #
    # ``OpenThreadsService`` is sync (raw sqlite3.Connection) per T4. The
    # rest of PaLLi consumes it through these async wrappers so the brain
    # stays the canonical entry point. We open a fresh sqlite3 connection
    # inside ``asyncio.to_thread`` for each call, in autocommit mode
    # (``isolation_level = None``) — matches the T4 contract that callers
    # own commit, mirrors the test fixture in tests/core/test_open_threads.py.
    # Schema is ensured via ``await self.initialize()`` first, same lazy
    # pattern that ``get_connection()`` uses for the async path.

    def _run_open_threads_sync(self, fn):
        """Open a sync sqlite3 connection bound to OpenThreadsService and run ``fn``.

        Used as the ``asyncio.to_thread`` body for every open_threads wrapper.
        Autocommit mode is required: the service does not commit (T4 fix
        ``5c35c54``), so the connection's per-statement autocommit is what
        makes writes durable.
        """
        conn = sqlite3.connect(self.db_path.as_posix())
        conn.isolation_level = None  # autocommit; caller owns commit per T4
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")  # match async-path config (sqlite_database_bootstrap.configure_sqlite_connection)
        try:
            return fn(OpenThreadsService(conn))
        finally:
            conn.close()

    async def add_open_thread(
        self,
        *,
        owner_user_id: int,
        topic_summary: str,
        why_it_matters: str,
        kind: "ThreadKind | str",
        urgency: str,
        source_id: "str | None" = None,
    ) -> str:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.add_thread(
                owner_user_id=owner_user_id,
                topic_summary=topic_summary,
                why_it_matters=why_it_matters,
                kind=kind,
                urgency=urgency,
                source_id=source_id,
            ),
        )

    async def expire_stale_open_threads(
        self,
        *,
        owner_user_id: int,
        followup_ttl_days: float,
        thread_ttl_days: float,
    ) -> "List[tuple]":
        await self.initialize()
        import time as _time

        _now = _time.time()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.expire_stale(
                owner_user_id=owner_user_id,
                now=_now,
                followup_ttl_days=followup_ttl_days,
                thread_ttl_days=thread_ttl_days,
            ),
        )

    async def close_open_thread(
        self,
        *,
        thread_id: str,
        resolution_reason: str,
        status: ThreadStatus,
    ) -> None:
        await self.initialize()
        await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.close_thread(
                thread_id=thread_id,
                resolution_reason=resolution_reason,
                status=status,
            ),
        )

    async def list_active_open_threads(
        self,
        *,
        owner_user_id: int,
    ) -> List[OpenThread]:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.list_active(owner_user_id=owner_user_id),
        )

    async def mark_open_thread_surfaced(
        self,
        *,
        thread_id: str,
    ) -> None:
        await self.initialize()
        await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.mark_surfaced(thread_id=thread_id),
        )

    async def list_recently_surfaced_open_threads(
        self,
        *,
        owner_user_id: int,
        since: float,
    ) -> List[OpenThread]:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.list_recently_surfaced(
                owner_user_id=owner_user_id,
                since=since,
            ),
        )

    # ── Batch 5, task 8 (2026-09-28): the reversible retirement ─────────
    # See ``OpenThreadsService.retire_unsurfaced`` / ``unretire`` /
    # ``list_dismissed``. The night pass retires; palli_ctl lists and
    # unretires.

    async def retire_unsurfaced_open_threads(
        self,
        *,
        owner_user_id: int,
        older_than_days: float,
    ) -> int:
        await self.initialize()
        import time as _time

        _now = _time.time()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.retire_unsurfaced(
                owner_user_id=owner_user_id,
                older_than_days=older_than_days,
                now=_now,
            ),
        )

    async def unretire_open_thread(
        self,
        *,
        thread_id: str,
    ) -> bool:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.unretire(thread_id=thread_id),
        )

    async def list_dismissed_open_threads(
        self,
        *,
        owner_user_id: int,
    ) -> List[OpenThread]:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.list_dismissed(owner_user_id=owner_user_id),
        )

    # ── C1.5: source-kind-aware wrappers (B5 Decision 5) ────────────────
    #
    # These power the absorbed-table read/write paths (e.g.,
    # ``proactive_record`` rows replacing the legacy ``proactive_actions``
    # table). Async surface mirrors the topic-flow above.

    async def add_source_open_thread(
        self,
        **kwargs: Any,
    ) -> str:
        """Insert an open_threads row with a non-default source_kind.

        Used by the ``proactive_actions`` rewire (C1.5) and future
        absorbed-table writers. See ``OpenThreadsService.add_source_thread``
        for the argument shape.
        """
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.add_source_thread(**kwargs),
        )

    async def list_open_threads_by_source_kind(
        self,
        *,
        owner_user_id: int,
        source_kind: "ThreadSourceKind | str",
        limit: int = 20,
    ) -> List[OpenThread]:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.list_by_source_kind(
                owner_user_id=owner_user_id,
                source_kind=source_kind,
                limit=limit,
            ),
        )

    async def count_open_threads_by_source_kind_since(
        self,
        *,
        owner_user_id: int,
        source_kind: "ThreadSourceKind | str",
        since: float,
        payload_action_types: Optional[List[str]] = None,
    ) -> int:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.count_by_source_kind_since(
                owner_user_id=owner_user_id,
                source_kind=source_kind,
                since=since,
                payload_action_types=payload_action_types,
            ),
        )

    async def max_last_surfaced_open_thread_by_source_kind(
        self,
        *,
        owner_user_id: int,
        source_kind: "ThreadSourceKind | str",
        since: float,
        payload_action_types: Optional[List[str]] = None,
    ) -> Optional[float]:
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.max_last_surfaced_by_source_kind(
                owner_user_id=owner_user_id,
                source_kind=source_kind,
                since=since,
                payload_action_types=payload_action_types,
            ),
        )

    # ── C8: source-id-keyed CRUD wrappers (B5 Decision 5) ───────────────
    # Used by the standing_orders rewire to update absorbed rows by stable
    # source_id. Future absorbs (followthrough) reuse the same surface.

    async def get_open_thread_by_source_id(
        self,
        *,
        owner_user_id: int,
        source_kind: "ThreadSourceKind | str",
        source_id: str,
    ):
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.get_by_source_id(
                owner_user_id=owner_user_id,
                source_kind=source_kind,
                source_id=source_id,
            ),
        )

    async def get_open_thread_by_source_id_unscoped(
        self,
        *,
        source_kind: "ThreadSourceKind | str",
        source_id: str,
    ):
        """Owner-less variant — for legacy unscoped lookups (e.g., the
        standing_order_id legacy contract). New code should pass owner."""
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.get_by_source_id_unscoped(
                source_kind=source_kind,
                source_id=source_id,
            ),
        )

    async def get_recurring_open_thread_unscoped(
        self,
        *,
        source_id: str,
    ):
        """Convenience: get_open_thread_by_source_id_unscoped fixed to RECURRING.

        Used by autonomy_control_store.get_standing_order which doesn't
        have the owner_user_id at the call site (legacy contract).
        """
        return await self.get_open_thread_by_source_id_unscoped(
            source_kind="recurring",
            source_id=source_id,
        )

    async def get_followthrough_open_thread_unscoped(
        self,
        *,
        source_id: str,
    ):
        """Convenience: get_open_thread_by_source_id_unscoped fixed to FOLLOWTHROUGH.

        Used by operator_continuity_store.get_operator_followthrough_item
        which is called with only ``followthrough_id`` (legacy contract is
        owner-less; the source_id has globally-unique ``ft_<uuid>``
        prefix-randomness so unscoped lookup is safe).
        """
        return await self.get_open_thread_by_source_id_unscoped(
            source_kind="followthrough",
            source_id=source_id,
        )

    async def update_source_open_thread(
        self,
        **kwargs: Any,
    ) -> bool:
        """Update an absorbed row by source_id. See ``OpenThreadsService.update_source_thread``."""
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.update_source_thread(**kwargs),
        )

    async def list_due_open_threads_by_source_kind(
        self,
        **kwargs: Any,
    ):
        """List due rows of a source_kind. See ``OpenThreadsService.list_due_by_source_kind``."""
        await self.initialize()
        return await asyncio.to_thread(
            self._run_open_threads_sync,
            lambda svc: svc.list_due_by_source_kind(**kwargs),
        )

    # ── sentiment engine (Phase 3) ──────────────────────────────────────

    async def get_user_sentiment_state(self, user_id: int):
        """Return the current SentimentReading for user. Always valid —
        bootstrap default on missing, is_stale=True on rows older than
        config.SENTIMENT_STALENESS_HOURS."""
        from ai.sentiment.store import get_current_reading
        return await get_current_reading(brain=self, user_id=int(user_id))

    async def update_user_sentiment_state(self, user_id: int, *, reading) -> None:
        """Upsert the current sentiment reading for a user. Last write wins."""
        from ai.sentiment.store import update_current_reading
        await update_current_reading(
            brain=self, user_id=int(user_id), reading=reading,
        )

    async def get_user_sentiment_trend(self, user_id: int, *, hours: int = 168):
        """Return TrendSummary aggregating monologue JSONL sentiment history
        over the given window (default 168h / 7 days). Pure file read + dict
        aggregation, no LLM call."""
        from ai.sentiment.store import get_trend
        return await get_trend(brain=self, user_id=int(user_id), hours=hours)

    async def get_user_from_pairing_code(self, code: str) -> Optional[int]:
        """Get user_id from pairing code if valid, then delete it (one-time use)."""
        return await self._device_node_registry_store.get_user_from_pairing_code(code)

    async def queue_job(self, job_type: str, payload: Dict[str, Any]) -> int:
        """Queue a background job."""
        return await self._background_job_queue_store.queue_job(job_type=job_type, payload=payload)

    async def pop_job(self, job_type: str = None) -> Optional[Dict]:
        """Get and lock the next pending job (simple implementation)."""
        return await self._background_job_queue_store.pop_job(job_type=job_type)

    async def upsert_runtime_session(
        self,
        *,
        session_key: str,
        owner_user_id: int,
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        session_type: str,
        platform: str,
        channel_id: Optional[int],
        surface_id: Optional[str] = None,
        status: str,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        last_activity: Optional[float] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        await self._runtime_state_store.upsert_runtime_session(
            session_key=session_key,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            thread_kind=thread_kind,
            thread_label=thread_label,
            session_type=session_type,
            platform=platform,
            channel_id=channel_id,
            surface_id=surface_id,
            status=status,
            label=label,
            metadata=metadata,
            last_activity=last_activity,
            archived_at=archived_at,
        )

    async def update_runtime_session(
        self,
        *,
        session_key: str,
        thread_id: Optional[str] = None,
        thread_kind: Optional[str] = None,
        thread_label: Optional[str] = None,
        status: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        last_activity: Optional[float] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        await self._runtime_state_store.update_runtime_session(
            session_key=session_key,
            thread_id=thread_id,
            thread_kind=thread_kind,
            thread_label=thread_label,
            status=status,
            label=label,
            metadata=metadata,
            last_activity=last_activity,
            archived_at=archived_at,
        )

    async def get_runtime_session(self, session_key: str) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_runtime_session(session_key)

    async def list_runtime_sessions(
        self,
        *,
        owner_user_id: Optional[int] = None,
        statuses: Optional[List[str]] = None,
        session_types: Optional[List[str]] = None,
        limit: int = 24,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self._runtime_state_store.list_runtime_sessions(
            owner_user_id=owner_user_id,
            statuses=statuses,
            session_types=session_types,
            limit=limit,
            include_archived=include_archived,
        )

    async def upsert_continuity_thread(
        self,
        *,
        thread_id: str,
        owner_user_id: int,
        thread_kind: str,
        status: str = "active",
        label: str = "",
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        root_agent_session_key: Optional[str] = None,
        last_runtime_session_key: Optional[str] = None,
        last_workflow_id: Optional[str] = None,
        reset_count: int = 0,
        last_activity: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        await self._continuity_thread_store.upsert_continuity_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            thread_kind=thread_kind,
            status=status,
            label=label,
            workspace_root=workspace_root,
            workspace_label=workspace_label,
            root_agent_session_key=root_agent_session_key,
            last_runtime_session_key=last_runtime_session_key,
            last_workflow_id=last_workflow_id,
            reset_count=reset_count,
            last_activity=last_activity,
            metadata=metadata,
            archived_at=archived_at,
        )

    async def update_continuity_thread(
        self,
        *,
        thread_id: str,
        status: Optional[str] = None,
        label: Optional[str] = None,
        workspace_root: Optional[str] = None,
        workspace_label: Optional[str] = None,
        root_agent_session_key: Optional[str] = None,
        last_runtime_session_key: Optional[str] = None,
        last_workflow_id: Optional[str] = None,
        reset_count: Optional[int] = None,
        last_activity: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        archived_at: Optional[float] = None,
    ) -> None:
        await self._continuity_thread_store.update_continuity_thread(
            thread_id=thread_id,
            status=status,
            label=label,
            workspace_root=workspace_root,
            workspace_label=workspace_label,
            root_agent_session_key=root_agent_session_key,
            last_runtime_session_key=last_runtime_session_key,
            last_workflow_id=last_workflow_id,
            reset_count=reset_count,
            last_activity=last_activity,
            metadata=metadata,
            archived_at=archived_at,
        )

    async def get_continuity_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        return await self._continuity_thread_store.get_continuity_thread(thread_id)

    async def find_continuity_thread_by_scope(
        self,
        *,
        owner_user_id: int,
        thread_kind: str,
        scope_key: str,
        include_archived: bool = False,
    ) -> Optional[Dict[str, Any]]:
        return await self._continuity_thread_store.find_continuity_thread_by_scope(
            owner_user_id=owner_user_id,
            thread_kind=thread_kind,
            scope_key=scope_key,
            include_archived=include_archived,
        )

    async def list_continuity_threads(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 24,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self._continuity_thread_store.list_continuity_threads(
            owner_user_id=owner_user_id,
            statuses=statuses,
            limit=limit,
            include_archived=include_archived,
        )

    async def store_verification_code(
        self,
        user_id: int,
        platform: str,
        platform_user_id: str,
        code: str,
        ttl_seconds: int = 600,
    ) -> bool:
        return await self._platform_connection_store.store_verification_code(
            user_id=user_id,
            platform=platform,
            platform_user_id=platform_user_id,
            code=code,
            ttl_seconds=ttl_seconds,
        )

    async def resolve_platform_subject_id(
        self,
        *,
        platform: str,
        platform_user_id: str,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        return await self._platform_connection_store.resolve_platform_subject_id(
            platform=platform,
            platform_user_id=platform_user_id,
            display_name=display_name,
            metadata=metadata,
        )

    async def get_platform_subject(
        self,
        *,
        platform: str,
        platform_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self._platform_connection_store.get_platform_subject(
            platform=platform,
            platform_user_id=platform_user_id,
        )

    async def resolve_platform_surface_id(
        self,
        *,
        platform: str,
        surface_id: str,
        surface_kind: str = "channel",
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        return await self._platform_connection_store.resolve_platform_surface_id(
            platform=platform,
            surface_id=surface_id,
            surface_kind=surface_kind,
            label=label,
            metadata=metadata,
        )

    async def get_platform_surface(
        self,
        *,
        platform: str,
        surface_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self._platform_connection_store.get_platform_surface(
            platform=platform,
            surface_id=surface_id,
        )

    async def verify_platform_connection(
        self,
        platform: str,
        platform_user_id: str,
        code: str,
    ) -> Optional[int]:
        return await self._platform_connection_store.verify_platform_connection(
            platform=platform,
            platform_user_id=platform_user_id,
            code=code,
        )

    async def get_verified_connection(self, platform: str, platform_user_id: str) -> Optional[int]:
        return await self._platform_connection_store.get_verified_connection(
            platform=platform,
            platform_user_id=platform_user_id,
        )

    async def link_verified_platform_connection(
        self,
        *,
        user_id: int,
        platform: str,
        platform_user_id: str,
        platform_username: str = "",
        display_name: str = "",
        connection_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        return await self._platform_connection_store.link_verified_platform_connection(
            user_id=user_id,
            platform=platform,
            platform_user_id=platform_user_id,
            platform_username=platform_username,
            display_name=display_name,
            connection_metadata=connection_metadata,
        )

    async def create_runtime_artifact(
        self,
        *,
        artifact_id: str,
        owner_user_id: int,
        source_kind: str,
        artifact_kind: str,
        storage_path: str,
        source_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
        source_action_id: Optional[str] = None,
        surface_id: Optional[str] = None,
        status: str = "ready",
        content_type: Optional[str] = None,
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        await self._runtime_artifact_store.create_runtime_artifact(
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            source_kind=source_kind,
            artifact_kind=artifact_kind,
            storage_path=storage_path,
            source_id=source_id,
            source_run_id=source_run_id,
            source_action_id=source_action_id,
            surface_id=surface_id,
            status=status,
            content_type=content_type,
            expires_at=expires_at,
            metadata=metadata,
            created_at=created_at,
        )

    async def get_runtime_artifact(self, *, artifact_id: str) -> Optional[Dict[str, Any]]:
        return await self._runtime_artifact_store.get_runtime_artifact(
            artifact_id=artifact_id,
        )

    async def list_runtime_artifacts(
        self,
        *,
        owner_user_id: int,
        source_kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        return await self._runtime_artifact_store.list_runtime_artifacts(
            owner_user_id=owner_user_id,
            source_kind=source_kind,
            status=status,
            limit=limit,
        )

    async def mark_runtime_artifact_delivered(
        self,
        *,
        artifact_id: str,
        surface_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_artifact_store.mark_runtime_artifact_delivered(
            artifact_id=artifact_id,
            surface_id=surface_id,
            metadata=metadata,
        )

    async def extend_runtime_artifact_expiry(
        self,
        *,
        artifact_id: str,
        expires_at: Optional[float] = None,
        extend_seconds: Optional[float] = None,
    ) -> None:
        await self._runtime_artifact_store.extend_runtime_artifact_expiry(
            artifact_id=artifact_id,
            expires_at=expires_at,
            extend_seconds=extend_seconds,
        )

    async def delete_runtime_artifact(self, *, artifact_id: str) -> None:
        await self._runtime_artifact_store.delete_runtime_artifact(
            artifact_id=artifact_id,
        )

    async def upsert_platform_space_connection(
        self,
        *,
        platform: str,
        space_id: str,
        account_user_id: int,
        space_kind: str = "channel",
        default_response_mode: str = "mention_or_reply",
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        return await self._platform_connection_store.upsert_platform_space_connection(
            platform=platform,
            space_id=space_id,
            account_user_id=account_user_id,
            space_kind=space_kind,
            default_response_mode=default_response_mode,
            label=label,
            metadata=metadata,
        )

    async def get_platform_space_connection(
        self,
        *,
        platform: str,
        space_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self._platform_connection_store.get_platform_space_connection(
            platform=platform,
            space_id=space_id,
        )

    async def list_platform_space_connections(
        self,
        *,
        account_user_id: int,
        platform: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await self._platform_connection_store.list_platform_space_connections(
            account_user_id=account_user_id,
            platform=platform,
        )

    async def link_platform(
        self,
        user_id: int,
        platform: str,
        platform_user_id: str,
        platform_username: str = None,
        display_name: str = None,
        verification_code: str = None,
        verification_expires_at: float = None,
    ) -> int:
        return await self._platform_connection_store.link_platform(
            user_id=user_id,
            platform=platform,
            platform_user_id=platform_user_id,
            platform_username=platform_username,
            display_name=display_name,
            verification_code=verification_code,
            verification_expires_at=verification_expires_at,
        )

    async def get_platform_connections(self, user_id: int) -> List[Dict[str, Any]]:
        return await self._platform_connection_store.get_platform_connections(user_id=user_id)

    async def link_discord_guild_to_account(
        self,
        guild_id: int,
        account_user_id: int,
        default_channel_id: Optional[int] = None,
    ) -> int:
        return await self._account_integration_store.link_discord_guild_to_account(
            guild_id=guild_id,
            account_user_id=account_user_id,
            default_channel_id=default_channel_id,
        )

    async def get_account_for_discord_guild(self, guild_id: int) -> Optional[Dict]:
        return await self._account_integration_store.get_account_for_discord_guild(guild_id)

    async def get_discord_guilds_for_account(self, account_user_id: int) -> List[Dict]:
        return await self._account_integration_store.get_discord_guilds_for_account(account_user_id)

    async def upsert_account_features(
        self,
        account_user_id: int,
        **feature_flags: bool,
    ) -> int:
        return await self._account_integration_store.upsert_account_features(
            account_user_id,
            **feature_flags,
        )

    async def get_account_features(self, account_user_id: int) -> Dict[str, bool]:
        return await self._account_integration_store.get_account_features(account_user_id)

    async def store_oauth_connection(
        self,
        user_id: int,
        provider: str,
        provider_user_id: str = None,
        provider_email: str = None,
        access_token: str = None,
        refresh_token: str = None,
        token_expires_at: float = None,
        scopes: List[str] = None,
    ) -> int:
        return await self._account_integration_store.store_oauth_connection(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_email=provider_email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            scopes=scopes,
        )

    async def get_oauth_connections(self, user_id: int) -> List[Dict]:
        return await self._account_integration_store.get_oauth_connections(user_id)

    async def update_onboarding_progress(
        self,
        user_id: int,
        current_step: str,
        completed_steps: List[str],
    ) -> Dict:
        return await self._account_integration_store.update_onboarding_progress(
            user_id=user_id,
            current_step=current_step,
            completed_steps=completed_steps,
        )

    async def get_onboarding_progress(self, user_id: int) -> Optional[Dict]:
        return await self._account_integration_store.get_onboarding_progress(user_id)

    async def get_scheduled_reminder_record(self, reminder_id: int) -> Optional[Dict[str, Any]]:
        return await self._scheduled_reminder_store.get_scheduled_reminder_record(reminder_id)

    async def list_user_scheduled_reminders(
        self,
        *,
        user_id: int,
        platform: str,
    ) -> List[Dict[str, Any]]:
        return await self._scheduled_reminder_store.list_user_scheduled_reminders(
            user_id=user_id,
            platform=platform,
        )

    async def list_open_scheduled_reminders(self, *, limit: int = 128) -> List[Dict[str, Any]]:
        return await self._scheduled_reminder_store.list_open_scheduled_reminders(limit=limit)

    async def list_due_scheduled_reminders(
        self,
        *,
        as_of: float,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return await self._scheduled_reminder_store.list_due_scheduled_reminders(
            as_of=as_of,
            limit=limit,
        )

    async def mark_scheduled_reminder_completed(
        self, *, reminder_id: int, user_id: Optional[int] = None,
    ) -> int:
        # Batch 4 task 4 (2026-09-28, ruling R8): forward user_id only when
        # given, so the pinned delegation test (kwargs == {"reminder_id": N})
        # and the system paths that call this with no owner stay unchanged.
        if user_id is None:
            return await self._scheduled_reminder_store.mark_scheduled_reminder_completed(
                reminder_id=reminder_id,
            )
        return await self._scheduled_reminder_store.mark_scheduled_reminder_completed(
            reminder_id=reminder_id, user_id=user_id,
        )

    async def merge_scheduled_reminder_metadata(
        self,
        *,
        reminder_id: int,
        patch: Dict[str, Any],
    ) -> None:
        await self._scheduled_reminder_store.merge_scheduled_reminder_metadata(
            reminder_id=reminder_id,
            patch=patch,
        )

    async def update_scheduled_reminder_schedule(
        self,
        *,
        reminder_id: int,
        scheduled_time: float,
        user_id: Optional[int] = None,
    ) -> int:
        # Same forwarding rule as mark_scheduled_reminder_completed above.
        if user_id is None:
            return await self._scheduled_reminder_store.update_scheduled_reminder_schedule(
                reminder_id=reminder_id,
                scheduled_time=scheduled_time,
            )
        return await self._scheduled_reminder_store.update_scheduled_reminder_schedule(
            reminder_id=reminder_id,
            scheduled_time=scheduled_time,
            user_id=user_id,
        )

    async def update_scheduled_reminder_runtime_link(
        self,
        *,
        reminder_id: int,
        runtime_job_id: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
    ) -> None:
        await self._scheduled_reminder_store.update_scheduled_reminder_runtime_link(
            reminder_id=reminder_id,
            runtime_job_id=runtime_job_id,
            runtime_session_key=runtime_session_key,
        )

    async def create_runtime_job(
        self,
        *,
        job_id: str,
        owner_user_id: int,
        runtime_session_key: str,
        job_type: str,
        subtype: str = "",
        status: str,
        request_text: str = "",
        scheduled_for: Optional[float] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.create_runtime_job(
            job_id=job_id,
            owner_user_id=owner_user_id,
            runtime_session_key=runtime_session_key,
            job_type=job_type,
            subtype=subtype,
            status=status,
            request_text=request_text,
            scheduled_for=scheduled_for,
            source_table=source_table,
            source_id=source_id,
            last_transition_reason=last_transition_reason,
            retry_of_job_id=retry_of_job_id,
            replacement_job_id=replacement_job_id,
            metadata=metadata,
        )

    async def update_runtime_job(
        self,
        *,
        job_id: str,
        status: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
        request_text: Optional[str] = None,
        scheduled_for: Optional[float] = None,
        started_at: Optional[float] = None,
        completed_at: Optional[float] = None,
        attempt_count: Optional[int] = None,
        increment_attempt: bool = False,
        last_error: Optional[str] = None,
        last_transition_reason: Optional[str] = None,
        retry_of_job_id: Optional[str] = None,
        replacement_job_id: Optional[str] = None,
        source_table: Optional[str] = None,
        source_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.update_runtime_job(
            job_id=job_id,
            status=status,
            runtime_session_key=runtime_session_key,
            request_text=request_text,
            scheduled_for=scheduled_for,
            started_at=started_at,
            completed_at=completed_at,
            attempt_count=attempt_count,
            increment_attempt=increment_attempt,
            last_error=last_error,
            last_transition_reason=last_transition_reason,
            retry_of_job_id=retry_of_job_id,
            replacement_job_id=replacement_job_id,
            source_table=source_table,
            source_id=source_id,
            metadata=metadata,
        )

    async def get_runtime_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_runtime_job(job_id)

    async def get_runtime_job_by_source(self, *, source_table: str, source_id: str) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_runtime_job_by_source(
            source_table=source_table,
            source_id=source_id,
        )

    async def list_runtime_jobs(
        self,
        *,
        owner_user_id: Optional[int] = None,
        statuses: Optional[List[str]] = None,
        job_types: Optional[List[str]] = None,
        limit: int = 32,
    ) -> List[Dict[str, Any]]:
        return await self._runtime_state_store.list_runtime_jobs(
            owner_user_id=owner_user_id,
            statuses=statuses,
            job_types=job_types,
            limit=limit,
        )

    async def create_context_event(
        self,
        *,
        event_id: str,
        owner_user_id: int,
        source: str,
        event_type: str,
        occurred_at: float,
        dedupe_key: Optional[str] = None,
        runtime_session_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.create_context_event(
            event_id=event_id,
            owner_user_id=owner_user_id,
            source=source,
            event_type=event_type,
            occurred_at=occurred_at,
            dedupe_key=dedupe_key,
            runtime_session_key=runtime_session_key,
            payload=payload,
            metadata=metadata,
        )

    async def get_recent_context_event_by_dedupe(
        self,
        *,
        owner_user_id: int,
        dedupe_key: str,
        since: float,
    ) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_recent_context_event_by_dedupe(
            owner_user_id=owner_user_id,
            dedupe_key=dedupe_key,
            since=since,
        )

    async def list_context_events(
        self,
        *,
        owner_user_id: int,
        event_types: Optional[List[str]] = None,
        limit: int = 32,
    ) -> List[Dict[str, Any]]:
        return await self._runtime_state_store.list_context_events(
            owner_user_id=owner_user_id,
            event_types=event_types,
            limit=limit,
        )

    async def upsert_home_context_state(
        self,
        *,
        owner_user_id: int,
        owner_presence_state: str,
        machine_state: str,
        privacy_state: str,
        quiet_hours_active: bool,
        last_arrival_at: Optional[float] = None,
        last_departure_at: Optional[float] = None,
        last_machine_active_at: Optional[float] = None,
        last_event_at: Optional[float] = None,
        source_health: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.upsert_home_context_state(
            owner_user_id=owner_user_id,
            owner_presence_state=owner_presence_state,
            machine_state=machine_state,
            privacy_state=privacy_state,
            quiet_hours_active=quiet_hours_active,
            last_arrival_at=last_arrival_at,
            last_departure_at=last_departure_at,
            last_machine_active_at=last_machine_active_at,
            last_event_at=last_event_at,
            source_health=source_health,
            metadata=metadata,
        )

    async def get_home_context_state(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_home_context_state(owner_user_id=owner_user_id)

    async def create_ambient_action(
        self,
        *,
        action_id: str,
        owner_user_id: int,
        source_event_id: Optional[str],
        runtime_job_id: Optional[str],
        action_type: str,
        status: str,
        delivery_surface: str,
        decision_reason: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.create_ambient_action(
            action_id=action_id,
            owner_user_id=owner_user_id,
            source_event_id=source_event_id,
            runtime_job_id=runtime_job_id,
            action_type=action_type,
            status=status,
            delivery_surface=delivery_surface,
            decision_reason=decision_reason,
            suppression_reason=suppression_reason,
            content=content,
            metadata=metadata,
        )

    async def update_ambient_action(
        self,
        *,
        action_id: str,
        status: Optional[str] = None,
        runtime_job_id: Optional[str] = None,
        delivery_surface: Optional[str] = None,
        decision_reason: Optional[str] = None,
        suppression_reason: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        delivered_at: Optional[float] = None,
        failed_at: Optional[float] = None,
    ) -> None:
        await self._runtime_state_store.update_ambient_action(
            action_id=action_id,
            status=status,
            runtime_job_id=runtime_job_id,
            delivery_surface=delivery_surface,
            decision_reason=decision_reason,
            suppression_reason=suppression_reason,
            content=content,
            metadata=metadata,
            delivered_at=delivered_at,
            failed_at=failed_at,
        )

    async def list_ambient_actions(
        self,
        *,
        owner_user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 24,
    ) -> List[Dict[str, Any]]:
        return await self._runtime_state_store.list_ambient_actions(
            owner_user_id=owner_user_id,
            statuses=statuses,
            limit=limit,
        )

    async def upsert_operator_state(
        self,
        *,
        owner_user_id: int,
        pinned_workspace_root: Optional[str] = None,
        pinned_workspace_label: Optional[str] = None,
        pinned_remote_name: Optional[str] = None,
        current_workspace_root: Optional[str] = None,
        current_workspace_label: Optional[str] = None,
        current_remote_name: Optional[str] = None,
        last_operator_action_id: Optional[str] = None,
        last_operator_activity_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._runtime_state_store.upsert_operator_state(
            owner_user_id=owner_user_id,
            pinned_workspace_root=pinned_workspace_root,
            pinned_workspace_label=pinned_workspace_label,
            pinned_remote_name=pinned_remote_name,
            current_workspace_root=current_workspace_root,
            current_workspace_label=current_workspace_label,
            current_remote_name=current_remote_name,
            last_operator_action_id=last_operator_action_id,
            last_operator_activity_at=last_operator_activity_at,
            metadata=metadata,
        )

    async def get_operator_state(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        return await self._runtime_state_store.get_operator_state(owner_user_id=owner_user_id)

    async def update_operator_action(self, **kwargs: Any) -> None:
        await self._operator_action_store.update_operator_action(**kwargs)

    async def store_conversation(
        self,
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
        activity_logger.log_database_operation("store_conversation", f"user {user_id}, {role} message")
        return await self._conversation_history_store.store_conversation(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            role=role,
            content=content,
            username=username,
            metadata=metadata,
            surface_id=surface_id,
        )

    async def export_user_conversations(self, user_ids: List[int]) -> Dict[str, Any]:
        return await self._conversation_history_store.export_user_conversations(user_ids)

    async def get_user_profile(self, user_id: int, platform: str) -> Dict:
        """Get comprehensive user profile."""
        return await self._profile_reflection_store.get_user_profile(user_id, platform)

    async def update_user_profile(self, user_id: int, platform: str, updates: Dict):
        """Update user profile with new learning data."""
        await self._profile_reflection_store.update_user_profile(user_id, platform, updates)

    async def _update_user_profile_internal(
        self, conn, user_id: int, platform: str, updates: Dict
    ):
        """Internal method to update user profile within an existing connection."""
        await self._profile_reflection_store._update_user_profile_internal(
            conn,
            user_id,
            platform,
            updates,
        )

    async def get_conversation_history(
        self,
        user_id: int = None,
        platform: str = None,
        channel_id: int = None,
        surface_id: str = None,
        thread_id: str = None,
        limit: int = 50,
        include_metadata: bool = False,
    ) -> List[Dict]:
        return await self._conversation_history_store.get_conversation_history(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            surface_id=surface_id,
            thread_id=thread_id,
            limit=limit,
            include_metadata=include_metadata,
        )

    async def get_thread_conversation_history(
        self,
        *,
        thread_id: str,
        limit: int = 50,
        include_metadata: bool = False,
        around_message_id: Optional[int] = None,
    ) -> List[Dict]:
        return await self._conversation_history_store.get_thread_conversation_history(
            thread_id=thread_id,
            limit=limit,
            include_metadata=include_metadata,
            around_message_id=around_message_id,
        )

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
        return await self._conversation_history_store.search_thread_conversation_history(
            thread_ids=thread_ids,
            query=query,
            after=after,
            before=before,
            limit=limit,
            include_metadata=include_metadata,
        )

    async def backfill_conversation_thread_id(
        self,
        *,
        platform: str,
        channel_id: int,
        surface_id: Optional[str] = None,
        thread_id: str,
    ) -> None:
        await self._conversation_history_store.backfill_conversation_thread_id(
            platform=platform,
            channel_id=channel_id,
            surface_id=surface_id,
            thread_id=thread_id,
        )

    async def get_unified_dm_conversation_history(
        self,
        account_user_id: int,
        current_user_id: Optional[int] = None,
        limit: int = 50,
        include_metadata: bool = False,
    ) -> List[Dict]:
        return await self._conversation_history_store.get_unified_dm_conversation_history(
            account_user_id=account_user_id,
            current_user_id=current_user_id,
            limit=limit,
            include_metadata=include_metadata,
        )

    async def get_conversations_for_day(self, **kwargs) -> List[Dict]:
        """Wave 1a consolidation input — one lived day's rows, oldest first."""
        return await self._conversation_history_store.get_conversations_for_day(**kwargs)

    async def search_conversation_content(self, **kwargs) -> List[Dict]:
        """Wave 1a introspect exact-mode source — windowed verbatim search."""
        return await self._conversation_history_store.search_conversation_content(**kwargs)

    async def store_user_memory(
        self,
        user_id: int,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        tags: List[str] = None,
        salience_weight: float = 1.0,
        source_detail: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        memory_id = await self._memory_store.store_user_memory(
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags,
            salience_weight=salience_weight,
            source_detail=source_detail,
        )
        logger.debug(f"Stored {memory_type} memory for user {user_id}")
        return memory_id

    async def supersede_user_memory_with_gist(self, **kwargs) -> Optional[int]:
        """Wave 1a: gist successor + superseded original in one transaction."""
        return await self._memory_store.supersede_user_memory_with_gist(**kwargs)

    async def get_user_memories(
        self, user_id: int, memory_type: str = None, min_importance: float = 0.3,
        include_superseded: bool = False,
    ) -> List[Dict]:
        """Retrieve user memories by type and importance (active-only by default)"""
        return await self._memory_store.get_user_memories(
            user_id=user_id,
            memory_type=memory_type,
            min_importance=min_importance,
            include_superseded=include_superseded,
        )

    async def get_user_memory(self, user_id: int, memory_id: int) -> Optional[Dict]:
        """Read one specific user memory row, scoped to its owner."""
        return await self._memory_store.get_user_memory(user_id=user_id, memory_id=memory_id)

    async def delete_user_memory(self, user_id: int, memory_id: int) -> bool:
        """Delete a specific user memory"""
        return await self._memory_store.delete_user_memory(user_id=user_id, memory_id=memory_id)

    async def delete_user_memory_by_source(
        self,
        *,
        user_id: int,
        source: str,
    ) -> int:
        """Delete every ``user_memory`` row matching a source-detail marker.

        Phase B / B4 step 5 (2026-05-09) — wrapper for the rebuild path
        in ``core.memory_service.rebuild_user_memory_from_documents``.
        Scopes the scrub via ``source_detail.source`` so derived rows
        from a previous rebuild can be cleared without touching rows
        written by a different writer.
        """
        return await self._memory_store.delete_user_memory_by_source(
            user_id=user_id,
            source=source,
        )

    async def bump_user_memory_references(self, user_id: int, memory_ids: List[int]) -> None:
        await self._memory_store.bump_user_memory_references(user_id=user_id, memory_ids=memory_ids)

    async def note_today_fact(self, *, user_id: int, content: str, source: str) -> Optional[int]:
        """R15, T12: keep one line in the owner's today slot (deduped; None at the cap)."""
        return await self._memory_store.note_today_fact(user_id=user_id, content=content, source=source)

    async def active_today_facts(self, *, user_id: int) -> List[Dict]:
        """R15, T12: the today slot's active rows, newest first."""
        return await self._memory_store.active_today_facts(user_id=user_id)

    async def retire_today_facts(self, *, user_id: int, before_ts: float) -> int:
        """R15, T12: the night retires the today rows written before ``before_ts``."""
        return await self._memory_store.retire_today_facts(user_id=user_id, before_ts=before_ts)

    async def prune_retired_today_facts(self, *, before_ts: float) -> int:
        """R15, T12 (fix round 1): the housekeeping deletes retired today rows written before ``before_ts``."""
        return await self._memory_store.prune_retired_today_facts(before_ts=before_ts)

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
        await self._memory_store.upsert_memory_index_item(
            item_id=item_id,
            owner_user_id=owner_user_id,
            source_kind=source_kind,
            source_id=source_id,
            title=title,
            content=content,
            snippet=snippet,
            salience=salience,
            tags=tags,
            related_refs=related_refs,
            content_hash=content_hash,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
        )

    async def get_memory_index_item(self, *, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._memory_store.get_memory_index_item(item_id=item_id)

    async def list_memory_index_items(
        self,
        *,
        owner_user_id: int,
        source_kinds: Optional[List[str]] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        return await self._memory_store.list_memory_index_items(
            owner_user_id=owner_user_id,
            source_kinds=source_kinds,
            limit=limit,
        )

    async def delete_memory_index_items(self, *, item_ids: List[str]) -> None:
        await self._memory_store.delete_memory_index_items(item_ids=item_ids)

    async def clear_memory_index_fts(self, *, owner_user_id: int) -> None:
        await self._memory_store.clear_memory_index_fts(owner_user_id=owner_user_id)

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
        await self._memory_store.upsert_memory_index_fts(
            item_id=item_id,
            owner_user_id=owner_user_id,
            source_kind=source_kind,
            title=title,
            content=content,
            tags=tags,
        )

    async def search_memory_index_fts(
        self,
        *,
        owner_user_id: int,
        query: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        return await self._memory_store.search_memory_index_fts(
            owner_user_id=owner_user_id,
            query=query,
            limit=limit,
        )

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
        await self._memory_store.upsert_memory_index_embedding(
            item_id=item_id,
            owner_user_id=owner_user_id,
            provider=provider,
            model=model,
            vector=vector,
            content_hash=content_hash,
        )

    async def list_memory_index_embeddings(
        self,
        *,
        owner_user_id: int,
        limit: int = 4096,
    ) -> List[Dict[str, Any]]:
        return await self._memory_store.list_memory_index_embeddings(
            owner_user_id=owner_user_id,
            limit=limit,
        )

    async def load_memory_bullet_refs(self, *, owner_user_id: int) -> List[Dict[str, Any]]:
        return await self._memory_store.load_memory_bullet_refs(owner_user_id=owner_user_id)

    async def bump_memory_bullet_refs(
        self,
        *,
        owner_user_id: int,
        item_ids: List[str],
        now: Optional[float] = None,
    ) -> None:
        await self._memory_store.bump_memory_bullet_refs(
            owner_user_id=owner_user_id,
            item_ids=item_ids,
            now=now,
        )

    async def _update_user_interaction_internal(
        self, conn, user_id: int, platform: str
    ):
        """Internal method to update user interaction statistics."""
        await self._profile_reflection_store._update_user_interaction_internal(
            conn,
            user_id,
            platform,
        )

    async def update_user_interaction(self, user_id: int, platform: str):
        """Update user interaction statistics."""
        await self._profile_reflection_store.update_user_interaction(user_id, platform)

    def _create_default_profile(self, user_id: int, platform: str) -> Dict:
        """Create default user profile structure."""
        return self._profile_reflection_store.create_default_profile(user_id, platform)

    async def create_identity_document_version(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        file_path: str,
        version: int,
        actor_type: str,
        actor_id: str,
        reason: str,
        content: str,
        frontmatter: Dict[str, Any],
    ) -> None:
        await self._profile_reflection_store.create_identity_document_version(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            file_path=file_path,
            version=version,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            content=content,
            frontmatter=frontmatter,
        )

    async def first_identity_document_version_at(self, *, user_id: int) -> Optional[float]:
        """When this user's identity documents were first versioned —
        MIN(created_at) over their identity_document_versions rows, None
        when there are none. Batch 5, task 9 (ruling R16): first contact,
        the start of the onboarding nag's window."""
        return await self._profile_reflection_store.first_identity_document_version_at(
            user_id=user_id,
        )

    async def list_identity_document_versions(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await self._profile_reflection_store.list_identity_document_versions(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            limit=limit,
        )

    async def get_identity_document_version(
        self,
        *,
        user_id: int,
        platform: str,
        doc_key: str,
        version: int,
    ) -> Optional[Dict[str, Any]]:
        return await self._profile_reflection_store.get_identity_document_version(
            user_id=user_id,
            platform=platform,
            doc_key=doc_key,
            version=version,
        )

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
        return await self._autonomy_control_store.create_proactive_action(
            user_id=user_id,
            action_type=action_type,
            content=content,
            trigger_reason=trigger_reason,
            scheduled_time=scheduled_time,
            executed_time=executed_time,
            success=success,
            user_response=user_response,
            effectiveness_score=effectiveness_score,
            runtime_job_id=runtime_job_id,
            runtime_session_key=runtime_session_key,
        )

    async def list_recent_proactive_actions_for_user(
        self,
        user_id: int,
        *,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        return await self._autonomy_control_store.list_recent_proactive_actions_for_user(
            user_id,
            limit=limit,
        )

    async def upsert_plugin_runtime_plugin(
        self,
        *,
        plugin_id: str,
        display_name: str,
        version: Optional[str] = None,
        plugin_api_version: Optional[str] = None,
        description: Optional[str] = None,
        entrypoint: Optional[str] = None,
        source_kind: str,
        source_path: Optional[str] = None,
        families: Optional[List[str]] = None,
        criticality: str,
        reload_mode: str,
        host_support: Optional[List[str]] = None,
        requires_json: Optional[Dict[str, Any]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        desired_enabled: bool = False,
        applied_enabled: bool = False,
        compatibility_status: str = "unknown",
        health_status: str = "unknown",
        restart_required: bool = False,
        last_error: Optional[str] = None,
        last_discovered_at: Optional[float] = None,
        last_applied_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        await self._plugin_runtime_store.upsert_plugin_runtime_plugin(
            plugin_id=plugin_id,
            display_name=display_name,
            version=version,
            plugin_api_version=plugin_api_version,
            description=description,
            entrypoint=entrypoint,
            source_kind=source_kind,
            source_path=source_path,
            families=families,
            criticality=criticality,
            reload_mode=reload_mode,
            host_support=host_support,
            requires_json=requires_json,
            manifest=manifest,
            desired_enabled=desired_enabled,
            applied_enabled=applied_enabled,
            compatibility_status=compatibility_status,
            health_status=health_status,
            restart_required=restart_required,
            last_error=last_error,
            last_discovered_at=last_discovered_at,
            last_applied_at=last_applied_at,
            metadata=metadata,
            created_at=created_at,
        )

    async def get_plugin_runtime_plugin(self, *, plugin_id: str) -> Optional[Dict[str, Any]]:
        return await self._plugin_runtime_store.get_plugin_runtime_plugin(plugin_id=plugin_id)

    async def list_plugin_runtime_plugins(self) -> List[Dict[str, Any]]:
        return await self._plugin_runtime_store.list_plugin_runtime_plugins()

    async def update_plugin_runtime_plugin(
        self,
        *,
        plugin_id: str,
        desired_enabled: Optional[bool] = None,
        applied_enabled: Optional[bool] = None,
        compatibility_status: Optional[str] = None,
        health_status: Optional[str] = None,
        restart_required: Optional[bool] = None,
        last_error: Optional[str] = None,
        last_discovered_at: Optional[float] = None,
        last_applied_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._plugin_runtime_store.update_plugin_runtime_plugin(
            plugin_id=plugin_id,
            desired_enabled=desired_enabled,
            applied_enabled=applied_enabled,
            compatibility_status=compatibility_status,
            health_status=health_status,
            restart_required=restart_required,
            last_error=last_error,
            last_discovered_at=last_discovered_at,
            last_applied_at=last_applied_at,
            metadata=metadata,
        )

    async def create_plugin_runtime_run(
        self,
        *,
        plugin_runtime_run_id: str,
        trigger_kind: str,
        action_kind: str,
        status: str,
        changed_plugin_ids: Optional[List[str]] = None,
        restart_required: bool = False,
        summary: Optional[str] = None,
        error_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        await self._plugin_runtime_store.create_plugin_runtime_run(
            plugin_runtime_run_id=plugin_runtime_run_id,
            trigger_kind=trigger_kind,
            action_kind=action_kind,
            status=status,
            changed_plugin_ids=changed_plugin_ids,
            restart_required=restart_required,
            summary=summary,
            error_text=error_text,
            metadata=metadata,
            created_at=created_at,
        )

    async def list_plugin_runtime_runs(self, *, limit: int = 12) -> List[Dict[str, Any]]:
        return await self._plugin_runtime_store.list_plugin_runtime_runs(limit=limit)

    async def set_preferred_device_node(self, *, owner_user_id: int, node_id: str) -> None:
        await self._device_node_registry_store.set_preferred_device_node(
            owner_user_id=owner_user_id,
            node_id=node_id,
        )

    async def consume_device_node_pairing(
        self,
        *,
        pairing_code: str,
        node_id: str,
        node_token_hash: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self._device_node_registry_store.consume_device_node_pairing(
            pairing_code=pairing_code,
            node_id=node_id,
            node_token_hash=node_token_hash,
            metadata=metadata,
        )

    async def touch_device_node_pairing(self, *, pairing_id: str) -> None:
        await self._device_node_registry_store.touch_device_node_pairing(pairing_id=pairing_id)

    async def store_image(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        image_data: bytes,
        content_type: str,
        description: str = None,
        original_filename: str = None,
        conversation_context: str = None,
        message_id: int = None,
        metadata: Dict = None,
        surface_id: Optional[str] = None,
    ) -> int:
        """Store an image with its analysis"""
        activity_logger.log_database_operation("store_image", f"user {user_id}, size {len(image_data)} bytes")
        return await self._media_history_store.store_image(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            image_data=image_data,
            content_type=content_type,
            description=description,
            original_filename=original_filename,
            conversation_context=conversation_context,
            message_id=message_id,
            metadata=metadata,
            surface_id=surface_id,
        )

    async def get_recent_images(
        self,
        user_id: int = None,
        channel_id: int = None,
        surface_id: str = None,
        limit: int = 10,
        include_image_data: bool = False,
    ) -> List[Dict]:
        """Get recent images from conversation history"""
        return await self._media_history_store.get_recent_images(
            user_id=user_id,
            channel_id=channel_id,
            surface_id=surface_id,
            limit=limit,
            include_image_data=include_image_data,
        )

    async def get_image_by_id(self, image_id: int, include_image_data: bool = True) -> Optional[Dict]:
        """Get a specific image by ID"""
        return await self._media_history_store.get_image_by_id(
            image_id,
            include_image_data=include_image_data,
        )

    async def store_passive_listening_event(
        self,
        channel_id: int,
        utterance_count: int,
        check_reason: str,
        decision: Dict,
        did_respond: bool = False,
        passive_context: List[Dict] = None,
        metadata: Dict = None
    ) -> int:
        """
        Store a passive listening interjection check event for analytics

        Args:
            channel_id: Channel ID where event occurred
            utterance_count: Number of utterances in passive buffer
            check_reason: Why the check was triggered (interval/utterance_count/silence)
            decision: Decision dict from voice_interjection_analyzer
            did_respond: Whether AI actually responded
            passive_context: The passive context buffer at time of check
            metadata: Additional metadata

        Returns:
            Event ID
        """
        return await self._passive_listening_store.store_passive_listening_event(
            channel_id=channel_id,
            utterance_count=utterance_count,
            check_reason=check_reason,
            decision=decision,
            did_respond=did_respond,
            passive_context=passive_context,
            metadata=metadata,
        )

    async def get_passive_listening_stats(self, channel_id: Optional[int] = None, days: int = 7) -> Dict:
        """
        Get analytics about passive listening performance

        Args:
            channel_id: Optional channel filter
            days: Number of days to look back

        Returns:
            Statistics dict
        """
        return await self._passive_listening_store.get_passive_listening_stats(
            channel_id=channel_id,
            days=days,
        )

    async def start_voice_session(
        self,
        channel_id: int,
        user_id: Optional[int] = None,
        platform: str = "discord",
        text_channel_id: Optional[int] = None,
        runtime_session_key: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        presence_profile: Optional[str] = None,
        focus_state: Optional[str] = None,
        social_context: Optional[str] = None,
        human_participant_count: Optional[int] = None,
        bot_participant_count: Optional[int] = None,
        owner_present: Optional[bool] = None,
        last_decision_reason: Optional[str] = None,
        initiative_source: Optional[str] = None,
        reply_posture: Optional[str] = None,
        local_transport_mode: Optional[str] = None,
        last_floor_state: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record the start of a voice session."""
        return await self._voice_session_store.start_voice_session(
            channel_id=channel_id,
            user_id=user_id,
            platform=platform,
            text_channel_id=text_channel_id,
            runtime_session_key=runtime_session_key,
            interaction_mode=interaction_mode,
            presence_profile=presence_profile,
            focus_state=focus_state,
            social_context=social_context,
            human_participant_count=human_participant_count,
            bot_participant_count=bot_participant_count,
            owner_present=owner_present,
            last_decision_reason=last_decision_reason,
            initiative_source=initiative_source,
            reply_posture=reply_posture,
            local_transport_mode=local_transport_mode,
            last_floor_state=last_floor_state,
            metadata=metadata,
        )

    async def update_voice_session_state(
        self,
        *,
        session_id: int,
        last_floor_state: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        presence_profile: Optional[str] = None,
        focus_state: Optional[str] = None,
        social_context: Optional[str] = None,
        human_participant_count: Optional[int] = None,
        bot_participant_count: Optional[int] = None,
        owner_present: Optional[bool] = None,
        last_decision_reason: Optional[str] = None,
        initiative_source: Optional[str] = None,
        reply_posture: Optional[str] = None,
        local_transport_mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        end_time: Optional[float] = None,
    ) -> None:
        await self._voice_session_store.update_voice_session_state(
            session_id=session_id,
            last_floor_state=last_floor_state,
            interaction_mode=interaction_mode,
            presence_profile=presence_profile,
            focus_state=focus_state,
            social_context=social_context,
            human_participant_count=human_participant_count,
            bot_participant_count=bot_participant_count,
            owner_present=owner_present,
            last_decision_reason=last_decision_reason,
            initiative_source=initiative_source,
            reply_posture=reply_posture,
            local_transport_mode=local_transport_mode,
            metadata=metadata,
            end_time=end_time,
        )

    async def end_voice_session_for_channel(self, channel_id: int, *, platform: Optional[str] = None):
        """Mark voice sessions for a channel as ended."""
        await self._voice_session_store.end_voice_session_for_channel(
            channel_id=channel_id,
            platform=platform,
        )

    async def list_open_voice_sessions(self, *, limit: int = 32) -> List[Dict[str, Any]]:
        return await self._voice_session_store.list_open_voice_sessions(limit=limit)

    async def close_voice_session(self, *, session_id: int, end_time: Optional[float] = None) -> None:
        await self._voice_session_store.close_voice_session(
            session_id=session_id,
            end_time=end_time,
        )

    async def store_web_search(
        self,
        user_id: Optional[int],
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Store a web search event for analytics."""
        return await self._analytics_audit_store.store_web_search(
            user_id=user_id,
            query=query,
            metadata=metadata,
        )

    async def log_audit_event(
        self,
        user_id: int,
        action: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[str] = None,
    ) -> int:
        """Log a security or significant account action."""
        return await self._analytics_audit_store.log_audit_event(
            user_id=user_id,
            action=action,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
        )

    async def get_audit_logs(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch recent audit logs for a user."""
        return await self._analytics_audit_store.get_audit_logs(
            user_id=user_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Password reset tokens (website)
    # ------------------------------------------------------------------

    async def create_password_reset_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._account_security_store.create_password_reset_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    async def get_password_reset_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._account_security_store.get_password_reset_token(token_hash=token_hash)

    async def mark_password_reset_token_used(self, token_hash: str) -> bool:
        return await self._account_security_store.mark_password_reset_token_used(token_hash=token_hash)

    # ------------------------------------------------------------------
    # Email verification tokens (website)
    # ------------------------------------------------------------------

    async def create_email_verification_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._account_security_store.create_email_verification_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    async def get_email_verification_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._account_security_store.get_email_verification_token(token_hash=token_hash)

    async def mark_email_verification_token_used(self, token_hash: str) -> bool:
        return await self._account_security_store.mark_email_verification_token_used(token_hash=token_hash)

    async def mark_user_email_verified(self, user_id: int) -> bool:
        return await self._account_security_store.mark_user_email_verified(user_id=user_id)

    # ------------------------------------------------------------------
    # Account deletion tokens (website)
    # ------------------------------------------------------------------

    async def create_account_deletion_token(self, user_id: int, token_hash: str, expires_at: float) -> int:
        return await self._account_security_store.create_account_deletion_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

    async def get_account_deletion_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await self._account_security_store.get_account_deletion_token(token_hash=token_hash)

    async def mark_account_deletion_token_used(self, token_hash: str) -> bool:
        return await self._account_security_store.mark_account_deletion_token_used(token_hash=token_hash)

    async def _purge_conversations_for_user_ids_internal(self, conn, user_ids: List[int]) -> int:
        return await self._account_purge_store.purge_conversations_for_user_ids(conn, user_ids)

    async def purge_user_data(self, user_ids: List[int], tables: List[str]) -> Dict[str, int]:
        return await self._account_purge_store.purge_user_data(
            user_ids=user_ids,
            tables=tables,
        )

    async def soft_delete_account(self, user_id: int) -> Dict[str, Any]:
        return await self._account_purge_store.soft_delete_account(user_id=user_id)

    # ------------------------------------------------------------------
    # SaaS-style account helpers / Discord guild mapping
    # ------------------------------------------------------------------

    async def create_app_user(
        self,
        email: str,
        password_hash: Optional[str] = None,
        google_sub: Optional[str] = None,
        role: str = "user",
        palli_user_id: Optional[int] = None,
    ) -> int:
        return await self._app_user_store.create_app_user(
            email=email,
            password_hash=password_hash,
            google_sub=google_sub,
            role=role,
            palli_user_id=palli_user_id,
        )

    async def get_app_user_by_id(self, user_id: int) -> Optional[Dict]:
        return await self._app_user_store.get_app_user_by_id(user_id=user_id)

    async def get_app_user_by_email(self, email: str) -> Optional[Dict]:
        return await self._app_user_store.get_app_user_by_email(email=email)

    async def get_app_user_by_google_sub(self, google_sub: str) -> Optional[Dict]:
        return await self._app_user_store.get_app_user_by_google_sub(google_sub=google_sub)

    async def get_app_user_by_discord_id(self, discord_user_id: str) -> Optional[Dict]:
        return await self._app_user_store.get_app_user_by_discord_id(
            discord_user_id=discord_user_id
        )

    async def get_app_user_by_telegram_id(self, telegram_user_id: str) -> Optional[Dict]:
        return await self._app_user_store.get_app_user_by_telegram_id(
            telegram_user_id=telegram_user_id
        )

    async def update_app_user(self, user_id: int, updates: Dict[str, Any]) -> bool:
        return await self._app_user_store.update_app_user(user_id=user_id, updates=updates)

    async def set_user_last_session_closed_at(
        self,
        *,
        owner_user_id: int,
        timestamp: float,
    ) -> None:
        # Phase B / B4 step 2 (2026-05-09) — IM-HIGH-2: redirect for the
        # reflect session-close hook. Previously the hook stored a
        # ``last_session_closed_at`` row in ``user_memory`` (a generic
        # decay-ranked store), polluting the recall budget. The new
        # column ``users.last_session_closed_at`` (added in B4 step 1)
        # is the dedicated home; this wrapper is the brain-level entry
        # point that ``core/turn/reflect.py:_record_session_close`` calls.
        await self._app_user_store.set_user_last_session_closed_at(
            owner_user_id=owner_user_id,
            timestamp=timestamp,
        )

    async def get_user_last_session_closed_at(
        self,
        *,
        owner_user_id: int,
    ) -> Optional[float]:
        # Phase B / B4 step 3 (2026-05-09) — IM-HIGH-2: symmetric reader
        # for the session-close column. Step 1 added the column, step 2
        # redirected the writer, and this wrapper closes the read side.
        # No production reader currently consumes this value — a survey
        # of the codebase at the time of step 3 found that
        # ``core/turn/recall.py:_safe_count_monologue_ticks_since_last_user``
        # reads from ``recent_history`` (conversation history rows) and
        # never touched ``user_memory`` for ``last_session_closed_at:%``.
        # The wrapper exists so any future consumer (e.g., a session-
        # boundary signal feeding ``decide``) routes through the
        # dedicated column rather than reviving the legacy decay-ranked
        # path.
        #
        # Returns ``None`` for both "user row missing" and "column NULL"
        # (the latter applies until the operator runs
        # ``scripts/migrate_last_session_closed_at.py --apply`` for any
        # user whose only timestamp lived in legacy ``user_memory``).
        return await self._app_user_store.get_user_last_session_closed_at(
            owner_user_id=owner_user_id,
        )


# Global database instance
brain = PalliBrain()
