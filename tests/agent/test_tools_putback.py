"""Batch 1, Task 3 (2026-09-26): the put-back into her tool arguments.

The model only ever sees `[MASKED:<hex>]`; when it hands a token back as a
tool argument, the tool must receive the REAL value (a key she files, a
number she looks up) — the swap happens on the PARSED input dict, never on
JSON text. The tool's RESULT is never put back: it stays exactly as the tool
produced it, and the provider seam masks it again on the next request.

Fake secrets only.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.agent import payload_masking as pm
from core.agent.session import ToolResultBlock, ToolUseBlock
from core.agent.tools import ToolContext, ToolRegistry

KEY = "gsk_" + "Zq9" * 12
SSN = "987-65-4321"


class _RecordingTool:
    name = "echo"
    description = "records what it was handed"
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = False

    def __init__(self, returns: str = "ok"):
        self.seen: dict[str, Any] = {}
        self._returns = returns

    async def execute(self, input: dict[str, Any], ctx) -> str:
        self.seen = input
        return self._returns


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True)


@pytest.mark.asyncio
async def test_tool_receives_the_real_value_for_a_masked_argument():
    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    tool = _RecordingTool()
    reg = ToolRegistry()
    reg.register(tool)

    result = await reg.dispatch(
        ToolUseBlock(id="t1", name="echo", input={"cmd": f"file {token}"}), _ctx(),
    )

    assert isinstance(result, ToolResultBlock) and not result.is_error
    assert tool.seen["cmd"] == f"file {KEY}"


@pytest.mark.asyncio
async def test_put_back_reaches_nested_dicts_and_lists_and_leaves_other_types_alone():
    pm.reset_for_tests()
    key_tok, ssn_tok = pm.mask_text(KEY), pm.mask_text(SSN)
    tool = _RecordingTool()
    reg = ToolRegistry()
    reg.register(tool)

    await reg.dispatch(
        ToolUseBlock(id="t2", name="echo", input={
            "entry": {"title": "groq", "password": key_tok},
            "lines": [f"ssn {ssn_tok}", "plain", 7],
            "count": 3,
            "flag": True,
            "nothing": None,
        }),
        _ctx(),
    )

    assert tool.seen["entry"] == {"title": "groq", "password": KEY}
    assert tool.seen["lines"] == [f"ssn {SSN}", "plain", 7]
    assert tool.seen["count"] == 3 and tool.seen["flag"] is True and tool.seen["nothing"] is None


@pytest.mark.asyncio
async def test_the_model_side_tool_use_block_keeps_the_token():
    """Put-back is a copy handed to the tool — the ToolUseBlock (what her
    transcript and the next request carry) still holds the token."""
    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    tool = _RecordingTool()
    reg = ToolRegistry()
    reg.register(tool)
    use = ToolUseBlock(id="t3", name="echo", input={"cmd": f"file {token}"})

    await reg.dispatch(use, _ctx())

    assert use.input == {"cmd": f"file {token}"}


@pytest.mark.asyncio
async def test_a_tool_result_is_never_put_back():
    """Review Focus 3: a tool whose output carries a token gets that token
    back in its RESULT, untouched — put-back applies to tool INPUT only."""
    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    tool = _RecordingTool(returns=f"stored as {token}")
    reg = ToolRegistry()
    reg.register(tool)

    result = await reg.dispatch(
        ToolUseBlock(id="t4", name="echo", input={"cmd": "list"}), _ctx(),
    )

    assert result.content == f"stored as {token}"
    assert KEY not in result.content


@pytest.mark.asyncio
async def test_an_unknown_token_passes_through_as_is():
    """A token this process never minted (a stale one from before a restart)
    has nothing to put back — the tool gets the literal token, not a crash."""
    pm.reset_for_tests()
    tool = _RecordingTool()
    reg = ToolRegistry()
    reg.register(tool)

    await reg.dispatch(
        ToolUseBlock(id="t5", name="echo", input={"cmd": "use [MASKED:deadbeef]"}), _ctx(),
    )

    assert tool.seen["cmd"] == "use [MASKED:deadbeef]"
