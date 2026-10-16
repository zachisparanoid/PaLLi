"""The payload must end on his words (2026-08-28, the thinking-mode 400).

A turn dispatched for an exchange already on the record — the deadline
fallback, the post-run sweep, a full_turn's real answer — reads a transcript
whose last row is HER interim reply. DeepSeek's thinking mode refuses to
continue an assistant message it has no reasoning_content for (nothing on
disk carries reasoning), so the payload 400'd every time, silently. The
repair is grammar, not meaning: re-present his last words as the tail,
payload-only. Identity on any payload that already ends on him or on a
tool result."""
from __future__ import annotations

from core.agent.session import (
    AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock, ensure_user_tail,
)


def _u(t): return AgentMessage(role="user", content=[TextBlock(text=t)])
def _a(t): return AgentMessage(role="assistant", content=[TextBlock(text=t)])


def test_assistant_tail_gets_his_last_words_re_presented():
    msgs = [_u("you stuck?"), _a("hold on, mid-thing")]
    out = ensure_user_tail(msgs)
    assert [m.role for m in out] == ["user", "assistant", "user"]
    assert out[-1].content[0].text == "you stuck?"
    assert len(msgs) == 2                                  # input never mutated


def test_identity_when_the_payload_already_ends_on_him():
    msgs = [_a("earlier"), _u("now")]
    assert ensure_user_tail(msgs) is msgs


def test_identity_when_the_payload_ends_on_a_tool_result():
    tu = AgentMessage(role="assistant", content=[ToolUseBlock(id="t1", name="x", input={})])
    tr = AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)])
    msgs = [_u("go"), tu, tr]
    assert ensure_user_tail(msgs) is msgs


def test_no_user_anywhere_uses_the_fallback_or_stays_identity():
    msgs = [_a("she spoke first")]
    out = ensure_user_tail(msgs, fallback_text="his turn text")
    assert out[-1].role == "user" and out[-1].content[0].text == "his turn text"
    assert ensure_user_tail(msgs) is msgs                  # nothing to invent


def test_empty_payload_is_identity():
    assert ensure_user_tail([]) == []


def test_continuation_note_replaces_re_presenting_his_words():
    """Correction 4: after a drained exchange mid-run the tail must say
    'carry on', not re-ask — or the parent answers him twice."""
    msgs = [_u("build it"), _a("on it"), _u("what's the wifi?"), _a("on the fridge")]
    note = "[his words above arrived while you worked and you already answered them — carry on with the task]"
    out = ensure_user_tail(msgs, continuation=note)
    assert out[-1].role == "user" and out[-1].content[0].text == note
    assert ensure_user_tail([_u("x")], continuation=note) == [_u("x")] or ensure_user_tail([_u("x")], continuation=note)[-1].content[0].text == "x"
