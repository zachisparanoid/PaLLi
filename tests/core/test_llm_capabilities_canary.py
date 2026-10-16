"""Three canaries (spec §3.1): the matrix is held to what the code does.

1. every provider the registry builds has a row, and vice versa;
2. every routing lane the schema knows has a row, and the runtime's env
   tables and severities agree with it;
3. every row's dialect is what core/agent/dispatch.py::_get_openai_compat_client
   would actually pick for a provider shaped like that — asserted by driving
   the real function with a stub. Add a provider without a row, or change
   the Path 1 / Path 2 condition, and this fails here instead of at 9pm."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm_capabilities import BRAIN_LANE, LANES, PROVIDERS, Auth, Dialect, Governance


def test_registry_and_matrix_name_the_same_providers():
    from ai.llm_provider_registry import build_default_provider_registry

    registry = set(build_default_provider_registry())
    assert registry == set(PROVIDERS), (
        f"registry-only: {sorted(registry - set(PROVIDERS))}  "
        f"matrix-only: {sorted(set(PROVIDERS) - registry)}"
    )


def test_schema_lanes_and_matrix_lanes_agree():
    from ai.llm_routing import TASK_PROVIDER_FIELDS, TASK_ROUTING_SEVERITY
    from core.settings_schema import RoutingConfig

    schema_lanes = set(RoutingConfig.model_fields)
    assert schema_lanes == set(LANES), (
        f"schema-only: {sorted(schema_lanes - set(LANES))}  "
        f"matrix-only: {sorted(set(LANES) - schema_lanes)}"
    )
    # every env-addressable lane is a matrix lane (narrative_author has no env key — that is fine)
    assert set(TASK_PROVIDER_FIELDS) <= set(LANES)
    for lane, severity in TASK_ROUTING_SEVERITY.items():
        if lane in LANES:
            assert LANES[lane].severity == severity, lane
    assert LANES[BRAIN_LANE].governed_by is Governance.ENV_ONLY


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(PROVIDERS))
async def test_matrix_dialect_is_what_dispatch_picks(name, monkeypatch):
    """Drive the real client factory with a stub shaped like the provider
    (its name, whether it has an api_key) and check which client comes back.
    UNSUPPORTED means dispatch builds an OpenAI client for a provider that
    does not speak OpenAI's wire — anthropic today (spec §1.3)."""
    import ai.llm_client as llm_client_module
    from ai.llm_openai_compatible import OpenAICompatibleProvider
    from ai.llm_provider_registry import build_default_provider_registry
    from core.agent import dispatch

    cap = PROVIDERS[name]
    stub = SimpleNamespace(
        provider_name=name,
        api_key="sk-test" if cap.auth is Auth.API_KEY else "",
        base_url="http://127.0.0.1:1/v1",
        chat_completion=None,
    )
    monkeypatch.setattr(llm_client_module.llm, "provider", stub)
    client = await dispatch._get_openai_compat_client()
    picked = type(client).__name__

    real_class = type(build_default_provider_registry()[name])
    speaks_openai_wire = issubclass(real_class, OpenAICompatibleProvider)

    if cap.runner_dialect is Dialect.SDK_ADAPTER:
        assert picked == "PaLLiProviderSDKAdapter", (name, picked)
    elif cap.runner_dialect is Dialect.OPENAI_COMPAT:
        assert picked == "AsyncOpenAI", (name, picked)
        assert speaks_openai_wire, f"{name}: matrix says openai_compat but the provider class does not speak that wire"
    elif cap.runner_dialect is Dialect.UNSUPPORTED:
        assert picked == "AsyncOpenAI", (name, picked)
        assert not speaks_openai_wire, f"{name}: dispatch picks AsyncOpenAI and the class speaks it — the row should be openai_compat"
    elif cap.runner_dialect is Dialect.ANTHROPIC:
        assert picked == "AsyncAnthropic", (name, picked)
