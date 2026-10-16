"""In-tree fake plugin classes used by the Phase 10b registry tests.

Each class implements the plugin contract (constructor takes a
PluginContext; optional async start/close/health_check) but does no
real work — they record what was called so tests can assert against
the registry's invocation pattern.

Living inside `tests/core/` keeps these classes off the production
import path while still being addressable via the import-by-string
`entry_point` mechanism the registry uses.
"""

from __future__ import annotations

from typing import Any


class GoodPlugin:
    """Plugin that loads cleanly."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.start_called = False
        self.close_called = False
        self.health_failures = 0

    async def start(self) -> None:
        self.start_called = True

    async def close(self) -> None:
        self.close_called = True

    async def health_check(self) -> bool:
        return self.health_failures == 0


class CrashAtConstruction:
    """Plugin whose __init__ raises — tests the instantiation_failed path."""

    def __init__(self, context: Any) -> None:
        raise RuntimeError("simulated construction failure")


class CrashAtStart:
    """Plugin that constructs fine but fails in start()."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.close_called = False

    async def start(self) -> None:
        raise RuntimeError("simulated start failure")

    async def close(self) -> None:
        self.close_called = True


class StartlessPlugin:
    """Plugin without a start() method — exercises the optional-hook path."""

    def __init__(self, context: Any) -> None:
        self.context = context

    async def close(self) -> None:
        pass


class PluginWithSecrets:
    """Plugin that records what secrets it received in PluginContext."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.secrets_seen = dict(context.secrets)

    async def close(self) -> None:
        pass


class PluginWithService:
    """Plugin that records what services it received via dependency
    injection (lifecycle.depends_on)."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.services_seen = dict(context.services)

    async def close(self) -> None:
        pass
