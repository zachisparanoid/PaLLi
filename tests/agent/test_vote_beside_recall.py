"""Fix 6 (the owner, 10-12 14:32: "do this"): the quick length vote starts
beside recall instead of after it. It reads only the turn's words and the
recent conversation; the history is fetched once, early, and recall reuses
it. About 2 s off every reply."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CADENCE_JSON = ('{"should_respond": true, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "what's on the calendar"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "what's on the calendar"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_vote_beside_recall")


def _one_step_llm(monkeypatch):
    import core.agent.llm_client as llm_mod

    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=["sure"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)


def _deps(mod, tmp_path, recall, cadence_llm, history=None):
    return mod.RunnerDeps(
        build_turn_context=recall, cadence_llm=cadence_llm, sessions_root=tmp_path,
        provider="openai_compat", model="m", client=None, tool_registry=None,
        polling_tools=set(), fetch_recent_history=history,
    )


def test_the_vote_runs_beside_recall_and_sees_the_recent_conversation(tmp_path, monkeypatch):
    from core.turn.types import HistoryEntry
    mod = _load_runner()
    _one_step_llm(monkeypatch)
    order: list[str] = []
    prompts: list[str] = []

    async def run():
        voted = asyncio.Event()

        async def slow_recall(turn):
            order.append("recall start")
            await asyncio.wait_for(voted.wait(), timeout=2)    # finishes only if the vote ran meanwhile
            order.append("recall end")
            return FakeCtx(turn=turn)

        async def cadence_llm(prompt):
            order.append("vote")
            prompts.append(prompt)
            voted.set()
            return CADENCE_JSON

        async def history(turn):
            return [HistoryEntry(role="user", content="earlier words about the dentist", at=datetime.now())]

        await mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, slow_recall, cadence_llm, history))

    asyncio.run(run())
    assert order.index("vote") < order.index("recall end")
    assert order.count("vote") == 1                             # never asked twice
    assert "earlier words about the dentist" in prompts[0]
    assert "what's on the calendar" in prompts[0]


def test_without_the_early_history_the_vote_waits_for_recall_as_before(tmp_path, monkeypatch):
    mod = _load_runner()
    _one_step_llm(monkeypatch)
    order: list[str] = []

    async def recall(turn):
        order.append("recall")
        return FakeCtx(turn=turn)

    async def cadence_llm(prompt):
        order.append("vote")
        return CADENCE_JSON

    asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, recall, cadence_llm)))
    assert order == ["recall", "vote"]


def test_recall_reuses_the_early_history(monkeypatch):
    from core.turn import history_prefetch, recall

    async def fetched_again(turn):
        raise AssertionError("the history was fetched twice")

    monkeypatch.setattr(recall, "fetch_recent_history", fetched_again)

    async def run():
        early = asyncio.get_running_loop().create_future()
        early.set_result(["h1"])
        token = history_prefetch.prefetched_history.set(early)
        try:
            return await recall.recent_history_for(FakeTurn())
        finally:
            history_prefetch.prefetched_history.reset(token)

    assert asyncio.run(run()) == ["h1"]


def test_a_failed_early_history_falls_back_to_recalls_own_fetch(monkeypatch):
    from core.turn import history_prefetch, recall

    async def own(turn):
        return ["own"]

    monkeypatch.setattr(recall, "fetch_recent_history", own)

    async def run():
        early = asyncio.get_running_loop().create_future()
        early.set_exception(RuntimeError("db down"))
        token = history_prefetch.prefetched_history.set(early)
        try:
            return await recall.recent_history_for(FakeTurn())
        finally:
            history_prefetch.prefetched_history.reset(token)

    assert asyncio.run(run()) == ["own"]
    assert asyncio.run(recall.recent_history_for(FakeTurn())) == ["own"]   # nothing early: its own fetch


def test_a_failed_recall_cancels_the_early_vote(tmp_path, monkeypatch):
    """Fable's review (10-12): when recall fails, the turn ends, so the vote
    started beside it is cancelled rather than left to bill for a dead turn."""
    mod = _load_runner()
    _one_step_llm(monkeypatch)
    seen: dict = {}

    async def run():
        release = asyncio.Event()

        async def history(turn):
            await release.wait()            # the vote waits on the history...
            return []

        async def failing_recall(turn):
            await asyncio.sleep(0)          # ...while recall fails first
            raise RuntimeError("recall broke")

        async def cadence_llm(prompt):
            seen["voted"] = True
            return CADENCE_JSON

        result = await mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, failing_recall, cadence_llm, history))
        release.set()                       # had the vote survived, it would run now
        await asyncio.sleep(0.05)
        seen["error"] = result.error

    asyncio.run(run())
    assert "recall_failed" in (seen.get("error") or "")
    assert "voted" not in seen
