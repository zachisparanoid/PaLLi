"""The reply call has a read timeout and one retry (Batch 4, task 7; spec 5.12).

"If the provider sends nothing for about 90 seconds, stop and retry once,
then tell you it failed." Before this, her reply client was a bare
``AsyncOpenAI(base_url, api_key)``: the SDK's 600-second read timeout and
its own two silent retries, under our loop's two — a stalled provider could
hold a waiting human for half an hour. Now the client reads for 90 s (300 s
on a local server, which sends nothing while it loads a model or reads a
long prompt), the SDK retries nothing, and our loop gives a stall exactly
one more try before the error reaches dispatch's outage line.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from core.agent.dispatch import _reply_client_timeout
from core.agent.llm_client import _is_retryable_llm_error


_REQ = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")


def _status_error(status: int) -> openai.APIStatusError:
    return openai.APIStatusError(
        f"status {status}", response=httpx.Response(status, request=_REQ), body=None,
    )


# ── the timeout itself ─────────────────────────────────────────────────────

def test_default_read_timeout_is_ninety_seconds(monkeypatch):
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    t = _reply_client_timeout()
    assert t.read == 90.0 and t.connect == 10.0


def test_a_cloud_provider_gets_ninety_seconds(monkeypatch):
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    for provider in ("deepseek", "openai", "openai_compat"):
        t = _reply_client_timeout(provider)
        assert (t.read, t.connect) == (90.0, 10.0), provider


def test_a_local_server_gets_five_minutes_by_default(monkeypatch):
    """A local Ollama sends no byte while it loads a model or reads a long
    prompt; a stranger's small brain on a 60-80k-token prompt can pass 90 s
    before the first token, and every such turn would end on the outage line."""
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    t = _reply_client_timeout("local_openai")
    assert (t.read, t.connect) == (300.0, 10.0)


def test_the_env_setting_wins_for_every_provider(monkeypatch):
    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", "45")
    assert _reply_client_timeout().read == 45.0
    assert _reply_client_timeout("local_openai").read == 45.0
    assert _reply_client_timeout("local_openai").connect == 10.0


@pytest.mark.parametrize("raw", ["", "  ", "0", "-5", "soon", "nan", "inf"])
def test_anything_but_a_positive_number_keeps_the_default(monkeypatch, raw):
    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raw)
    assert _reply_client_timeout().read == 90.0
    assert _reply_client_timeout("local_openai").read == 300.0


def test_the_setting_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", "30")
    assert _reply_client_timeout().read == 30.0
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS")
    assert _reply_client_timeout().read == 90.0


# ── the client dispatch builds ─────────────────────────────────────────────

def _built_client(monkeypatch, provider_name: str, api_key: str):
    import ai.llm_client as llm_client_module
    from core.agent import dispatch

    stub = SimpleNamespace(
        provider_name=provider_name, api_key=api_key,
        base_url="http://127.0.0.1:1/v1", chat_completion=None,
    )
    monkeypatch.setattr(llm_client_module.llm, "provider", stub)
    return asyncio.run(dispatch._get_openai_compat_client())


def test_the_reply_client_reads_for_ninety_seconds_and_the_sdk_retries_nothing(monkeypatch):
    """max_retries=0: the SDK's own two retries under our loop's would make
    one 90-second stall cost 3 x 3 x 90 s = 810 s before he heard anything."""
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    client = _built_client(monkeypatch, "deepseek", "sk-test")
    assert type(client).__name__ == "AsyncOpenAI"
    assert client.max_retries == 0
    assert (client.timeout.read, client.timeout.connect) == (90.0, 10.0)


def test_a_keyless_local_server_client_gets_its_own_default(monkeypatch):
    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    client = _built_client(monkeypatch, "local_openai", "")
    assert client.max_retries == 0
    assert (client.timeout.read, client.timeout.connect) == (300.0, 10.0)


# ── what counts as worth a retry ───────────────────────────────────────────

def test_a_read_timeout_is_transient():
    assert _is_retryable_llm_error(httpx.ReadTimeout("slow"))


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout(""),          # a mid-stream stall often has no message at all
    httpx.ConnectTimeout("x"),
    httpx.WriteTimeout("x"),
    httpx.PoolTimeout("x"),
    openai.APITimeoutError(request=_REQ),
])
def test_every_timeout_is_transient(exc):
    assert _is_retryable_llm_error(exc)


@pytest.mark.parametrize("status", [408, 409, 429, 500, 501, 502, 503, 504, 520, 529])
def test_what_the_sdk_used_to_retry_our_loop_now_retries(status):
    """With the SDK's retries off, the classifier must cover what the SDK
    retried itself: 408, 409, 429 and every 5xx (openai _base_client
    ``_should_retry``) — and a dropped connection."""
    assert _is_retryable_llm_error(_status_error(status))


def test_a_dropped_connection_is_still_transient():
    assert _is_retryable_llm_error(openai.APIConnectionError(request=_REQ))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_deterministic_error_is_still_never_retried(status):
    assert not _is_retryable_llm_error(_status_error(status))


# ── the loop: a stall gets one retry, then he is told ──────────────────────

def _client(outcomes: list, calls: dict):
    """A duck-typed client: each create() takes the next outcome — an
    exception raised at create time, "stall" (a stream that times out while
    it is read), or "ok"."""
    class _Stall:
        def __aiter__(self):
            async def gen():
                raise httpx.ReadTimeout("")
                yield  # pragma: no cover - makes this an async generator
            return gen()

    class _Ok:
        def __aiter__(self):
            async def gen():
                yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return gen()

    async def create(**kwargs):
        calls["n"] += 1
        outcome = outcomes[min(calls["n"], len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Stall() if outcome == "stall" else _Ok()

    class _Completions:
        pass
    _Completions.create = staticmethod(create)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _reply(monkeypatch, outcomes: list):
    import core.agent.llm_client as mod

    monkeypatch.setattr(mod, "_LLM_RETRY_BACKOFF", (0.0, 0.0))   # no real sleeping
    calls = {"n": 0}
    coro = mod.call_openai_compat(
        client=_client(outcomes, calls), model="m", system="s", messages=[], tools=[],
    )
    return calls, coro


def test_a_timeout_is_retried_exactly_once_then_raises(monkeypatch):
    calls, coro = _reply(monkeypatch, [openai.APITimeoutError(request=_REQ)])
    with pytest.raises(openai.APITimeoutError):
        asyncio.run(coro)
    assert calls["n"] == 2


def test_a_mid_stream_stall_is_retried_exactly_once_then_raises(monkeypatch):
    calls, coro = _reply(monkeypatch, ["stall"])
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(coro)
    assert calls["n"] == 2


def test_a_timeout_then_an_answer_is_an_answer(monkeypatch):
    calls, coro = _reply(monkeypatch, ["stall", "ok"])
    assert asyncio.run(coro).text_blocks == ["ok"]
    assert calls["n"] == 2


def test_a_server_error_still_gets_two_retries(monkeypatch):
    calls, coro = _reply(monkeypatch, [_status_error(503)])
    with pytest.raises(openai.APIStatusError):
        asyncio.run(coro)
    assert calls["n"] == 3


def test_a_server_error_then_a_stall_never_waits_through_a_third_timeout(monkeypatch):
    """The two budgets together: no reply call ever sits through more than
    two read timeouts, whatever came before them."""
    calls, coro = _reply(monkeypatch, [_status_error(503), "stall", "stall", "ok"])
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(coro)
    assert calls["n"] == 3


def test_a_stall_does_not_spend_a_later_server_errors_retry(monkeypatch):
    calls, coro = _reply(monkeypatch, ["stall", _status_error(503), "ok"])
    assert asyncio.run(coro).text_blocks == ["ok"]
    assert calls["n"] == 3


# ── the ninety seconds count real content, not keep-alive bytes (R11a) ─────
#
# DeepSeek sends a queued streaming request ": keep-alive" SSE comments until
# inference starts, for up to ten minutes. Every one of those bytes resets
# httpx's read timer, and the SDK's SSE decoder drops comment lines, so no
# chunk ever reaches her. The httpx read timeout alone never fires there: the
# same seconds must also bound each wait for the next real CHUNK.

class _GappedStream:
    """A stream that waits ``gap`` seconds before each chunk (a keep-alive-
    only stall looks exactly like this from above the SSE decoder), and
    records whether it was closed."""
    def __init__(self, gaps):
        self.gaps = list(gaps)
        self.closed = False

    def __aiter__(self):
        async def gen():
            for i, gap in enumerate(self.gaps):
                await asyncio.sleep(gap)
                last = i == len(self.gaps) - 1
                yield {"choices": [{"delta": {"content": "x"},
                                    "finish_reason": "stop" if last else None}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return gen()

    async def close(self):
        self.closed = True


def _real_reply_client(monkeypatch, seconds: float, streams: list, provider="deepseek"):
    """The client dispatch really builds (the env knob set tiny), with only
    its network call swapped for canned streams."""
    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", str(seconds))
    client = _built_client(monkeypatch, provider, "sk-test")
    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        return streams[min(calls["n"], len(streams)) - 1]

    client.chat.completions.create = create
    return client, calls


def _run(monkeypatch, client):
    import core.agent.llm_client as mod

    monkeypatch.setattr(mod, "_LLM_RETRY_BACKOFF", (0.0, 0.0))
    return asyncio.run(mod.call_openai_compat(
        client=client, model="m", system="s", messages=[], tools=[],
    ))


def test_a_stream_with_no_content_is_a_timeout_retried_once_then_he_is_told(monkeypatch):
    silent = [_GappedStream([30.0]), _GappedStream([30.0]), _GappedStream([30.0])]
    client, calls = _real_reply_client(monkeypatch, 0.2, silent)
    with pytest.raises(httpx.ReadTimeout, match="no content"):
        _run(monkeypatch, client)
    assert calls["n"] == 2                                   # one retry, never a third wait
    assert silent[0].closed and silent[1].closed             # each silent stream let go


def test_the_deadline_is_per_chunk_not_the_whole_reply(monkeypatch):
    steady = _GappedStream([0.1] * 8)                        # 0.8 s in all, 0.1 s apart
    client, calls = _real_reply_client(monkeypatch, 0.5, [steady])
    response = _run(monkeypatch, client)
    assert response.text_blocks == ["x" * 8]
    assert calls["n"] == 1
    assert not steady.closed


def test_a_silent_first_attempt_then_an_answer_is_an_answer(monkeypatch):
    client, calls = _real_reply_client(
        monkeypatch, 0.2, [_GappedStream([30.0]), _GappedStream([0.0])],
    )
    assert _run(monkeypatch, client).text_blocks == ["x"]
    assert calls["n"] == 2


def test_the_content_deadline_is_the_clients_own_read_seconds(monkeypatch):
    """One number: the helper computes it, dispatch puts it on the client,
    the reply call reads it back — the two waits cannot disagree."""
    from core.agent import llm_client

    monkeypatch.delenv("LLM_REPLY_READ_TIMEOUT_SECONDS", raising=False)
    for provider, key, expected in (("deepseek", "sk-test", 90.0), ("local_openai", "", 300.0)):
        client = _built_client(monkeypatch, provider, key)
        assert llm_client.reply_read_seconds(provider) == expected
        assert llm_client._content_deadline(client) == expected
        assert _reply_client_timeout(provider).read == expected
    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", "45")
    client = _built_client(monkeypatch, "deepseek", "sk-test")
    assert llm_client._content_deadline(client) == 45.0 == llm_client.reply_read_seconds("deepseek")


def test_a_cli_brain_is_never_cut_short(monkeypatch):
    """The CLI brains' adapter makes one whole CLI call and only then yields
    its chunks; its own timeout governs it. It carries no read timeout, so
    no content deadline applies, however small the knob."""
    from core.agent import llm_client

    monkeypatch.setenv("LLM_REPLY_READ_TIMEOUT_SECONDS", "0.05")
    adapter = _built_client(monkeypatch, "codex", "")
    assert type(adapter).__name__ == "PaLLiProviderSDKAdapter"
    assert llm_client._content_deadline(adapter) is None

    slow = _GappedStream([0.3])
    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        return slow

    class _Completions:
        pass
    _Completions.create = staticmethod(create)

    class _Chat:
        completions = _Completions()

    class _NoTimeoutClient:
        chat = _Chat()

    assert _run(monkeypatch, _NoTimeoutClient()).text_blocks == ["x"]
    assert calls["n"] == 1
