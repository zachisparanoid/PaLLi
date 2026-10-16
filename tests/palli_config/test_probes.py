"""probes — moved out of scripts/verify_providers.py so the brain switch and
the script share one classifier. HTTP bodies are not exercised (no socket in
the suite); the classifier and the CLI probes are."""
from __future__ import annotations

import pytest

from palli_config import probes
from palli_config.probes import ProbeResult, classify_http, probe_cli


@pytest.mark.parametrize(
    "status, body, expected_prefix",
    [
        (200, "", "WORKS"),
        (401, "", "AUTH FAILED"),
        (403, "", "AUTH FAILED"),
        (402, "", "NO CREDIT"),
        (429, '{"error": "insufficient_quota"}', "NO CREDIT"),
        (429, "slow down", "RATE LIMITED"),
        (404, "", "MODEL NOT FOUND"),
        (500, "", "ERROR (http 500)"),
    ],
)
def test_classify_http_matches_verify_providers_vocabulary(status, body, expected_prefix):
    assert classify_http(status, body).startswith(expected_prefix)


def test_probe_cli_codex_uses_the_login_status_helper(monkeypatch):
    from palli_config.providers import CodexLoginStatus
    monkeypatch.setattr(probes, "codex_login_status", lambda: CodexLoginStatus(True, "Logged in using ChatGPT"))
    result = probe_cli("codex", {})
    assert result == ProbeResult("codex", "logged in (Logged in using ChatGPT)", True)
    monkeypatch.setattr(probes, "codex_login_status", lambda: CodexLoginStatus(False, "codex CLI not found in PATH"))
    assert probe_cli("codex", {}).ok is False


def test_probe_cli_claude_looks_for_the_binary(monkeypatch):
    """Ruling 24 (final review F6): `which` proves the binary is on PATH and nothing else — the
    old text claimed a login no check had made."""
    monkeypatch.setattr(probes.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    assert probe_cli("claude", {}) == ProbeResult("claude", "installed (login not checked)", True)
    monkeypatch.setattr(probes.shutil, "which", lambda name: None)
    result = probe_cli("claude", {"CLAUDE_CLI_PATH": "/nope/claude"})
    assert result.ok is False and "not found" in result.status


def test_probe_cli_unknown_provider_is_not_configured():
    assert probe_cli("gemini", {}).status.startswith("NOT CONFIGURED")


@pytest.mark.asyncio
async def test_probe_provider_dispatches_by_provider(monkeypatch):
    calls = []

    async def fake_openai(session, **kw):
        calls.append(("openai_style", kw["label"], kw["base_url"]))
        return "WORKS"

    async def fake_anthropic(session, **kw):
        calls.append(("anthropic", kw["base_url"]))
        return "WORKS"

    async def fake_ollama(session, **kw):
        calls.append(("ollama", kw["base_url"]))
        return "WORKS (3 models present)"

    monkeypatch.setattr(probes, "probe_openai_style", fake_openai)
    monkeypatch.setattr(probes, "probe_anthropic", fake_anthropic)
    monkeypatch.setattr(probes, "probe_ollama", fake_ollama)
    env = {
        "OPENAI_API_KEY": "sk-a", "DEEPSEEK_API_KEY": "sk-b", "ANTHROPIC_API_KEY": "sk-c",
        "LOCAL_LLM_API_BASE": "http://box:11434/v1", "OLLAMA_BASE_URL": "http://box:11434",
    }
    for name in ("openai", "deepseek", "local_openai", "anthropic", "ollama"):
        result = await probes.probe_provider(name, env, "m", session=object())
        assert result.provider == name and result.ok
    assert ("openai_style", "openai", "https://api.openai.com/v1") in calls
    assert ("openai_style", "deepseek", "https://api.deepseek.com") in calls
    assert ("openai_style", "local_openai", "http://box:11434/v1") in calls
    assert ("anthropic", "https://api.anthropic.com") in calls
    assert ("ollama", "http://box:11434") in calls


def test_probe_providers_sync_wrapper_uses_injected_session(monkeypatch):
    async def fake_probe(name, env, model, *, session):
        return ProbeResult(name, "WORKS", True)

    monkeypatch.setattr(probes, "probe_provider", fake_probe)
    out = probes.probe_providers(["deepseek", "claude"], {}, {"deepseek": "x", "claude": "sonnet"})
    assert set(out) == {"deepseek", "claude"} and all(r.ok for r in out.values())


def test_verify_providers_script_imports_the_shared_probes():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "scripts" / "verify_providers.py").read_text(encoding="utf-8")
    assert "from palli_config.probes import" in text
    assert "def _classify(" not in text
