from __future__ import annotations

import asyncio


def _load_whoami():
    from tests.agent.conftest import load_module
    return load_module("core/agent/builtin_tools/whoami.py", "whoami_test")


def _ctx(is_owner: bool = True):
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_whoami_test")
    return tools_mod.ToolContext(turn_owner_id=1, is_owner=is_owner)


def test_whoami_describes_loop_and_tools():
    mod = _load_whoami()
    tool = mod.WhoamiTool(loop_description="ReAct mode", available_tool_names=["read_file", "local_shell"])
    out = asyncio.run(tool.execute({}, _ctx()))
    assert "ReAct" in out
    assert "read_file" in out
    assert "local_shell" in out


def test_whoami_hands_paragraph_gated_on_owner_and_registration():
    # The hands paragraph narrates owner-only levers: it renders ONLY in an
    # owner session where the hands tools are actually registered. A guest
    # asking "what can you do?" never hears about them, and an owner
    # session without the flag doesn't get promised absent levers.
    mod = _load_whoami()
    with_hands = mod.WhoamiTool(
        loop_description="x",
        available_tool_names=["send_message", "read_file"])
    assert "Your hands" in asyncio.run(with_hands.execute({}, _ctx()))
    assert "Your hands" not in asyncio.run(
        with_hands.execute({}, _ctx(is_owner=False)))
    without_hands = mod.WhoamiTool(
        loop_description="x", available_tool_names=["read_file"])
    assert "Your hands" not in asyncio.run(without_hands.execute({}, _ctx()))


def test_whoami_input_schema_is_empty():
    mod = _load_whoami()
    tool = mod.WhoamiTool(loop_description="x", available_tool_names=[])
    assert tool.input_schema["properties"] == {}
