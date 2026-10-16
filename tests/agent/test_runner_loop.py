from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_under_test")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "hello"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: FakeTurn = field(default_factory=FakeTurn)
    user_text: str = "hello"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


def test_runner_short_circuits_on_should_not_respond_for_palli_initiated(tmp_path):
    """should_respond=false silences ONLY PaLLi-initiated turns (a proactive/
    monologue outreach impulse deciding not to reach out)."""
    from types import SimpleNamespace

    mod = _load()

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence_llm(prompt): return '{"should_respond": false, "register": "neutral", "length": "short", "phase": "settled_conversation", "target_beats": 1, "observation_text": "skip"}'

    deps = mod.RunnerDeps(
        build_turn_context=fake_recall,
        cadence_llm=fake_cadence_llm,
        sessions_root=tmp_path,
        provider="openai_compat",
        model="m",
        client=None,
        tool_registry=None,
        polling_tools=set(),
    )
    turn = FakeTurn(surface="proactive", metadata=SimpleNamespace(origin="monologue"))
    result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    assert result.responded is False
    assert result.response_text == ""


def test_runner_user_message_never_dropped_by_cadence_silence(tmp_path):
    """Regression (2026-07-06): the owner's git-commit action_request was
    dropped because the Haiku cadence model voted should_respond=false and the
    runner honored it for a user turn — no reply, and the message never even
    entered the session. A message a HUMAN sent must always get a response;
    only PaLLi-initiated turns may be silenced."""
    from types import SimpleNamespace

    mod = _load()
    import core.agent.llm_client as llm_mod_real

    async def fake_recall(turn):
        return FakeCtx(user_text="check the latest git commits and give me a summary")

    async def fake_cadence_llm(prompt):
        # cadence mis-votes silence on an action_request (the live Haiku bug)
        return ('{"should_respond": false, "register": "action_request", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "skip"}')

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["On it — here are the latest commits…"],
            tool_uses=[],
            stop_reason="end_turn",
            prompt_tokens=10, completion_tokens=5,
        )

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence_llm,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=None,
            polling_tools=set(),
        )
        # default metadata=None -> origin resolves to "user"
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert "commits" in result.response_text


def test_runner_reminder_origin_survives_cadence_silence(tmp_path):
    """A cadence should_respond=false verdict must never silence a
    reminder-origin turn — the user asked for that delivery. The gate
    is skipped for origin="reminder" (the string TurnMetadata.origin
    carries for scheduler-synthesized fires) and the loop runs."""
    from types import SimpleNamespace

    mod = _load()
    import core.agent.llm_client as llm_mod_real

    async def fake_recall(turn):
        return FakeCtx(user_text="[REMINDER FIRE] take a break")

    async def fake_cadence_llm(prompt):
        return ('{"should_respond": false, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "skip"}')

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["hey — you asked me to remind you: take a break."],
            tool_uses=[],
            stop_reason="end_turn",
            prompt_tokens=10, completion_tokens=5,
        )

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence_llm,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=None,
            polling_tools=set(),
        )
        turn = FakeTurn(
            surface="reminder_fire",
            metadata=SimpleNamespace(origin="reminder", reminder_id=42),
        )
        result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert "take a break" in result.response_text


class FakeTool:
    name = "echo"
    description = "echo input"
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    requires_approval = False
    owner_only = False
    async def execute(self, input, ctx): return f"echoed: {input.get('text', '')}"


def test_runner_single_tool_then_text_terminates(tmp_path):
    mod = _load()
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_runner_test_19")
    # Use canonical session import (avoid dual-instance bug)
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    registry = tools_mod.ToolRegistry()
    registry.register(FakeTool())

    # Mock LLM: first call returns tool_use, second returns text-only
    call_count = {"n": 0}
    async def fake_call(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[sess_mod.ToolUseBlock(id="t1", name="echo", input={"text": "hi"})],
                stop_reason="tool_use",
                prompt_tokens=10, completion_tokens=5,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["I echoed it."],
            tool_uses=[],
            stop_reason="end_turn",
            prompt_tokens=20, completion_tokens=10,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    # Monkeypatch the llm_client.call used by runner
    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=registry, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert "echoed" in result.response_text or "I echoed it" in result.response_text
    assert result.iterations == 2


def test_runner_windows_history_for_llm_payload(tmp_path, monkeypatch):
    """The runner must send a bounded WINDOW of session history to the LLM,
    not the full unbounded list (2026-06-11: a live channel reached 471
    messages / ~85k prompt tokens, re-sent on every single turn). The full
    history must still be preserved in the session itself — the window is
    payload-only, not destructive."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "10")
    monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", "1")  # the window moves in steps since Batch 2; step 1 restores the old slide

    # Pre-populate the session the runner will open (FakeTurn has no
    # metadata → session key derives to "discord_text:1").
    pre = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    for i in range(40):
        role = "user" if i % 2 == 0 else "assistant"
        asyncio.run(pre.append(sess_mod.AgentMessage(
            role=role, content=[sess_mod.TextBlock(text=f"m{i}")],
        )))

    seen: dict = {}

    async def fake_call(**kw):
        seen["n_messages"] = len(kw["messages"])
        return llm_mod_real.LLMResponse(
            text_blocks=["ok"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    # 40 pre-existing + 1 appended user message = 41 in session; cap=10.
    assert seen["n_messages"] <= 10
    # Window is non-destructive: the on-disk session still holds everything.
    post = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    assert len(post.messages) >= 41


def test_runner_windows_history_in_steps_at_the_default_step(tmp_path, monkeypatch, caplog):
    """Batch 2 (2026-09-27): at the default step (50) the window's START only
    moves on 50-message boundaries, so consecutive turns share a byte-identical
    history prefix the provider can cache. The cap still bounds the payload
    from below; the step can add at most step-1 messages above it."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "10")
    monkeypatch.delenv("PALLI_AGENT_HISTORY_STEP", raising=False)

    pre = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    for i in range(64):
        role = "user" if i % 2 == 0 else "assistant"
        asyncio.run(pre.append(sess_mod.AgentMessage(
            role=role, content=[sess_mod.TextBlock(text=f"m{i}")],
        )))

    seen: dict = {}

    async def fake_call(**kw):
        seen["messages"] = kw["messages"]
        return llm_mod_real.LLMResponse(
            text_blocks=["ok"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        with caplog.at_level(logging.INFO):
            result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    payload = seen["messages"]
    # 64 pre-existing + 1 appended user message = 65; cap 10 → start 55,
    # floored to the 50-message boundary.
    assert payload[0].content[0].text == "m50"
    assert 10 <= len(payload) <= 10 + 49
    # The windowed line names the step, since the payload can exceed cap=.
    windowed = [r.getMessage() for r in caplog.records
                if "history windowed" in r.getMessage()]
    assert windowed and "(cap=10 step=50)" in windowed[0], windowed


def test_resolve_history_step(monkeypatch):
    """PALLI_AGENT_HISTORY_STEP: default 50; garbage → 50; <= 1 → 1 (the old
    one-message slide)."""
    mod = _load()
    monkeypatch.delenv("PALLI_AGENT_HISTORY_STEP", raising=False)
    assert mod._resolve_history_step() == 50
    monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", "")
    assert mod._resolve_history_step() == 50
    monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", "25")
    assert mod._resolve_history_step() == 25
    monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", "fifty")
    assert mod._resolve_history_step() == 50
    for low in ("1", "0", "-3"):
        monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", low)
        assert mod._resolve_history_step() == 1


def test_runner_payload_carries_no_past_tool_results(tmp_path, monkeypatch):
    """The transcript diet (2026-08-18): earlier turns' tool round-trips leave
    the payload — the model sees the conversation, not 79% shell output. The
    on-disk session keeps everything."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "120")
    pre = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    for i in range(6):
        asyncio.run(pre.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text=f"u{i}")])))
        asyncio.run(pre.append(sess_mod.AgentMessage(role="assistant", content=[
            sess_mod.TextBlock(text=f"looking {i}"),
            sess_mod.ToolUseBlock(id=f"c{i}", name="local_shell", input={"cmd": "ls"})])))
        asyncio.run(pre.append(sess_mod.AgentMessage(role="tool", content=[
            sess_mod.ToolResultBlock(tool_use_id=f"c{i}", content="SHELL OUTPUT " * 200)])))
        asyncio.run(pre.append(sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text=f"a{i}")])))

    seen: dict = {}

    async def fake_call(**kw):
        seen["messages"] = kw["messages"]
        return llm_mod_real.LLMResponse(
            text_blocks=["ok"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    payload = seen["messages"]
    assert all(m.role != "tool" for m in payload)
    assert not any(isinstance(b, (sess_mod.ToolUseBlock, sess_mod.ToolResultBlock))
                   for m in payload for b in m.content)
    texts = [b.text for m in payload for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert "looking 3" in texts and "a5" in texts
    # Tool receipts (2026-09-14): a past round leaves ONE bounded line per call
    # behind — the machine's word, ≤ ~350 chars — never the 2,400-char output.
    assert ("SHELL OUTPUT " * 200) not in " ".join(texts)
    receipts = [t for t in texts if t.startswith(sess_mod.RECEIPT_MARK)]
    assert 1 <= len(receipts) <= 6 and all(len(r) < 420 for r in receipts)
    post = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    assert len(post.messages) >= 25            # the archive keeps everything


def test_runner_empty_llm_response_stays_silent_not_stale_resend(tmp_path):
    """2026-06-12 'looping' incident: when the LLM returns 0 text + 0 tool
    calls (reasoning model chose silence / burned its budget), the runner
    must stay SILENT — responded=False, empty response_text.

    The old fallback scanned the ENTIRE session for the last assistant
    message, so an empty response resurrected the PREVIOUS turn's reply and
    re-sent it: Discord showed PaLLi repeating itself verbatim, and dispatch
    persisted the duplicate to conversation_history (rows #5425/#5427)."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    # Pre-populate the session with a prior exchange — the stale reply the
    # buggy fallback used to resurrect.
    pre = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    asyncio.run(pre.append(sess_mod.AgentMessage(
        role="user", content=[sess_mod.TextBlock(text="wuddup g")],
    )))
    asyncio.run(pre.append(sess_mod.AgentMessage(
        role="assistant", content=[sess_mod.TextBlock(text="Nah, I didn't get it, man.")],
    )))

    async def fake_call(**kw):
        # The incident shape: model emits only reasoning, no visible output.
        return llm_mod_real.LLMResponse(
            text_blocks=[], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=100, completion_tokens=69,
        )

    async def fake_recall(turn): return FakeCtx(user_text="lmao. that was waaay earlier.")
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "settled_conversation", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    # The previous turn's reply must NOT come back as this turn's response.
    assert "Nah, I didn't get it" not in (result.response_text or "")
    assert result.response_text == ""
    assert result.responded is False


def test_runner_nonstreaming_text_still_recovered_from_this_turn(tmp_path):
    """The fallback's legitimate job stays intact: when a non-streaming
    provider (or test mock) returns text without feeding the chunker, the
    text of THIS turn's assistant message is still recovered."""
    mod = _load()
    import core.agent.llm_client as llm_mod_real

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["fresh reply this turn"], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=10, completion_tokens=5,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert result.response_text == "fresh reply this turn"


def test_runner_detection_critical_terminates_loop(tmp_path):
    mod = _load()
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_test_20")
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    registry = tools_mod.ToolRegistry()
    registry.register(FakeTool())

    # Mock LLM that calls echo forever, its argument flipping x, y, x, y.
    # Second read F3 (2026-10-04): a same-args loop with the same answer is
    # now ended at its third round by the runner's brake, before any
    # detector could fire (tests/agent/test_run_brakes.py pins that). This
    # loop's answers alternate, so the brake never sees three identical
    # rounds and the detector's ping-pong CRITICAL is what must end it —
    # which keeps this test on the runner's detection hookup.
    calls = {"n": 0}

    async def fake_call(**kw):
        # Terminate once the CRITICAL stop signal reaches the payload, whatever
        # its delivery shape: a raw synthetic tool_result (.content) pre-2026-07-26,
        # or the normalize_for_llm "[system observation]" user TextBlock (.text)
        # it becomes at the payload boundary now (spec R3 — the unmatched
        # tool_call_id was a latent provider 400).
        msgs = kw.get("messages", [])
        if any("CRITICAL" in str(getattr(b, "content", "") or getattr(b, "text", ""))
               for m in msgs for b in m.content):
            return llm_mod_real.LLMResponse(text_blocks=["stopping"], tool_uses=[], stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
        calls["n"] += 1
        arg = "x" if calls["n"] % 2 else "y"
        return llm_mod_real.LLMResponse(
            text_blocks=[], tool_uses=[sess_mod.ToolUseBlock(id=f"t{len(msgs)}", name="echo", input={"text": arg})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1,
        )

    reflected = {"count": 0}
    async def fake_reflect(ctx, response_text, **kw):
        reflected["count"] += 1
        return True

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=registry, polling_tools=set(),
            apply_reflection=fake_reflect,
        )

        # Reflection is detached (2026-07-08): flush before asserting it ran.
        async def _drive():
            r = await mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
            await mod.flush_detached_tasks()
            return r

        result = asyncio.run(_drive())
    finally:
        llm_mod_real.call = orig_call

    # Loop must have terminated (didn't run forever)
    assert result.responded is True
    # And reflect was called
    assert reflected["count"] == 1
    # Bound iterations explicitly: if detection regresses, fail loud rather
    # than infinite-loop. Global circuit breaker fires at 30; 35 leaves cushion.
    assert result.iterations <= 35


def test_run_result_has_attachments_field():
    """D.2 wave 1: RunResult exposes an attachments tuple slot. Defaults
    to empty so existing constructors keep working without churn; the
    runner-side accumulation logic lands in Wave 2."""
    mod = _load()
    result = mod.RunResult(
        responded=True, response_text="hi", iterations=1, error=None,
    )
    assert result.attachments == ()
    assert isinstance(result.attachments, tuple)


def test_run_result_accepts_attachments_kwarg():
    """RunResult constructor takes attachments as a kwarg without
    breaking the frozen-dataclass contract."""
    mod = _load()
    result = mod.RunResult(
        responded=True,
        response_text="here",
        iterations=2,
        attachments=("/tmp/snap.jpg", "/tmp/log.txt"),
    )
    assert result.attachments == ("/tmp/snap.jpg", "/tmp/log.txt")


def test_runner_barge_in_cancel_cannot_drop_user_message(tmp_path):
    """Regression (2026-07-07 burst-drop): a rapid follow-up message cancels
    the in-flight turn (bots/discord/client.py on_message barge-in). Pre-fix,
    the user append lived AFTER the recall + cadence pre-reads (~6s of Haiku
    round-trips), so a cancel landing in that window erased the message from
    the session transcript entirely — persisted to the conversations DB, yet
    invisible to every future LLM payload. Live burst test: 4 of 5 messages
    dropped, including "sorry. after that call last night".

    The user's words must be in the transcript even when the turn dies
    mid-recall."""
    mod = _load()
    import core.agent.session as sess_mod

    async def main():
        reached_recall = asyncio.Event()

        async def hanging_recall(turn):
            # Models the in-flight turn_read Haiku call the barge-in
            # cancels into. Never returns.
            reached_recall.set()
            await asyncio.Event().wait()

        async def cadence_must_not_run(prompt):
            raise AssertionError("cadence must not be reached before cancel")

        deps = mod.RunnerDeps(
            build_turn_context=hanging_recall,
            cadence_llm=cadence_must_not_run,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=None,
            polling_tools=set(),
        )
        # default metadata=None -> origin resolves to "user"
        turn = FakeTurn(content="sorry. after that call last night")
        task = asyncio.create_task(mod.run_turn(turn, deps=deps, on_chunk=None))
        await asyncio.wait_for(reached_recall.wait(), timeout=2)

        task.cancel()  # the barge-in: a follow-up message arrived
        with pytest.raises(asyncio.CancelledError):
            await task

        # FakeTurn has no metadata -> session key derives to "discord_text:1".
        sess = await sess_mod.Session.open_or_create("discord_text:1", root=tmp_path)
        texts = [
            b.text
            for m in sess.messages if m.role == "user"
            for b in m.content if isinstance(b, sess_mod.TextBlock)
        ]
        assert "sorry. after that call last night" in texts

    asyncio.run(main())


def test_runner_user_message_appended_exactly_once_per_turn(tmp_path):
    """Companion guard for the burst-drop fix: the early cancellation-safe
    append must REPLACE the late one for genuine user turns, never stack on
    top of it — a doubled user message would read as the user repeating
    themselves on every future turn."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    async def fake_recall(turn):
        return FakeCtx(user_text="yo palli, you there?")

    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["yeah, right here."], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=10, completion_tokens=5,
        )

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        turn = FakeTurn(content="yo palli, you there?")
        result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    sess = asyncio.run(sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    occurrences = sum(
        1
        for m in sess.messages if m.role == "user"
        for b in m.content
        if isinstance(b, sess_mod.TextBlock) and b.text == "yo palli, you there?"
    )
    assert occurrences == 1


def test_runner_max_iterations_terminates_loop(tmp_path):
    mod = _load()
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_test_21")
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    registry = tools_mod.ToolRegistry()

    class VariadicTool:
        name = "v"
        description = "varies"
        input_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx): return f"out_{input.get('x', 0)}"
    registry.register(VariadicTool())

    n = {"i": 0}
    async def fake_call(**kw):
        n["i"] += 1
        # Return ever-changing tool_use so detection doesn't fire
        return llm_mod_real.LLMResponse(
            text_blocks=[], tool_uses=[sess_mod.ToolUseBlock(id=f"t{n['i']}", name="v", input={"x": n["i"]})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    import os
    os.environ["MAX_REACT_ITERATIONS"] = "5"
    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=registry, polling_tools=set(),
            apply_reflection=None,
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        os.environ.pop("MAX_REACT_ITERATIONS", None)
        llm_mod_real.call = orig_call

    # Respected the ceiling: max_iter=5 normal + 1 grace LLM iteration where the
    # injected CRITICAL is visible + 1 break-checkpoint iteration that exits before
    # any LLM call. With the fake LLM never terminating gracefully, hard break at
    # max_iter+2 = 7 is the worst-case bound.
    assert result.iterations <= 7


def test_cadence_emotional_context_threads_into_reflection_decision(tmp_path):
    """Momentum wire (2026-07-08): cadence's emotional_context tag must reach
    reflect's TurnDecision, else ai/relationship_momentum never nudges and the
    relationship state only decays (audit 2026-07-06)."""
    mod = _load()
    import core.agent.llm_client as llm_mod_real

    captured = {}

    async def fake_reflect(ctx, response_text, **kw):
        captured["decision"] = kw.get("decision")
        return True

    async def fake_recall(turn):
        return FakeCtx()

    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "support", "length": "short",
            "phase": "settled_conversation", "target_beats": 1,
            "observation_text": "rough day", "emotional_context": "venting",
        })

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["i hear you"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
            apply_reflection=fake_reflect,
        )

        async def _drive():
            r = await mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
            await mod.flush_detached_tasks()
            return r

        result = asyncio.run(_drive())
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert captured["decision"].emotional_context == "venting"


def test_cadence_bogus_emotional_context_sanitized_to_none(tmp_path):
    """A tag outside the TurnDecision Literal vocabulary must be dropped, not
    passed through (types.py Literal contract; momentum ignores unknowns but
    the decision object should stay type-honest)."""
    mod = _load()
    import core.agent.llm_client as llm_mod_real

    captured = {}

    async def fake_reflect(ctx, response_text, **kw):
        captured["decision"] = kw.get("decision")
        return True

    async def fake_recall(turn):
        return FakeCtx()

    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1,
            "observation_text": "ok", "emotional_context": "extremely_sad",
        })

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["hey"], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
            apply_reflection=fake_reflect,
        )

        async def _drive():
            r = await mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
            await mod.flush_detached_tasks()
            return r

        asyncio.run(_drive())
    finally:
        llm_mod_real.call = orig_call

    assert captured["decision"].emotional_context is None


def test_prerecorded_enrich_survives_barge_in_cancel(tmp_path, monkeypatch):
    """Bug-5/6 residual (2026-07-08): the placeholder enrich spawns detached
    right after recall, so a barge-in cancelling run_turn mid-turn can no
    longer lose the rich attachment form from the transcript or the DB."""
    from dataclasses import dataclass as _dc, field as _field
    from types import SimpleNamespace

    mod = _load()
    import core.agent.session as sess_mod
    import core.turn.persistence as persist_mod

    persisted = {}

    async def fake_persist(turn, rich):
        persisted["rich"] = rich

    monkeypatch.setattr(persist_mod, "_persist_user_side_with_content", fake_persist)

    async def seed():
        s = await sess_mod.Session.open_or_create("discord:901", root=tmp_path)
        await s.append(sess_mod.AgentMessage(
            role="user", content=[sess_mod.TextBlock(text="[image attached]")]))

    asyncio.run(seed())

    @_dc
    class FakeCtxImg(FakeCtx):
        image_descriptions: tuple = ("a red bicycle leaning on a dock",)

    async def fake_recall(turn):
        return FakeCtxImg()

    async def hanging_cadence(prompt):
        await asyncio.Event().wait()

    deps = mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=hanging_cadence,
        sessions_root=tmp_path, provider="openai_compat", model="m",
        client=None, tool_registry=None, polling_tools=set(),
    )
    turn = FakeTurn(
        content="[image attached]",
        metadata=SimpleNamespace(origin="user", channel_id="901",
                                 user_side_prerecorded=True,
                                 text_attachments=None),
    )

    async def _drive():
        task = asyncio.create_task(mod.run_turn(turn, deps=deps, on_chunk=None))
        await asyncio.sleep(0.1)   # recall done; enrich spawned; cadence hanging
        task.cancel()              # the barge-in
        try:
            await task
        except asyncio.CancelledError:
            pass
        await mod.flush_detached_tasks()

    asyncio.run(_drive())

    assert "a red bicycle" in persisted.get("rich", "")

    async def readback():
        s = await sess_mod.Session.open_or_create("discord:901", root=tmp_path)
        return "".join(
            b.text for m in s.messages for b in m.content if hasattr(b, "text"))

    assert "a red bicycle" in asyncio.run(readback())


def test_runner_separates_narration_across_tool_rounds(tmp_path):
    """Streaming text from model call 1 ("First I'll check.") and model call 2
    ("Found it.") must not weld into "First I'll check.Found it." — the runner
    tells its chunker an iteration boundary passed whenever a tool round-trip
    sits between two model calls (2026-08-18: 15% of her Discord messages had
    fused seams; every worst case was a multi-tool turn)."""
    mod = _load()
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module("core/agent/tools.py", "tools_for_seam_test")
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod_real

    registry = tools_mod.ToolRegistry()
    registry.register(FakeTool())

    call_count = {"n": 0}
    async def fake_call(**kw):
        # Mirror the real streaming client: text reaches the runner's chunker
        # through on_chunk BEFORE the response object is returned.
        call_count["n"] += 1
        on_chunk = kw.get("on_chunk")
        if call_count["n"] == 1:
            await on_chunk("First I'll check.")
            return llm_mod_real.LLMResponse(
                text_blocks=["First I'll check."],
                tool_uses=[sess_mod.ToolUseBlock(id="t1", name="echo", input={"text": "hi"})],
                stop_reason="tool_use", prompt_tokens=10, completion_tokens=5,
            )
        await on_chunk("Found it.")
        return llm_mod_real.LLMResponse(
            text_blocks=["Found it."], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=20, completion_tokens=10,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=registry, polling_tools=set(),
        )
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    assert result.iterations == 2
    assert result.response_text == "First I'll check.\n\nFound it."


def test_runner_sets_the_reply_shape_and_passes_the_note_for_conversation_turns(tmp_path):
    """The register fix (2026-08-22): the runner hands the cadence vote to the
    adapter by context variable and the trailing note to the LLM call — for
    conversation turns only (govern_replies=True), never her workbench."""
    import core.agent.reply_governor as rg
    import core.agent.llm_client as llm_mod_real
    mod = _load()
    seen = {}

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence_llm(prompt):
        return ('{"should_respond": true, "register": "neutral", "length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "quick"}')

    async def fake_call(**kw):
        seen["shape"] = rg.current_reply_shape.get()
        seen["note"] = kw.get("trailing_system_note")
        return llm_mod_real.LLMResponse(text_blocks=["ok"], tool_uses=[], stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)

    orig = llm_mod_real.call; llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence_llm, sessions_root=tmp_path,
                              provider="openai_compat", model="m", client=None, tool_registry=None, polling_tools=set())
        asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
        assert seen["shape"] == "short" and seen["note"] and "short" in seen["note"]
        assert rg.current_reply_shape.get() == ""   # reset after the turn
        seen.clear()
        deps2 = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence_llm, sessions_root=tmp_path,
                               provider="openai_compat", model="m", client=None, tool_registry=None, polling_tools=set(),
                               govern_replies=False)
        asyncio.run(mod.run_turn(FakeTurn(), deps=deps2, on_chunk=None))
        assert seen["shape"] == "" and seen["note"] is None
    finally:
        llm_mod_real.call = orig


# ── DEFECT_RECORD §18 (2026-09-01): the silence override ─────────────────────
# "dont respond once you see this message" → cadence voted should_respond=
# false → the runner FORCED a reply ("🤐"). The guarantee changes from a
# forced REPLY to a forced RUN: his message is always processed and recorded,
# and the reply may be nothing at all.

def _silence_vote(register="neutral"):
    return ('{"should_respond": false, "register": "%s", "length": "short", '
            '"phase": "settled_conversation", "target_beats": 1, '
            '"observation_text": "he asked for no response"}' % register)


def test_user_turn_cadence_silence_runs_and_may_output_nothing(tmp_path):
    """The run executes (his words reach the transcript) and an empty reply
    is honored as silence — nothing to send, nothing forced."""
    import core.agent.llm_client as llm_mod_real
    import core.agent.session as sess_mod
    mod = _load()
    seen = {}

    async def fake_recall(turn):
        return FakeCtx(user_text="dont respond once you see this message")

    async def fake_cadence_llm(prompt):
        return _silence_vote()

    async def fake_call(**kw):
        seen["note"] = kw.get("trailing_system_note") or ""
        return llm_mod_real.LLMResponse(text_blocks=[], tool_uses=[], stop_reason="end_turn",
                                        prompt_tokens=1, completion_tokens=0)
    orig = llm_mod_real.call; llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
                              sessions_root=tmp_path, provider="openai_compat", model="m",
                              client=None, tool_registry=None, polling_tools=set())
        result = asyncio.run(mod.run_turn(FakeTurn(content="dont respond once you see this message"),
                                          deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig
    assert result.responded is False and result.response_text == ""
    assert "silence" in seen["note"].lower() and "emoji" in seen["note"].lower()
    sess = asyncio.run(sess_mod.Session.open_or_create("discord:1", root=tmp_path)) \
        if False else asyncio.run(sess_mod.Session.open_or_create(mod._derive_session_key(FakeTurn()), root=tmp_path))
    texts = [b.text for m in sess.messages for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert any("dont respond" in t for t in texts)          # never dropped


def test_user_turn_cadence_silence_still_replies_when_the_model_does(tmp_path):
    """The 2026-07-06 protection, kept: a mis-vote never eats a real question —
    the model sees the message and answers it."""
    import core.agent.llm_client as llm_mod_real
    mod = _load()

    async def fake_recall(turn):
        return FakeCtx(user_text="check the latest git commits")

    async def fake_cadence_llm(prompt):
        return _silence_vote("action_request")

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(text_blocks=["On it — here are the commits…"], tool_uses=[],
                                        stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    orig = llm_mod_real.call; llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
                              sessions_root=tmp_path, provider="openai_compat", model="m",
                              client=None, tool_registry=None, polling_tools=set())
        result = asyncio.run(mod.run_turn(FakeTurn(content="check the latest git commits"), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig
    assert result.responded is True and "commits" in result.response_text


def test_silence_laws_are_in_the_prompts():
    from core.agent.cadence import SHAPE_RESPONSE_PROMPT
    from core.agent import system_prompt as sp
    assert "explicitly asks" in SHAPE_RESPONSE_PROMPT           # false is legal on a real ask for silence
    assert "NEVER return false" not in SHAPE_RESPONSE_PROMPT
    import inspect
    src = inspect.getsource(sp)
    assert "an emoji is a response" in src


# ── Batch 2, task 3 (cut the cost, 2026-09-27): the runner sends the split ──

def test_runner_sends_the_stable_prefix_as_system_and_the_volatile_tail_beside_the_note(tmp_path):
    """The hot path: `system` is the stable prefix (no clock, no memory
    hits), `system_tail` is the volatile tail (the clock, the bands, the
    directive) and the shape note keeps today's bytes in
    `trailing_system_note` — llm_client joins tail and note into the one
    trailing message."""
    import core.agent.llm_client as llm_mod_real
    mod = _load()
    seen = {}

    async def fake_recall(turn):
        return FakeCtx(runtime_text="## Current Time\n10:00", memory_text="- he told me about Mochi")

    async def fake_cadence_llm(prompt):
        return ('{"should_respond": true, "register": "neutral", "length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "quick"}')

    async def fake_call(**kw):
        seen.update(kw)
        return llm_mod_real.LLMResponse(text_blocks=["ok"], tool_uses=[], stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)

    orig = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence_llm, sessions_root=tmp_path,
                              provider="openai_compat", model="m", client=None, tool_registry=None, polling_tools=set())
        asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig
    assert "I'm PaLLi." in seen["system"]
    assert "## Current Time" not in seen["system"] and "Mochi" not in seen["system"]
    assert "## Current Time" in seen["system_tail"] and "Mochi" in seen["system_tail"]
    assert seen["trailing_system_note"] and "short" in seen["trailing_system_note"]
    assert "## Current Time" not in seen["trailing_system_note"]  # the tail is not folded into the note
