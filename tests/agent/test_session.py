from __future__ import annotations

import asyncio
from pathlib import Path


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/session.py", "agent_session_under_test")


def test_session_create_appends_and_persists(tmp_path: Path):
    mod = _load()
    sess = asyncio.run(mod.Session.open_or_create("chan-1", root=tmp_path))
    assert sess.messages == []

    msg = mod.AgentMessage(role="user", content=[mod.TextBlock(text="hi")])
    asyncio.run(sess.append(msg))

    # Persisted to disk
    path = tmp_path / "chan-1.jsonl"
    assert path.exists()
    contents = path.read_text(encoding="utf-8").strip()
    assert "hi" in contents


def test_session_open_restores_messages(tmp_path: Path):
    mod = _load()
    sess1 = asyncio.run(mod.Session.open_or_create("chan-2", root=tmp_path))
    asyncio.run(sess1.append(mod.AgentMessage(role="user", content=[mod.TextBlock(text="first")])))
    asyncio.run(sess1.append(mod.AgentMessage(role="assistant", content=[mod.TextBlock(text="reply")])))

    sess2 = asyncio.run(mod.Session.open_or_create("chan-2", root=tmp_path))
    assert len(sess2.messages) == 2
    assert sess2.messages[0].role == "user"
    assert sess2.messages[1].role == "assistant"


def test_session_corrupt_line_truncates_to_last_good(tmp_path: Path):
    mod = _load()
    path = tmp_path / "chan-3.jsonl"
    path.write_text(
        '{"role": "user", "content": [{"type": "text", "text": "a"}], "timestamp": "2026-05-07T00:00:00Z"}\n'
        'this is not json\n',
        encoding="utf-8",
    )
    sess = asyncio.run(mod.Session.open_or_create("chan-3", root=tmp_path))
    assert len(sess.messages) == 1
    assert sess.messages[0].content[0].text == "a"


def test_session_key_with_colon_works_on_windows(tmp_path: Path):
    """Windows-incompatible characters in session_key get sanitized for the file path."""
    mod = _load()
    sess = asyncio.run(mod.Session.open_or_create("proactive:owner-7", root=tmp_path))
    msg = mod.AgentMessage(role="user", content=[mod.TextBlock(text="x")])
    asyncio.run(sess.append(msg))
    # File should be created (sanitized) — not raise OSError
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert ":" not in files[0].name
    # And the original session_key is preserved on the object
    assert sess.session_key == "proactive:owner-7"


# ---------------------------------------------------------------------------
# window_for_llm — bounded history for the LLM payload (2026-06-11).
# A live Discord channel reached 471 messages / ~85k prompt tokens, ALL
# replayed on every turn. The window caps what goes to the provider while
# the JSONL on disk stays the full archive.
# ---------------------------------------------------------------------------


def _text_msg(mod, role: str, text: str):
    return mod.AgentMessage(role=role, content=[mod.TextBlock(text=text)])


def test_window_for_llm_short_history_unchanged():
    mod = _load()
    msgs = [_text_msg(mod, "user", "a"), _text_msg(mod, "assistant", "b")]
    assert mod.window_for_llm(msgs, 10) == msgs


def test_window_for_llm_nonpositive_cap_disables_windowing():
    """Cap <= 0 is the explicit 'unlimited' escape hatch."""
    mod = _load()
    msgs = [_text_msg(mod, "user", f"m{i}") for i in range(20)]
    assert mod.window_for_llm(msgs, 0) == msgs
    assert mod.window_for_llm(msgs, -1) == msgs


def test_window_for_llm_caps_and_starts_on_user_message():
    """The window keeps the most recent messages but must begin on a user
    message: an OpenAI-style payload whose first messages are a dangling
    assistant reply (or worse, a tool result whose tool_use was cut) is
    rejected by the provider."""
    mod = _load()
    msgs = []
    for i in range(15):
        msgs.append(_text_msg(mod, "user", f"u{i}"))
        msgs.append(_text_msg(mod, "assistant", f"a{i}"))
    # 30 messages; cap 9 → naive slice starts at index 21 (an assistant
    # message) and must snap forward to the next user message.
    out = mod.window_for_llm(msgs, 9)
    assert len(out) <= 9
    assert out[0].role == "user"
    # The tail is preserved verbatim — most recent message is last.
    assert out[-1] is msgs[-1]


def test_window_for_llm_never_splits_a_tool_round_trip():
    """Regression guard for the 400 class of failures: a role=tool message
    is only valid if the SAME payload contains the prior assistant message
    carrying the matching tool_use id. The window must never cut between
    them."""
    mod = _load()
    msgs = []
    for t in range(10):
        msgs.append(_text_msg(mod, "user", f"u{t}"))
        msgs.append(mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id=f"call_{t}", name="echo", input={"text": str(t)}),
        ]))
        msgs.append(mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id=f"call_{t}", content=f"echoed {t}"),
        ]))
        msgs.append(_text_msg(mod, "assistant", f"done {t}"))
    # 40 messages; cap 6 → naive slice starts at index 34, which is a tool
    # result whose tool_use lives at index 33 — outside the naive window.
    out = mod.window_for_llm(msgs, 6)
    assert out[0].role == "user"
    seen_tool_use_ids = set()
    for m in out:
        for b in m.content:
            if isinstance(b, mod.ToolUseBlock):
                seen_tool_use_ids.add(b.id)
            if isinstance(b, mod.ToolResultBlock):
                assert b.tool_use_id in seen_tool_use_ids, (
                    f"tool result {b.tool_use_id} has no matching tool_use in the window"
                )


def test_window_for_llm_no_user_in_window_anchors_to_earlier_user():
    """Pathological tail: the last ``max_messages`` are one long tool-call chain
    with NO user message inside the window. The old fallback returned the plain
    ``messages[start:]`` slice, which begins on a tool_result whose tool_use was
    cut off — the provider 400s ("tool result has no matching tool_use"). The
    window must instead walk BACK to the earlier user message."""
    mod = _load()
    msgs = [_text_msg(mod, "user", "u0")]
    for t in range(10):
        msgs.append(mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id=f"call_{t}", name="echo", input={"text": str(t)}),
        ]))
        msgs.append(mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id=f"call_{t}", content=f"echoed {t}"),
        ]))
    # 21 messages; cap 7 → naive start=14 (a tool_result whose tool_use is at
    # index 13, outside the window) and messages[14:] has no user message.
    out = mod.window_for_llm(msgs, 7)
    assert out[0].role == "user", (
        f"window must start on a user message, got role={out[0].role!r}"
    )
    # And no tool result may be orphaned from its matching tool_use.
    seen_ids: set[str] = set()
    for m in out:
        for b in m.content:
            if isinstance(b, mod.ToolUseBlock):
                seen_ids.add(b.id)
            if isinstance(b, mod.ToolResultBlock):
                assert b.tool_use_id in seen_ids, (
                    f"orphaned tool result {b.tool_use_id} in window"
                )


def test_window_for_llm_degenerate_no_user_never_starts_on_tool_result():
    """Truly degenerate session with NO user message anywhere (e.g. a proactive
    tool-only transcript). The window still must not BEGIN on a tool_result,
    which the provider rejects as an orphaned round-trip."""
    mod = _load()
    msgs = []
    for t in range(6):
        msgs.append(mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id=f"call_{t}", name="echo", input={"text": str(t)}),
        ]))
        msgs.append(mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id=f"call_{t}", content=f"echoed {t}"),
        ]))
    # 12 messages, no user anywhere; cap 5 → naive start=7 (a tool_result).
    out = mod.window_for_llm(msgs, 5)
    first = out[0]
    is_tool_result = first.role == "tool" or any(
        isinstance(b, mod.ToolResultBlock) for b in first.content
    )
    assert not is_tool_result, (
        f"window must not start on a tool_result; got role={first.role!r}"
    )


# The window moves in steps (Batch 2, 2026-09-27): its START only advances
# every ``step`` messages, so consecutive turns send a byte-identical history
# prefix the provider can serve from its cache. The cap is untouched.


def test_window_step_moves_the_start_only_every_step_messages():
    mod = _load()
    msgs = [_text_msg(mod, "user", f"u{i}") if i % 2 == 0 else _text_msg(mod, "assistant", f"a{i}")
            for i in range(1000)]
    starts = set()
    for n in range(900, 960):
        w = mod.window_for_llm(msgs[:n], 400, step=50)
        # cap..cap+step-1 (400..449) for THIS data: every floored start (500,
        # 550) is an even index, a user message, so the snap does not move it.
        # In general the user-snap can shorten the window below the cap.
        assert 400 <= len(w) <= 449
        starts.add(w[0].content[0].text)
    assert len(starts) <= 2  # sixty consecutive turns share at most two window starts


def test_window_step_one_is_todays_behaviour():
    mod = _load()
    msgs = [_text_msg(mod, "user", f"u{i}") if i % 2 == 0 else _text_msg(mod, "assistant", f"a{i}")
            for i in range(500)]
    assert mod.window_for_llm(msgs, 100, step=1) == mod.window_for_llm(msgs, 100)


def test_window_step_never_splits_a_tool_round_trip():
    """The step floor runs BEFORE the snap-to-user logic, so a floored start
    that lands mid-round-trip still snaps to a user message."""
    mod = _load()

    def assert_no_orphaned_tool_result(out):
        assert out[0].role == "user"
        seen_tool_use_ids = set()
        for m in out:
            for b in m.content:
                if isinstance(b, mod.ToolUseBlock):
                    seen_tool_use_ids.add(b.id)
                if isinstance(b, mod.ToolResultBlock):
                    assert b.tool_use_id in seen_tool_use_ids, (
                        f"tool result {b.tool_use_id} has no matching tool_use in the window"
                    )

    # The message list of test_window_for_llm_never_splits_a_tool_round_trip.
    msgs = []
    for t in range(10):
        msgs.append(_text_msg(mod, "user", f"u{t}"))
        msgs.append(mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id=f"call_{t}", name="echo", input={"text": str(t)}),
        ]))
        msgs.append(mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id=f"call_{t}", content=f"echoed {t}"),
        ]))
        msgs.append(_text_msg(mod, "assistant", f"done {t}"))
    # 40 messages; cap 6 → naive start 34 (a tool result) floors to 0.
    assert_no_orphaned_tool_result(mod.window_for_llm(msgs, 6, step=50))

    # Same rounds, longer: 160 messages; cap 100 → naive start 60 floors to
    # 50, which is a tool result (round 12); the snap moves on to u13 at 52.
    for t in range(10, 40):
        msgs.append(_text_msg(mod, "user", f"u{t}"))
        msgs.append(mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id=f"call_{t}", name="echo", input={"text": str(t)}),
        ]))
        msgs.append(mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id=f"call_{t}", content=f"echoed {t}"),
        ]))
        msgs.append(_text_msg(mod, "assistant", f"done {t}"))
    out = mod.window_for_llm(msgs, 100, step=50)
    assert_no_orphaned_tool_result(out)
    assert out[0] is msgs[52]


def test_load_after_corruption_rewrites_file(tmp_path: Path):
    """When _load truncates at a corrupt line, it rewrites the file so the corrupt
    tail doesn't reappear on next load and append doesn't write past the bad data."""
    mod = _load()
    path = tmp_path / "chan-rewrite.jsonl"
    path.write_text(
        '{"role": "user", "content": [{"type": "text", "text": "a"}], "timestamp": "2026-05-07T00:00:00Z"}\n'
        'this is not json\n'
        '{"role": "user", "content": [{"type": "text", "text": "b"}], "timestamp": "2026-05-07T00:00:00Z"}\n',
        encoding="utf-8",
    )
    sess1 = asyncio.run(mod.Session.open_or_create("chan-rewrite", root=tmp_path))
    assert len(sess1.messages) == 1  # only the first good line

    # File should have been rewritten — corrupt line and "b" line both gone
    contents = path.read_text(encoding="utf-8")
    assert "this is not json" not in contents
    assert '"b"' not in contents
    assert '"a"' in contents

    # Now append a new message — it should land cleanly after the rewritten content
    asyncio.run(sess1.append(mod.AgentMessage(role="assistant", content=[mod.TextBlock(text="c")])))

    # Re-open and verify the persisted state
    sess2 = asyncio.run(mod.Session.open_or_create("chan-rewrite", root=tmp_path))
    assert len(sess2.messages) == 2
    assert sess2.messages[0].content[0].text == "a"
    assert sess2.messages[1].content[0].text == "c"


# ---------------------------------------------------------------------------
# diet_for_llm — past turns' tool round-trips leave the payload (2026-08-18).
# Measured 08-18 00:44: the 120-message window was 105,616 chars, 79% tool
# results/calls, 6% his words — she answered "whatcha been up to" from forty
# thousand tokens of shell output. Long-term memory lives in recall/reflect;
# the transcript is for CONVERSATION. Payload-only, never the archive.
# ---------------------------------------------------------------------------


def _tool_round(mod, i: int):
    call = mod.AgentMessage(role="assistant", content=[
        mod.TextBlock(text=f"let me look {i}"),
        mod.ToolUseBlock(id=f"c{i}", name="local_shell", input={"cmd": "ls"}),
    ])
    result = mod.AgentMessage(role="tool", content=[
        mod.ToolResultBlock(tool_use_id=f"c{i}", content="x" * 5000)])
    return [call, result]


def test_diet_strips_past_tool_rounds_and_keeps_the_current_turn():
    mod = _load()
    msgs = [_text_msg(mod, "user", "u0")] + _tool_round(mod, 0) + [
        _text_msg(mod, "assistant", "a0 — found it"),
        _text_msg(mod, "user", "u1"),
    ] + _tool_round(mod, 1) + [
        mod.AgentMessage(role="assistant", content=[
            mod.ToolUseBlock(id="c9", name="web_search", input={"q": "x"})]),  # tool-only
        mod.AgentMessage(role="tool", content=[
            mod.ToolResultBlock(tool_use_id="c9", content="r")]),
        _text_msg(mod, "assistant", "a1"),
        _text_msg(mod, "user", "u2 (current)"),
    ] + _tool_round(mod, 2)
    keep_from = len(msgs) - 3          # index of "u2 (current)"
    out = mod.diet_for_llm(msgs, keep_from=keep_from)
    texts = [b.text for m in out for b in m.content
             if isinstance(b, mod.TextBlock) and not b.text.startswith(mod.RECEIPT_MARK)]   # receipts (2026-09-14) are the system's lines, not hers
    # every past user/assistant TEXT survives, in order
    assert texts[:6] == ["u0", "let me look 0", "a0 — found it", "u1", "let me look 1", "a1"]
    # no past tool blocks / tool messages (a tool-only assistant message now
    # survives as receipts — tests/agent/test_session_receipts.py)
    past = out[:-3]
    assert all(m.role != "tool" for m in past)
    assert not any(isinstance(b, (mod.ToolUseBlock, mod.ToolResultBlock))
                   for m in past for b in m.content)
    # the current turn is verbatim, same objects
    assert out[-3:] == msgs[-3:]
    # input untouched
    assert len(msgs) == 13


def test_diet_is_identity_when_there_is_nothing_to_strip():
    mod = _load()
    msgs = [_text_msg(mod, "user", "u0"), _text_msg(mod, "assistant", "a0"),
            _text_msg(mod, "user", "u1")]
    assert mod.diet_for_llm(msgs, keep_from=2) == msgs
    assert mod.diet_for_llm(msgs, keep_from=0) == msgs
    assert mod.diet_for_llm([], keep_from=0) == []


def test_diet_then_window_then_normalize_is_valid_grammar():
    """The three payload passes compose: dieted history windows to more real
    conversation and normalize finds nothing to repair."""
    mod = _load()
    msgs = []
    for i in range(30):
        msgs.append(_text_msg(mod, "user", f"u{i}"))
        msgs.extend(_tool_round(mod, i))
        msgs.append(_text_msg(mod, "assistant", f"a{i}"))
    msgs.append(_text_msg(mod, "user", "now"))
    keep_from = len(msgs) - 1
    dieted = mod.diet_for_llm(msgs, keep_from=keep_from)
    windowed = mod.window_for_llm(dieted, 20)
    normalized = mod.normalize_for_llm(windowed)
    assert normalized == windowed and windowed[0].role == "user"
    assert sum(1 for m in windowed if m.role == "user") >= 6   # real turns, not shell output
