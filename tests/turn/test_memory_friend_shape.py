"""Friend-shaped MEMORY.md (2026-08-18, item 4 of the 08-18 program).

Owner: "Everything, how a friend does." A friend's memory has salience and
decay; hers only accrued: MEMORY.md was 20,887 chars / 193 bullets — 84
"Active Threads" of which 74 traced to no Discord message he ever sent (May
dashboard-harness fiction and stale to-dos), and Durable Context kept
"cat named Miso" beside "cat's name is Mochi (corrected)". Three causes:
Active Threads carried no dates and nothing swept them; the reflect writer
never SAW the Active Threads (only Durable Context), so its resolve op could
not match; and there was no way to retire an outdated durable line.

So: new Active Threads carry a date stamp; a nightly deterministic sweep
ages the threads; reflect sees the threads and gains durable_context_retire
(a meaning call — LLM decides what is superseded, code applies it).

Batch 5 (memory that picks, 2026-09-28, ruling R11) replaced the sweep: the
08-18 pass DROPPED a thread older than memory_thread_days and every undated
one; the new one (ai/memory_sweep.py) MOVES a thread untouched for
memory.thread_archive_days to the same section of MEMORY_ARCHIVE.md, marked
with the day, and stamps an undated line with today instead of dropping it.
Nothing is deleted; `palli_ctl memory restore <mcb_id>` brings a line back.
The sweep tests below are the old ones re-premised; the full rules live in
tests/ai/test_memory_sweep.py.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import core.turn.reflect as reflect
from ai.memory_sweep import apply_sweep, plan_sweep
from core.turn.types import ReflectionMemoryUpdates

DOC = """---
doc_type: memory
version: 3
---

# Memory

## Durable Context

- Has a cat named Miso (often sits on their keyboard)
- Prefers casual tone; jokes are acceptable.

## Active Threads

- Investigate the home router dropping overnight — user asked me to check it
- [2026-07-01] Cellar build budget — offered a one-page checklist
- [2026-08-15] Waiting on the new phone case

## Important Decisions

- No important decisions recorded yet
"""


def test_new_active_threads_carry_a_date_stamp():
    out = reflect._append_to_section(
        DOC, section_heading="## Active Threads",
        items=["Cellar shelving arrives Friday"], stamp="2026-08-18",
    )
    assert "- [2026-08-18] Cellar shelving arrives Friday" in out
    # dedup ignores the stamp: the same text stamped another day is one line
    again = reflect._append_to_section(
        out, section_heading="## Active Threads",
        items=["Cellar shelving arrives Friday"], stamp="2026-08-19",
    )
    assert again.count("Cellar shelving arrives Friday") == 1


def test_appender_clears_a_template_line_the_sweep_has_stamped():
    """Fix round 1 (M2): the nightly sweep stamps every unstamped Durable line,
    the template-born ones included; the appender's placeholder filter must
    still recognise `- [day] Interests: …` and `- [day] Preferred name …`."""
    doc = (
        "## Durable Context\n\n"
        "- [2026-09-28] Interests: No explicit interest list yet.\n"
        "- [2026-09-28] Preferred name if explicitly confirmed: Sam\n"
        "- Interests: woodworking\n\n"
        "## Active Threads\n\n- No active curated threads yet.\n"
    )
    out = reflect._append_to_section(
        doc, section_heading="## Durable Context", items=["Has a dog named Pip"],
        placeholder_lines=("- Interests:", "- Preferred name", "No durable", "Curated memory is just getting started"),
    )
    assert "- Has a dog named Pip" in out
    assert "Interests:" not in out and "Preferred name" not in out
    assert "No active curated threads yet." in out  # another section, untouched


BODY = DOC.split("---\n\n", 1)[1]
ARCHIVE = (
    "# Memory Archive\n\n## Durable Context\n\n- Nothing archived yet.\n\n"
    "## Active Threads\n\n- Nothing archived yet.\n\n## Important Decisions\n\n- Nothing archived yet.\n"
)


def test_sweep_moves_old_threads_to_the_archive_and_stamps_undated_ones():
    """Re-premised (R11): the 08-18 sweep DROPPED the old thread and the undated
    one. Now the old thread MOVES to the archive with the day it moved, the
    undated one (and every undated Durable line) is stamped with today, and
    the fresh thread stays. Nothing is deleted."""
    now = time.mktime(time.strptime("2026-08-18 12:00", "%Y-%m-%d %H:%M"))
    plan = plan_sweep(BODY, refs={}, now=now, durable_days=60, thread_days=30)
    assert [m.text for m in plan.move] == ["- [2026-07-01] Cellar build budget — offered a one-page checklist"]
    assert plan.move[0].section == "## Active Threads"
    assert plan.stamp == [
        "- Has a cat named Miso (often sits on their keyboard)",
        "- Prefers casual tone; jokes are acceptable.",
        "- Investigate the home router dropping overnight — user asked me to check it",
    ]
    new, archive = apply_sweep(BODY, ARCHIVE, plan)
    assert "[2026-08-15] Waiting on the new phone case" in new
    assert "Cellar build budget" not in new
    assert "- [2026-08-18] Investigate the home router dropping overnight — user asked me to check it" in new
    assert "- [2026-08-18] Has a cat named Miso (often sits on their keyboard)" in new
    assert "- [2026-07-01] Cellar build budget — offered a one-page checklist [archived 2026-08-18]" in archive
    # decisions untouched
    assert "- No important decisions recorded yet" in new
    # idempotent: the next night finds nothing
    again = plan_sweep(new, refs={}, now=now, durable_days=60, thread_days=30)
    assert again.move == [] and again.stamp == []
    assert apply_sweep(new, archive, again) == (new, archive)


def test_sweep_zero_thread_days_moves_no_thread():
    """Re-premised (R11): 0 turned the old sweep off; now it turns thread
    moves off (the undated lines are still stamped — a stamp loses nothing)."""
    now = time.mktime(time.strptime("2026-08-18 12:00", "%Y-%m-%d %H:%M"))
    plan = plan_sweep(BODY, refs={}, now=now, durable_days=60, thread_days=0)
    assert plan.move == []
    assert len(plan.stamp) == 3


def test_parser_takes_durable_context_retire():
    plan = reflect._plan_from_payload({"memory_updates": {
        "durable_context_add": ["Cat's name is Mochi"],
        "durable_context_retire": ["Has a cat named Miso (often sits on their keyboard)"],
    }})
    assert plan.memory_updates.durable_context_retire == [
        "Has a cat named Miso (often sits on their keyboard)"]
    assert ReflectionMemoryUpdates().durable_context_retire == []


@pytest.mark.asyncio
async def test_apply_retires_the_superseded_durable_line():
    doc = SimpleNamespace(content=DOC.split("---\n\n", 1)[1], frontmatter={"doc_type": "memory", "version": 3})
    bundle = SimpleNamespace(owner={"memory": doc})
    written = {}

    async def fake_update(**kw):
        written.update(kw)

    with patch.object(reflect.identity_service, "compile_for_owner", return_value=bundle), \
         patch.object(reflect.identity_service, "update_raw_document", side_effect=fake_update):
        await reflect._apply_memory_updates(owner_id=1, updates=ReflectionMemoryUpdates(
            durable_context_add=["Cat's name is Mochi (corrected from 'Miso')"],
            durable_context_retire=["Has a cat named Miso"],
        ))
    md = written["markdown"]
    assert "Mochi" in md and "Has a cat named Miso" not in md
    assert "jokes are acceptable" in md


def test_prompt_shows_the_threads_and_offers_retire():
    from pathlib import Path
    tpl = Path("prompts/turn_reflect.txt").read_text(encoding="utf-8")
    assert "{memory_active_threads}" in tpl
    assert "durable_context_retire" in tpl


def test_night_cycle_runs_the_memory_sweep():
    """Re-premised (R11): the memory pass's slot in _run_once runs the new
    sweep; the deleting one is gone from the night. (The write-back + sidecar
    test that sat here moved to tests/ai/test_memory_sweep.py, against
    sweep_memory.)"""
    import inspect
    from ai.night_cycle import NightCycleEngine
    body = inspect.getsource(NightCycleEngine._run_once)
    assert "memory_sweep" in body and "sweep_memory" in body
    assert "run_nightly_memory_sweep" not in body


def test_memory_thread_days_knob():
    """The knob stays in the schema (the live yaml carries it; extra="forbid")
    but nothing reads it any more — memory.thread_archive_days took over."""
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().memory_thread_days == 30
    assert CompanionConfig(memory_thread_days=0).memory_thread_days == 0
