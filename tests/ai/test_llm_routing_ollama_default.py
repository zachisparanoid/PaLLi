"""2026-09-02 18:26 — the first minute on the desktop's Ollama: `arbiter_freshness`
and `daydream_tick` asked Ollama for `gpt-4o` and got 404. Both lanes have no
routing entry, so their model comes from `_default_model_for_provider`, which
knew deepseek / local_openai / codex / anthropic and answered OPENAI_MODEL for
everything else — before the caller's own fallback. The default provider's
model must win, and the fallback must beat a foreign default."""
from __future__ import annotations

import pytest

from ai import llm_routing as rt


@pytest.fixture
def ollama_default(monkeypatch):
    monkeypatch.setattr(rt, "_settings_default_provider", lambda: "ollama")
    monkeypatch.setattr(rt.config, "LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(rt.config, "OLLAMA_MODEL", "qwen3.6-palli", raising=False)
    monkeypatch.setattr(rt.config, "OPENAI_MODEL", "gpt-4o", raising=False)
    for field in rt.TASK_PROVIDER_FIELDS.values():
        monkeypatch.setattr(rt.config, field, "", raising=False)


def test_an_unrouted_lane_takes_the_ollama_default_model(ollama_default, monkeypatch):
    monkeypatch.setattr(rt, "_settings_routing", lambda: None)
    assert rt.get_task_model("arbiter_freshness") == "ollama::qwen3.6-palli"
    assert rt.get_task_model("daydream", fallback=rt.get_task_model("reflection")) == "ollama::qwen3.6-palli"


def test_get_llm_model_knows_ollama(ollama_default):
    assert rt.get_llm_model() == "qwen3.6-palli"


def test_a_foreign_default_never_beats_the_callers_fallback(monkeypatch):
    """A provider with no default-model field still must not answer gpt-4o
    when the caller supplied a fallback."""
    monkeypatch.setattr(rt, "_settings_default_provider", lambda: "claude")
    monkeypatch.setattr(rt.config, "LLM_PROVIDER", "claude", raising=False)
    monkeypatch.setattr(rt.config, "OPENAI_MODEL", "gpt-4o", raising=False)
    assert rt._default_model_for_provider("claude", fallback="sonnet") == "sonnet"
