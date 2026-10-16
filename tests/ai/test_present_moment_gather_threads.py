"""The threads digest (arbiter spec §3): flag-conditional (flag-off cycles
must not even carry the health key — byte-identical-off), budgeted,
age-visible, deterministically ordered (presentation is code; judgment is
hers)."""
import time
import types

import pytest

import ai.present_moment_gather as gather


class _Comp:
    def __init__(self, on):
        self.arbiter = on
        self.present_moment = True


def _thread(i, kind="curiosity", urgency="low", age_days=3, summary="s", why="w"):
    return types.SimpleNamespace(
        id=f"{i:0>8x}" + "0" * 24, kind=types.SimpleNamespace(value=kind),
        urgency=urgency, created_at=time.time() - age_days * 86400,
        topic_summary=summary, why_it_matters=why,
    )


@pytest.mark.asyncio
async def test_flag_off_no_threads_source_no_health_key(monkeypatch):
    monkeypatch.setattr(gather, "_companion", lambda: _Comp(False))
    out = await gather.gather_moment_inputs(owner_user_id=1)
    assert out.threads_block == ""
    assert "threads" not in out.sense_health   # byte-identical-off pin


@pytest.mark.asyncio
async def test_flag_on_digest_renders_age_and_id(monkeypatch):
    monkeypatch.setattr(gather, "_companion", lambda: _Comp(True))

    async def _actives(*, owner_user_id):
        return [
            _thread(1, urgency="high", age_days=23, summary="doctor visit",
                    why="a friend"),
            _thread(2, urgency="low", age_days=1, summary="ferret name"),
        ]

    monkeypatch.setattr(gather, "_list_active_threads", _actives)
    out = await gather.gather_moment_inputs(owner_user_id=1)
    assert out.sense_health.get("threads") == "ok"
    assert "open 23d" in out.threads_block
    assert "[00000001" in out.threads_block
    assert "doctor visit" in out.threads_block
    # high urgency sorts first
    assert out.threads_block.index("doctor") < out.threads_block.index("ferret")


@pytest.mark.asyncio
async def test_digest_budget_clips_with_honest_count(monkeypatch):
    monkeypatch.setattr(gather, "_companion", lambda: _Comp(True))

    async def _many(*, owner_user_id):
        return [_thread(i, summary="x" * 140, why="y" * 100) for i in range(60)]

    monkeypatch.setattr(gather, "_list_active_threads", _many)
    out = await gather.gather_moment_inputs(owner_user_id=1)
    assert len(out.threads_block) < 2200
    assert "more not shown" in out.threads_block
