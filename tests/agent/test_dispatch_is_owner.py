"""D.3 wave 2 step 2: dispatch.py resolves is_owner via local_mode and
pins it onto RunnerDeps.is_owner so the runner gates owner-only tools
(local_shell at builtin_tools/local_shell.py:21) correctly.

Pre-Wave-2 the runner read ``getattr(ctx, "is_owner", True)`` which
defaulted to True for everyone. Combined with local_shell's
``owner_only=True`` flag, any surface (Discord guild channel with a
non-owner speaker, voice with a guest) could invoke operator-class
tools in ReAct mode. Security fix: dispatch resolves once,
runner uses what dispatch told it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.turn.types import Turn, TurnMetadata


def _make_turn(owner_id: int = 42) -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=owner_id,
        content="hi",
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )


@pytest.mark.asyncio
async def test_dispatch_passes_is_owner_true_when_owner_matches(monkeypatch):
    """When turn.owner_id matches the resolved local owner app_user_id,
    dispatch must set RunnerDeps.is_owner=True."""
    captured_deps = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured_deps["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    # Stub persistence so we don't touch DB.
    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    # Stub local_mode to return owner app_user_id matching turn.owner_id=42.
    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    # Set the env var AFTER the local_mode setattr — that import path
    # transitively imports core.config, which calls load_dotenv(override=True)
    # and clobbers PALLI_AGENT_LOOP from .env.local. Same pattern as
    # test_dispatch.test_dispatch_turn_pipeline_mode_calls_process_turn.
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_make_turn(owner_id=42))

    assert captured_deps["deps"].is_owner is True


@pytest.mark.asyncio
async def test_dispatch_passes_is_owner_false_when_owner_mismatches(monkeypatch):
    """When turn.owner_id does NOT match the resolved local owner,
    dispatch must set RunnerDeps.is_owner=False — so local_shell stays
    gated for the non-owner caller."""
    captured_deps = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured_deps["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    # Resolved owner is id=42; turn arrives with owner_id=999 (some other user).
    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_make_turn(owner_id=999))

    assert captured_deps["deps"].is_owner is False


@pytest.mark.asyncio
async def test_dispatch_is_owner_defaults_false_on_resolver_failure(monkeypatch):
    """If local_mode.get_local_owner_refs raises (DB transient,
    not yet bootstrapped), dispatch falls back to is_owner=False —
    safer than wrongly allowing operator tools."""
    captured_deps = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured_deps["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def boom_refs():
        raise RuntimeError("DB transient error")
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", boom_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_make_turn(owner_id=42))

    assert captured_deps["deps"].is_owner is False


@pytest.mark.asyncio
async def test_dispatch_is_owner_defaults_false_when_owner_unset(monkeypatch):
    """If get_local_owner_refs returns None for app_user_id (local mode
    disabled or owner not yet bootstrapped), is_owner=False — same
    fail-secure default."""
    captured_deps = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured_deps["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": None, "palli_user_id": None, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_make_turn(owner_id=42))

    assert captured_deps["deps"].is_owner is False


@pytest.mark.asyncio
async def test_runner_propagates_deps_is_owner_into_tool_context(tmp_path):
    """Confirm the runner threads RunnerDeps.is_owner into the ToolContext
    that ToolRegistry.dispatch reads — so owner_only tool gates fire."""
    from dataclasses import dataclass, field
    from typing import Any
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.tools import ToolContext, ToolRegistry
    from core.agent.session import ToolUseBlock

    @dataclass
    class FakeTurn:
        surface: str = "discord_text"
        owner_id: int = 1
        content: str = "do something"
        metadata: Any = None
        received_at: Any = None

    @dataclass
    class FakeCtx:
        turn: FakeTurn = field(default_factory=FakeTurn)
        user_text: str = "do something"
        recent_history_text: str = ""
        identity_core_text: str = "I'm PaLLi."
        skills_text: str = ""
        runtime_text: str = ""
        memory_text: str = ""
        facts_text: str = ""
        is_owner: bool = True  # ctx says True, but deps must override

    captured_ctx = {}

    class CaptureTool:
        name = "capture"
        description = "capture ctx"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False

        async def execute(self, input, ctx):
            captured_ctx["ctx"] = ctx
            return "captured"

    registry = ToolRegistry()
    registry.register(CaptureTool())

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[ToolUseBlock(id="t1", name="capture", input={})],
                stop_reason="tool_use",
                prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["done"],
            tool_uses=[],
            stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
            is_owner=False,  # explicitly override the ctx default
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    # The captured ToolContext must reflect deps.is_owner=False, NOT the
    # FakeCtx's is_owner=True default. This is the wave 2 fix in action.
    assert captured_ctx["ctx"].is_owner is False


def test_local_shell_tool_marked_owner_only():
    """Static check: local_shell carries owner_only=True so the gate at
    ToolRegistry.dispatch (core/agent/tools.py:134) fires for non-owners.
    If this assertion ever flips to False, security regression — file a
    follow-up immediately."""
    from core.agent.builtin_tools.local_shell import LocalShellTool
    tool = LocalShellTool()
    assert tool.owner_only is True


@pytest.mark.asyncio
async def test_tool_registry_blocks_owner_only_for_non_owner():
    """End-to-end: when ToolRegistry.dispatch is called with a
    ToolContext(is_owner=False), an owner_only tool returns an error
    block instead of executing. This is the contract the wave 2 fix
    relies on at the bottom of the call chain."""
    from core.agent.builtin_tools.local_shell import LocalShellTool
    from core.agent.tools import ToolContext, ToolRegistry
    from core.agent.session import ToolUseBlock

    registry = ToolRegistry()
    registry.register(LocalShellTool())

    ctx = ToolContext(turn_owner_id=42, is_owner=False, surface="discord_text")
    use = ToolUseBlock(id="t1", name="local_shell", input={"command": "echo hi"})

    result = await registry.dispatch(use, ctx)
    assert result.is_error is True
    assert "owner-only" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_search", [False, True])
async def test_guest_turn_gets_no_tools_no_reflection_and_is_not_owner(monkeypatch, guest_search):
    """origin='guest' (a non-owner Discord author / voice speaker): no tool
    registry at all, no reflection, is_owner False — a plain reply.

    Moved deliberately (R14, T8; plan §2b.6): with features.guest_web_search
    on, the guest's registry holds web_search and nothing else; off (as
    shipped), none at all. The look's switch never reaches here (run_turn is
    a fake: the look is the runner's)."""
    if guest_search:
        from tests.agent.she_decides_harness import guest_search_on

        guest_search_on(monkeypatch)
    captured_deps = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured_deps["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="hi", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)
    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)
    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)
    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": 777}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn
    guest = Turn(surface="discord_text", owner_id=12345, content="[Dana]: hi",
                 metadata=TurnMetadata(channel_id=12345, origin="guest"),
                 received_at=datetime(2026, 8, 19, 6, 30, 0))
    await dispatch_turn(guest)
    deps = captured_deps["deps"]
    assert deps.is_owner is False
    if guest_search:
        assert deps.tool_registry.all_names() == ["web_search"]
    else:
        assert deps.tool_registry is None
    assert deps.apply_reflection is None
