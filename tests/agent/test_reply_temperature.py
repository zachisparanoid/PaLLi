"""Her reply temperature is a visible setting (Batch 4, task 6; owner call 1).

The reply call used to hard-code ``temperature=0.9`` inside
``call_openai_compat``. The owner's call keeps 0.9 but puts it where he can
see and change it: ``LLM_REPLY_TEMPERATURE``. Blank means "the provider's
own default" — the keyword is left out of the request, never sent as 0.0.
A brain that rejects the keyword (gpt-5*) never gets it, the same rule the
analysis-lane payload builder already follows.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path

import pytest

from core.agent.llm_client import _sampling_kwargs


REPO = Path(__file__).resolve().parents[2]


def test_temperature_setting_shapes_the_call(monkeypatch):
    assert _sampling_kwargs(temperature=0.9) == {"temperature": 0.9}
    assert _sampling_kwargs(temperature=None) == {}


def test_a_brain_that_rejects_temperature_never_gets_it():
    assert _sampling_kwargs(0.9, "gpt-5.5") == {}
    assert _sampling_kwargs(0.9, "gpt-5-mini") == {}
    assert _sampling_kwargs(0.9, "deepseek-v4-flash") == {"temperature": 0.9}


@pytest.mark.parametrize("model", ["o1", "o1-preview", "o3", "o3-mini", "o4-mini"])
def test_an_openai_o_series_brain_never_gets_it(model):
    """OpenAI's o-series refuse a temperature the same way gpt-5 does (fix
    round 1, R10a): a 0.9 there would 400 every reply."""
    assert _sampling_kwargs(0.9, model) == {}


def test_the_rule_is_the_payload_builders_own_rule():
    """One predicate, not two copies of a string check: the analysis-lane
    builder and the reply call must agree on which brains reject it."""
    from ai.llm_providers import build_openai_chat_payload, model_rejects_temperature

    for model in ("gpt-5.5", "gpt-5-mini", "o1", "o3-mini", "o4-mini",
                  "gpt-4o", "deepseek-v4-flash", "qwen3.5:9b"):
        payload = build_openai_chat_payload(
            model=model, messages=[], temperature=0.9, max_tokens=10, stream=False,
        )
        assert ("temperature" in payload) is (not model_rejects_temperature(model)), model
        assert (_sampling_kwargs(0.9, model) == {}) is model_rejects_temperature(model), model


# ── the env reader ─────────────────────────────────────────────────────────

def test_unset_means_the_default(monkeypatch):
    from core.config import _env_float_or_none

    monkeypatch.delenv("LLM_REPLY_TEMPERATURE", raising=False)
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) == 0.9


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_blank_means_the_provider_default_not_zero(monkeypatch, raw):
    from core.config import _env_float_or_none

    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", raw)
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) is None


def test_a_number_is_read(monkeypatch):
    from core.config import _env_float_or_none

    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", "0.7")
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) == 0.7
    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", "0")
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) == 0.0
    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", "1.7")            # no upper bound
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) == 1.7


@pytest.mark.parametrize("raw", ["warm", "0,9", "nan", "inf", "-0.9", "-1"])
def test_an_unreadable_value_keeps_the_default_and_says_so(monkeypatch, caplog, raw):
    """A negative counts as unreadable (fix round 1, R10a): -0.9 would 400
    every reply, and he would hear the outage line on every turn. No upper
    bound: what a provider accepts above 1 is the provider's business."""
    from core.config import _env_float_or_none

    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", raw)
    with caplog.at_level(logging.WARNING):
        assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) == 0.9
    assert any("LLM_REPLY_TEMPERATURE" in r.getMessage() for r in caplog.records)


def test_the_live_default_is_his_0_9():
    if "LLM_REPLY_TEMPERATURE" in os.environ:
        pytest.skip("this process sets LLM_REPLY_TEMPERATURE itself")
    from core.config import Config, config

    assert Config.LLM_REPLY_TEMPERATURE == 0.9
    assert config.LLM_REPLY_TEMPERATURE == 0.9


def test_the_template_ships_0_9_and_a_blank_copy_reads_as_none(monkeypatch):
    from dotenv import dotenv_values
    from core.config import _env_float_or_none

    values = dotenv_values(REPO / ".env.template")
    assert values.get("LLM_REPLY_TEMPERATURE") == "0.9"

    blanked = dotenv_values(stream=io.StringIO("LLM_REPLY_TEMPERATURE=\n"))
    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", blanked["LLM_REPLY_TEMPERATURE"] or "")
    assert _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9) is None


# ── the reply call itself ──────────────────────────────────────────────────

class _Stream:
    def __aiter__(self):
        async def gen():
            yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return gen()


def _client(captured: dict):
    async def create(**kwargs):
        captured.update(kwargs)
        return _Stream()

    class _Completions:
        pass
    _Completions.create = staticmethod(create)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _reply(captured: dict, model: str = "deepseek-v4-flash"):
    from core.agent.llm_client import call_openai_compat

    return asyncio.run(call_openai_compat(
        client=_client(captured), model=model, system="s", messages=[], tools=[],
    ))


def test_the_create_call_receives_0_9_by_default(monkeypatch):
    from core.config import _env_float_or_none, config

    monkeypatch.delenv("LLM_REPLY_TEMPERATURE", raising=False)
    monkeypatch.setattr(config, "LLM_REPLY_TEMPERATURE",
                        _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9))
    captured: dict = {}
    assert _reply(captured).text_blocks == ["hi"]
    assert captured["temperature"] == 0.9


def test_the_setting_is_read_at_call_time(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "LLM_REPLY_TEMPERATURE", 0.4)
    captured: dict = {}
    _reply(captured)
    assert captured["temperature"] == 0.4


def test_blank_setting_leaves_the_keyword_out(monkeypatch):
    from core.config import _env_float_or_none, config

    monkeypatch.setenv("LLM_REPLY_TEMPERATURE", "")
    monkeypatch.setattr(config, "LLM_REPLY_TEMPERATURE",
                        _env_float_or_none("LLM_REPLY_TEMPERATURE", 0.9))
    captured: dict = {}
    _reply(captured)
    assert "temperature" not in captured


def test_a_gpt5_reply_brain_is_sent_no_temperature(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "LLM_REPLY_TEMPERATURE", 0.9)
    captured: dict = {}
    _reply(captured, model="gpt-5.5")
    assert "temperature" not in captured
    assert captured["model"] == "gpt-5.5"
