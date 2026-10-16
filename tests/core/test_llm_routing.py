from __future__ import annotations

from ai.llm_routing import build_routing_runtime_status, describe_model_route


def test_describe_model_route_infers_openai_provider_from_model_name():
    route = describe_model_route(
        "gpt-4o",
        default_provider_name="local_openai",
        fallback_provider_name="openai",
        providers_by_name={
            "openai": {
                "provider": "openai",
                "label": "OpenAI",
                "configured": True,
                "default_model": "gpt-4o",
                "base_url": "",
                "supports_vision": True,
            }
        },
    )

    assert route["requested_provider"] == "openai"
    assert route["requested_provider_source"] == "model_inference"
    assert route["requested_model_reference"] == "openai::gpt-4o"
    assert route["effective_model_reference"] == "openai::gpt-4o"
    assert route["route_changed"] is False


def test_build_routing_runtime_status_uses_fallback_provider_alias(monkeypatch):
    # Disable settings service so the env-fallback path is exercised
    monkeypatch.setattr("ai.llm_routing._settings_routing", lambda: None)
    monkeypatch.setattr("ai.llm_routing.config.LLM_PROVIDER", "custom_primary", raising=False)
    monkeypatch.setattr("ai.llm_routing.config.LLM_RESPONSE_PROVIDER", "mystery", raising=False)
    monkeypatch.setattr("ai.llm_routing.config.LLM_RESPONSE_MODEL", "gpt-4o", raising=False)

    status = build_routing_runtime_status(
        default_provider_name="custom_primary",
        fallback_provider_name="custom_primary",
        providers=[
            {
                "provider": "custom_primary",
                "label": "Custom Primary",
                "configured": True,
                "default_model": "claude-3-7-sonnet",
                "base_url": "",
                "supports_vision": False,
            }
        ],
    )
    route = status["task_routing"]["response"]

    assert route["requested_provider"] == "mystery"
    assert route["requested_model_reference"] == "mystery::gpt-4o"
    assert route["effective_provider"] == "custom_primary"
    assert route["effective_provider_label"] == "Custom Primary"
    assert route["effective_model_reference"] == "custom_primary::gpt-4o"
    assert route["resolution_reason"] == "unknown_provider_fallback"
    assert route["issue"]["code"] == "unknown_provider"
