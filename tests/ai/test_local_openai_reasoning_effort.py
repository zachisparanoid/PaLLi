"""2026-09-02: the local OpenAI-style provider (Ollama's /v1) can send
`reasoning_effort` on every request — `LOCAL_LLM_REASONING_EFFORT=none` is
what keeps a Qwen brain from thinking its budget away on judge lanes. Absent
when the knob is empty; the cloud OpenAI provider never sends it."""
from __future__ import annotations

import ai.llm_providers as lp


def _payload(provider):
    return provider._build_chat_payload(model="qwen3.6-palli", messages=[{"role": "user", "content": "hi"}],
                                        temperature=0.2, max_tokens=100, stream=False)


def test_local_provider_sends_reasoning_effort_when_the_knob_is_set(monkeypatch):
    monkeypatch.setattr(lp.config, "LOCAL_LLM_REASONING_EFFORT", "none", raising=False)
    p = lp.LocalOpenAIProvider()
    assert _payload(p)["reasoning_effort"] == "none"


def test_local_provider_sends_nothing_when_the_knob_is_empty(monkeypatch):
    monkeypatch.setattr(lp.config, "LOCAL_LLM_REASONING_EFFORT", "", raising=False)
    assert "reasoning_effort" not in _payload(lp.LocalOpenAIProvider())


def test_cloud_openai_provider_never_sends_it(monkeypatch):
    monkeypatch.setattr(lp.config, "LOCAL_LLM_REASONING_EFFORT", "none", raising=False)
    assert "reasoning_effort" not in _payload(lp.OpenAIProvider())
