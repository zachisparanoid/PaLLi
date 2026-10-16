from __future__ import annotations

import asyncio

import pytest

from ai.llm_codex_cli import CodexCLIProvider, build_codex_cli_prompt
from ai.llm_providers import (
    OpenAIProvider,
    build_deepseek_chat_payload,
    build_openai_chat_payload,
    parse_openai_vision_response_content,
)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": dict(json or {})})
        return _FakeResponse(self._payloads.pop(0))


def test_build_openai_chat_payload_uses_gpt5_token_field():
    payload = build_openai_chat_payload(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.8,
        max_tokens=321,
        stream=False,
    )

    assert payload["model"] == "gpt-5-mini"
    assert payload["max_completion_tokens"] == 321
    assert "max_tokens" not in payload
    assert "temperature" not in payload


@pytest.mark.parametrize("model", ["o1", "o1-preview", "o3-mini", "o4-mini"])
def test_build_openai_chat_payload_omits_temperature_for_o_series(model):
    """OpenAI's o-series refuse a temperature like gpt-5 does (Batch 4 task 6,
    fix round 1): the one shared rule, model_rejects_temperature, covers both."""
    payload = build_openai_chat_payload(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.8,
        max_tokens=321,
        stream=False,
    )

    assert "temperature" not in payload


def test_build_openai_chat_payload_keeps_temperature_for_other_models():
    payload = build_openai_chat_payload(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.8,
        max_tokens=321,
        stream=False,
    )

    assert payload["temperature"] == 0.8


def test_build_deepseek_chat_payload_keeps_temperature_and_max_tokens():
    payload = build_deepseek_chat_payload(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.25,
        max_tokens=222,
        stream=True,
    )

    assert payload == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.25,
        "max_tokens": 222,
        "stream": True,
    }


def test_parse_openai_vision_response_content_falls_back_to_description():
    parsed = parse_openai_vision_response_content("not-json text", log_label="OpenAI")

    assert parsed["description"] == "not-json text"
    assert parsed["contextual_understanding"] == "not-json text"
    assert parsed["suggested_response"] == "I see your image, thanks for sharing!"


@pytest.mark.asyncio
async def test_openai_provider_retries_gpt5_length_failure_with_max_completion_tokens(monkeypatch):
    provider = OpenAIProvider(api_key="sk-test")
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
                        "message": {"content": "Finished response", "reasoning": "done"},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_openai_compatible.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Say hi"}],
        model="gpt-5-mini",
        max_tokens=120,
        log_context="response_generation_42",
    )

    assert result == "Finished response"
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["max_completion_tokens"] == 120
    assert "max_tokens" not in session.calls[0]["json"]
    assert "temperature" not in session.calls[0]["json"]
    assert session.calls[1]["json"]["max_completion_tokens"] > session.calls[0]["json"]["max_completion_tokens"]


def test_build_codex_cli_prompt_requests_json_only_when_needed():
    prompt = build_codex_cli_prompt(
        messages=[{"role": "system", "content": "Return strict JSON only."}],
        max_tokens=400,
    )

    assert "Return strict JSON only" in prompt
    assert "Return strict JSON only with no preface." in prompt
    assert "approximately 400 output tokens" in prompt


def test_codex_cli_provider_parse_login_status_sanitizes_api_key_auth():
    details = CodexCLIProvider._parse_login_status(
        returncode=0,
        stdout_text="Logged in using an API key - sk-proj-***abcd",
        stderr_text="",
        executable_path="C:\\Users\\Sam\\AppData\\Roaming\\npm\\codex.cmd",
    )

    assert details["configured"] is True
    assert details["auth_mode"] == "apikey"
    assert details["login_status"] == "authenticated"
    assert details["login_status_text"] == "Logged in using API key"
    assert details["codex_executable"].endswith("codex.cmd")


@pytest.mark.asyncio
async def test_codex_cli_provider_uses_output_file_content_and_reports_usage(monkeypatch, tmp_path):
    provider = CodexCLIProvider(
        codex_path="codex",
        default_model="gpt-5.1-codex-max",
        # tmp_path, not a literal Windows path: the provider makedirs() its
        # workspace, so a hardcoded "C:\temp\..." resurrects a junk dir at the
        # repo root on every Linux test run.
        workspace_dir=str(tmp_path / "codex-ws"),
        timeout_seconds=30,
    )
    seen_telemetry = []

    class _FakeProcess:
        returncode = 0

        async def communicate(self, payload: bytes):
            assert b"Hello from user" in payload
            stdout = (
                '{"type":"thread.started","thread_id":"thread-1"}\n'
                '{"type":"turn.started"}\n'
                '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"fallback text"}}\n'
                '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":4}}\n'
            )
            return stdout.encode("utf-8"), b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        output_index = args.index("--output-last-message") + 1
        output_path = args[output_index]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("final codex reply\n")
        assert "--sandbox" in args
        assert "read-only" in args
        assert "-C" in args
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        return _FakeProcess()

    async def record_telemetry(payload):
        seen_telemetry.append(dict(payload))

    monkeypatch.setattr("ai.llm_codex_cli.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    result = await provider.chat_completion(
        [{"role": "user", "content": "Hello from user"}],
        log_context="codex_provider_test",
        telemetry_callback=record_telemetry,
    )

    assert result == "final codex reply"
    assert seen_telemetry[0]["provider"] == "codex"
    assert seen_telemetry[0]["success"] is True
    assert seen_telemetry[0]["usage"] == {"input_tokens": 12, "output_tokens": 4}
