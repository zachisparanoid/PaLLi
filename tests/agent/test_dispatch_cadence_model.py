"""D.4 step 2: CADENCE_MODEL env knob lets the cadence-shaping pass
target a cheaper/faster model than the main runner.

Pre-D.4 the cadence pass at ``core.agent.dispatch._build_cadence_llm_callable``
called ``provider.chat_completion(...)`` with no ``model=`` kwarg, so
the cadence pass always used the active provider's default model
(typically the same one as the main runner). The cadence pass is a
small text-shaping step where a cheaper model usually suffices.

These tests pin three cases:

1. ``CADENCE_MODEL`` set → cadence call receives ``model=<that value>``.
2. ``PALLI_CADENCE_MODEL`` set (alternate name read by config) → same.
3. Neither set → no ``model`` kwarg passed (provider falls back to its
   own default, preserving pre-D.4 behavior).
"""
from __future__ import annotations

import pytest


def _load_dispatch():
    """Import core.agent.dispatch fresh. The cadence model is read at the
    time ``_build_cadence_llm_callable`` is called (call time, not module
    load), so a re-import isn't strictly required — but doing it via the
    same load_module helper as the rest of the dispatch tests keeps the
    isolation consistent."""
    from tests.agent.conftest import load_module
    return load_module("core/agent/dispatch.py", "agent_dispatch_cadence_model_under_test")


class _FakeSingleton:
    """Stand-in for ai.llm_client.llm that captures the kwargs passed to
    chat_completion. Post-Bug-3 (2026-07-04) the cadence pass routes via the
    routing-aware ``llm.chat_completion`` (not ``llm.provider.chat_completion``)
    so a ``provider::model`` reference can cross providers (e.g. Anthropic
    Haiku). These tests pin that the CADENCE_MODEL knob still forwards the
    model reference into that call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "cadence ok"


@pytest.mark.asyncio
async def test_cadence_llm_passes_model_when_env_set(monkeypatch):
    """When CADENCE_MODEL=test-cadence-model is set, the cadence pass
    must forward model="test-cadence-model" into llm.chat_completion."""
    fake = _FakeSingleton()

    # Order note: setattr first, then setenv. Same dotenv-clobber trick
    # as test_dispatch_is_owner — core.config does load_dotenv(override=True)
    # at import time and would clobber PALLI_CADENCE_MODEL otherwise.
    monkeypatch.setattr("ai.llm_client.llm", fake)
    # config.CADENCE_MODEL is the first-priority source and is populated from
    # .env.local in production, so patch it directly to keep the test hermetic
    # (a setenv would be shadowed by the config value).
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "test-cadence-model")
    monkeypatch.delenv("PALLI_CADENCE_MODEL", raising=False)

    mod = _load_dispatch()
    callable_ = await mod._build_cadence_llm_callable()
    result = await callable_("test prompt")

    assert result == "cadence ok"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.get("model") == "test-cadence-model"
    # Sanity: other kwargs still flow through.
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 600
    assert call["log_context"] == "agent_cadence_shape"


@pytest.mark.asyncio
async def test_cadence_llm_palli_prefix_env_also_honored(monkeypatch):
    """The PaLLi-prefixed alias PALLI_CADENCE_MODEL takes precedence
    (per core/config.py's getenv ordering). Pin so a future env-name
    refactor doesn't silently drop the prefix variant."""
    fake = _FakeSingleton()
    monkeypatch.setattr("ai.llm_client.llm", fake)
    # Clear the config-level value so the PALLI_CADENCE_MODEL env fallback is
    # what's exercised (config.CADENCE_MODEL is populated from .env.local in prod).
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "")
    monkeypatch.delenv("CADENCE_MODEL", raising=False)
    monkeypatch.setenv("PALLI_CADENCE_MODEL", "palli-cadence-model")

    mod = _load_dispatch()
    callable_ = await mod._build_cadence_llm_callable()
    await callable_("test prompt")

    assert len(fake.calls) == 1
    assert fake.calls[0].get("model") == "palli-cadence-model"


@pytest.mark.asyncio
async def test_cadence_llm_omits_model_when_env_unset(monkeypatch):
    """When neither CADENCE_MODEL nor PALLI_CADENCE_MODEL is set, the
    cadence call must not include a ``model`` kwarg at all — the routing-aware
    client falls back to its own default routing."""
    fake = _FakeSingleton()
    monkeypatch.setattr("ai.llm_client.llm", fake)
    monkeypatch.delenv("CADENCE_MODEL", raising=False)
    monkeypatch.delenv("PALLI_CADENCE_MODEL", raising=False)

    mod = _load_dispatch()
    # The cadence model is read at _build_cadence_llm_callable() time. Patch
    # config.CADENCE_MODEL to "" so any value loaded from .env.local at
    # import time doesn't leak through.
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "")

    callable_ = await mod._build_cadence_llm_callable()
    await callable_("test prompt")

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert "model" not in call, (
        f"chat_completion was passed model={call.get('model')!r} "
        f"when CADENCE_MODEL/PALLI_CADENCE_MODEL were unset"
    )
