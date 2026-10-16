# tests/turn/test_outreach_residuals.py
"""Step 19 (spec §7): the old 15-min loop's three residual jobs — stake-
horizon threads, the arbiter's daily expiry sweep, the monologue-candidate
rail — ride the moment engine's loop now. Each guarded; one failing job
never costs the others or the cycle."""
import inspect

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_residuals_run_all_three_and_survive_failures():
    import core.turn.proactive as pro
    calls = []

    async def _stakes(**k):
        calls.append("stakes")

    async def _rail(**k):
        calls.append("rail")
        return False

    with patch.object(pro, "_ensure_stake_threads", new=AsyncMock(side_effect=_stakes)), \
         patch.object(pro, "_maybe_run_arbiter_sweep", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(pro, "_consider_candidate_rail", new=AsyncMock(side_effect=_rail)):
        await pro.run_outreach_residuals(owner_id=1)
    assert calls == ["stakes", "rail"]          # the sweep's failure cost nothing


def test_moment_loop_carries_the_hook():
    import ai.present_moment as pm
    body = inspect.getsource(pm.PresentMomentEngine._loop_once)
    assert "run_outreach_residuals" in body
