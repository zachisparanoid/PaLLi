"""Under companion.arbiter the ambient arrival planner yields with a
recorded reason; the gate fails closed. (This file's other half — the
consider_outreach surfacing-pass routing pins — retired with step 19:
the lane itself is deleted, the arbiter is the one door.)"""
import pytest


@pytest.mark.asyncio
async def test_arrival_planner_yields_when_arbiter_on(monkeypatch):
    import core.ambient_service as amb

    monkeypatch.setattr(amb, "_arbiter_owns_arrivals", lambda: True)
    svc = object.__new__(amb.AmbientPlannerService)
    decision = await amb.AmbientPlannerService._plan_arrival_action(
        svc, owner_user_id=1, event={"metadata": {}}, context_snapshot={},
        previous_context={}, policy=None,
    )
    assert decision["should_act"] is False
    assert decision["suppression_reason"] == "arbiter_owns_arrivals"


def test_arbiter_owns_arrivals_fails_closed(monkeypatch):
    import core.ambient_service as amb

    def _boom():
        raise RuntimeError("settings unbootable")

    monkeypatch.setattr("ai.arbiter.arbiter_enabled", _boom)
    assert amb._arbiter_owns_arrivals() is False   # off = greet as today
