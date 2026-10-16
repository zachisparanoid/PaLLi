"""Verify which LLM provider 'brains' are actually usable right now.

Companion-restoration Phase 1. Answers the practical question: of the
providers wired into PaLLi, which can I actually route a lane to today?
Distinguishes WORKS / NO CREDIT / AUTH FAILED / UNREACHABLE / NOT
CONFIGURED so you can pick brains per-lane without guessing.

It probes each cloud provider with a direct minimal API call (max_tokens
small) so HTTP status codes surface cleanly, instead of relying on the
in-app providers which swallow errors and return None. CLI providers
(codex, claude) are reported as needing a separate `palli_ctl auth`
check since they authenticate via subprocess, not an API key.

Run: venv/bin/python scripts/verify_providers.py
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local", override=True)

import aiohttp  # noqa: E402

from core.config import config  # noqa: E402
from palli_config.probes import (  # noqa: E402
    probe_anthropic as _probe_anthropic,
    probe_ollama as _probe_ollama,
    probe_openai_style as _probe_openai_style,
)


async def main() -> int:
    rows: list[tuple[str, str, str]] = []
    async with aiohttp.ClientSession() as session:
        # OpenAI
        rows.append((
            "openai", getattr(config, "OPENAI_MODEL", "gpt-4o"),
            await _probe_openai_style(
                session, label="openai",
                base_url=getattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1",
                api_key=getattr(config, "OPENAI_API_KEY", "") or "",
                model=getattr(config, "OPENAI_MODEL", "gpt-4o") or "gpt-4o",
            ),
        ))
        # Anthropic
        rows.append((
            "anthropic", getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            await _probe_anthropic(
                session,
                api_key=getattr(config, "ANTHROPIC_API_KEY", "") or "",
                base_url=getattr(config, "ANTHROPIC_BASE_URL", "https://api.anthropic.com") or "https://api.anthropic.com",
                model=getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6") or "claude-sonnet-4-6",
            ),
        ))
        # DeepSeek (OpenAI-compatible)
        rows.append((
            "deepseek", getattr(config, "DEEPSEEK_MODEL", "deepseek-chat"),
            await _probe_openai_style(
                session, label="deepseek",
                base_url="https://api.deepseek.com",
                api_key=getattr(config, "DEEPSEEK_API_KEY", "") or "",
                model=getattr(config, "DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat",
            ),
        ))
        # Ollama (native)
        rows.append((
            "ollama", getattr(config, "OLLAMA_MODEL", "qwen3:14b"),
            await _probe_ollama(
                session,
                base_url=getattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434") or "http://127.0.0.1:11434",
                model=getattr(config, "OLLAMA_MODEL", "qwen3:14b") or "qwen3:14b",
            ),
        ))
        # local_openai (OpenAI-compat local endpoint)
        rows.append((
            "local_openai", getattr(config, "LOCAL_LLM_MODEL", ""),
            await _probe_openai_style(
                session, label="local_openai",
                base_url=getattr(config, "LOCAL_LLM_API_BASE", "http://127.0.0.1:11434/v1") or "http://127.0.0.1:11434/v1",
                api_key=getattr(config, "LOCAL_LLM_API_KEY", "") or "x",  # local often needs no key
                model=getattr(config, "LOCAL_LLM_MODEL", "qwen3:14b") or "qwen3:14b",
            ),
        ))

    print(f"\n{'PROVIDER':<14} {'MODEL':<28} STATUS")
    print("-" * 78)
    for name, model, status in rows:
        print(f"{name:<14} {str(model):<28} {status}")
    print("-" * 78)
    print("CLI providers (codex, claude): auth via subprocess — check with")
    print("  venv/bin/python scripts/palli_ctl.py auth codex   /   ...auth claude")
    print()

    usable = [r[0] for r in rows if r[2].startswith("WORKS")]
    print(f"Usable right now: {', '.join(usable) if usable else '(none of the API providers)'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
