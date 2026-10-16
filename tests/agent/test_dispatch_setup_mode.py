"""dispatch_turn in setup mode (first door, task 1).

A turn that arrives while she has no brain gets one fixed honest sentence —
before the loop mode is read, before owner resolution, before recall,
persistence or reflection, and without a provider being touched. Every
stand-in below raises if it is reached, so a gate that slid further down
`dispatch_turn` fails here rather than in a stranger's first minute.

The dashboard's chat endpoint is the door a stranger is actually standing at,
so its own path is pinned too: the sentence arrives as plain response text.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.config import config
from core.turn.types import Turn, TurnMetadata, TurnResult

THE_SENTENCE = (
    "I don't have a brain yet — open the dashboard at http://127.0.0.1:3000 "
    "and add a key, then I'm here."
)


def _turn(*, surface: str = "dashboard", origin: str = "dashboard_user") -> Turn:
    return Turn(
        surface=surface,
        owner_id=42,
        content="hello?",
        metadata=TurnMetadata(origin=origin, channel_id=12345),
        received_at=datetime(2026, 9, 17, 16, 30, 0),
    )


def _everything_explodes(monkeypatch) -> None:
    """The provider, and everything else a real turn reaches first."""

    def _boom(name: str):
        async def _raise(*_args, **_kwargs):
            raise AssertionError(f"setup mode reached {name}")

        return _raise

    from ai.llm_openai_compatible import BaseLLMProvider

    monkeypatch.setattr(BaseLLMProvider, "_get_session", _boom("a provider session"))
    monkeypatch.setattr("ai.llm_client.llm.chat_completion", _boom("llm.chat_completion"))
    monkeypatch.setattr("core.agent.runner.run_turn", _boom("run_turn"))
    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", _boom("the cadence callable")
    )
    monkeypatch.setattr(
        "core.agent.dispatch._get_openai_compat_client", _boom("the runner's client")
    )
    monkeypatch.setattr(
        "core.agent.dispatch._resolve_is_owner_for_turn", _boom("owner resolution")
    )
    monkeypatch.setattr("core.turn.recall.build_turn_context", _boom("recall"))
    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content", _boom("user-side persist")
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history",
        _boom("assistant-side persist"),
    )
    monkeypatch.setattr("core.turn.reflect.apply_reflection", _boom("reflection"))
    monkeypatch.setattr("ai.stake_appraiser.appraise_exchange", _boom("the stake appraiser"))


@pytest.fixture
def no_brain(monkeypatch):
    monkeypatch.setattr(config, "brain_ready", False, raising=False)
    monkeypatch.setattr(config, "FRONTEND_URL", "", raising=False)


@pytest.mark.asyncio
async def test_a_turn_with_no_brain_gets_the_sentence_and_touches_nothing(no_brain, monkeypatch):
    _everything_explodes(monkeypatch)
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn())

    assert isinstance(result, TurnResult)
    assert result.responded is True
    assert result.response_text == THE_SENTENCE
    assert result.error is None
    assert result.decision is None
    assert result.reflection_applied is False
    assert result.delegation_tier_used == 0
    # Plain text: no beats to fan out, nothing to attach.
    assert result.cadenced_reply is None
    assert result.attachments == ()


@pytest.mark.asyncio
async def test_the_gate_sits_above_the_loop_mode_split(no_brain, monkeypatch):
    """An unsupported PALLI_AGENT_LOOP must not turn the honest sentence into a
    crash: the answer does not depend on which loop would have run."""
    _everything_explodes(monkeypatch)
    monkeypatch.setattr(
        "core.agent.dispatch._resolve_loop_mode",
        lambda: (_ for _ in ()).throw(AssertionError("setup mode read the loop mode")),
    )
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn(surface="discord_text", origin="user"))

    assert result.response_text == THE_SENTENCE


@pytest.mark.asyncio
async def test_the_sentence_names_the_dashboard_this_install_declares(no_brain, monkeypatch):
    _everything_explodes(monkeypatch)
    monkeypatch.setattr(config, "FRONTEND_URL", "http://192.0.2.7:3000", raising=False)
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn())

    assert result.response_text == (
        "I don't have a brain yet — open the dashboard at http://192.0.2.7:3000 "
        "and add a key, then I'm here."
    )


# api.chat builds its Turn through core/turn/ingest.py, whose `datetime.utcnow()`
# is production debt pytest.ini leaves unfiltered on purpose. It is not this
# test's finding, so this test does not add one to the gate's warning count.
@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)
@pytest.mark.asyncio
async def test_the_dashboard_chat_path_renders_it_as_her_reply(no_brain, monkeypatch):
    """api/chat.py is the surface a stranger is standing at when it happens."""
    _everything_explodes(monkeypatch)
    import api.chat as chat

    async def _adapter_registered() -> None:
        return None

    async def _owner_id() -> int:
        return 42

    monkeypatch.setattr(chat, "_ensure_dashboard_adapter_registered", _adapter_registered)
    monkeypatch.setattr(chat, "_resolve_local_owner_id", _owner_id)

    response_text, delegation_text, attachments, beats = await chat._dispatch_to_spine("hello?")

    assert response_text == THE_SENTENCE
    assert delegation_text == ""
    assert attachments == []
    assert beats == []


@pytest.mark.asyncio
async def test_with_a_brain_the_turn_runs_exactly_as_it_did(monkeypatch):
    """The owner's case: the gate is one attribute read and nothing else."""
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    reached: list[str] = []

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        reached.append("run_turn")
        from core.agent.runner import RunResult

        return RunResult(responded=True, response_text="hi back", iterations=1)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    async def stub_client():
        return object()

    async def noop_user(turn, content):
        return None

    async def noop_asst(*, turn, response_text):
        return None

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn(surface="discord_text", origin="user"))

    assert reached == ["run_turn"]
    assert result.response_text == "hi back"


def test_dispatch_turn_never_makes_config_a_local_name():
    """Fix round 1. `dispatch_turn` used to carry its own `from core.config
    import config` inside the react branch. An import anywhere in a function
    makes the name LOCAL to the whole function, so a `config.<x>` read above it
    — the natural way to write the setup-mode gate — is an UnboundLocalError on
    every turn on every surface. The module already imports `config`; this
    keeps a second, local binding from coming back."""
    from core.agent import dispatch

    assert "config" not in dispatch.dispatch_turn.__code__.co_varnames
    assert dispatch.config is config
