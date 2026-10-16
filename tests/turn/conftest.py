from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_proactive_dedup_ring():
    """Clear the in-memory rumination-dedup ring before each test.

    `core.turn.proactive._RECENTLY_FIRED_NORMS` is module-level state
    that records norms across test runs in the same process. Tests in
    test_proactive_action.py reuse `action_hint="git status"` across
    multiple cases, and after the 2026-05-06 dedup-on-action_hint fix
    the second test would hit the dedup ring and short-circuit
    unexpectedly. Reset before each test so dedup state is per-test.
    """
    try:
        from core.turn import proactive

        proactive._RECENTLY_FIRED_NORMS.clear()
    except Exception:
        # Module may not be importable in every test path; non-fatal.
        pass
    yield
