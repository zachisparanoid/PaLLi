"""Shared fixtures for delegation tests."""
from __future__ import annotations

import json
import time
import re
from typing import Any, Dict, List, Sequence

import pytest


class _StubConnection:
    """Mimics the brain's async connection interface enough for store tests.

    Storage is a single dict keyed by session_id. SQL is parsed with
    simple pattern matching — we understand INSERT, UPDATE ... WHERE
    session_id, SELECT ... WHERE session_id, and SELECT ... WHERE
    owner_user_id AND status IN.
    """

    def __init__(self, rows: Dict[str, Dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, query: str, *args):
        q = query.strip().lower()

        if q.startswith("insert into delegation_sessions"):
            # Positional args follow the column order in the plan's INSERT.
            columns = [
                "session_id", "owner_user_id", "platform", "channel_id", "status",
                "autonomy_context", "task_hint", "task_full_prompt",
                "artifacts", "events", "started_at",
            ]
            row: Dict[str, Any] = {
                "session_id": args[0],
                "owner_user_id": args[1],
                "platform": args[2],
                "channel_id": args[3],
                "status": args[4],
                "autonomy_context": args[5],
                "task_hint": args[6],
                "task_full_prompt": args[7],
                "summary_for_user": None,
                "voiced_summary": None,
                "artifacts": args[8],
                "events": args[9],
                "error": None,
                "started_at": args[10],
                "completed_at": None,
                "claude_session_id": None,
            }
            self._rows[args[0]] = row
            return None

        if q.startswith("update delegation_sessions"):
            # Find SET columns via regex of "<col> = $N" pattern.
            set_pattern = re.compile(r"(\w+)\s*=\s*\$(\d+)", re.IGNORECASE)
            set_pairs = set_pattern.findall(query.split("WHERE")[0])
            # Last positional arg is the session_id per our pattern
            session_id = args[-1]
            row = self._rows.get(session_id)
            if row is None:
                return None
            for col, idx in set_pairs:
                value = args[int(idx) - 1]
                row[col] = value
            return None

        return None

    async def fetchrow(self, query: str, *args):
        q = query.strip().lower()
        if "where session_id" in q and args:
            row = self._rows.get(args[0])
            return dict(row) if row else None
        return None

    async def fetch(self, query: str, *args):
        q = query.strip().lower()
        results: List[Dict[str, Any]] = []
        if "where owner_user_id" in q and "status in" in q and len(args) >= 3:
            owner_id = args[0]
            statuses = {args[1], args[2]}
            for row in self._rows.values():
                if row.get("owner_user_id") == owner_id and row.get("status") in statuses:
                    results.append(dict(row))
            # Sort by started_at desc to match the real query
            results.sort(key=lambda r: r.get("started_at", 0.0), reverse=True)
            return results
        # Phase 8 — list_recent_for_owner: WHERE owner_user_id = $1 AND started_at >= $2 ... LIMIT $3
        if "where owner_user_id" in q and "started_at >=" in q and len(args) >= 3:
            owner_id = args[0]
            since_ts = float(args[1])
            limit = int(args[2])
            for row in self._rows.values():
                if (
                    row.get("owner_user_id") == owner_id
                    and float(row.get("started_at") or 0.0) >= since_ts
                ):
                    results.append(dict(row))
            results.sort(key=lambda r: r.get("started_at", 0.0), reverse=True)
            return results[:limit]
        return results

    async def close(self):
        return None


class _StubBrain:
    """In-memory stand-in for Brain used by delegation store tests."""

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}

    async def get_connection(self) -> _StubConnection:
        return _StubConnection(self._rows)

    def _load_json(self, raw: Any, fallback: Any) -> Any:
        if raw is None:
            return fallback
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback


@pytest.fixture
def stub_brain() -> _StubBrain:
    return _StubBrain()


@pytest.fixture
def frozen_time(monkeypatch):
    """Pin time.time() to a fixed value for deterministic tests."""
    fixed = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed)
    return fixed


# ─── SDK message builders for test doubles ─────────────────────────────
#
# The real claude-agent-sdk v0.1.x API yields typed Message objects
# (SystemMessage, AssistantMessage, ResultMessage, ...). These builders
# let tests construct those objects with minimal boilerplate, so fakes
# match the production contract instead of the old dict-shaped events.

def sdk_system_message(*, subtype: str = "init", data: Dict[str, Any] | None = None):
    from claude_agent_sdk import SystemMessage
    return SystemMessage(subtype=subtype, data=data or {})


def sdk_assistant_text_message(text: str, *, model: str = "test-model"):
    from claude_agent_sdk import AssistantMessage, TextBlock
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model=model,
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id=None,
        stop_reason=None,
        session_id="test-session",
        uuid=None,
    )


def sdk_result_message(
    *,
    result: str,
    is_error: bool = False,
    session_id: str = "test-session",
):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=50,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        stop_reason=None,
        total_cost_usd=None,
        usage=None,
        result=result,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        errors=None,
        uuid=None,
    )


class FakeSDKClient:
    """Test double matching claude-agent-sdk v0.1.x client contract.

    - `async with` enters/exits cleanly
    - `await query(prompt)` returns None (fire-and-forget send)
    - `async for msg in receive_response()` yields the pre-seeded messages
    """

    def __init__(self, messages: Sequence[Any] | None = None, *, session_id: str = "test-session"):
        self._messages = list(messages or [])
        self.session_id = session_id
        self.queries: List[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)
        return None

    async def receive_response(self):
        for msg in self._messages:
            yield msg
