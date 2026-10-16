from __future__ import annotations

import asyncio
import json
import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/llm_client.py", "agent_llm_client_under_test")


def _load_session():
    """Canonical import — see tests/agent/test_tools.py for rationale.
    llm_client.py imports session.py, so we must use the same module instance."""
    import core.agent.session as m
    return m


class FakeOpenAIStream:
    """Mimics an OpenAI streaming response. Emits chunks as deltas."""
    def __init__(self, deltas):
        self.deltas = deltas

    def __aiter__(self):
        async def gen():
            for d in self.deltas:
                yield d
        return gen()


class FakePydanticChunk:
    """Mimics openai SDK v1+ ChatCompletionChunk: a Pydantic-style object
    that exposes data via model_dump() and has NO dict-style .get(). The
    real openai SDK yields these from chat.completions.create(stream=True);
    the PaLLi adapter path (sdk_adapter.py) yields plain dicts instead.
    The parser must handle both shapes."""
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def _delta(content=None, tool_calls=None, finish_reason=None, usage=None, reasoning_content=None):
    return {
        "choices": [{
            "delta": {
                "content": content,
                "tool_calls": tool_calls,
                "reasoning_content": reasoning_content,
            },
            "finish_reason": finish_reason,
        }],
        "usage": usage,
    }


def test_openai_stream_collects_text_and_calls_on_chunk():
    mod = _load()
    chunks = []

    async def on_chunk(s):
        chunks.append(s)

    deltas = [
        _delta(content="Hello "),
        _delta(content="world"),
        _delta(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5}),
    ]
    response = asyncio.run(mod._collect_openai_stream(FakeOpenAIStream(deltas), on_chunk))
    assert response.text_blocks == ["Hello world"]
    assert response.tool_uses == []
    assert response.stop_reason == "end_turn"
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 5
    assert "".join(chunks) == "Hello world"


def test_openai_stream_handles_pydantic_chunks():
    """Real openai SDK (v1+) yields Pydantic ChatCompletionChunk objects,
    not dicts. Regression for the 2026-05 incident where switching the
    default provider from codex (CLI auth -> SDK adapter -> dict chunks)
    to deepseek (HTTP auth -> real openai SDK -> Pydantic chunks) crashed
    the agent loop with AttributeError: 'ChatCompletionChunk' object has
    no attribute 'get'."""
    mod = _load()
    chunks = []

    async def on_chunk(s):
        chunks.append(s)

    deltas = [
        FakePydanticChunk(_delta(content="Hello ")),
        FakePydanticChunk(_delta(content="world")),
        FakePydanticChunk(_delta(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5})),
    ]
    response = asyncio.run(mod._collect_openai_stream(FakeOpenAIStream(deltas), on_chunk))
    assert response.text_blocks == ["Hello world"]
    assert response.stop_reason == "end_turn"
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 5
    assert "".join(chunks) == "Hello world"


def test_openai_stream_collects_tool_calls():
    mod = _load()
    deltas = [
        _delta(tool_calls=[{
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":'},
        }]),
        _delta(tool_calls=[{
            "index": 0,
            "function": {"arguments": ' "/tmp/x"}'},
        }]),
        _delta(finish_reason="tool_calls", usage={"prompt_tokens": 50, "completion_tokens": 20}),
    ]
    response = asyncio.run(mod._collect_openai_stream(FakeOpenAIStream(deltas), None))
    assert len(response.tool_uses) == 1
    assert response.tool_uses[0].id == "call_1"
    assert response.tool_uses[0].name == "read_file"
    assert response.tool_uses[0].input == {"path": "/tmp/x"}
    assert response.stop_reason == "tool_use"


def test_openai_stream_captures_reasoning_but_does_not_stream_it():
    """DeepSeek thinking mode emits reasoning_content deltas. We must capture
    them onto LLMResponse.reasoning (to replay on the post-tool-call request)
    but NEVER forward them to on_chunk — reasoning is internal, not the reply."""
    mod = _load()
    chunks = []

    async def on_chunk(s):
        chunks.append(s)

    deltas = [
        _delta(reasoning_content="Let me think... "),
        _delta(reasoning_content="the file is at /tmp."),
        _delta(content="Here's the answer."),
        _delta(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5}),
    ]
    response = asyncio.run(mod._collect_openai_stream(FakeOpenAIStream(deltas), on_chunk))
    assert response.reasoning == "Let me think... the file is at /tmp."
    assert response.text_blocks == ["Here's the answer."]
    # The visible reply must NOT contain the reasoning.
    assert "".join(chunks) == "Here's the answer."


def test_openai_translator_replays_reasoning_on_tool_call():
    """An assistant message that made a tool call must replay its reasoning as
    reasoning_content, or DeepSeek thinking mode 400s on the follow-up request."""
    mod = _load()
    sess_mod = _load_session()

    msgs = [
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="read /tmp/x")]),
        sess_mod.AgentMessage(
            role="assistant",
            content=[sess_mod.ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"})],
            reasoning="The user wants /tmp/x; call read_file.",
        ),
        sess_mod.AgentMessage(role="tool", content=[
            sess_mod.ToolResultBlock(tool_use_id="call_1", content="contents"),
        ]),
    ]
    out = mod._agent_messages_to_openai(msgs)
    assistant_msg = next(m for m in out if m["role"] == "assistant")
    assert assistant_msg["reasoning_content"] == "The user wants /tmp/x; call read_file."
    assert "tool_calls" in assistant_msg


def test_openai_translator_omits_reasoning_without_tool_call():
    """A plain text answer (no tool call) must NOT carry reasoning_content —
    only the active tool round-trip needs it replayed."""
    mod = _load()
    sess_mod = _load_session()

    msgs = [
        sess_mod.AgentMessage(
            role="assistant",
            content=[sess_mod.TextBlock(text="just an answer")],
            reasoning="some thinking that should not be replayed",
        ),
    ]
    out = mod._agent_messages_to_openai(msgs)
    assert "reasoning_content" not in out[0]


def test_openai_translator_drops_empty_text_user_message():
    """Regression (2026-06-11 incident): a user message whose only block is an
    empty text block must NOT emit {"role":"user","content":None}.

    `"".join([""]) or None` is None, and OpenAI/DeepSeek reject a user message
    with content=None — the live error was:
        400 messages[479]: content should be a string or a list
    A single empty user turn (an untranscribed Discord voice message) was
    persisted to the channel's agent session and then re-sent on every turn,
    so the 400 repeated forever and the user got no response to anything.
    The empty-message guard must cover ALL non-assistant roles, not just
    assistant — a contentless turn carries nothing and is dropped."""
    mod = _load()
    sess_mod = _load_session()

    msgs = [
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="hello")]),
        sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text="hi")]),
        # The poison record: empty-text user turn (untranscribed voice message).
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="")]),
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="did you get my audio?")]),
    ]
    out = mod._agent_messages_to_openai(msgs)
    # The invariant DeepSeek/OpenAI enforce: every content is a str or a list.
    for m in out:
        assert isinstance(m.get("content"), (str, list)), (
            f"content must be str|list, got {m.get('content')!r} for role={m.get('role')!r}"
        )
    # The empty user turn carries nothing, so it is dropped — exactly like an
    # empty assistant turn already is.
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [m["content"] for m in out] == ["hello", "hi", "did you get my audio?"]


def _make_client(create_fn):
    """Build a duck-typed client whose chat.completions.create is create_fn."""
    class _Completions:
        create = staticmethod(create_fn)
    class _Chat:
        completions = _Completions()
    class _Client:
        chat = _Chat()
    return _Client()


@pytest.mark.parametrize("extra_body", [None, {"thinking": {"type": "enabled"}}])
def test_main_response_sends_temperature_without_changing_thinking(extra_body):
    mod = _load()
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return FakeOpenAIStream([
            _delta(content="Hello"),
            _delta(finish_reason="stop"),
        ])

    response = asyncio.run(mod.call(
        provider="openai_compat",
        client=_make_client(create),
        model="deepseek-v4-flash",
        system="You are PaLLi.",
        messages=[],
        tools=[],
        extra_body=extra_body,
    ))

    assert response.text_blocks == ["Hello"]
    assert captured["temperature"] == 0.9
    assert captured.get("extra_body") == extra_body


def test_call_openai_retries_transient_then_succeeds():
    """A retryable error (timeout/connection) before any output should be
    retried, and the eventual success returned."""
    mod = _load()
    mod._LLM_RETRY_BACKOFF = (0.0, 0.0)  # no real sleeping in tests
    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("connection timeout while reading")
        return FakeOpenAIStream([
            _delta(content="ok", finish_reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 1}),
        ])

    resp = asyncio.run(mod.call_openai_compat(
        client=_make_client(create), model="m", system="s", messages=[], tools=[],
    ))
    assert calls["n"] == 2  # retried once
    assert resp.text_blocks == ["ok"]


def test_call_openai_does_not_retry_400():
    """A deterministic 400 must NOT be retried — retrying won't help."""
    mod = _load()
    mod._LLM_RETRY_BACKOFF = (0.0, 0.0)
    calls = {"n": 0}

    class BadRequest(Exception):
        status_code = 400

    async def create(**kwargs):
        calls["n"] += 1
        raise BadRequest("invalid_request_error: nope")

    with pytest.raises(BadRequest):
        asyncio.run(mod.call_openai_compat(
            client=_make_client(create), model="m", system="s", messages=[], tools=[],
        ))
    assert calls["n"] == 1  # no retry


def test_mid_stream_drop_retries_bounded_and_partial_output_never_leaks():
    """Rewritten 2026-08-24 (the governor moved to the live path): collection
    now BUFFERS — nothing reaches on_chunk until the final text is settled —
    so a mid-stream connection drop is safely retryable (the old law, "never
    retry after output started", protected against duplicated partials that
    can no longer exist). New pins: the drop retries up to the bounded
    ladder, the exception still surfaces when every attempt drops, and NO
    partial text ever leaks to on_chunk."""
    mod = _load()
    seen = []

    async def on_chunk(t):
        seen.append(t)

    class FailMidStream:
        def __aiter__(self):
            async def gen():
                yield {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}
                raise ConnectionError("connection dropped mid-stream")
            return gen()

    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        return FailMidStream()

    with pytest.raises(Exception, match="mid-stream"):
        asyncio.run(mod.call_openai_compat(
            client=_make_client(create), model="m", system="s", messages=[], tools=[], on_chunk=on_chunk,
        ))
    assert calls["n"] == 1 + len(mod._LLM_RETRY_BACKOFF)   # bounded retries, then surface
    assert seen == []                                       # partial output never leaks

def test_a_real_mid_stream_read_error_with_no_message_is_retried():
    """2026-09-29 18:27 (her reply to the owner's voice-message question): the
    connection to the provider dropped mid-stream. httpx raised ReadError with
    an EMPTY message and no status code, so the substring check could not see
    it; the turn died on the first attempt and the owner got the outage line.
    The fake above raises ConnectionError("connection dropped…"), whose words
    the substring check does catch, which is why that test stayed green."""
    import httpx
    mod = _load()

    class FailOnceMidStream:
        def __init__(self, fail):
            self.fail = fail

        def __aiter__(self):
            fail = self.fail

            async def gen():
                yield {"choices": [{"delta": {"content": "hel"}, "finish_reason": None}]}
                if fail:
                    raise httpx.ReadError("")
                yield {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}
            return gen()

    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        return FailOnceMidStream(fail=calls["n"] == 1)

    response = asyncio.run(mod.call_openai_compat(
        client=_make_client(create), model="m", system="s", messages=[], tools=[],
    ))
    assert calls["n"] == 2
    assert response.text_blocks == ["hello"]


def test_network_drops_are_retryable_by_type_even_with_no_message():
    import httpx
    import openai
    mod = _load()
    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    assert mod._is_retryable_llm_error(httpx.ReadError("")) is True
    assert mod._is_retryable_llm_error(httpx.WriteError("")) is True
    assert mod._is_retryable_llm_error(httpx.ConnectError("")) is True
    assert mod._is_retryable_llm_error(httpx.RemoteProtocolError("")) is True
    assert mod._is_retryable_llm_error(openai.APIConnectionError(request=request)) is True
    wrapped = RuntimeError("")
    wrapped.__cause__ = httpx.ReadError("")
    assert mod._is_retryable_llm_error(wrapped) is True
    # A request we built wrong is not a network drop: retrying cannot help.
    assert mod._is_retryable_llm_error(httpx.LocalProtocolError("")) is False
    assert mod._is_retryable_llm_error(httpx.UnsupportedProtocol("")) is False


def test_is_retryable_llm_error_classification():
    mod = _load()

    class E(Exception):
        def __init__(self, msg, status=None):
            super().__init__(msg)
            if status is not None:
                self.status_code = status

    assert mod._is_retryable_llm_error(E("bad", status=400)) is False
    assert mod._is_retryable_llm_error(E("rate limited", status=429)) is True
    assert mod._is_retryable_llm_error(E("server error", status=503)) is True
    assert mod._is_retryable_llm_error(E("Request timed out")) is True
    assert mod._is_retryable_llm_error(E("totally unknown thing")) is False


class FakeAnthropicStream:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()


def test_anthropic_stream_collects_text():
    mod = _load()
    chunks = []
    async def on_chunk(s): chunks.append(s)
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " there"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    response = asyncio.run(mod._collect_anthropic_stream(FakeAnthropicStream(events), on_chunk, prompt_tokens_hint=42))
    assert response.text_blocks == ["Hi there"]
    assert response.tool_uses == []
    assert response.stop_reason == "end_turn"
    assert response.completion_tokens == 3
    assert response.prompt_tokens == 42
    assert "".join(chunks) == "Hi there"


def test_anthropic_stream_collects_tool_use():
    mod = _load()
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"path":'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": ' "/x"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 10}},
    ]
    response = asyncio.run(mod._collect_anthropic_stream(FakeAnthropicStream(events), None, prompt_tokens_hint=0))
    assert len(response.tool_uses) == 1
    assert response.tool_uses[0].id == "tu_1"
    assert response.tool_uses[0].name == "read_file"
    assert response.tool_uses[0].input == {"path": "/x"}
    assert response.stop_reason == "tool_use"


def test_call_routes_by_provider():
    mod = _load()
    sess_mod = _load_session()

    seen = {}

    class FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    seen["openai_called"] = kwargs
                    return FakeOpenAIStream([_delta(content="oai", finish_reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 1})])

    class FakeAnthropic:
        class messages:
            @staticmethod
            def stream(**kwargs):
                seen["anthropic_called"] = kwargs
                class Ctx:
                    async def __aenter__(self): return iter([])
                    async def __aexit__(self, *a): pass
                return Ctx()

    response = asyncio.run(mod.call(
        provider="openai_compat",
        client=FakeOpenAI(),
        model="m",
        system="sys",
        messages=[],
        tools=[],
        on_chunk=None,
    ))
    assert response.text_blocks == ["oai"]
    assert "openai_called" in seen


def test_call_unknown_provider_raises():
    mod = _load()
    with pytest.raises(ValueError, match="unknown provider"):
        asyncio.run(mod.call(
            provider="bogus", client=None, model="m", system="", messages=[], tools=[],
        ))


def test_anthropic_translator_strips_orphan_tool_results():
    """Orphan tool_results (synthetic CRITICAL injections from runner detection
    and max_iterations safety) get converted to user text blocks instead of
    tool_result blocks, since Anthropic rejects unmatched tool_use_ids."""
    mod = _load()
    sess_mod = _load_session()

    msgs = [
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="hi")]),
        sess_mod.AgentMessage(role="assistant", content=[
            sess_mod.ToolUseBlock(id="real-1", name="echo", input={"text": "x"}),
        ]),
        sess_mod.AgentMessage(role="tool", content=[
            sess_mod.ToolResultBlock(tool_use_id="real-1", content="echoed: x"),
        ]),
        # Synthetic CRITICAL injection — no matching tool_use
        sess_mod.AgentMessage(role="tool", content=[
            sess_mod.ToolResultBlock(
                tool_use_id="loop-detection-5",
                content="CRITICAL: stop",
                is_error=True,
            ),
        ]),
    ]
    result = mod._agent_messages_to_anthropic(msgs)
    # Last message must be a user text, not a tool_result with orphan id
    last = result[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "text"
    assert "CRITICAL: stop" in last["content"][0]["text"]
    # The matched tool_result for real-1 is preserved
    matched_msg = next(
        m for m in result
        if m["role"] == "user"
        and any(b.get("type") == "tool_result" for b in m["content"])
    )
    assert matched_msg["content"][0]["tool_use_id"] == "real-1"
    assert matched_msg["content"][0]["content"] == "echoed: x"


def test_anthropic_translator_strips_orphan_max_iter_injection():
    """The max-iter-N synthetic injection is also an orphan and must be
    downgraded to user text."""
    mod = _load()
    sess_mod = _load_session()

    msgs = [
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="hi")]),
        sess_mod.AgentMessage(role="tool", content=[
            sess_mod.ToolResultBlock(
                tool_use_id="max-iter-6",
                content="CRITICAL: max iterations (5) reached. Stop and report.",
                is_error=True,
            ),
        ]),
    ]
    result = mod._agent_messages_to_anthropic(msgs)
    # No tool_result blocks should appear anywhere
    for m in result:
        for b in m.get("content", []):
            assert b.get("type") != "tool_result"
    # The CRITICAL text must still be in a user text block
    last = result[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "text"
    assert "max iterations" in last["content"][0]["text"]


def test_call_openai_appends_the_trailing_shape_note_last():
    """The prompt half of the register fix (2026-08-22): the shape note rides
    at the END of the message list, right before she generates — its only
    other copy sits in the system prompt ahead of ~24k tokens of history."""
    import asyncio
    mod = _load() if "_load" in globals() else None
    import core.agent.llm_client as llm_mod
    captured = {}

    class _Stream:
        def __aiter__(self):
            async def gen():
                yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return gen()

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs); return _Stream()

    class _Client:
        chat = type("chat", (), {"completions": _Completions()})()

    from core.agent.session import AgentMessage, TextBlock
    msgs = [AgentMessage(role="user", content=[TextBlock(text="hey")])]
    asyncio.run(llm_mod.call_openai_compat(client=_Client(), model="m", system="SYS", messages=msgs, tools=[],
                                           trailing_system_note="[reply shape: short — one or two sentences; hard]"))
    assert captured["messages"][-1] == {"role": "system", "content": "[reply shape: short — one or two sentences; hard]"}
    captured.clear()
    asyncio.run(llm_mod.call_openai_compat(client=_Client(), model="m", system="SYS", messages=msgs, tools=[]))
    assert captured["messages"][-1]["role"] == "user"


# ── the governor on the LIVE path (2026-08-24 21:5x) ─────────────────────
# The register fix (08-22) hung the governor on PaLLiProviderSDKAdapter — but
# production with an api-key provider rides the raw AsyncOpenAI client
# (dispatch Path 1), so it NEVER fired: 0 reply_govern lines ever, 9 of 31
# replies over the caps since "live". The governor moves to
# call_openai_compat — the one seam both client kinds flow through — with
# emission deferred until after governing so the chunker, the session and
# Discord all see only the governed text.


def _stream_of(deltas, finish="stop", tool=False):
    class _S:
        def __aiter__(self):
            async def gen():
                for d in deltas:
                    yield {"choices": [{"delta": {"content": d}, "finish_reason": None}]}
                if tool:
                    yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "type": "function",
                                                                  "function": {"name": "read_file", "arguments": "{}"}}]},
                                       "finish_reason": None}]}
                yield {"choices": [{"delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 5, "completion_tokens": 9}}
            return gen()
    return _S()


class _Client1:
    def __init__(self, streams):
        self._streams = list(streams)
        class _Completions:
            def __init__(s, outer): s._o = outer
            async def create(s, **kwargs): return s._o._streams.pop(0)
        class _Chat:
            def __init__(s, outer): s.completions = _Completions(outer)
        self.chat = _Chat(self)


_OVER = ("word " * 80).strip()          # 80 words — over the short cap of 50


def test_live_path_governs_a_final_reply_and_defers_emission():
    import asyncio
    import core.agent.llm_client as llm_mod
    import core.agent.reply_governor as rg
    from core.agent.session import AgentMessage, TextBlock
    chunks = []

    async def on_chunk(t):
        chunks.append(t)

    async def fake_compress(text, cap):
        return "short and true."

    tok = rg.current_reply_shape.set("short")
    try:
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(rg, "governor_enabled", lambda: True), \
             __import__("unittest.mock", fromlist=["patch"]).patch.object(llm_mod, "_reply_compressor", fake_compress, create=True):
            resp = asyncio.run(llm_mod.call_openai_compat(
                client=_Client1([_stream_of([_OVER[:200], _OVER[200:]])]), model="m", system="SYS",
                messages=[AgentMessage(role="user", content=[TextBlock(text="hey")])], tools=[], on_chunk=on_chunk))
    finally:
        rg.current_reply_shape.reset(tok)
    assert "".join(resp.text_blocks) == "short and true."
    assert chunks == ["short and true."], "emission must be deferred and carry only the governed text"


def test_live_path_leaves_tool_call_responses_and_unvoted_turns_alone():
    import asyncio
    import core.agent.llm_client as llm_mod
    import core.agent.reply_governor as rg
    from core.agent.session import AgentMessage, TextBlock

    async def boom(text, cap):
        raise AssertionError("compressor must not run")

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(rg, "governor_enabled", lambda: True), \
         __import__("unittest.mock", fromlist=["patch"]).patch.object(llm_mod, "_reply_compressor", boom, create=True):
        # a tool-call response is not a final reply
        tok = rg.current_reply_shape.set("short")
        try:
            resp = asyncio.run(llm_mod.call_openai_compat(
                client=_Client1([_stream_of(["let me check"], tool=True)]), model="m", system="SYS",
                messages=[AgentMessage(role="user", content=[TextBlock(text="hey")])], tools=[]))
        finally:
            rg.current_reply_shape.reset(tok)
        assert resp.tool_uses and "let me check" in "".join(resp.text_blocks)
        # no vote set → untouched
        resp2 = asyncio.run(llm_mod.call_openai_compat(
            client=_Client1([_stream_of([_OVER])]), model="m", system="SYS",
            messages=[AgentMessage(role="user", content=[TextBlock(text="hey")])], tools=[]))
        assert "".join(resp2.text_blocks) == _OVER


def test_call_strips_an_imitated_working_note_stamp_before_delivery():
    """DEFECT_RECORD §20 (2026-09-02): the working-note stamp is the system's.
    If the model writes one itself, it never leaves the client — not into
    the streamed delivery, not into the response the runner records."""
    mod = _load()
    from core.agent.session import WORKING_NOTE_MARK
    chunks = []

    async def on_chunk(s):
        chunks.append(s)

    class _Completions:
        async def create(self, **kw):
            return FakeOpenAIStream([
                _delta(content=WORKING_NOTE_MARK + "the list: "),
                _delta(content="1. rent"),
                _delta(finish_reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 1}),
            ])

    client = type("C", (), {})()
    client.chat = type("Ch", (), {})()
    client.chat.completions = _Completions()
    resp = asyncio.run(mod.call_openai_compat(
        client=client, model="m", system="s", messages=[], tools=[], on_chunk=on_chunk,
    ))
    assert resp.text_blocks == ["the list: 1. rent"]
    assert "".join(chunks) == "the list: 1. rent"


def test_call_openai_compat_forwards_extra_body_to_the_sdk():
    """2026-09-02, the desktop-Ollama evening: through Ollama's OpenAI-style
    endpoint Qwen makes real tool calls only with thinking off, and that is a
    request field (`reasoning_effort: "none"`) the runner must be able to send.
    `extra_body` rides through to the SDK verbatim; absent by default."""
    mod = _load()
    seen = {}

    class FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    seen.update(kwargs)
                    return FakeOpenAIStream([_delta(content="hi", finish_reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 1})])

    asyncio.run(mod.call(provider="openai_compat", client=FakeOpenAI(), model="m", system="s", messages=[], tools=[],
                         extra_body={"reasoning_effort": "none"}))
    assert seen["extra_body"] == {"reasoning_effort": "none"}
    seen.clear()
    asyncio.run(mod.call(provider="openai_compat", client=FakeOpenAI(), model="m", system="s", messages=[], tools=[]))
    assert "extra_body" not in seen          # flag-off: byte-identical kwargs


# ── Batch 2, task 3 (cut the cost, 2026-09-27): the volatile tail ──────────
# The runner sends the stable prefix as `system` and the volatile tail as
# `system_tail`. openai_compat: the tail and the shape note share the ONE
# trailing system message (tail first). anthropic: the tail folds into
# `system`; the note stays dropped, exactly as today.

def _capturing_openai(captured):
    class _Stream:
        def __aiter__(self):
            async def gen():
                yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return gen()

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _Stream()

    class _Client:
        chat = type("chat", (), {"completions": _Completions()})()
    return _Client()


def _capturing_anthropic(captured):
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _CM()

    class _Client:
        messages = _Messages()
    return _Client()


def _one_user_message():
    from core.agent.session import AgentMessage, TextBlock
    return [AgentMessage(role="user", content=[TextBlock(text="hey")])]


def test_call_openai_puts_the_tail_and_the_note_in_one_trailing_system_message():
    import core.agent.llm_client as llm_mod
    captured = {}
    asyncio.run(llm_mod.call(provider="openai_compat", client=_capturing_openai(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[],
                             system_tail="## Current Time\n10:00", trailing_system_note="[reply shape: short]"))
    msgs = captured["messages"]
    assert msgs[0] == {"role": "system", "content": "STABLE"}
    assert msgs[-1] == {"role": "system", "content": "## Current Time\n10:00\n\n[reply shape: short]"}
    assert [m["role"] for m in msgs] == ["system", "user", "system"]  # exactly one trailing system message


def test_call_openai_tail_alone_and_note_alone_each_ride_last():
    import core.agent.llm_client as llm_mod
    captured = {}
    asyncio.run(llm_mod.call(provider="openai_compat", client=_capturing_openai(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[],
                             system_tail="## Current Time\n10:00\n", trailing_system_note=""))
    assert captured["messages"][-1] == {"role": "system", "content": "## Current Time\n10:00"}
    captured.clear()
    asyncio.run(llm_mod.call(provider="openai_compat", client=_capturing_openai(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[],
                             system_tail="", trailing_system_note="[reply shape: short]"))
    assert captured["messages"][-1] == {"role": "system", "content": "[reply shape: short]"}  # today's bytes
    captured.clear()
    asyncio.run(llm_mod.call(provider="openai_compat", client=_capturing_openai(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[],
                             system_tail="  ", trailing_system_note=None))
    assert captured["messages"][-1]["role"] == "user"  # nothing to trail


def test_call_anthropic_folds_the_tail_into_system_and_still_drops_the_note():
    import core.agent.llm_client as llm_mod
    captured = {}
    asyncio.run(llm_mod.call(provider="anthropic", client=_capturing_anthropic(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[],
                             system_tail="## Current Time\n10:00", trailing_system_note="[reply shape: short]"))
    assert captured["system"] == "STABLE\n\n## Current Time\n10:00"
    assert "[reply shape: short]" not in json.dumps(captured, default=str)
    assert all(m["role"] != "system" for m in captured["messages"])
    captured.clear()
    asyncio.run(llm_mod.call(provider="anthropic", client=_capturing_anthropic(captured), model="m",
                             system="STABLE", messages=_one_user_message(), tools=[], system_tail=""))
    assert captured["system"] == "STABLE"


def test_call_anthropic_empty_system_is_the_tail_exactly():
    """Whole-branch review (2026-09-27): an empty `system` with a tail sends
    the tail alone — no leading blank line."""
    import core.agent.llm_client as llm_mod
    captured = {}
    asyncio.run(llm_mod.call(provider="anthropic", client=_capturing_anthropic(captured), model="m",
                             system="", messages=_one_user_message(), tools=[],
                             system_tail="## Current Time\n10:00"))
    assert captured["system"] == "## Current Time\n10:00"
    captured.clear()
    asyncio.run(llm_mod.call(provider="anthropic", client=_capturing_anthropic(captured), model="m",
                             system="", messages=_one_user_message(), tools=[], system_tail=""))
    assert captured["system"] == ""
