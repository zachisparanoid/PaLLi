"""Is this provider usable right now? One classifier for the brain switch and
for scripts/verify_providers.py (which used to own these four functions).

Statuses keep the script's vocabulary: WORKS / NOT CONFIGURED / AUTH FAILED /
NO CREDIT / RATE LIMITED / MODEL NOT FOUND / UNREACHABLE, plus `logged in` for
codex (which is asked) and `installed (login not checked)` for the claude CLI
(which is only looked for on PATH). A non-WORKS result is a warning to the
brain switch, never a refusal — a cold desktop must not block a move away
from it (spec §3.2 step 2).
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Iterable, Mapping

import aiohttp

from palli_config.providers import codex_login_status


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    status: str
    ok: bool


def classify_http(status: int, body: str) -> str:
    if status == 200:
        return "WORKS"
    if status in (401, 403):
        return "AUTH FAILED (bad/expired key)"
    if status == 402:
        return "NO CREDIT (payment required)"
    if status == 429:
        low = (body or "").lower()
        if "insufficient_quota" in low or "credit" in low or "billing" in low:
            return "NO CREDIT (quota exhausted)"
        return "RATE LIMITED (try again)"
    if status == 404:
        return "MODEL NOT FOUND (http 404)"
    return f"ERROR (http {status})"


async def probe_openai_style(
    session: aiohttp.ClientSession, *, label: str, base_url: str, api_key: str, model: str,
) -> str:
    if not api_key:
        return "NOT CONFIGURED (no API key)"
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.text()
            return classify_http(resp.status, body)
    except Exception as exc:
        return f"UNREACHABLE ({type(exc).__name__})"


async def probe_anthropic(
    session: aiohttp.ClientSession, *, api_key: str, base_url: str, model: str,
) -> str:
    if not api_key:
        return "NOT CONFIGURED (no API key)"
    url = f"{base_url.rstrip('/')}/v1/messages"
    payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.text()
            return classify_http(resp.status, body)
    except Exception as exc:
        return f"UNREACHABLE ({type(exc).__name__})"


async def probe_ollama(session: aiohttp.ClientSession, *, base_url: str, model: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        async with session.get(f"{base}/api/tags",
                               timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                return f"UNREACHABLE (tags http {resp.status})"
            data = await resp.json()
            models = [m.get("name") for m in (data.get("models") or [])]
        if model and model not in models:
            return f"REACHABLE but model '{model}' not pulled ({len(models)} present)"
        return f"WORKS ({len(models)} models present)"
    except Exception as exc:
        return f"UNREACHABLE ({type(exc).__name__})"


def probe_cli(provider: str, env: Mapping[str, str]) -> ProbeResult:
    """The two subscription CLIs authenticate by login, not by key."""
    if provider == "codex":
        status = codex_login_status()
        if status.logged_in:
            return ProbeResult("codex", f"logged in ({status.message or 'codex'})", True)
        return ProbeResult("codex", f"not logged in ({status.message or 'codex login status failed'})", False)
    if provider == "claude":
        # Ruling 24 (final review F6): `which` proves the binary is on PATH and nothing more.
        # "logged in" answered a question no check had asked — and the path it printed was
        # a filesystem detail nobody reading this line needs.
        wanted = str(env.get("CLAUDE_CLI_PATH") or "claude")
        if shutil.which(wanted):
            return ProbeResult("claude", "installed (login not checked)", True)
        return ProbeResult("claude", f"not installed ({wanted} not found in PATH)", False)
    return ProbeResult(provider, "NOT CONFIGURED (no probe for this provider)", False)


_OPENAI_STYLE = {
    # provider -> (api key env, base url env, base url default)
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "local_openai": ("LOCAL_LLM_API_KEY", "LOCAL_LLM_API_BASE", "http://127.0.0.1:11434/v1"),
}


async def probe_provider(
    provider: str, env: Mapping[str, str], model: str, *, session: aiohttp.ClientSession,
) -> ProbeResult:
    if provider in ("codex", "claude"):
        return probe_cli(provider, env)
    if provider in _OPENAI_STYLE:
        key_env, base_env, base_default = _OPENAI_STYLE[provider]
        api_key = str(env.get(key_env) or "")
        if provider == "local_openai" and not api_key:
            api_key = "x"  # local endpoints take no key; the SDK wants a non-empty one
        status = await probe_openai_style(
            session, label=provider, base_url=str(env.get(base_env) or base_default),
            api_key=api_key, model=model,
        )
        return ProbeResult(provider, status, status.startswith("WORKS"))
    if provider == "anthropic":
        status = await probe_anthropic(
            session, api_key=str(env.get("ANTHROPIC_API_KEY") or ""),
            base_url=str(env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"), model=model,
        )
        return ProbeResult(provider, status, status.startswith("WORKS"))
    if provider == "ollama":
        status = await probe_ollama(
            session, base_url=str(env.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"), model=model,
        )
        return ProbeResult(provider, status, status.startswith("WORKS"))
    return ProbeResult(provider, "NOT CONFIGURED (no probe for this provider)", False)


def probe_providers(
    providers: Iterable[str], env: Mapping[str, str], models: Mapping[str, str],
) -> dict[str, ProbeResult]:
    """Sync entry for the CLI: one session, every provider, in order."""
    names = list(dict.fromkeys(providers))

    async def _run() -> dict[str, ProbeResult]:
        out: dict[str, ProbeResult] = {}
        async with aiohttp.ClientSession() as session:
            for name in names:
                out[name] = await probe_provider(name, env, str(models.get(name) or ""), session=session)
        return out

    return asyncio.run(_run())
