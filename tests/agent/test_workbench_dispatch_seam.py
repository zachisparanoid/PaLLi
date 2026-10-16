"""The null-delivery seam (own-work spec §4b, §5, §6b).

The plan read "caller-owned delivery" as sufficient: the workbench engine
discards the TurnResult, so nothing reaches a channel. True for CHANNELS —
but the dispatch spine does three more things to a finished turn, and on a
work session all three are wrong:

  * it persists the assistant's text to `conversations` (NOT origin-gated),
    so her private sign-off would join the history her next reply recalls;
  * it fires reflection, which writes memories, user facts and OPEN THREADS
    — a rail into proactive outreach outside the single deliberate SHARE
    line that §5 says is the only door from her work to him;
  * it caps rounds from a process-global env var, so the §4e per-session
    round budget had nothing to attach to.

Spec §6b#1 defers her work entering the nightly metabolism "to keep v1's
surface honest", which settles it: the seam is wider than one discard.
These tests run the REAL dispatch body with fakes at its edges.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.turn.types import Turn, TurnMetadata


def _turn(surface, origin):
    return Turn(surface=surface, owner_id=1, content="hello", images=[],
                metadata=TurnMetadata(origin=origin))


def test_is_workbench_turn_truth_table():
    from core.agent.dispatch import _is_workbench_turn

    assert _is_workbench_turn(_turn("workbench", "workbench")) is True
    assert _is_workbench_turn(_turn("discord_text", "user")) is False
    assert _is_workbench_turn(_turn("reminder_fire", "reminder")) is False
    assert _is_workbench_turn(SimpleNamespace()) is False


def _wire(monkeypatch, *, responded=True, text="did work."):
    """Fake dispatch's edges: no LLM, no DB, real dispatch body."""
    import core.agent.dispatch as d
    import core.agent.runner as runner_mod
    import core.turn.persistence as persistence_mod
    import ai.present_moment as pm
    import ai.stake_ledger as ledger

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")
    seen = {"deps": None, "assistant_persist": [], "user_persist": [],
            "notices": []}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        seen["deps"] = deps
        return SimpleNamespace(responded=responded, response_text=text,
                               image_descriptions=(), text_attachment_contents=(),
                               attachments=(), error=None)

    async def fake_assistant_persist(*, turn, response_text):
        seen["assistant_persist"].append(response_text)

    async def fake_user_persist(turn, content):
        seen["user_persist"].append(content)

    async def fake_client():
        return object()

    async def fake_cadence():
        async def _c(prompt):
            return ""
        return _c

    async def fake_is_owner(turn):
        return True

    monkeypatch.setattr(runner_mod, "run_turn", fake_run_turn)
    monkeypatch.setattr(persistence_mod, "_persist_assistant_side_to_history",
                        fake_assistant_persist)
    monkeypatch.setattr(persistence_mod, "_persist_user_side_with_content",
                        fake_user_persist)
    monkeypatch.setattr(d, "_get_openai_compat_client", fake_client)
    monkeypatch.setattr(d, "_build_cadence_llm_callable", fake_cadence)
    monkeypatch.setattr(d, "_resolve_is_owner_for_turn", fake_is_owner)
    monkeypatch.setattr(pm, "notice", lambda kind, **kw: seen["notices"].append(kind))
    monkeypatch.setattr(ledger, "ledger_enabled", lambda: False)
    return seen


@pytest.mark.asyncio
async def test_workbench_turn_sends_nowhere_and_metabolizes_nothing(monkeypatch):
    from core.agent.dispatch import dispatch_turn

    seen = _wire(monkeypatch, text="moved the essay.\nSHARE: the opening works")
    await dispatch_turn(_turn("workbench", "workbench"))

    # her sign-off never joins the conversation she'll recall next time
    assert seen["assistant_persist"] == []
    assert seen["user_persist"] == []
    # reflection is not merely unused — it is not wired for this turn, so it
    # cannot write memories, user facts, or open threads
    assert seen["deps"].apply_reflection is None
    # the per-session round budget has something to attach to. Pinned, NOT
    # read from the ambient yaml: this assertion was `== 8` against the live
    # config and went red the moment the owner raised the knob to 100
    # (2026-07-29) — the third instance of the step-07 disease class (live
    # settings are ENVIRONMENT, never test state). What matters here is that
    # the cap is threaded at all, and that it is HERS rather than the global.
    import core.agent.dispatch as dispatch_mod
    import core.turn.companion_flags as flags

    monkeypatch.setattr(
        flags, "companion_settings",
        lambda: SimpleNamespace(own_work_session_rounds=8))
    assert dispatch_mod._workbench_round_cap(_turn("workbench", "workbench")) == 8
    assert dispatch_mod._workbench_round_cap(
        _turn("discord_text", "discord_text")) is None
    assert seen["deps"].max_iterations is not None
    # sense A stays quiet; sense H is the engine's single truth
    assert seen["notices"] == []


@pytest.mark.asyncio
async def test_ordinary_turn_is_untouched(monkeypatch):
    """The byte-identical law: every other surface keeps persistence,
    reflection, the global round cap, and its present-moment senses."""
    from core.agent.dispatch import dispatch_turn

    seen = _wire(monkeypatch)
    await dispatch_turn(_turn("discord_text", "user"))

    assert seen["assistant_persist"] == ["did work."]
    assert seen["deps"].apply_reflection is not None
    assert seen["deps"].max_iterations is None
    assert "owner_message" in seen["notices"]
    assert "turn_completed" in seen["notices"]
