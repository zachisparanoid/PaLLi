"""A tool that ran and failed is an ERROR result (Codex assessment 2026-09-08,
verified 2026-09-09: a non-zero exit and a missing artifact both came back
is_error=False, so the streak detector and the record of her deeds saw success).
"""
from __future__ import annotations

import asyncio

from core.agent.session import ToolUseBlock
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext, ToolRegistry


class _Tool:
    name = "probe"
    description = "x"
    input_schema = {"type": "object"}
    owner_only = False
    requires_approval = False

    def __init__(self, raw):
        self._raw = raw

    async def execute(self, input, ctx):
        return self._raw


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True)


def _dispatch(raw):
    reg = ToolRegistry()
    reg.register(_Tool(raw))
    return asyncio.run(reg.dispatch(ToolUseBlock(id="t1", name="probe", input={}), _ctx()))


def test_a_returned_failure_is_an_error_result():
    res = _dispatch(ToolFailure("exit=1\nno such file"))
    assert res.is_error is True and res.content.startswith("exit=1")


def test_a_returned_string_is_still_success():
    assert _dispatch("exit=0\nok").is_error is False


def test_str_of_a_failure_is_its_text():
    assert str(ToolFailure("boom")) == "boom"


def test_local_shell_nonzero_exit_is_a_failure():
    from core.agent.builtin_tools.local_shell import LocalShellTool
    tool = LocalShellTool()
    raw = asyncio.run(tool.execute({"command": "exit 3"}, _ctx()))
    assert isinstance(raw, ToolFailure) and "exit=3" in str(raw)


def test_deliver_artifact_missing_file_is_a_failure(tmp_path, monkeypatch):
    # tmp_path is outside the allowed roots since week 1 task 2, so the roots
    # are widened the documented way (as test_builtin_tools_basic.py does) —
    # otherwise the refusal, not "not found", is what comes back.
    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", str(tmp_path))
    from core.agent.builtin_tools.deliver_artifact import DeliverArtifactTool
    raw = asyncio.run(DeliverArtifactTool().execute({"path": str(tmp_path / "nope.mp3")}, _ctx()))
    assert isinstance(raw, ToolFailure) and "not found" in str(raw)
