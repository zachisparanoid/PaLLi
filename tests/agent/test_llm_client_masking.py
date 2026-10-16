"""Batch 1, Task 3 (2026-09-26): the live brain's two provider seams.

Every request that leaves `core/agent/llm_client.py` — the openai-compat
path (every api-key brain, and the SDK adapter behind it) and the native
Anthropic path — is redacted (known literals, gone for good) and then
masked (secret-shaped strings swapped for `[MASKED:<hex>]`). The whole
request: system prompt, his words, her tool-call arguments (JSON-encoded),
the tool RESULTS, and the reply-shape note that rides last.

Fake secrets only. Nothing here reads the live vault: `sr.VAULT_DIR` and
`sr.DENYLIST_PATH` point into tmp for the vault case.
"""
from __future__ import annotations

import json

import pytest

from core import secrets_redaction as sr
from core.agent import llm_client
from core.agent import payload_masking as pm
from core.agent.session import AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock

SSN = "987-65-4321"
KEY = "gsk_" + "Zq9" * 12
PASSPHRASE = "correct-horse-battery-staple-99"


class _FakeStream:
    def __init__(self, captured, kwargs):
        captured.append(kwargs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeClient:
    """client.chat.completions.create(**kwargs) — captures the request and
    streams nothing back (a clean empty stop: no re-roll, no governor)."""

    def __init__(self):
        self.captured: list[dict] = []

        class _C:
            pass

        self.chat = _C()
        self.chat.completions = _C()

        async def create(**kwargs):
            return _FakeStream(self.captured, kwargs)

        self.chat.completions.create = create


def _conversation(secret_in_result: str) -> list[AgentMessage]:
    """His words, her tool call (the key in its arguments, once after a
    newline so the JSON-escaped `\\n` boundary is exercised), and the tool's
    result carrying a secret."""
    return [
        AgentMessage(role="user", content=[TextBlock(text=f"look up {SSN} and file {KEY}")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="On it."),
            ToolUseBlock(id="t1", name="local_shell",
                         input={"command": f"echo {KEY}", "note": f"line one\n{KEY}"}),
        ]),
        AgentMessage(role="tool", content=[
            ToolResultBlock(tool_use_id="t1", content=f"found: {secret_in_result}"),
        ]),
    ]


@pytest.mark.asyncio
async def test_openai_compat_request_never_carries_the_ssn():
    pm.reset_for_tests()
    client = _FakeClient()
    msgs = [AgentMessage(role="user", content=[TextBlock(text=f"look up {SSN}")])]
    await llm_client.call_openai_compat(
        client=client, model="m", system=f"memory: {SSN}", messages=msgs, tools=[],
    )
    sent = str(client.captured[0]["messages"])
    assert SSN not in sent and "[MASKED:" in sent


@pytest.mark.asyncio
async def test_openai_compat_masks_every_part_of_the_request():
    """System, user text, the tool call's JSON arguments, the tool RESULT and
    the trailing system note — none carries the SSN or the key."""
    pm.reset_for_tests()
    client = _FakeClient()
    await llm_client.call_openai_compat(
        client=client, model="m",
        system=f"memory: his number is {SSN}",
        messages=_conversation(SSN),
        tools=[],
        trailing_system_note=f"(reply short; do not repeat {KEY})",
        system_tail=f"tail sees {KEY} too",
    )
    payload = client.captured[0]["messages"]
    whole = json.dumps(payload)
    assert SSN not in whole
    assert KEY not in whole
    assert "[MASKED:" in whole

    by_role = {}
    for m in payload:
        by_role.setdefault(m["role"], []).append(m)
    system_first, system_last = payload[0], payload[-1]
    assert system_first["role"] == "system" and "[MASKED:" in system_first["content"]
    assert system_last["role"] == "system" and "[MASKED:" in system_last["content"]
    assert KEY not in system_last["content"]
    # The volatile tail (Batch 2) rides in that same trailing message, masked.
    assert f"tail sees {pm.mask_text(KEY)} too" in system_last["content"]
    user = by_role["user"][0]
    assert "[MASKED:" in user["content"] and SSN not in user["content"]
    call = by_role["assistant"][0]["tool_calls"][0]["function"]["arguments"]
    assert KEY not in call and "[MASKED:" in call
    # Still valid JSON after masking — the provider must be able to parse it.
    args = json.loads(call)
    assert args["command"].startswith("echo [MASKED:")
    assert "[MASKED:" in args["note"]
    result = by_role["tool"][0]["content"]
    assert SSN not in result and "[MASKED:" in result


@pytest.mark.asyncio
async def test_openai_compat_one_value_one_token_across_the_request():
    """The key appears in his words, her arguments and the note — one token
    everywhere, so the model can refer to 'that key'."""
    pm.reset_for_tests()
    client = _FakeClient()
    await llm_client.call_openai_compat(
        client=client, model="m", system="sys", messages=_conversation("nothing"),
        tools=[], trailing_system_note=f"note {KEY}",
    )
    token = pm.mask_text(KEY)
    whole = json.dumps(client.captured[0]["messages"])
    assert whole.count(token) >= 3


@pytest.mark.asyncio
async def test_openai_compat_does_not_mutate_her_session_messages():
    """Masking is payload-only: the session's own blocks keep the real
    values (disk keeps them; only the wire is masked)."""
    pm.reset_for_tests()
    client = _FakeClient()
    msgs = _conversation(SSN)
    await llm_client.call_openai_compat(
        client=client, model="m", system="sys", messages=msgs, tools=[],
    )
    assert msgs[0].content[0].text == f"look up {SSN} and file {KEY}"
    assert msgs[1].content[1].input["command"] == f"echo {KEY}"
    assert msgs[2].content[0].content == f"found: {SSN}"


def _fake_vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    (v / ".passphrase").write_text(PASSPHRASE + "\n", encoding="utf-8")
    (v / "vault.key").write_text("b" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "no-denylist.txt")
    monkeypatch.setattr(sr, "VAULT_DIR", v)
    sr.reset_for_tests()


@pytest.mark.asyncio
async def test_a_tool_result_carrying_the_vault_passphrase_goes_out_redacted(tmp_path, monkeypatch):
    """Review Focus 3: `cat ~/.palli/vault/.passphrase` in a tool — its
    RESULT reaches the next request as [REDACTED], never the passphrase."""
    _fake_vault(tmp_path, monkeypatch)
    pm.reset_for_tests()
    client = _FakeClient()
    try:
        await llm_client.call_openai_compat(
            client=client, model="m", system="sys",
            messages=_conversation(PASSPHRASE), tools=[],
        )
    finally:
        sr.reset_for_tests()
    whole = json.dumps(client.captured[0]["messages"])
    assert PASSPHRASE not in whole
    tool_msg = [m for m in client.captured[0]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "found: [REDACTED]"


class _FakeAnthropicStreamCM:
    """`client.messages.stream(**kwargs)` — an async context manager whose
    stream yields no events (an empty stream collects to an empty reply)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeAnthropicClient:
    def __init__(self):
        self.captured: list[dict] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.captured.append(kwargs)
                return _FakeAnthropicStreamCM()

        self.messages = _Messages()


@pytest.mark.asyncio
async def test_anthropic_request_is_masked_system_and_messages():
    pm.reset_for_tests()
    client = _FakeAnthropicClient()
    resp = await llm_client.call_anthropic(
        client=client, model="claude-x", system=f"memory: {SSN} and {KEY}",
        messages=_conversation(SSN), tools=[],
        system_tail=f"tail sees {KEY} too",
    )
    assert resp.text_blocks == []
    sent = client.captured[0]
    assert SSN not in sent["system"] and KEY not in sent["system"]
    assert "[MASKED:" in sent["system"]
    # The volatile tail (Batch 2) folds onto `system` and is masked with it.
    assert f"tail sees {pm.mask_text(KEY)} too" in sent["system"]
    whole = json.dumps(sent["messages"])
    assert SSN not in whole and KEY not in whole
    assert "[MASKED:" in whole
    # Tool-use input stays a dict (Anthropic takes objects, not JSON text).
    tool_use = [b for m in sent["messages"] for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "tool_use"][0]
    assert isinstance(tool_use["input"], dict)
    assert tool_use["input"]["command"].startswith("echo [MASKED:")


@pytest.mark.asyncio
async def test_anthropic_tool_result_with_the_vault_passphrase_goes_out_redacted(tmp_path, monkeypatch):
    _fake_vault(tmp_path, monkeypatch)
    pm.reset_for_tests()
    client = _FakeAnthropicClient()
    try:
        await llm_client.call_anthropic(
            client=client, model="claude-x", system="sys",
            messages=_conversation(PASSPHRASE), tools=[],
        )
    finally:
        sr.reset_for_tests()
    whole = json.dumps(client.captured[0]["messages"])
    assert PASSPHRASE not in whole
    assert "found: [REDACTED]" in whole
