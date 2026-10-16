from __future__ import annotations

import pytest

from ai.llm_client import DeepSeekProvider


class _FakeStreamContent:
    def __init__(self, lines: list[str] | None = None):
        self._lines = [line.encode("utf-8") for line in (lines or [])]

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeResponse:
    def __init__(
        self,
        payload: dict | str | None = None,
        *,
        status: int = 200,
        headers: dict | None = None,
        stream_lines: list[str] | None = None,
    ):
        self._payload = payload if payload is not None else {}
        self.status = status
        self.headers = headers or {}
        self.content = _FakeStreamContent(stream_lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._payload if isinstance(self._payload, str) else str(self._payload)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers or {}),
                "json": dict(json or {}),
                "timeout": timeout,
            }
        )
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_deepseek_chat_completion_retries_retryable_http_error(monkeypatch):
    provider = DeepSeekProvider()
    session = _FakeSession(
        [
            _FakeResponse({"error": "busy"}, status=429, headers={"Retry-After": "0"}),
            _FakeResponse(
                {
                    "choices": [{"message": {"content": "Recovered output"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
                }
            ),
        ]
    )
    seen_telemetry: list[dict] = []

    async def fake_get_session():
        return session

    async def fake_sleep(_delay: float):
        return None

    async def record_telemetry(payload):
        seen_telemetry.append(dict(payload))

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_openai_compatible.asyncio.sleep", fake_sleep)

    result = await provider.chat_completion(
        [{"role": "user", "content": "hello"}],
        model="deepseek-chat",
        log_context="analysis_retry",
        telemetry_callback=record_telemetry,
    )

    assert result == "Recovered output"
    assert len(session.calls) == 2
    assert seen_telemetry == [
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "streamed": False,
            "log_context": "analysis_retry",
            "latency_seconds": pytest.approx(seen_telemetry[0]["latency_seconds"]),
            "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            "provider_retry_count": 1,
            "success": True,
            "finish_reason": "stop",
        }
    ]


@pytest.mark.asyncio
async def test_deepseek_chat_completion_stream_retries_and_aggregates_deltas(monkeypatch):
    provider = DeepSeekProvider()
    session = _FakeSession(
        [
            _FakeResponse({"error": "temporarily unavailable"}, status=503, headers={"Retry-After": "0"}),
            _FakeResponse(
                stream_lines=[
                    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
                    "\n",
                    'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
                    "\n",
                    "data: [DONE]\n",
                    "\n",
                ]
            ),
        ]
    )
    seen_deltas: list[str] = []
    seen_telemetry: list[dict] = []

    async def fake_get_session():
        return session

    async def fake_sleep(_delay: float):
        return None

    async def on_delta(text: str):
        seen_deltas.append(text)

    async def record_telemetry(payload):
        seen_telemetry.append(dict(payload))

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_openai_compatible.asyncio.sleep", fake_sleep)

    result = await provider.chat_completion_stream(
        [{"role": "user", "content": "hello"}],
        model="deepseek-chat",
        log_context="stream_retry",
        on_delta=on_delta,
        telemetry_callback=record_telemetry,
    )

    assert result == "Hello"
    assert seen_deltas == ["Hel", "lo"]
    assert len(session.calls) == 2
    assert seen_telemetry == [
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "streamed": True,
            "log_context": "stream_retry",
            "latency_seconds": pytest.approx(seen_telemetry[0]["latency_seconds"]),
            "usage": {},
            "provider_retry_count": 1,
            "success": True,
        }
    ]
