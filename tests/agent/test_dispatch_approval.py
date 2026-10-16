"""D.4 step 3: approval-hook enforcement for owner_only tools in ReAct mode.

Pre-D.4 the ``requires_approval`` flag on tools had no effect — the hook
slot at ``core/agent/tools.py:110`` existed but no caller wired a hook,
and no builtin tool had ``requires_approval=True``. So the security
gate at ``tools.py:141`` (``if tool.requires_approval and self._approval_hook is not None``)
was inert.

This commit wires the hook in ``core.agent.dispatch.dispatch_turn``'s
react branch and flips ``LocalShellTool.requires_approval`` to True.
The hook approves owner-only tools only for the resolved local owner;
denies for non-owners.

These tests pin three layers of the new behavior:

1. ``local_shell`` carries ``requires_approval=True`` (static check).
2. The hook returned by ``_build_approval_hook`` allows for owners and
   denies for non-owners — direct call against ``ToolRegistry.dispatch``
   without spinning up the full dispatch path.
3. End-to-end: a ReAct turn whose dispatched LLM emits a local_shell
   call is executed when is_owner is resolved True, and produces an
   owner-only deny error block when is_owner is False (the owner_only
   gate fires first; the approval hook is the second-layer defense
   verified by test 2 above).
"""
from __future__ import annotations

import pytest


def test_local_shell_marked_requires_approval():
    """Static check: local_shell carries requires_approval=True so the
    approval-hook gate at ToolRegistry.dispatch (core/agent/tools.py:141)
    actually fires when a hook is installed. Pre-D.4 this was False,
    silently disabling the gate."""
    from core.agent.builtin_tools.local_shell import LocalShellTool
    tool = LocalShellTool()
    assert tool.requires_approval is True


@pytest.mark.asyncio
async def test_approval_hook_allows_owner_only_for_owner():
    """The hook returned by _build_approval_hook returns ("allow", "")
    when the caller is the resolved owner — even if the tool is
    owner_only=True. Pin via a fake tool object so we don't depend
    on registry wiring here."""
    from core.agent.dispatch import _build_approval_hook

    class FakeOwnerTool:
        name = "local_shell"
        owner_only = True
        requires_approval = True

    hook = _build_approval_hook(is_owner=True)
    decision, reason = await hook(FakeOwnerTool(), {"command": "echo hi"}, None)
    assert decision == "allow"
    assert reason == ""


@pytest.mark.asyncio
async def test_approval_hook_denies_owner_only_for_non_owner():
    """When is_owner=False, the hook denies owner_only tools with a
    sensible reason. This is the defense-in-depth layer behind the
    owner_only gate at tools.py:134 — even if a future code path
    accidentally un-flags owner_only at the surface, the approval
    hook still blocks the call as long as the tool keeps
    requires_approval=True."""
    from core.agent.dispatch import _build_approval_hook

    class FakeOwnerTool:
        name = "local_shell"
        owner_only = True
        requires_approval = True

    hook = _build_approval_hook(is_owner=False)
    decision, reason = await hook(FakeOwnerTool(), {"command": "rm -rf /"}, None)
    assert decision == "deny"
    assert "owner" in reason.lower()


@pytest.mark.asyncio
async def test_approval_hook_allows_non_owner_only_with_approval():
    """A future tool that requires_approval=True but ISN'T owner_only
    (e.g. a deliver_artifact with a safe-path allowlist) auto-approves
    in the current v1 hook. Pinning the behavior so the policy is
    intentional and discoverable; a user-prompting flow can replace
    this branch later without changing dispatch wiring."""
    from core.agent.dispatch import _build_approval_hook

    class FakeNonOwnerTool:
        name = "deliver_artifact"
        owner_only = False
        requires_approval = True

    hook = _build_approval_hook(is_owner=False)
    decision, reason = await hook(FakeNonOwnerTool(), {"path": "/tmp/x"}, None)
    assert decision == "allow"
    assert reason == ""


@pytest.mark.asyncio
async def test_registry_dispatch_denies_owner_only_for_non_owner_with_hook():
    """End-to-end: when ToolRegistry has the D.4 approval hook installed
    AND a non-owner ToolContext is presented, dispatching an owner_only
    tool returns an error block. ToolRegistry.dispatch hits the
    owner_only gate at tools.py:134 first (returns the 'owner-only'
    error message) — this is the existing security layer the hook
    layers behind. We pin both the dispatch denial AND the error
    content here."""
    from core.agent.builtin_tools.local_shell import LocalShellTool
    from core.agent.dispatch import _build_approval_hook
    from core.agent.tools import ToolContext, ToolRegistry
    from core.agent.session import ToolUseBlock

    registry = ToolRegistry()
    registry.register(LocalShellTool())
    registry.set_approval_hook(_build_approval_hook(is_owner=False))

    ctx = ToolContext(turn_owner_id=999, is_owner=False, surface="discord_text")
    use = ToolUseBlock(id="t1", name="local_shell", input={"command": "echo hi"})

    result = await registry.dispatch(use, ctx)
    assert result.is_error is True
    # owner_only gate fires first at tools.py:134 with this message.
    assert "owner-only" in result.content


@pytest.mark.asyncio
async def test_registry_dispatch_executes_owner_only_for_owner_with_hook():
    """Symmetric to above: when is_owner=True, the hook returns "allow"
    AND the owner_only gate clears, so the tool executes. Uses
    `echo` (cross-platform-ish for local_shell on Windows cmd) to
    confirm we actually got past both gates."""
    from core.agent.builtin_tools.local_shell import LocalShellTool
    from core.agent.dispatch import _build_approval_hook
    from core.agent.tools import ToolContext, ToolRegistry
    from core.agent.session import ToolUseBlock

    registry = ToolRegistry()
    registry.register(LocalShellTool())
    registry.set_approval_hook(_build_approval_hook(is_owner=True))

    ctx = ToolContext(turn_owner_id=42, is_owner=True, surface="dashboard")
    use = ToolUseBlock(id="t1", name="local_shell", input={"command": "echo approval-passed"})

    result = await registry.dispatch(use, ctx)
    assert result.is_error is False
    # echo command output should appear in the content.
    assert "approval-passed" in result.content


@pytest.mark.asyncio
async def test_dispatch_installs_approval_hook_in_react_mode(monkeypatch):
    """When dispatch_turn routes through the react branch, the registry
    it constructs has the approval hook installed. We verify this by
    intercepting the registry passed into RunnerDeps and confirming
    its hook reflects the resolved is_owner. This is the wiring
    contract — without it, the per-tool approval gate stays inert."""
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

    # Make local_mode resolve to a known owner id.
    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    # Same dotenv-clobber order as the other dispatch tests.
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from datetime import datetime
    from core.agent.dispatch import dispatch_turn
    from core.turn.types import Turn, TurnMetadata

    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content="hi",
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )
    await dispatch_turn(turn)

    registry = captured_deps["deps"].tool_registry
    # The registry should have an approval hook installed (pre-D.4 it was None).
    assert registry._approval_hook is not None, (
        "dispatch did not install an approval hook on the tool registry"
    )
    # And the hook should approve for the resolved owner (is_owner=True).
    class FakeOwnerTool:
        name = "local_shell"
        owner_only = True
        requires_approval = True
    decision, _ = await registry._approval_hook(FakeOwnerTool(), {}, None)
    assert decision == "allow"


@pytest.mark.asyncio
async def test_dispatch_installs_approval_hook_denies_for_non_owner(monkeypatch):
    """Symmetric to above: when dispatch resolves is_owner=False (turn
    owner mismatch), the installed approval hook denies owner_only
    tools."""
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

    from datetime import datetime
    from core.agent.dispatch import dispatch_turn
    from core.turn.types import Turn, TurnMetadata

    turn = Turn(
        surface="discord_text",
        owner_id=999,  # NOT the owner
        content="hi",
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )
    await dispatch_turn(turn)

    registry = captured_deps["deps"].tool_registry
    assert registry._approval_hook is not None
    class FakeOwnerTool:
        name = "local_shell"
        owner_only = True
        requires_approval = True
    decision, reason = await registry._approval_hook(FakeOwnerTool(), {}, None)
    assert decision == "deny"
    assert "owner" in reason.lower()


def test_deliver_artifact_remains_no_approval_required():
    """deliver_artifact stays at requires_approval=False per the D.4
    step 3 trade-off note: the safe-path allowlist concept doesn't
    exist yet, so auto-approving every deliver_artifact call would be
    a UX regression (every screenshot, every webcam snap would prompt).
    The trade-off: a malicious LLM can still write arbitrary files via
    deliver_artifact, but the surface adapter (Discord) just renders
    them as message attachments — no privilege escalation. Future work:
    add a configurable artifact-directory allowlist, then mark
    requires_approval=True with a permissive hook for paths under
    that directory. Tracked in docs/PHASE-D-HANDOFF.md."""
    from core.agent.builtin_tools.deliver_artifact import DeliverArtifactTool
    tool = DeliverArtifactTool()
    assert tool.requires_approval is False
    # owner_only stays False too — non-owner surfaces can still surface
    # screenshots to the user without elevation.
    assert tool.owner_only is False
