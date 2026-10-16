"""Fixture suite for normalize_for_llm — the transcript grammar law.

Spec: docs/superpowers/specs/2026-07-26-transcript-validity-boundary-design.md
Fixtures F-incident…F-golden mirror spec §9. House pattern: the module under
test is loaded isolated via tests/agent/conftest.load_module, so all message
objects in THIS file must be built from that same loaded module (isinstance
checks inside it would fail against classes from a second load).
"""
from __future__ import annotations


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/session.py", "agent_session_under_test")


# --- builders (take the loaded module so classes always match) -------------

def _u(mod, text):
    return mod.AgentMessage(role="user", content=[mod.TextBlock(text=text)])


def _a_text(mod, text):
    return mod.AgentMessage(role="assistant", content=[mod.TextBlock(text=text)])


def _a_tools(mod, *ids, text=None):
    blocks = [mod.TextBlock(text=text)] if text else []
    blocks += [mod.ToolUseBlock(id=i, name="local_shell", input={"command": "x"}) for i in ids]
    return mod.AgentMessage(role="assistant", content=blocks)


def _t(mod, *pairs):
    """pairs: (tool_use_id, content) or (tool_use_id, content, is_error)."""
    return mod.AgentMessage(role="tool", content=[
        mod.ToolResultBlock(tool_use_id=p[0], content=p[1],
                            is_error=(p[2] if len(p) > 2 else False))
        for p in pairs
    ])


# --- the oracle (duck-typed: works for messages from ANY module load) ------

def assert_provider_valid(messages) -> None:
    """Asserts spec invariants I1–I5 over an AgentMessage list."""
    pending: set[str] = set()
    for m in messages:
        results = [b for b in m.content if getattr(b, "type", "") == "tool_result"]
        if results:
            assert pending, (
                f"orphan tool_result(s): {[b.tool_use_id for b in results]} (I2/I4)")
            assert len(results) == len(m.content), "mixed result/non-result message"
            for b in results:
                assert b.tool_use_id in pending, (
                    f"unexpected/duplicate tool_use_id {b.tool_use_id!r} (I2)")
                pending.discard(b.tool_use_id)
            continue
        assert not pending, (
            f"open tool calls {sorted(pending)} followed by a {m.role} message (I1)")
        texts = [b for b in m.content
                 if getattr(b, "type", "") == "text" and b.text]
        uses = [b for b in m.content if getattr(b, "type", "") == "tool_use"]
        assert texts or uses, f"empty {m.role} message (I3)"
        if m.role == "assistant" and uses:
            ids = [b.id for b in uses]
            assert all(ids), "empty tool_use id (I5)"
            assert len(set(ids)) == len(ids), "duplicate tool_use ids (I5)"
            pending = set(ids)
    assert not pending, f"payload ends on unanswered tool calls {sorted(pending)} (I1)"


# --- fixtures --------------------------------------------------------------

def test_f_incident_orphan_call_with_barge_in_users_behind_it():
    """The verbatim 2026-07-25 on-disk shape: assistant tool_use, then the
    owner's barge-in messages. Placeholder must land ADJACENT to its call
    (where the hand-repair went), user messages preserved after it."""
    mod = _load()
    msgs = [
        _u(mod, "Login to my laptop and reduce the volume to 0."),
        _a_tools(mod, "call_00_OACeXg", text="Machine's alive. Let me check."),
        _u(mod, "Oh and by the way when you SSH into it..."),
        _u(mod, "Uh oh, everything all right?"),
        _u(mod, "Hey yo, you there?"),
    ]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert [m.role for m in out] == ["user", "assistant", "tool", "user", "user", "user"]
    placeholder = out[2].content[0]
    assert placeholder.tool_use_id == "call_00_OACeXg"
    assert placeholder.is_error is True
    assert placeholder.content == mod._INTERRUPTED_RESULT_TEXT
    assert out[3].content[0].text.startswith("Oh and by the way")


def test_f_trailing_bare_orphan_call():
    mod = _load()
    msgs = [_u(mod, "hi"), _a_tools(mod, "c1")]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert out[-1].role == "tool"
    assert out[-1].content[0].tool_use_id == "c1"
    assert out[-1].content[0].is_error is True


def test_f_late_result_reordered_not_synthesized():
    """Shield-race shape (spec §7): call → user → real result. The real
    result MOVES adjacent; nothing is synthesized."""
    mod = _load()
    msgs = [
        _a_tools(mod, "c1"),
        _u(mod, "barge-in"),
        _t(mod, ("c1", "exit=0 done")),
    ]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert [m.role for m in out] == ["assistant", "tool", "user"]
    assert out[1].content[0].content == "exit=0 done"
    assert out[1].content[0].is_error is False


def test_f_partial_multi_tool_synthesizes_only_missing():
    mod = _load()
    msgs = [_a_tools(mod, "c1", "c2"), _t(mod, ("c2", "two"))]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    blocks = out[1].content
    assert [b.tool_use_id for b in blocks] == ["c1", "c2"]   # tool_use order
    assert blocks[0].content == mod._INTERRUPTED_RESULT_TEXT and blocks[0].is_error
    assert blocks[1].content == "two" and not blocks[1].is_error


def test_f_synthetic_injections_downgraded_to_observations():
    """max-iter-N / loop-detection-N results (runner.py:414/:534) have no
    matching call → become user-visible [system observation] text (fixes
    latent B4 on the openai_compat path)."""
    mod = _load()
    msgs = [
        _a_tools(mod, "c1"),
        _t(mod, ("c1", "real result")),
        _t(mod, ("loop-detection-3", "CRITICAL: stuck", True)),
        _a_text(mod, "stopping now"),
    ]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert [m.role for m in out] == ["assistant", "tool", "user", "assistant"]
    assert out[2].content[0].text == mod._SYSTEM_OBSERVATION_PREFIX + "CRITICAL: stuck"


def test_f_empty_messages_dropped_every_role():
    mod = _load()
    msgs = [
        _u(mod, ""),                                              # 2026-06-11 voice shape
        mod.AgentMessage(role="assistant", content=[]),           # reasoning-burn shape
        mod.AgentMessage(role="tool", content=[]),
        _u(mod, "still here"),
    ]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert len(out) == 1 and out[0].content[0].text == "still here"


def test_f_id_hygiene_empty_and_duplicate_call_ids():
    mod = _load()
    msgs = [_a_tools(mod, "", "c1", "c1", text="doing things"), _t(mod, ("c1", "ok"))]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    uses = [b for b in out[0].content if getattr(b, "type", "") == "tool_use"]
    assert [b.id for b in uses] == ["c1"]                         # ""-id and dup dropped
    assert [b.tool_use_id for b in out[1].content] == ["c1"]


def test_f_duplicate_results_first_wins_second_dropped():
    mod = _load()
    msgs = [_a_tools(mod, "c1"), _t(mod, ("c1", "first"), ("c1", "second"))]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert len(out) == 2
    assert [b.content for b in out[1].content] == ["first"]


def test_f_leading_orphan_results_downgraded():
    """A window that begins mid-round-trip (I4): leading results have no
    claiming call in-window → observations, never role=tool."""
    mod = _load()
    msgs = [_t(mod, ("c9", "stale result")), _u(mod, "morning")]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert out[0].role == "user"
    assert out[0].content[0].text == mod._SYSTEM_OBSERVATION_PREFIX + "stale result"


def test_f_golden_valid_transcript_is_identity():
    """P3 — the no-regression guarantee. A realistic valid multi-iteration
    transcript passes through unchanged (same objects, so == holds)."""
    mod = _load()
    msgs = [
        _u(mod, "check the ports"),
        _a_tools(mod, "c1", text="on it"),
        _t(mod, ("c1", "22 closed")),
        _a_tools(mod, "c2", "c3"),
        _t(mod, ("c2", "80 open"), ("c3", "443 open")),
        _a_text(mod, "80 and 443 are open."),
        _u(mod, "thanks"),
    ]
    out = mod.normalize_for_llm(msgs)
    assert_provider_valid(out)
    assert out == msgs
    assert all(a is b for a, b in zip(out, msgs))


def test_p_idempotent_and_pure_across_all_repair_shapes():
    """P1/P2 over one combined worst-case input."""
    mod = _load()
    msgs = [
        _t(mod, ("ghost", "leading orphan")),
        _u(mod, ""),
        _a_tools(mod, "c1", "c2"),
        _u(mod, "barge-in"),
        _t(mod, ("c2", "late real")),
        _t(mod, ("max-iter-51", "CRITICAL", True)),
        _a_tools(mod, "c4"),
    ]
    snapshot = [(m.role, list(m.content)) for m in msgs]
    once = mod.normalize_for_llm(msgs)
    assert_provider_valid(once)
    twice = mod.normalize_for_llm(once)
    assert twice == once                                          # P2
    assert [(m.role, list(m.content)) for m in msgs] == snapshot  # P1: input untouched


def test_reasoning_survives_normalization():
    """DeepSeek thinking replay: the in-turn assistant message's .reasoning
    must ride through untouched (session.py:54-61 contract)."""
    mod = _load()
    asst = mod.AgentMessage(
        role="assistant",
        content=[mod.ToolUseBlock(id="c1", name="local_shell", input={})],
        reasoning="chain of thought",
    )
    out = mod.normalize_for_llm([asst])
    assert out[0].reasoning == "chain of thought"
