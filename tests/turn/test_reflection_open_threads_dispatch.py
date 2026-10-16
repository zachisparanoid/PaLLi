"""apply_reflection dispatches open_threads_to_add/close to the brain.

Once the ReflectionPlan carries open_threads_to_add and open_threads_to_close
(parsed in turn_reflect output), apply_reflection dispatches each entry
through ``brain.add_open_thread`` / ``brain.close_open_thread``. The legacy
``resolved_agenda_topics`` path was deleted in T16 of the rebuild plan;
dispatch is now unconditional.

Mocking note: tests use ``create_autospec(PalliBrain, instance=True)``
per the project convention (see feedback_autospec_brain_mocks.md). A
plain MagicMock would silently allow missing methods and could mask a
real wiring bug.
"""
from unittest.mock import create_autospec, patch

import pytest

from core.database import PalliBrain
from core.open_threads import ThreadStatus
from core.turn.reflect import apply_reflection
from core.turn.types import (
    ReflectionMemoryUpdates,
    ReflectionPlan,
)


def _ctx_for_owner(owner_id: int):
    """Minimal duck-typed TurnContext for dispatch tests.

    apply_reflection only reads ctx.turn.owner_id on the path under test
    here (_extract_plan is patched to return a fixed ReflectionPlan).
    Python doesn't runtime-enforce TurnContext typing, so a SimpleNamespace
    stub with the field structure suffices.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        turn=SimpleNamespace(
            owner_id=owner_id,
            surface="local",
            content="",
            metadata=SimpleNamespace(),
        ),
    )


def _empty_plan(**overrides) -> ReflectionPlan:
    """Construct a ReflectionPlan with all required positional fields filled."""
    base: dict = dict(
        memory_updates=ReflectionMemoryUpdates(),
        personality_observations=[],
        relationship_delta=None,
        user_facts_add=[],
        monologue_note=None,
        first_contact_resolutions={},
        skip_reason=None,
    )
    base.update(overrides)
    return ReflectionPlan(**base)


@pytest.mark.asyncio
async def test_apply_reflection_dispatches_open_threads_to_add():
    brain = create_autospec(PalliBrain, instance=True)
    brain.add_open_thread.return_value = "new-thread-id"

    plan = _empty_plan(
        skip_reason=None,
        open_threads_to_add=[
            {
                "topic_summary": "x",
                "why_it_matters": "y",
                "kind": "work",
                "urgency": "z",
            }
        ],
    )

    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain):
        await apply_reflection(_ctx_for_owner(1), response_text="...")

    brain.add_open_thread.assert_awaited_once()
    call_kwargs = brain.add_open_thread.await_args.kwargs
    assert call_kwargs["topic_summary"] == "x"
    assert call_kwargs["kind"] == "work"


@pytest.mark.asyncio
async def test_apply_reflection_dispatches_open_threads_to_close():
    brain = create_autospec(PalliBrain, instance=True)
    plan = _empty_plan(
        skip_reason=None,
        open_threads_to_close=[
            {"thread_id": "abc123", "resolution_reason": "user said sent"},
        ],
    )

    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain):
        await apply_reflection(_ctx_for_owner(1), response_text="...")

    brain.close_open_thread.assert_awaited_once()
    call_kwargs = brain.close_open_thread.await_args.kwargs
    assert call_kwargs["thread_id"] == "abc123"
    assert call_kwargs["status"] == ThreadStatus.RESOLVED
