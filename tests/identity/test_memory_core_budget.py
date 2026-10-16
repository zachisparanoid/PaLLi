"""Batch 5, task 1 (memory that picks, 2026-09-28): the guard for task 3.

The memory doc is rendered into the prompt as a CORE under a character
budget — decisions whole, no bullet cut mid-line, and a footer that says how
many lines are looked up instead of shown. This test is committed RED on
purpose (the plan's step 4): task 1 ships a stub ``render_memory_core`` that
raises NotImplementedError, and task 3 turns it green.
"""
from core.identity.compiler import render_memory_core


def test_core_render_respects_the_budget_and_keeps_decisions_whole(memory_bundle_factory):
    big = "\n".join(f"- [2026-01-{i%28+1:02d}] durable line {i} " + "x" * 120 for i in range(400))
    doc = f"## Durable Context\n{big}\n\n## Active Threads\n- [2026-09-01] a thread\n\n## Important Decisions\n- decided A\n- decided B\n\n## Useful References\n- ref 1\n"
    out = render_memory_core(memory_bundle_factory(doc), budget_chars=12000, refs={})
    assert len(out) <= 12000 + 400          # the budget plus the footer line
    assert "- decided A" in out and "- decided B" in out
    assert "is looked up when a moment calls for it" in out  # the footer says the rest is looked up
    assert not any(line.endswith("x" * 5) and len(line) < 120 for line in out.splitlines())  # no mid-line cut
