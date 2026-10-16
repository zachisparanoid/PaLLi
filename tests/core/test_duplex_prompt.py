"""Step 27 Phase A — the law of working alongside them rides the system
prompt, zero bytes when off (the where-you-live pattern)."""
from __future__ import annotations

import inspect
from unittest.mock import patch


def test_block_is_zero_bytes_when_off():
    from core.agent import system_prompt as sp
    assert sp._duplex_block() == ""


def test_block_is_the_law_when_on():
    from core.agent import system_prompt as sp
    from core.agent import duplex as dx
    with patch.object(dx, "duplex_enabled", lambda: True):
        block = sp._duplex_block()
    assert block.startswith("## When I'm working")
    assert "chime_in" in block and "ask_him" in block


def test_block_is_spliced_right_after_where_you_live():
    """The AST canary: the block sits in the cache-stable prefix, under the
    belonging passage — not appended somewhere a future edit can drop.
    Since Batch 2 (2026-09-27) the section list lives in
    ``build_system_prompt_parts``; ``build_system_prompt`` wraps it."""
    from core.agent import system_prompt as sp
    src = inspect.getsource(sp.build_system_prompt_parts)
    # Batch 8, task 1 (R1): the call is owner-gated in place, so the anchor
    # is the call itself, not the call followed by its comma.
    i = src.index("_where_you_live_block()")
    # Step 2 (add-on a, 2026-10-08): the call passes whether tools are offered.
    # R14, T7 (the owner, 10-12 22:03, Q6): and it is owner-gated in place,
    # as the passage above it is.
    j = src.index('_duplex_block(tools) if bool(getattr(ctx, "is_owner", True)) else "",')
    assert 0 < j - i < 500


def test_block_never_raises_into_the_prompt(monkeypatch):
    from core.agent import system_prompt as sp
    from core.agent import duplex as dx

    def boom():
        raise RuntimeError("settings exploded")
    monkeypatch.setattr(dx, "render_duplex_block", boom)
    assert sp._duplex_block() == ""
