"""Tests for the PaLLi-provider-to-SDK adapter.

The adapter wraps any PaLLi provider's `async chat_completion(...)` so
that it presents the `client.chat.completions.create(...)` shape that
the runner's `core.agent.llm_client.call_openai_compat` expects.
"""
from __future__ import annotations

import asyncio
import json

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/sdk_adapter.py", "agent_sdk_adapter_under_test")


def _load_runner_llm():
    """Load the runner's llm_client by absolute path so we share types
    with the rest of the agent suite."""
    from tests.agent.conftest import load_module
    return load_module("core/agent/llm_client.py", "agent_llm_client_under_test")


class FakeProvider:
    """Minimal stand-in for a PaLLi BaseLLMProvider subclass.

    Records the messages it was called with so tests can assert how the
    adapter translated the OpenAI-shape input. `response` is the canned
    string the provider returns from `chat_completion`. Set `raises` to
    have the provider raise on call.
    """
    def __init__(self, response: str = "", raises: Exception | None = None):
        self.response = response
        self.raises = raises
        self.last_call: dict | None = None
        self.provider_name = "codex"

    async def chat_completion(self, **kwargs):
        self.last_call = kwargs
        if self.raises is not None:
            raise self.raises
        return self.response


async def _drain(stream):
    """Consume an async iterable into a list."""
    out = []
    async for chunk in stream:
        out.append(chunk)
    return out


def test_adapter_constructs_with_provider():
    mod = _load()
    provider = FakeProvider(response="ok")
    adapter = mod.PaLLiProviderSDKAdapter(provider)
    # Must expose the OpenAI SDK surface
    assert hasattr(adapter, "chat")
    assert hasattr(adapter.chat, "completions")
    assert hasattr(adapter.chat.completions, "create")


@pytest.mark.asyncio
async def test_adapter_text_only_response():
    """Plain text response with no tools — single content chunk + finish stop."""
    mod = _load()
    provider = FakeProvider(response="Hello, world!")
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        tool_choice=None,
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = await _drain(stream)

    # Expect: one text chunk, one finish chunk with usage
    text_chunks = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
    assert len(text_chunks) == 1
    assert text_chunks[0]["choices"][0]["delta"]["content"] == "Hello, world!"
    assert len(finish_chunks) == 1
    assert finish_chunks[0]["choices"][0]["finish_reason"] == "stop"
    assert finish_chunks[0]["usage"]["completion_tokens"] > 0


@pytest.mark.asyncio
async def test_adapter_extracts_single_tool_call():
    """A single <tool_call> block becomes a tool_calls delta chunk."""
    mod = _load()
    response = (
        '<tool_call name="read_file">\n'
        '{"path": "foo.txt"}\n'
        '</tool_call>'
    )
    provider = FakeProvider(response=response)
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "read foo"}],
        tools=[{"type": "function",
                "function": {"name": "read_file",
                             "description": "read a file",
                             "parameters": {"type": "object"}}}],
        tool_choice="auto",
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = await _drain(stream)

    tool_chunks = [c for c in chunks
                   if c["choices"][0]["delta"].get("tool_calls")]
    finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
    assert len(tool_chunks) == 1
    tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tc["function"]["name"] == "read_file"
    assert json.loads(tc["function"]["arguments"]) == {"path": "foo.txt"}
    assert tc["id"].startswith("call_")
    assert finish_chunks[0]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_adapter_extracts_multiple_tool_calls():
    """Two tool_call blocks become two separate tool_calls delta chunks."""
    mod = _load()
    response = (
        '<tool_call name="read_file">\n{"path": "a.txt"}\n</tool_call>\n'
        '<tool_call name="write_file">\n{"path": "b.txt", "data": "x"}\n</tool_call>'
    )
    provider = FakeProvider(response=response)
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "do stuff"}],
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    chunks = await _drain(stream)

    tool_chunks = [c for c in chunks
                   if c["choices"][0]["delta"].get("tool_calls")]
    assert len(tool_chunks) == 2
    names = [c["choices"][0]["delta"]["tool_calls"][0]["function"]["name"]
             for c in tool_chunks]
    assert names == ["read_file", "write_file"]
    # Each tool call gets a distinct index (so the runner can distinguish them)
    indices = [c["choices"][0]["delta"]["tool_calls"][0]["index"]
               for c in tool_chunks]
    assert indices == [0, 1]


@pytest.mark.asyncio
async def test_adapter_text_plus_tool_call():
    """Text outside the tool_call block becomes a content chunk; the
    tool_call becomes a tool_calls chunk. Both arrive in the same stream."""
    mod = _load()
    response = (
        "I'll read that file for you.\n"
        '<tool_call name="read_file">\n'
        '{"path": "foo.txt"}\n'
        '</tool_call>'
    )
    provider = FakeProvider(response=response)
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "read foo"}],
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    chunks = await _drain(stream)

    text_chunks = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    tool_chunks = [c for c in chunks
                   if c["choices"][0]["delta"].get("tool_calls")]
    assert len(text_chunks) == 1
    assert "I'll read" in text_chunks[0]["choices"][0]["delta"]["content"]
    assert len(tool_chunks) == 1


@pytest.mark.asyncio
async def test_adapter_malformed_tool_call_falls_back_to_text():
    """Malformed JSON inside <tool_call> should NOT crash. The block is
    preserved in the text portion so the model can self-correct on the
    next iteration."""
    mod = _load()
    response = (
        '<tool_call name="read_file">\n'
        '{this is not valid JSON\n'
        '</tool_call>'
    )
    provider = FakeProvider(response=response)
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "read foo"}],
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    chunks = await _drain(stream)

    # No tool_calls chunk emitted (parsing failed)
    tool_chunks = [c for c in chunks
                   if c["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks == []
    # Text chunk should preserve the malformed block verbatim
    text_chunks = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    assert len(text_chunks) == 1
    assert "<tool_call" in text_chunks[0]["choices"][0]["delta"]["content"]
    # Finish reason is "stop" since no tool_calls succeeded
    finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
    assert finish_chunks[0]["choices"][0]["finish_reason"] == "stop"


def test_adapter_handles_tool_choice_kwarg():
    """tool_choice='required' or any other value is accepted and ignored
    (the prompt-engineered protocol can't strictly enforce the same shape
    as native tool_use)."""
    mod = _load()
    provider = FakeProvider(response="text")
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    async def go():
        return await adapter.chat.completions.create(
            model="gpt-x",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function",
                    "function": {"name": "x", "description": "y",
                                 "parameters": {}}}],
            tool_choice="required",  # should not crash
            stream=True,
            stream_options=None,
        )

    stream = asyncio.run(go())
    assert stream is not None  # No crash; an async iterable was returned


@pytest.mark.asyncio
async def test_adapter_translates_tool_role_messages_to_user_text():
    """Prior tool-result messages (role=tool) get re-rendered as a
    user-text <tool_result> block so the underlying provider sees them
    as natural-language conversation history."""
    mod = _load()
    provider = FakeProvider(response="ok")
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    messages = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "read foo"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "read_file",
                                      "arguments": '{"path":"foo.txt"}'}}]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": "file contents: hello"},
    ]

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=messages,
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    await _drain(stream)

    sent = provider.last_call["messages"]
    # The role="tool" message must have become role="user" with a
    # <tool_result> wrapper, since codex (and similar) don't understand
    # the role="tool" convention.
    assert all(m["role"] != "tool" for m in sent)
    user_msgs = [m for m in sent if m["role"] == "user"]
    has_tool_result = any('<tool_result tool_call_id="call_1">' in m["content"]
                          and "file contents: hello" in m["content"]
                          for m in user_msgs)
    assert has_tool_result


@pytest.mark.asyncio
async def test_adapter_works_through_runner_call_openai_compat():
    """End-to-end: the adapter is callable as the `client` argument to
    the runner's call_openai_compat. The runner consumes the chunks via
    _collect_openai_stream and returns a populated LLMResponse."""
    adapter_mod = _load()
    runner_llm = _load_runner_llm()
    import core.agent.session as sess_mod  # canonical types — same module

    response = (
        "I'll do it.\n"
        '<tool_call name="ping">\n{"q": "hello"}\n</tool_call>'
    )
    provider = FakeProvider(response=response)
    adapter = adapter_mod.PaLLiProviderSDKAdapter(provider)

    user_msg = sess_mod.AgentMessage(
        role="user",
        content=[sess_mod.TextBlock(text="say hi")],
    )

    captured_chunks: list[str] = []

    async def on_chunk(s: str) -> None:
        captured_chunks.append(s)

    result = await runner_llm.call_openai_compat(
        client=adapter,
        model="gpt-x",
        system="you are a helpful agent",
        messages=[user_msg],
        tools=[{"name": "ping", "description": "ping a target",
                "input_schema": {"type": "object"}}],
        on_chunk=on_chunk,
    )

    # The runner's collector should have parsed the text + tool_call
    # back into the LLMResponse shape.
    assert result.text_blocks  # at least the "I'll do it." text
    assert any("I'll do it" in t for t in result.text_blocks)
    assert len(result.tool_uses) == 1
    assert result.tool_uses[0].name == "ping"
    assert result.tool_uses[0].input == {"q": "hello"}
    assert result.stop_reason == "tool_use"
    # on_chunk got the streaming text
    assert any("I'll do it" in c for c in captured_chunks)


@pytest.mark.asyncio
async def test_adapter_handles_provider_returning_none():
    """When the underlying provider returns None (timeout, error, etc.),
    the adapter should yield an empty-content finish chunk rather than
    crash."""
    mod = _load()
    # FakeProvider with no response = returns "" — let's force None instead
    class NoneProvider:
        provider_name = "codex"
        async def chat_completion(self, **kwargs):
            return None

    adapter = mod.PaLLiProviderSDKAdapter(NoneProvider())

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    chunks = await _drain(stream)
    # At minimum there should be a finish chunk with stop
    finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
    assert len(finish_chunks) == 1
    assert finish_chunks[0]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_adapter_handles_provider_raising():
    """If the underlying provider raises, the adapter should still yield
    a finish chunk (as if the provider returned empty), so the runner
    sees a clean LLMResponse rather than an exception."""
    mod = _load()
    provider = FakeProvider(raises=RuntimeError("boom"))
    adapter = mod.PaLLiProviderSDKAdapter(provider)

    stream = await adapter.chat.completions.create(
        model="gpt-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice=None,
        stream=True,
        stream_options=None,
    )
    chunks = await _drain(stream)
    finish_chunks = [c for c in chunks if c["choices"][0].get("finish_reason")]
    assert len(finish_chunks) == 1


# ── the reply-length governor rides the adapter (2026-08-22) ─────────────
# Her final reply text exists in exactly one place before her session, the
# Discord chunks and the reflection see it: here. Governing here keeps all
# four identical (the custody law).

def _over_short():
    return ("I think the honest answer is that the build landed cleanly and the tests are green, "
            "so there is nothing for you to do tonight except rest; the forge tool survived its first "
            "night without a single error, the reminder you set fired on time, and I kept an eye on the "
            "stake review as well, which finally ran the way it was supposed to — all good here.")


@pytest.mark.asyncio
async def test_adapter_governs_a_final_reply_when_the_turn_is_short():
    import core.agent.reply_governor as rg
    mod = _load()
    provider = FakeProvider(response=_over_short())

    async def compress(text, cap):
        return "Build landed, tests green — rest tonight."

    adapter = mod.PaLLiProviderSDKAdapter(provider, reply_governor_enabled=lambda: True, reply_compressor=compress)
    token = rg.current_reply_shape.set("short")
    try:
        stream = await adapter.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                                       tools=None, tool_choice=None, stream=True,
                                                       stream_options={"include_usage": True})
        chunks = await _drain(stream)
    finally:
        rg.current_reply_shape.reset(token)
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert text == "Build landed, tests green — rest tonight."


@pytest.mark.asyncio
async def test_adapter_leaves_tool_call_iterations_and_unvoted_turns_alone():
    import core.agent.reply_governor as rg
    mod = _load()
    called = []

    async def compress(text, cap):
        called.append(text); return "x"

    # a tool-call response is not a final reply
    provider = FakeProvider(response=_over_short() + '\n<tool_call name="read_file">\n{"path": "a"}\n</tool_call>')
    adapter = mod.PaLLiProviderSDKAdapter(provider, reply_governor_enabled=lambda: True, reply_compressor=compress)
    token = rg.current_reply_shape.set("short")
    try:
        chunks = await _drain(await adapter.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                                                    tools=None, tool_choice=None, stream=True,
                                                                    stream_options={"include_usage": True}))
    finally:
        rg.current_reply_shape.reset(token)
    assert not called
    assert any(c["choices"][0]["delta"].get("tool_calls") for c in chunks)
    # no vote set → untouched
    provider2 = FakeProvider(response=_over_short())
    adapter2 = mod.PaLLiProviderSDKAdapter(provider2, reply_governor_enabled=lambda: True, reply_compressor=compress)
    chunks2 = await _drain(await adapter2.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                                                  tools=None, tool_choice=None, stream=True,
                                                                  stream_options={"include_usage": True}))
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks2) == _over_short()
    assert not called


@pytest.mark.asyncio
async def test_adapter_flag_off_is_byte_identical():
    import core.agent.reply_governor as rg
    mod = _load()
    called = []

    async def compress(text, cap):
        called.append(text); return "x"

    provider = FakeProvider(response=_over_short())
    adapter = mod.PaLLiProviderSDKAdapter(provider, reply_governor_enabled=lambda: False, reply_compressor=compress)
    token = rg.current_reply_shape.set("short")
    try:
        chunks = await _drain(await adapter.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                                                    tools=None, tool_choice=None, stream=True,
                                                                    stream_options={"include_usage": True}))
    finally:
        rg.current_reply_shape.reset(token)
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks) == _over_short()
    assert not called
