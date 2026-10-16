"""The mobile door's switch, address and verdict (spec §9): off by default,
never a refused boot, shut until a brain is chosen, started by the channel map."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config import Config
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS

TASK_PROVIDER_VARS = (
    "LLM_RESPONSE_PROVIDER", "LLM_ANALYSIS_PROVIDER", "LLM_MEMORY_PROVIDER", "LLM_DECISION_PROVIDER",
    "LLM_PROACTIVE_PROVIDER", "LLM_REFLECTION_PROVIDER", "LLM_WEB_SEARCH_PROVIDER", "LLM_VISION_PROVIDER",
)


def _config(*, brain: bool = True) -> Config:
    cfg = Config()
    cfg.LLM_PROVIDER = "deepseek"
    cfg.DEEPSEEK_API_KEY = "sk-test" if brain else ""
    cfg.OPENAI_API_KEY = ""
    for name in TASK_PROVIDER_VARS:
        setattr(cfg, name, "")
    cfg.ENABLE_DISCORD_BOT = False
    cfg.DISCORD_TOKEN = ""
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""
    cfg.ENABLE_TELEGRAM_BOT = False
    cfg.TELEGRAM_BOT_TOKEN = ""
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""
    cfg.ENABLE_MOBILE_DOOR = False
    cfg.MOBILE_PUBLIC_URL = ""
    cfg.BACKEND_HOST = "0.0.0.0"
    cfg.BACKEND_ALLOWED_HOSTS = "palli-box.example.ts.net"
    cfg.BACKEND_ALLOWED_NETWORKS = DEFAULT_ALLOWED_NETWORKS
    cfg.BACKEND_PORT = 8000
    cfg.ENVIRONMENT = "development"
    cfg.ALLOW_INSECURE_CORS = False
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.FRONTEND_URL = "http://localhost:3000"
    return cfg


def test_the_knobs_exist_and_default_to_off_and_blank():
    from core.config_schema import CoreConfigSchema

    assert CoreConfigSchema.model_fields["ENABLE_MOBILE_DOOR"].default is False
    assert CoreConfigSchema.model_fields["MOBILE_PUBLIC_URL"].default == ""


def test_off_says_nothing_and_opens_nothing():
    cfg = _config()
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" not in text and "Mobile" not in text, text
    assert cfg.effective_mobile_enabled is False


def test_on_with_a_reachable_address_opens_quietly():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://palli-box.example.ts.net:8000"
    assert cfg.validate() is True
    assert "MOBILE_PUBLIC_URL" not in cfg.punch_list.render()
    assert cfg.effective_mobile_enabled is True


def test_on_with_no_address_is_soft_and_still_opens():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" in text and "by hand" in text, text
    assert cfg.effective_mobile_enabled is True


def test_an_address_the_host_check_would_refuse_is_named():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://palli.example.net:8000"
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "palli.example.net" in text and "BACKEND_ALLOWED_HOSTS" in text, text


def test_a_listed_host_written_with_a_scheme_or_a_port_is_still_listed():
    # The host gate reads BACKEND_ALLOWED_HOSTS with or without a scheme, a
    # port or a path (core/trusted_hosts.py); the warning must judge the same way.
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://palli-box.example.ts.net:8000"
    cfg.BACKEND_ALLOWED_HOSTS = "http://palli-box.example.ts.net:8000/, other.example.ts.net"
    cfg.validate()
    assert "BACKEND_ALLOWED_HOSTS" not in cfg.punch_list.render()


def test_a_trailing_dot_on_the_address_is_judged_the_way_the_gate_judges_it():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://palli-box.example.ts.net.:8000"
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" not in text and "BACKEND_ALLOWED_HOSTS" not in text, text


@pytest.mark.parametrize("url", ["http://[abc", "http://exa[mple:8000"])
def test_an_address_urlparse_refuses_is_a_warning_that_names_the_setting_never_a_refused_boot(url):
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = url
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" in text and "http://" in text and "host" in text, text
    assert cfg.effective_mobile_enabled is True


@pytest.mark.parametrize("url", ["100.64.0.7:8000", "palli-box.example.ts.net:8000"])
def test_an_address_with_no_scheme_is_named_because_no_host_can_be_read_from_it(url):
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = url
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" in text and "http://" in text and "host" in text, text
    assert cfg.effective_mobile_enabled is True


@pytest.mark.parametrize("url", ["http://[abc", "100.64.0.7:8000", "http://palli.example.net:8000"])
def test_switched_off_the_address_is_nobodys_business(url):
    cfg = _config()
    cfg.MOBILE_PUBLIC_URL = url
    cfg.BACKEND_HOST = "127.0.0.1"
    assert cfg.validate() is True
    text = cfg.punch_list.render()
    assert "MOBILE_PUBLIC_URL" not in text and "BACKEND_HOST" not in text and "Mobile" not in text, text
    assert cfg.effective_mobile_enabled is False


def test_an_ip_address_needs_no_host_listing():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    cfg.validate()
    assert "BACKEND_ALLOWED_HOSTS" not in cfg.punch_list.render()


def test_a_loopback_bind_is_named_because_no_phone_can_reach_it():
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    cfg.BACKEND_HOST = "127.0.0.1"
    cfg.validate()
    text = cfg.punch_list.render()
    assert "BACKEND_HOST" in text and "127.0.0.1" in text, text


def test_no_brain_shuts_it_with_a_line_that_does_not_mention_a_token():
    cfg = _config(brain=False)
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    assert cfg.validate() is True
    assert cfg.effective_mobile_enabled is False
    text = cfg.punch_list.render()
    assert "- Mobile — closed until a brain is configured" in text, text
    assert "it is switched on; it opens at the first restart after you add a brain" in text, text


def test_the_brain_validator_still_has_the_last_word():
    from core.config_schema import CoreConfigSchema

    order = [d.cls_var_name for d in CoreConfigSchema.__pydantic_decorators__.model_validators.values()]
    assert order.index("_validate_mobile_door") < order.index("_validate_llm_provider_keys")


def test_the_channel_map_follows_the_verdict():
    from core.launcher_runtime import build_channel_enabled_map

    assert build_channel_enabled_map(config_obj=SimpleNamespace(effective_mobile_enabled=True))["mobile_door"] is True
    assert build_channel_enabled_map(config_obj=SimpleNamespace(effective_mobile_enabled=False,
                                                                ENABLE_MOBILE_DOOR=True))["mobile_door"] is False
    assert build_channel_enabled_map(config_obj=SimpleNamespace(ENABLE_MOBILE_DOOR=True))["mobile_door"] is True


def test_off_means_off_in_the_channel_map_whatever_the_config_object_is():
    """The map is what decides whether start_mobile_door runs at all; a contribution
    the map does not name would be started (`channel_enabled.get(id, True)`)."""
    from core.launcher_runtime import build_channel_enabled_map

    assert build_channel_enabled_map(config_obj=Config())["mobile_door"] is False
    assert build_channel_enabled_map(config_obj=SimpleNamespace())["mobile_door"] is False
    assert build_channel_enabled_map(config_obj=SimpleNamespace(ENABLE_MOBILE_DOOR=False))["mobile_door"] is False
    cfg = _config()
    assert cfg.validate() is True
    assert build_channel_enabled_map(config_obj=cfg)["mobile_door"] is False


def test_the_catalog_carries_the_door_and_its_bundle_starts_it():
    from core.plugin_runtime import plugin_runtime

    items = plugin_runtime.prepare_startup_catalog(force=True)
    entry = [i for i in items if i.get("plugin_id") == "builtin.channel.mobile_door"]
    assert entry and entry[0]["families"] == ["channel_integration"]
    bundle = plugin_runtime._load_contribution_bundle(
        {"plugin_id": "builtin.channel.mobile_door", "source_kind": "builtin"})
    assert [c.channel_id for c in bundle.channel_integrations] == ["mobile_door"]
    assert bundle.channel_integrations[0].start.__name__ == "start_mobile_door"


def test_the_readout_names_the_door(monkeypatch):
    import core.launcher_support as ls

    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    cfg.validate()
    monkeypatch.setattr(ls, "config", cfg)
    assert ls.build_channels_readout()["mobile"] == {"enabled": True, "configured": True, "ready": True}


# --- the doctor's two codes (core/launcher_support.py, build_doctor_report) -----


def _doctor_codes(monkeypatch, cfg) -> list[str]:
    """build_doctor_report() on `cfg`, with everything that touches this machine stubbed
    (the pattern tests/core/test_launcher_support.py uses). Only the codes come back."""
    import core.launcher_support as ls

    monkeypatch.setattr(ls, "config", cfg)
    monkeypatch.setattr(ls, "resolve_repo_python_path",
                        lambda: {"path": "/srv/palli/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr(ls, "resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr(ls, "resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr(ls, "website_node_modules_present", lambda: True)
    monkeypatch.setattr(ls, "website_build_present", lambda: True)
    monkeypatch.setattr(ls, "database_writability",
                        lambda: {"path": "data/test.sqlite3", "absolute_path": "/srv/palli/test.sqlite3",
                                 "writable": True, "error": ""})
    monkeypatch.setattr(ls, "launcher_snapshot", lambda: {"services": {}, "last_doctor": None})
    monkeypatch.setattr(ls, "_check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status",
                        lambda: {"default_provider": "deepseek", "providers": [],
                                 "task_routing": {}, "route_issues": []})
    return [c["code"] for c in ls.build_doctor_report()["checks"]]


def test_the_doctor_says_a_switched_on_door_waits_on_a_brain(monkeypatch):
    cfg = _config(brain=False)
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    assert cfg.validate() is True
    codes = _doctor_codes(monkeypatch, cfg)
    assert "mobile_closed_no_brain" in codes and "mobile_public_url_missing" not in codes, codes


def test_the_doctor_says_a_switched_on_door_has_no_address(monkeypatch):
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    assert cfg.validate() is True
    codes = _doctor_codes(monkeypatch, cfg)
    assert "mobile_public_url_missing" in codes and "mobile_closed_no_brain" not in codes, codes


def test_a_door_switched_on_with_an_address_and_a_brain_leaves_the_doctor_quiet(monkeypatch):
    cfg = _config()
    cfg.ENABLE_MOBILE_DOOR = True
    cfg.MOBILE_PUBLIC_URL = "http://100.64.0.7:8000"
    assert cfg.validate() is True
    codes = _doctor_codes(monkeypatch, cfg)
    assert not [c for c in codes if c.startswith("mobile_")], codes


@pytest.mark.parametrize("brain", [True, False])
def test_a_door_switched_off_leaves_the_doctor_quiet_with_or_without_a_brain(monkeypatch, brain):
    cfg = _config(brain=brain)
    assert cfg.validate() is True
    codes = _doctor_codes(monkeypatch, cfg)
    assert not [c for c in codes if c.startswith("mobile_")], codes
