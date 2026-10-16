"""The recent conversation, fetched once and early (fix 6, the owner 10-12).

The runner starts the history fetch before recall so the quick length vote
can run beside recall instead of after it. It sets ``prefetched_history``
around ``build_turn_context``; recall's history step reuses that fetch
instead of querying again (``core.turn.recall.recent_history_for``)."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Optional

prefetched_history: ContextVar[Optional["asyncio.Future"]] = ContextVar(
    "prefetched_history", default=None)
