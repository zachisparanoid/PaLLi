"""A drained interim inside a tool round (2026-08-29 20:09, DEFECT_RECORD §13).

The boundary drain writes his mid-run words and her judged (or sidecar)
reply as a user row + a bare assistant row. When the run then continues
with tool calls, that assistant row sits INSIDE the current tool-calling
round with no reasoning_content — which a thinking-mode provider refuses
("must be passed back"). The repair is grammar, payload-only: the interim
becomes an annotation inside the user message it answered. Identity on any
payload with no such row."""
from __future__ import annotations

from core.agent.session import (
    AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock, fold_interims_for_thinking,
)


def _u(t): return AgentMessage(role="user", content=[TextBlock(text=t)])
def _a(t): return AgentMessage(role="assistant", content=[TextBlock(text=t)])
def _tu(i, note=""):
    blocks = ([TextBlock(text=note)] if note else []) + [ToolUseBlock(id=i, name="local_shell", input={})]
    return AgentMessage(role="assistant", content=blocks, reasoning="thought")
def _tr(i): return AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id=i, content="ok", is_error=False)])


def test_the_20_09_shape_folds_the_interim_into_his_message():
    msgs = [_u("print a dummy 13"), _tu("t1", "checking"), _tr("t1"),
            _u("also, PLA or PETG?"), _a("PLA for armor, PETG for frame parts"),
            _tu("t2"), _tr("t2")]
    out = fold_interims_for_thinking(msgs)
    roles = [m.role for m in out]
    assert roles == ["user", "assistant", "tool", "user", "assistant", "tool"]
    folded = out[3].content[0].text
    assert folded.startswith("also, PLA or PETG?") and "PLA for armor" in folded and "carry on" in folded
    assert all(m.reasoning for m in out if m.role == "assistant")      # nothing unreasoned left in the round
    assert len(msgs) == 7                                              # input untouched


def test_interim_at_the_tail_is_folded_too():
    msgs = [_u("build it"), _tu("t1"), _tr("t1"), _u("wifi?"), _a("on the fridge")]
    out = fold_interims_for_thinking(msgs)
    assert [m.role for m in out] == ["user", "assistant", "tool", "user"]
    assert "on the fridge" in out[-1].content[0].text


def test_identity_on_ordinary_history_and_previous_rounds():
    msgs = [_u("hi"), _a("hey"), _u("how's it going"), _a("good"), _u("now build it"), _tu("t1"), _tr("t1")]
    assert fold_interims_for_thinking(msgs) is msgs                    # earlier replies are previous rounds
    plain = [_u("hi"), _a("hey")]
    assert fold_interims_for_thinking(plain) is plain                  # a finished plain exchange


def test_only_the_interim_in_the_current_round_folds():
    """Two stacked interims: the earlier one sits before a later user row —
    a PREVIOUS round to the provider, which needs no reasoning there. Only
    the one after the last user row is inside the tool round."""
    msgs = [_u("build it"), _tu("t1"), _tr("t1"), _u("q1"), _a("a1"), _u("q2"), _a("a2"), _tu("t2"), _tr("t2")]
    out = fold_interims_for_thinking(msgs)
    assert [m.role for m in out] == ["user", "assistant", "tool", "user", "assistant", "user", "assistant", "tool"]
    assert out[4].content[0].text == "a1"                              # left as record
    assert out[5].content[0].text.startswith("q2") and "a2" in out[5].content[0].text
