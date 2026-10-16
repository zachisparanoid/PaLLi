"""ReflectionPlan carries open_threads_to_add and open_threads_to_close fields."""
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan


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


def test_reflection_plan_has_open_threads_to_add():
    plan = _empty_plan()
    assert plan.open_threads_to_add == []


def test_reflection_plan_has_open_threads_to_close():
    plan = _empty_plan()
    assert plan.open_threads_to_close == []


def test_reflection_plan_can_carry_open_threads_to_add():
    plan = _empty_plan(
        open_threads_to_add=[
            {
                "topic_summary": "x",
                "why_it_matters": "y",
                "kind": "work",
                "urgency": "z",
            }
        ],
    )
    assert len(plan.open_threads_to_add) == 1
    assert plan.open_threads_to_add[0]["topic_summary"] == "x"


def test_reflection_plan_can_carry_open_threads_to_close():
    plan = _empty_plan(
        open_threads_to_close=[
            {"thread_id": "abc", "resolution_reason": "done"},
        ],
    )
    assert len(plan.open_threads_to_close) == 1
    assert plan.open_threads_to_close[0]["thread_id"] == "abc"
