"""Tool receipts (2026-09-14): when the payload diet strips an earlier turn's
tool round-trips, it leaves one short line per call behind instead of nothing.

Why: since 2026-08-18 the diet has dropped every past turn's tool results and
kept only her own text about them. So from the next turn on, her record of
what a command said is her summary of it — and if the summary was wrong, the
wrong version is the only version she has. "Breaking Bad finished clean"
(2026-09-11 22:21) was false when she said it and became the only Breaking
Bad fact in her history; the grounding check (DEFECT §28) could not catch it
because the disproving output lived in the previous turn.

The receipt is the system's, not hers: it opens with a mark that says so and
says he did not see it, like the working-note stamp (§20). Bounded: input ≤100
chars, result ≤200, at most 8 per assistant message. A tool-only assistant
message, which the diet used to drop entirely, now survives as receipts."""
from __future__ import annotations

from tests.agent.conftest import load_module


def _mod():
    return load_module("core/agent/session.py", "agent_session_receipts_under_test")


def _u(mod, t):
    return mod.AgentMessage(role="user", content=[mod.TextBlock(text=t)])


def _a(mod, text=None, calls=()):
    blocks = ([mod.TextBlock(text=text)] if text else []) + [
        mod.ToolUseBlock(id=cid, name=name, input=inp) for cid, name, inp in calls]
    return mod.AgentMessage(role="assistant", content=blocks)


def _t(mod, results):
    return mod.AgentMessage(role="tool", content=[
        mod.ToolResultBlock(tool_use_id=cid, content=text, is_error=err) for cid, text, err in results])


def _receipts(mod, msg):
    return [b.text for b in msg.content if isinstance(b, mod.TextBlock) and b.text.startswith(mod.RECEIPT_MARK)]


def test_a_past_tool_round_leaves_a_receipt_behind():
    mod = _mod()
    msgs = [
        _u(mod, "is the link reachable?"),
        _a(mod, "checking", calls=[("c1", "local_shell", {"command": "curl -sI https://x.test/f.apk"})]),
        _t(mod, [("c1", "HTTP/2 200 remote_ip=100.64.0.9", False)]),
        _a(mod, "Serves fine from here."),
        _u(mod, "and now?"),
    ]
    out = mod.diet_for_llm(msgs, keep_from=4)
    assert all(m.role != "tool" for m in out[:4])
    first = out[1]
    texts = [b.text for b in first.content if isinstance(b, mod.TextBlock)]
    assert texts[0] == "checking"
    rec = _receipts(mod, first)
    assert len(rec) == 1
    assert "local_shell(" in rec[0] and "curl -sI https://x.test/f.apk" in rec[0]
    assert "HTTP/2 200 remote_ip=100.64.0.9" in rec[0]
    assert not any(isinstance(b, (mod.ToolUseBlock, mod.ToolResultBlock)) for m in out[:4] for b in m.content)
    assert out[-1] is msgs[-1]            # this run (the last user message) is the same object
    assert len(out) == 4                   # the tool message is gone, nothing else is


def test_the_receipt_says_it_is_the_systems_and_unseen():
    mod = _mod()
    assert mod.RECEIPT_MARK.startswith("[tool receipt")
    assert "not seen by them" in mod.RECEIPT_MARK
    msgs = [_u(mod, "x"), _a(mod, "t", calls=[("c1", "read_file", {"path": "/a"})]), _t(mod, [("c1", "hello", False)]), _u(mod, "y")]
    rec = _receipts(mod, mod.diet_for_llm(msgs, keep_from=3)[1])
    assert rec[0].startswith(mod.RECEIPT_MARK)


def test_a_failed_tool_is_marked_failed():
    mod = _mod()
    msgs = [_u(mod, "x"), _a(mod, "t", calls=[("c1", "local_shell", {"command": "ssh pve"})]),
            _t(mod, [("c1", "exit=255\nssh: connect timed out", True)]), _u(mod, "y")]
    rec = _receipts(mod, mod.diet_for_llm(msgs, keep_from=3)[1])
    assert "FAILED" in rec[0] and "timed out" in rec[0]


def test_long_input_and_result_are_bounded():
    mod = _mod()
    msgs = [_u(mod, "x"), _a(mod, "t", calls=[("c1", "local_shell", {"command": "c" * 5000})]),
            _t(mod, [("c1", "r" * 5000, False)]), _u(mod, "y")]
    rec = _receipts(mod, mod.diet_for_llm(msgs, keep_from=3)[1])
    assert len(rec[0]) < 420
    assert "…" in rec[0]


def test_a_tool_only_assistant_message_survives_as_receipts():
    """The diet used to drop it entirely ("a tool-only assistant message goes
    entirely"); now the machine's word stays, as a receipt-only message."""
    mod = _mod()
    msgs = [_u(mod, "x"), _a(mod, None, calls=[("c1", "web_search", {"q": "z"})]),
            _t(mod, [("c1", "3 results", False)]), _a(mod, "here they are"), _u(mod, "y")]
    out = mod.diet_for_llm(msgs, keep_from=4)
    assert out[1].role == "assistant"
    rec = _receipts(mod, out[1])
    assert len(rec) == 1 and "web_search(" in rec[0] and "3 results" in rec[0]
    assert len([b for b in out[1].content if isinstance(b, mod.TextBlock)]) == 1


def test_receipts_are_capped_per_message():
    mod = _mod()
    calls = [(f"c{i}", "local_shell", {"command": f"cmd{i}"}) for i in range(10)]
    results = [(f"c{i}", f"out{i}", False) for i in range(10)]
    msgs = [_u(mod, "x"), _a(mod, "t", calls=calls), _t(mod, results), _u(mod, "y")]
    rec = _receipts(mod, mod.diet_for_llm(msgs, keep_from=3)[1])
    assert len(rec) == mod.RECEIPTS_PER_MESSAGE == 8
    assert "cmd9" in rec[-1] or "more call" in rec[-1]   # the last ones, or a tail count
    assert any("more call" in r for r in rec)


def test_a_missing_result_is_said_so():
    mod = _mod()
    msgs = [_u(mod, "x"), _a(mod, "t", calls=[("c1", "local_shell", {"command": "ls"})]), _u(mod, "y")]
    rec = _receipts(mod, mod.diet_for_llm(msgs, keep_from=2)[1])
    assert "no result recorded" in rec[0]


def test_this_run_is_untouched_and_identity_holds():
    mod = _mod()
    msgs = [_u(mod, "x"), _a(mod, "t", calls=[("c1", "local_shell", {"command": "ls"})]), _t(mod, [("c1", "a", False)])]
    assert mod.diet_for_llm(msgs, keep_from=0) is msgs          # everything is this run: identity
    plain = [_u(mod, "x"), _a(mod, "plain")]
    assert mod.diet_for_llm(plain, keep_from=2) is plain         # nothing to strip: identity


def test_an_imitated_receipt_mark_is_stripped_from_her_reply():
    """§20's rule for the working-note stamp holds for the receipt mark too:
    it is the system's; if she writes it herself it never reaches him."""
    mod = _mod()
    text = f"{mod.RECEIPT_MARK} local_shell(ls) → ok\nAll clean here."
    assert mod.strip_working_note_mark(text) == "All clean here."
