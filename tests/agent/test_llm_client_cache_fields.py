"""Cut the Cost, Task 1: the provider's cache counters are carried and logged.

DeepSeek returns ``usage.prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
on every completion. Nothing read them, so the batch's before/after would have
been a hope. These tests pin the two places the counters now show up: the reply
lane's ``llm_call`` line (via ``LLMResponse``) and each background lane's
``[lane] cache_hit=N cache_miss=N`` line.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.agent.llm_client import LLMResponse, _collect_openai_stream, _response_from_usage


def test_cache_fields_read_from_usage():
    r = _response_from_usage({"prompt_tokens": 100, "completion_tokens": 5,
                              "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36},
                             text_blocks=["x"])
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (64, 36)


def test_missing_cache_fields_are_zero_not_an_error():
    r = _response_from_usage({"prompt_tokens": 10, "completion_tokens": 1}, text_blocks=["x"])
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (0, 0)


def test_none_counts_are_zero_and_other_fields_pass_through():
    r = _response_from_usage(
        {"prompt_tokens": None, "completion_tokens": 3,
         "prompt_cache_hit_tokens": None, "prompt_cache_miss_tokens": None},
        text_blocks=["x"], stop_reason="max_tokens", reasoning="thought",
    )
    assert (r.prompt_tokens, r.completion_tokens) == (0, 3)
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (0, 0)
    assert r.text_blocks == ["x"]
    assert r.stop_reason == "max_tokens"
    assert r.reasoning == "thought"


def test_non_numeric_counts_are_zero_and_never_raise():
    """A counter must never fail a billed request: a provider's odd value
    (a string, a nested dict) reads as 0 and the other counts still read
    (whole-branch review, 2026-09-27)."""
    r = _response_from_usage(
        {"prompt_tokens": 100, "completion_tokens": 5,
         "prompt_cache_hit_tokens": "n/a", "prompt_cache_miss_tokens": {"x": 1}},
        text_blocks=["x"],
    )
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (0, 0)
    assert (r.prompt_tokens, r.completion_tokens) == (100, 5)
    assert r.text_blocks == ["x"]


def test_response_defaults_are_zero():
    r = LLMResponse()
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (0, 0)


def test_stream_collector_carries_the_cache_counters():
    """The reply lane's real build site: the final usage chunk (a dict after
    model_dump(), DeepSeek's extra fields intact) reaches the LLMResponse."""
    async def stream():
        yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        yield {"choices": [], "usage": {
            "prompt_tokens": 1000, "completion_tokens": 10,
            "prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 360,
        }}

    r = asyncio.run(_collect_openai_stream(stream(), None))
    assert r.text_blocks == ["hi"]
    assert (r.prompt_tokens, r.completion_tokens) == (1000, 10)
    assert (r.cache_hit_tokens, r.cache_miss_tokens) == (640, 360)


def test_llm_call_line_ends_with_the_cache_counters(caplog):
    from core.agent.observability import log_llm_call

    with caplog.at_level(logging.INFO, logger="core.agent.llm_client"):
        log_llm_call(
            session_key="discord:1", iteration_n=1, provider="openai_compat", model="m",
            prompt_tokens=1000, completion_tokens=10, latency_ms=5,
            cache_hit_tokens=640, cache_miss_tokens=360,
        )
    messages = [r.getMessage() for r in caplog.records if r.name == "core.agent.llm_client"]
    assert messages == [
        "llm_call session=discord:1 iter=1 provider=openai_compat model=m "
        "prompt_tokens=1000 completion_tokens=10 latency_ms=5 cache_hit=640 cache_miss=360"
    ]


def test_llm_call_line_counters_default_to_zero(caplog):
    from core.agent.observability import log_llm_call

    with caplog.at_level(logging.INFO, logger="core.agent.llm_client"):
        log_llm_call(
            session_key="s", iteration_n=2, provider="anthropic", model="m",
            prompt_tokens=1, completion_tokens=1, latency_ms=1,
        )
    messages = [r.getMessage() for r in caplog.records if r.name == "core.agent.llm_client"]
    assert messages[-1].endswith("latency_ms=1 cache_hit=0 cache_miss=0")


# --- the runner passes the response's counters to the llm_call line ---------

@dataclass
class _Turn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "hello"
    metadata: Any = None
    received_at: Any = None


@dataclass
class _Ctx:
    turn: _Turn = field(default_factory=_Turn)
    user_text: str = "hello"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


def test_runner_logs_the_responses_cache_counters(tmp_path, caplog):
    from tests.agent.conftest import load_module
    import core.agent.llm_client as llm_mod_real

    mod = load_module("core/agent/runner.py", "agent_runner_cache_fields_under_test")

    async def fake_recall(turn):
        return _Ctx()

    async def fake_cadence_llm(prompt):
        return ('{"should_respond": true, "register": "neutral", "length": "short", '
                '"phase": "settled_conversation", "target_beats": 1, "observation_text": "ok"}')

    async def fake_call(**kw):
        return llm_mod_real.LLMResponse(
            text_blocks=["Hello there."], tool_uses=[], stop_reason="end_turn",
            prompt_tokens=100, completion_tokens=5,
            cache_hit_tokens=64, cache_miss_tokens=36,
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
        with caplog.at_level(logging.INFO, logger="core.agent.llm_client"):
            asyncio.run(mod.run_turn(_Turn(), deps=deps, on_chunk=None))
    finally:
        llm_mod_real.call = orig_call

    llm_calls = [r.getMessage() for r in caplog.records
                 if r.name == "core.agent.llm_client" and r.getMessage().startswith("llm_call ")]
    assert llm_calls, "the runner logged no llm_call line"
    assert re.search(
        r" prompt_tokens=100 completion_tokens=5 latency_ms=\d+ cache_hit=64 cache_miss=36$",
        llm_calls[0],
    ), llm_calls[0]


# --- the background lanes: OpenAICompatibleProvider.chat_completion ----------

class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status
        self.headers: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)

    def post(self, url, headers=None, json=None, timeout=None):
        return self._responses.pop(0)


def _run_background_lane(monkeypatch, caplog, usage: Any, log_context: str) -> list[str]:
    from ai.llm_client import DeepSeekProvider

    provider = DeepSeekProvider()
    session = _FakeSession([_FakeResponse({
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": usage,
    })])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    with caplog.at_level(logging.INFO, logger="ai.llm_client"):
        result = asyncio.run(provider.chat_completion(
            [{"role": "user", "content": "hello"}],
            model="deepseek-chat",
            log_context=log_context,
        ))
    assert result == "ok"
    return [r.getMessage() for r in caplog.records
            if r.name == "ai.llm_client" and "cache_hit=" in r.getMessage()]


def test_background_lane_logs_its_cache_counters(monkeypatch, caplog):
    lines = _run_background_lane(monkeypatch, caplog, {
        "prompt_tokens": 6300, "completion_tokens": 20,
        "prompt_cache_hit_tokens": 6000, "prompt_cache_miss_tokens": 300,
    }, "reflect_turn")
    assert lines == ["[reflect_turn] cache_hit=6000 cache_miss=300"]


def test_background_lane_without_counters_logs_zeros(monkeypatch, caplog):
    lines = _run_background_lane(monkeypatch, caplog, None, "present_moment_cycle")
    assert lines == ["[present_moment_cycle] cache_hit=0 cache_miss=0"]


def test_background_lane_with_non_numeric_counters_keeps_its_response(monkeypatch, caplog):
    """A non-numeric cache field must not send a paid 200 into the generic
    ``except`` -- the lane keeps its content and logs zeros."""
    lines = _run_background_lane(monkeypatch, caplog, {
        "prompt_tokens": 6300, "completion_tokens": 20,
        "prompt_cache_hit_tokens": "n/a", "prompt_cache_miss_tokens": {"x": 1},
    }, "reflect_turn")
    assert lines == ["[reflect_turn] cache_hit=0 cache_miss=0"]


# --- the call ledger (the owner, 10-05: "call by call") ----------------------

def _ledger_rows() -> list[dict]:
    from ai import call_ledger

    return call_ledger.read_calls(0, 4_102_444_800)  # everything this test wrote


def test_background_lane_writes_its_call_to_the_ledger(monkeypatch, caplog):
    _run_background_lane(monkeypatch, caplog, {
        "prompt_tokens": 6300, "completion_tokens": 20,
        "prompt_cache_hit_tokens": 6000, "prompt_cache_miss_tokens": 300,
    }, "reflect_turn")
    rows = _ledger_rows()
    assert [(r["lane"], r["provider"], r["model"], r["hit"], r["miss"], r["out"]) for r in rows] == [
        ("reflect_turn", "deepseek", "deepseek-chat", 6000, 300, 20)]


def test_background_lane_without_usage_still_writes_a_zero_line(monkeypatch, caplog):
    _run_background_lane(monkeypatch, caplog, None, "present_moment_cycle")
    assert [(r["lane"], r["hit"], r["miss"], r["out"]) for r in _ledger_rows()] == [
        ("present_moment_cycle", 0, 0, 0)]


def test_runner_writes_each_step_to_the_ledger_as_reply(tmp_path, caplog):
    test_runner_logs_the_responses_cache_counters(tmp_path, caplog)
    rows = _ledger_rows()
    assert rows, "the runner wrote no ledger line"
    assert (rows[0]["lane"], rows[0]["provider"], rows[0]["model"],
            rows[0]["hit"], rows[0]["miss"], rows[0]["out"]) == ("reply", "openai_compat", "m", 64, 36, 5)
    assert rows[0]["usd"] is None  # "m" has no price; the counts are still kept
