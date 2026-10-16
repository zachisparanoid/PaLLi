from __future__ import annotations

import json as _json

import pytest

from ai.llm_client import DeepSeekProvider, OpenAIProvider, is_reasoning_prone_model
from ai.llm_openai_compatible import is_max_tokens_400_error


class _FakeResponse:
    def __init__(self, payload: dict | str, status: int = 200):
        self._payload = payload
        self.status = status
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if isinstance(self._payload, str):
            return _json.loads(self._payload)
        return self._payload

    async def text(self):
        if isinstance(self._payload, str):
            return self._payload
        return _json.dumps(self._payload)


class _FakeSession:
    def __init__(self, payloads):
        # Accept either a list of dicts (legacy: all 200s) or a list of
        # (payload, status) tuples for tests that need to mix status codes.
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": dict(json or {})})
        item = self._payloads.pop(0)
        if isinstance(item, tuple):
            payload, status = item
            return _FakeResponse(payload, status=status)
        return _FakeResponse(item)


def test_is_reasoning_prone_model_detects_local_reasoning_models():
    assert is_reasoning_prone_model("local_openai::qwen3:14b") is True
    assert is_reasoning_prone_model("qwen3:14b") is True
    assert is_reasoning_prone_model("gpt-4o") is False


@pytest.mark.asyncio
async def test_chat_completion_retries_empty_reasoning_response(monkeypatch):
    provider = OpenAIProvider(
        provider_name="local_openai",
        log_label="Local LLM",
        base_url="http://127.0.0.1:11434/v1",
        default_model="qwen3:14b",
        supports_vision=False,
    )
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {"content": "", "reasoning": "long internal reasoning"},
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {"content": "Hey there.", "reasoning": "done"},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Say hi"}],
        model="qwen3:14b",
        max_tokens=85,
        log_context="response_generation_123",
    )

    assert result == "Hey there."
    assert len(session.calls) == 2
    assert session.calls[1]["json"]["max_tokens"] > session.calls[0]["json"]["max_tokens"]


@pytest.mark.asyncio
async def test_chat_completion_retries_truncated_json_response(monkeypatch):
    provider = OpenAIProvider(
        provider_name="local_openai",
        log_label="Local LLM",
        base_url="http://127.0.0.1:11434/v1",
        default_model="qwen3:14b",
        supports_vision=False,
    )
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"should_respond": true, "confidence": 0.95',
                            "reasoning": "used the whole budget before closing the object",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"should_respond": true, "confidence": 0.95}',
                            "reasoning": "done",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)

    result = await provider.chat_completion(
        [
            {"role": "system", "content": "Respond with strict JSON only."},
            {"role": "user", "content": "Should I respond?"},
        ],
        model="qwen3:14b",
        max_tokens=300,
        log_context="combined_decision",
    )

    assert result == '{"should_respond": true, "confidence": 0.95}'
    assert len(session.calls) == 2
    assert session.calls[1]["json"]["max_tokens"] > session.calls[0]["json"]["max_tokens"]


# --- Part A tests: 400-error retry on reasoning-model max_tokens too low -----

def test_is_max_tokens_400_error_matches_canonical_message():
    body = (
        '{"error": {"message": "Could not finish the message because '
        'max_tokens or model output limit was reached. Please try again with '
        'higher max_tokens.", "type": "invalid_request_error"}}'
    )
    assert is_max_tokens_400_error(body) is True


def test_is_max_tokens_400_error_matches_paraphrase():
    body = '{"error": {"message": "model output limit was reached"}}'
    assert is_max_tokens_400_error(body) is True


def test_is_max_tokens_400_error_rejects_unrelated_400():
    body = '{"error": {"message": "Invalid request: bad parameter"}}'
    assert is_max_tokens_400_error(body) is False


def test_is_max_tokens_400_error_handles_empty_string():
    assert is_max_tokens_400_error("") is False
    assert is_max_tokens_400_error(None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_chat_completion_retries_on_max_tokens_400_for_reasoning_model(monkeypatch):
    """When OpenAI rejects with a 400 saying max_tokens is too low for a
    reasoning model to fit visible output, the provider bumps max_tokens
    once and retries — same shape as the finish_reason=length retry path.
    """
    provider = OpenAIProvider(
        provider_name="openai",
        log_label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5-mini",
        supports_vision=False,
    )
    body_400 = _json.dumps({
        "error": {
            "message": (
                "Could not finish the message because max_tokens or model "
                "output limit was reached. Please try again with higher "
                "max_tokens."
            ),
            "type": "invalid_request_error",
        },
    })
    session = _FakeSession([
        (body_400, 400),
        ({
            "choices": [{
                "message": {"content": "Quick thought after retry.", "reasoning": "done"},
                "finish_reason": "stop",
            }],
        }, 200),
    ])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Tick"}],
        model="gpt-5-mini",
        max_tokens=300,
        log_context="inner_monologue_tick",
    )

    assert result == "Quick thought after retry."
    assert len(session.calls) == 2
    # gpt-5 family models use `max_completion_tokens` in the wire payload;
    # legacy models use `max_tokens`. Read whichever is set on each call.
    def _budget(call: dict) -> int:
        body = call["json"]
        return int(body.get("max_completion_tokens") or body.get("max_tokens") or 0)
    initial = _budget(session.calls[0])
    bumped = _budget(session.calls[1])
    assert initial == 300
    assert bumped > initial
    assert bumped >= 1000


@pytest.mark.asyncio
async def test_chat_completion_does_not_retry_400_for_non_reasoning_model(monkeypatch):
    """A 400 that matches the max_tokens signature but the model is NOT a
    reasoning model — don't retry, because non-reasoning models don't burn
    output budget on chain-of-thought, so the cause is something else (e.g.
    a misconfigured tiny budget). Surface the failure cleanly.
    """
    provider = OpenAIProvider(
        provider_name="openai",
        log_label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        supports_vision=False,
    )
    body_400 = _json.dumps({
        "error": {
            "message": "max_tokens or model output limit was reached",
            "type": "invalid_request_error",
        },
    })
    session = _FakeSession([(body_400, 400)])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Hi"}],
        model="gpt-4o",  # NOT reasoning-prone
        max_tokens=50,
        log_context="general",
    )

    assert result is None
    assert len(session.calls) == 1  # no retry fired


@pytest.mark.asyncio
async def test_chat_completion_does_not_retry_unrelated_400(monkeypatch):
    """A genuine 400 (not a max_tokens budget issue) on a reasoning model —
    don't retry, just surface the failure. Bumping the budget would not
    help and could mask a real configuration problem.
    """
    provider = OpenAIProvider(
        provider_name="openai",
        log_label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5-mini",
        supports_vision=False,
    )
    body_400 = _json.dumps({
        "error": {
            "message": "Invalid request: 'temperature' must be a number",
            "type": "invalid_request_error",
        },
    })
    session = _FakeSession([(body_400, 400)])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Hi"}],
        model="gpt-5-mini",
        max_tokens=300,
        log_context="inner_monologue_tick",
    )

    assert result is None
    assert len(session.calls) == 1  # no retry fired


@pytest.mark.asyncio
async def test_chat_completion_400_retry_only_fires_once(monkeypatch):
    """If the retry ALSO fails with a 400 (the bumped budget still wasn't
    enough, or some other reason), don't loop — surface failure cleanly.
    The state["budget_retry_used"] flag enforces at-most-one retry.
    """
    provider = OpenAIProvider(
        provider_name="openai",
        log_label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5-mini",
        supports_vision=False,
    )
    body_400 = _json.dumps({
        "error": {
            "message": "max_tokens or model output limit was reached",
            "type": "invalid_request_error",
        },
    })
    session = _FakeSession([(body_400, 400), (body_400, 400)])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Tick"}],
        model="gpt-5-mini",
        max_tokens=200,
        log_context="inner_monologue_tick",
    )

    assert result is None
    # First call (initial) + second call (retry). Third call must NOT fire.
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_retries_when_reasoning_arrives_as_reasoning_content(monkeypatch):
    """DeepSeek reports its chain-of-thought as ``reasoning_content``, not
    ``reasoning`` — and is not in the is_reasoning_prone_model prefix list.

    The empty+length retry is gated on
    ``is_reasoning_prone_model(model) or reasoning_chars > 0``. That second
    clause is the RUNTIME fallback that catches models the prefix list misses,
    but it read only ``message["reasoning"]``, so for the model on the live
    proactive/reflection/memory lanes it was permanently 0 and the retry never
    fired. Cost: the same bug hand-fixed three times (87cc2d6 authorship
    1200->4000, 6146914 day-gist 400->1500, 17ea054 surface-decision 512->1500).

    Live-log evidence for the field name: 1,110 occurrences of
    "reasoning_content" vs 2 of "reasoning".
    """
    provider = OpenAIProvider(
        provider_name="deepseek",
        log_label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="experimental-flash-x",
        supports_vision=False,
    )
    # Precondition: the prefix list does NOT cover this model, so the retry can
    # only come from the runtime reasoning signal. (deepseek-v4 itself joined
    # the prefix list 2026-07-24, so this test now uses a deliberately
    # unlisted id to keep exercising the runtime-signal path on its own.)
    assert is_reasoning_prone_model("deepseek::experimental-flash-x") is False

    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "spent the whole ceiling thinking",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"skip": true, "reason": "nothing to raise"}',
                            "reasoning_content": "done",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "should she raise a thread?"}],
        model="experimental-flash-x",
        max_tokens=512,
        log_context="open_threads_surface_decision",
    )

    assert result == '{"skip": true, "reason": "nothing to raise"}'
    assert len(session.calls) == 2, "empty+length should have triggered one retry"
    assert session.calls[1]["json"]["max_tokens"] > session.calls[0]["json"]["max_tokens"]


# --- R1-durable (2026-08-04): the retry must live on the BASE class ----------
#
# The 4 AM organs (night reflection, day-gist, monologue, stake appraisal,
# surface decisions) run on the real DeepSeekProvider, which extended
# OpenAICompatibleProvider and inherited the NAIVE parse — warn-and-return-
# empty, no retry. Every test above simulated "DeepSeek" on OpenAIProvider,
# which is exactly how the gap survived: the class that needed the fallback
# most never had it, and no test pinned it. Measured cost (Aug 2-4 logs):
# inner_monologue_tick lost 64% of calls, agent_cadence_shape 50%,
# stake_appraisal 47%, open_threads_surface_decision 25%; nights 07-30/07-31
# lost outright.


@pytest.mark.asyncio
async def test_real_deepseek_provider_retries_empty_reasoning_response(monkeypatch):
    """The REAL DeepSeekProvider class — not an OpenAIProvider dressed as one —
    must retry when reasoning burns the whole budget (empty + length)."""
    provider = DeepSeekProvider()
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "spent the whole ceiling thinking",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "Tonight held three threads, and they wove together.",
                            "reasoning_content": "done",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Reflect on the day."}],
        model="deepseek-v4-flash",
        max_tokens=400,
        log_context="night_cycle_daily_meaning",
    )

    assert result == "Tonight held three threads, and they wove together."
    assert len(session.calls) == 2, "empty+length on the real DeepSeek class must retry"
    assert session.calls[1]["json"]["max_tokens"] > session.calls[0]["json"]["max_tokens"]


@pytest.mark.asyncio
async def test_retries_truncated_prose_under_length(monkeypatch):
    """The 08-02 casualty shape: a reasoning model hit the budget mid-clause and
    the 54-char fragment was stored as a success. finish_reason=length with
    NONEMPTY prose from a reasoning model is a truncation, not a success —
    one retry at a raised budget."""
    provider = DeepSeekProvider()
    fragment = "The day carried three distinct threads that wove toget"
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": fragment,
                            "reasoning_content": "spent 387 of 400 thinking",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "The day carried three distinct threads that wove together into something quieter.",
                            "reasoning_content": "done",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Reflect on the day in one short paragraph."}],
        model="deepseek-v4-flash",
        max_tokens=400,
        log_context="night_cycle_daily_meaning",
    )

    assert result == "The day carried three distinct threads that wove together into something quieter."
    assert len(session.calls) == 2, "nonempty-but-truncated prose must retry once"


@pytest.mark.asyncio
async def test_short_prose_on_stop_never_retries(monkeypatch):
    """Pin the boundary: a SHORT completion that finished on `stop` is a real
    answer, not a truncation — no retry, no second call."""
    provider = DeepSeekProvider()
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {"content": "Quiet day.", "reasoning_content": "brief"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Reflect."}],
        model="deepseek-v4-flash",
        max_tokens=400,
        log_context="night_cycle_daily_meaning",
    )

    assert result == "Quiet day."
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_truncated_prose_on_non_reasoning_model_never_retries(monkeypatch):
    """Pin the other boundary: a non-reasoning model clipped at length is just a
    long answer hitting its cap — the historical behavior stands (no retry),
    so reply lanes on plain models don't grow surprise latency."""
    provider = OpenAIProvider(
        provider_name="openai",
        log_label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        supports_vision=False,
    )
    session = _FakeSession(
        [
            {
                "choices": [
                    {
                        "message": {"content": "A long answer that got clipped mid"},
                        "finish_reason": "length",
                    }
                ]
            }
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Say something long"}],
        model="gpt-4o",
        max_tokens=40,
        log_context="general",
    )

    assert result == "A long answer that got clipped mid"
    assert len(session.calls) == 1


# --- Caller timeouts must fit the retry envelope (2026-08-04 22:07 live) -----
#
# The retry above costs a SECOND provider request. A caller whose own
# asyncio.wait_for deadline is shorter than two provider requests can kill
# that retry mid-flight — converting a recoverable slow call into a permanent
# skip, silently. Observed live: the stake appraisal of the owner's flag-trap
# correction burned 700/700 on reasoning, the new retry fired correctly at a
# raised budget, and the appraiser's own 25s deadline cut it off 18s later
# ("stake_appraisal: timed out after 25s; skipping"). The exchange that
# mattered most that day moved nothing in her ledger.


def test_retry_capable_callers_allow_two_provider_requests():
    """Any lane that can trigger the budget-retry must allow at least two
    provider requests' worth of wall clock, or its retry is structurally
    unable to finish. Since the 2026-08-09 patient/snappy split there are two
    floors: patient lanes budget against the 60s ceiling, snappy against 30s."""
    from ai import arbiter, daydream, stake_appraiser
    from ai.llm_openai_compatible import OpenAICompatibleProvider
    from core.agent import steering_reply

    snappy_floor = OpenAICompatibleProvider.REQUEST_TIMEOUT_SECONDS * 2
    patient_floor = OpenAICompatibleProvider.PATIENT_REQUEST_TIMEOUT_SECONDS * 2

    # patient lanes (stake_appraisal / arbiter_freshness / daydream_tick)
    assert stake_appraiser.APPRAISAL_TIMEOUT_SECONDS >= patient_floor, (
        "the appraiser can trigger a budget-retry; its deadline must fit one"
    )
    assert arbiter._FRESHNESS_TIMEOUT_S >= patient_floor, (
        "the freshness judge fail-closes on timeout; its deadline must fit a retry"
    )
    assert daydream._TICK_TIMEOUT_S >= patient_floor, (
        "the daydream tick can trigger a budget-retry; its deadline must fit one"
    )
    # snappy lane (steering_judgment)
    assert steering_reply._TIMEOUT_SECONDS >= snappy_floor, (
        "the steer judge can trigger a budget-retry; its deadline must fit one"
    )


def test_narrative_author_fits_the_claude_cli_envelope():
    """The author lane rides claude::haiku (2026-08-11 routing) and its real
    prompt measures ~54s through the CLI — the old 60s cap timed out W33's
    third attempt at 04:00, twice per doc. The cap must clear the measured
    call with cold-start headroom, and the author's caller must fit its
    in-function retry (two attempts) under the envelope law."""
    import inspect

    from ai import narrative_self
    from ai.llm_claude_cli import ClaudeCLIProvider

    cap = ClaudeCLIProvider().timeout_seconds
    assert cap >= 150.0, "the CLI cap must clear the measured 53.7s call with headroom"
    src = inspect.getsource(narrative_self._make_llm_call)
    assert "timeout=320" in src, (
        "the narrative caller must fit two CLI attempts (2 x cap + headroom)"
    )
    assert 320 >= 2 * cap


def test_supersede_deadline_is_deliberately_short():
    """The supersede judge is the exception and must STAY the exception: it
    races a reply that is already streaming, so a verdict arriving after the
    reply lands is worthless. Its fail-open (finish + follow-up turn) is the
    correct default, so it deliberately does not budget for a retry. Pinned so
    nobody 'fixes' it into a slow lane."""
    from ai.llm_openai_compatible import OpenAICompatibleProvider
    from core.agent import steering_reply

    assert steering_reply._SUPERSEDE_TIMEOUT_SECONDS < (
        OpenAICompatibleProvider.REQUEST_TIMEOUT_SECONDS
    )


def _empty_length(reason="long internal reasoning"):
    return {"choices": [{"message": {"content": "", "reasoning_content": reason}, "finish_reason": "length"}]}


@pytest.mark.asyncio
async def test_deep_lane_doubles_twice_then_lands(monkeypatch):
    """04:00 family: empty+length → double → empty+length again → double again
    (to the deep cap) → content. Three calls, budgets 4000 → 8000 → 16000."""
    provider = DeepSeekProvider()
    session = _FakeSession([
        _empty_length(), _empty_length(),
        {"choices": [{"message": {"content": "[]", "reasoning_content": "done"}, "finish_reason": "stop"}]},
    ])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    result = await provider.chat_completion([{"role": "user", "content": "review"}], model="deepseek-v4-flash",
                                            max_tokens=4000, log_context="stake_review")
    assert result == "[]"
    assert [c["json"]["max_tokens"] for c in session.calls] == [4000, 8000, 16000]


@pytest.mark.asyncio
async def test_deep_lane_stops_at_the_cap(monkeypatch):
    """At the cap there is no further rung: a third empty+length is returned
    as the (empty) answer, not retried forever."""
    provider = DeepSeekProvider()
    session = _FakeSession([_empty_length(), _empty_length(), _empty_length(), _empty_length()])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    result = await provider.chat_completion([{"role": "user", "content": "review"}], model="deepseek-v4-flash",
                                            max_tokens=4000, log_context="stake_review")
    assert not (result or "").strip()
    assert len(session.calls) == 3  # 4000, 8000, 16000 — and no fourth


@pytest.mark.asyncio
async def test_daytime_lane_still_bumps_exactly_once(monkeypatch):
    provider = DeepSeekProvider()
    session = _FakeSession([_empty_length(), _empty_length(), _empty_length()])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    await provider.chat_completion([{"role": "user", "content": "tick"}], model="deepseek-v4-flash",
                                   max_tokens=2500, log_context="inner_monologue_tick")
    assert len(session.calls) == 2
