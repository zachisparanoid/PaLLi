"""Batch 1, Task 3 (2026-09-26): the background lanes' provider seam.

`ai/llm_client.LLMClient` is the one funnel every background lane rides
(reflection, the moment loop, judges, the reply governor …): each call to
`execute_llm_provider_request` goes through `chat_completion` or
`chat_completion_stream`. The messages it hands on are redacted then masked.
`vision_completion` masks the prompt (and any history) — never the image.

Fake secrets only; the route resolution is stubbed so nothing reaches a
provider.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import ai.llm_client as llm_mod
from core.agent import payload_masking as pm

SSN = "987-65-4321"
KEY = "gsk_" + "Zq9" * 12


def _messages():
    return [
        {"role": "system", "content": f"his memory: {SSN}"},
        {"role": "user", "content": [
            {"type": "text", "text": f"file this key: {KEY}"},
        ]},
    ]


@pytest.fixture
def captured(monkeypatch):
    seen: list = []

    def fake_prepare(**kwargs):
        return SimpleNamespace(kwargs=kwargs)

    async def fake_execute(execution, request):
        seen.append(request)
        return "ok"

    monkeypatch.setattr(llm_mod, "prepare_llm_route_execution", fake_prepare)
    monkeypatch.setattr(llm_mod, "execute_llm_provider_request", fake_execute)
    return seen


@pytest.mark.asyncio
async def test_chat_completion_hands_on_masked_messages(captured):
    pm.reset_for_tests()
    msgs = _messages()
    out = await llm_mod.llm.chat_completion(messages=msgs, log_context="test")
    assert out == "ok"
    sent = json.dumps(captured[0].messages)
    assert SSN not in sent and KEY not in sent
    assert sent.count("[MASKED:") == 2
    # The caller's list is not rewritten in place.
    assert msgs[0]["content"] == f"his memory: {SSN}"


@pytest.mark.asyncio
async def test_chat_completion_stream_hands_on_masked_messages(captured):
    pm.reset_for_tests()
    out = await llm_mod.llm.chat_completion_stream(
        messages=_messages(), log_context="test",
    )
    assert out == "ok"
    request = captured[0]
    assert request.streamed is True
    sent = json.dumps(request.messages)
    assert SSN not in sent and KEY not in sent
    assert "[MASKED:" in sent


@pytest.mark.asyncio
async def test_vision_completion_masks_the_prompt_and_history_never_the_image(monkeypatch):
    pm.reset_for_tests()
    seen: dict = {}

    class _Provider:
        async def vision_completion(self, image_base64, prompt, conversation_history,
                                    model, temperature, max_tokens, content_type):
            seen.update(image=image_base64, prompt=prompt, history=conversation_history)
            return {"ok": True}

    def fake_prepare(**kwargs):
        return SimpleNamespace(provider=_Provider(), resolved_model="m")

    monkeypatch.setattr(llm_mod, "prepare_vision_execution", fake_prepare)
    # An image payload that happens to contain a key-shaped run: it must
    # arrive byte-exact, because masking inside base64 corrupts the image.
    image = "AKIA" + "ABCDEFGHIJKLMNOP"

    out = await llm_mod.llm.vision_completion(
        image, f"what does this say? ({SSN})",
        conversation_history=[{"role": "user", "content": f"earlier: {KEY}"}],
    )

    assert out == {"ok": True}
    assert seen["image"] == image
    assert SSN not in seen["prompt"] and "[MASKED:" in seen["prompt"]
    assert KEY not in json.dumps(seen["history"]) and "[MASKED:" in json.dumps(seen["history"])


@pytest.mark.asyncio
async def test_vision_completion_without_history_passes_none(monkeypatch):
    pm.reset_for_tests()
    seen: dict = {}

    class _Provider:
        async def vision_completion(self, image_base64, prompt, conversation_history, *a):
            seen.update(history=conversation_history)
            return None

    monkeypatch.setattr(
        llm_mod, "prepare_vision_execution",
        lambda **kw: SimpleNamespace(provider=_Provider(), resolved_model="m"),
    )
    await llm_mod.llm.vision_completion("img", "describe")
    assert seen["history"] is None
