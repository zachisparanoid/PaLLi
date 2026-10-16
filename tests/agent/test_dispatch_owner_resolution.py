"""Week 2 task 3 (gap §5.1.5): the owner path is never the default.

The surfaces decide WHO is speaking (bots/discord/client.py on_message,
voice/runtime.py _voice_turn_identity) and, since task 3, build a GUEST turn
for anyone who cannot be proved to be him — an unresolved owner Discord id
included. These are the assertions that new behaviour rests on, held here
where dispatch turns a turn into a tool belt:

  * a turn whose origin is "guest" resolves to is_owner False, and a
    ToolContext built from that flag lists no owner_only tool at all — the
    shell is not merely refused at call time, it is never offered;
  * an owner the resolver cannot name (app_user_id None) is not the owner.

Both are pins on behaviour dispatch already had: the leak was upstream, at
the surfaces. A pin is how it stays closed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.turn.types import Turn, TurnMetadata


def _guest_turn() -> Turn:
    """The turn a surface builds for someone who is not provably him: THEIR
    id as owner_id, their words tagged, origin 'guest'."""
    return Turn(
        surface="discord_text",
        owner_id=12345,
        content="[Dana]: hi",
        metadata=TurnMetadata(channel_id=67890, origin="guest"),
        received_at=datetime(2026, 9, 10, 14, 30, 0),
    )


def _stub_dispatch_edges(monkeypatch, captured: dict) -> None:
    """Everything around the owner resolution: the runner, the model client
    and persistence. The resolution itself is what this file tests."""
    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="hi", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)

    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", noop_asst)


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_search", [False, True])
async def test_a_guest_turn_is_never_the_owner_and_is_offered_no_owner_tool(
    monkeypatch, guest_search,
):
    # Moved deliberately (R14, T8; plan §2b.6): with features.guest_web_search
    # on, the guest's registry is web_search and nothing else — still no owner
    # tool; off (as shipped), none at all. (run_turn is a fake here, so the
    # look's switch, which is the runner's, never reaches this pin.)
    if guest_search:
        from tests.agent.she_decides_harness import guest_search_on

        guest_search_on(monkeypatch)
    captured: dict = {}
    _stub_dispatch_edges(monkeypatch, captured)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": 777}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    turn = _guest_turn()
    await dispatch_turn(turn)

    deps = captured["deps"]
    assert deps.is_owner is False
    # The real object dispatch handed the runner: no registry at all, so
    # there is no belt to gate in the first place — or, with friends' web
    # search on, a belt of one tool that is not owner-only.
    if guest_search:
        assert deps.tool_registry.all_names() == ["web_search"]
        assert not getattr(deps.tool_registry.get("web_search"), "owner_only", False)
    else:
        assert deps.tool_registry is None

    # And why that matters even where a registry IS built: what the runner
    # would construct from this flag, and what the model would be shown.
    from core.agent.builtin_tools import build_default_registry
    from core.agent.tools import ToolContext

    registry = build_default_registry(surface="discord_text")
    owner_only = {
        name for name in registry.all_names()
        if getattr(registry.get(name), "owner_only", False)
    }
    assert owner_only, "no owner_only tool in the registry — this pin would prove nothing"

    ctx = ToolContext(
        turn_owner_id=turn.owner_id, is_owner=deps.is_owner, surface="discord_text",
    )
    offered = {schema["name"] for schema in registry.schemas_for(ctx)}
    assert not (offered & owner_only), sorted(offered & owner_only)


@pytest.mark.asyncio
async def test_an_owner_the_resolver_cannot_name_is_not_the_owner(monkeypatch):
    """No app_user_id (local mode off, the owner not provisioned yet): the
    answer is False, not 'probably him'."""
    async def fake_refs():
        return {"app_user_id": None, "palli_user_id": None, "discord_user_id": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    from core.agent.dispatch import _resolve_is_owner_for_turn

    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content="hi",
        metadata=TurnMetadata(channel_id=67890),
        received_at=datetime(2026, 9, 10, 14, 30, 0),
    )
    assert await _resolve_is_owner_for_turn(turn) is False
