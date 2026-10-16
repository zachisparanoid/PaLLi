"""Wave 4: assert the W1 _refresh_memory_summary stub is gone.

The stub was a no-op pending Wave 4 deletion (see the previous
docstring at core/memory_service.py:1028). Wave 4 takes over
content-driven memory summary regeneration via core/turn/reflect.py;
the stub plus its single caller go away.
"""
from __future__ import annotations

import pytest


def test_refresh_memory_summary_method_is_gone() -> None:
    """The stub method should no longer exist on MemoryWorkspaceService."""
    from core.memory_service import MemoryWorkspaceService
    assert not hasattr(MemoryWorkspaceService, "_refresh_memory_summary"), (
        "Wave 4 deletes the stub; reflect.py owns memory_summary writeback now"
    )
