from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/tools.py", "agent_tools_under_test")


def _load_session():
    """Returns the canonical core.agent.session module — same instance
    that core/agent/tools.py imports. importlib re-loading would create
    a separate class object and break isinstance checks against
    ToolResultBlock returned from registry.dispatch()."""
    import core.agent.session as m
    return m


class StubTool:
    name = "stub"
    description = "echoes input.text"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx) -> str:
        return f"echo: {input['text']}"


def test_tool_registry_register_and_schemas():
    mod = _load()
    reg = mod.ToolRegistry()
    reg.register(StubTool())

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    schemas = reg.schemas_for(ctx)
    assert len(schemas) == 1
    assert schemas[0]["name"] == "stub"
    assert schemas[0]["description"] == "echoes input.text"
    assert "input_schema" in schemas[0]


def test_tool_registry_dispatch_returns_tool_result():
    mod = _load()
    sess_mod = _load_session()
    reg = mod.ToolRegistry()
    reg.register(StubTool())

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t1", name="stub", input={"text": "hi"})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert isinstance(result, sess_mod.ToolResultBlock)
    assert result.tool_use_id == "t1"
    assert "echo: hi" in result.content
    assert not result.is_error


def test_tool_registry_unknown_tool_returns_error_result():
    mod = _load()
    sess_mod = _load_session()
    reg = mod.ToolRegistry()

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t2", name="nonexistent", input={})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert result.is_error
    assert "nonexistent" in result.content


def test_tool_registry_dispatch_catches_exceptions():
    mod = _load()
    sess_mod = _load_session()

    class BoomTool:
        name = "boom"
        description = "always fails"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx):
            raise RuntimeError("kaboom")

    reg = mod.ToolRegistry()
    reg.register(BoomTool())

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t3", name="boom", input={})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert result.is_error
    assert "kaboom" in result.content


def test_owner_only_tool_filtered_for_non_owner():
    mod = _load()
    reg = mod.ToolRegistry()

    class OwnerTool:
        name = "owner_tool"
        description = "owner only"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = True
        async def execute(self, input, ctx): return "ok"

    reg.register(StubTool())
    reg.register(OwnerTool())

    owner_ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    nonowner_ctx = mod.ToolContext(turn_owner_id=2, is_owner=False)

    owner_names = {s["name"] for s in reg.schemas_for(owner_ctx)}
    nonowner_names = {s["name"] for s in reg.schemas_for(nonowner_ctx)}

    assert "stub" in owner_names
    assert "owner_tool" in owner_names
    assert "stub" in nonowner_names
    assert "owner_tool" not in nonowner_names


def test_approval_hook_can_deny():
    mod = _load()
    sess_mod = _load_session()
    reg = mod.ToolRegistry()

    class GatedTool:
        name = "gated"
        description = "needs approval"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = True
        owner_only = False
        async def execute(self, input, ctx): return "ran"

    reg.register(GatedTool())

    async def hook(tool, args, ctx):
        return ("deny", "user said no")

    reg.set_approval_hook(hook)

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t4", name="gated", input={})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert result.is_error
    assert "user said no" in result.content


def test_approval_hook_default_allow_for_non_gated():
    mod = _load()
    sess_mod = _load_session()
    reg = mod.ToolRegistry()
    reg.register(StubTool())

    # Hook that would deny if called — but stub doesn't require approval, so hook isn't called
    async def hook(tool, args, ctx): return ("deny", "should not see this")
    reg.set_approval_hook(hook)

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t5", name="stub", input={"text": "hi"})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert not result.is_error
    assert "echo: hi" in result.content


def test_tool_result_truncation_writes_sidecar(tmp_path):
    import os
    mod = _load()
    sess_mod = _load_session()

    big_text = "X" * 50_000  # 50KB

    class BigTool:
        name = "big"
        description = "returns lots"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx): return big_text

    reg = mod.ToolRegistry()
    reg.register(BigTool())

    sidecar_root = tmp_path / "tool_results"
    os.environ["TOOL_RESULT_MAX_BYTES"] = "10240"
    os.environ["TOOL_RESULT_SIDECAR_ROOT"] = str(sidecar_root)
    try:
        ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
        tu = sess_mod.ToolUseBlock(id="t-big", name="big", input={})
        result = asyncio.run(reg.dispatch(tu, ctx))
    finally:
        os.environ.pop("TOOL_RESULT_MAX_BYTES", None)
        os.environ.pop("TOOL_RESULT_SIDECAR_ROOT", None)

    # Result body is truncated
    assert len(result.content) < 12_000  # 10KB body + truncation marker
    assert "[result truncated" in result.content
    assert str(sidecar_root) in result.content or "t-big" in result.content

    # Sidecar file holds full content
    sidecar = sidecar_root / "t-big.txt"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == big_text


def test_tool_result_under_limit_not_truncated():
    mod = _load()
    sess_mod = _load_session()

    class SmallTool:
        name = "small"
        description = "small output"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx): return "tiny"

    reg = mod.ToolRegistry()
    reg.register(SmallTool())

    ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
    tu = sess_mod.ToolUseBlock(id="t-small", name="small", input={})
    result = asyncio.run(reg.dispatch(tu, ctx))

    assert result.content == "tiny"
    assert "truncated" not in result.content


def test_truncation_marker_uses_absolute_sidecar_path(tmp_path):
    import os
    mod = _load()
    sess_mod = _load_session()

    big_text = "Y" * 30_000

    class BigTool:
        name = "big2"
        description = "returns lots"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx): return big_text

    reg = mod.ToolRegistry()
    reg.register(BigTool())

    # Use a relative env-var-set path; verify the marker holds the absolute form.
    os.environ["TOOL_RESULT_MAX_BYTES"] = "5000"
    os.environ["TOOL_RESULT_SIDECAR_ROOT"] = str(tmp_path / "rel" / "sidecars")
    try:
        ctx = mod.ToolContext(turn_owner_id=1, is_owner=True)
        tu = sess_mod.ToolUseBlock(id="t-abspath", name="big2", input={})
        result = asyncio.run(reg.dispatch(tu, ctx))
    finally:
        os.environ.pop("TOOL_RESULT_MAX_BYTES", None)
        os.environ.pop("TOOL_RESULT_SIDECAR_ROOT", None)

    # The marker should embed an absolute path
    import re
    match = re.search(r"saved to (.+?)\]", result.content)
    assert match, f"truncation marker not found in: {result.content[-200:]}"
    embedded = Path(match.group(1))
    assert embedded.is_absolute(), f"path is relative: {embedded}"
    # And the file exists at that absolute path
    assert embedded.exists()
