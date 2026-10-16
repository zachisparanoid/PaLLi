"""The Telegram door on the punch list, the channel map and the catalog
(telegram door, task 2).

Week 2 task 7's shape, on the second door: a door turned ON without the key
that opens it is not a reason to refuse to start. It is one line saying the
door is closed for this boot and where she IS reachable — and a runtime
verdict (`effective_telegram_enabled`) every consumer reads instead of the
raw flag.

Three ways this door can be shut, and each is said out loud exactly once:
the flag is off (nothing to say), the token is blank, or the library is not
installed. The last one is why the check exists at all: a stranger who set
the token but skipped `pip install -r requirements.txt` would otherwise get
a silent no-op door.

In-process, the `_fresh_clone_config` shape of tests/core/test_fresh_clone_boot.py:
`Config()` reads env into CLASS attributes at import, so instance attributes
are the only seam a test can reach.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.config import Config
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS

TASK_PROVIDER_VARS = (
    "LLM_RESPONSE_PROVIDER",
    "LLM_ANALYSIS_PROVIDER",
    "LLM_MEMORY_PROVIDER",
    "LLM_DECISION_PROVIDER",
    "LLM_PROACTIVE_PROVIDER",
    "LLM_REFLECTION_PROVIDER",
    "LLM_WEB_SEARCH_PROVIDER",
    "LLM_VISION_PROVIDER",
)
FICTIONAL_TOKEN = "123456:ABC-fictional"


def _fresh_clone_config() -> Config:
    cfg = Config()
    cfg.LLM_PROVIDER = "deepseek"
    cfg.DEEPSEEK_API_KEY = "sk-test"
    cfg.OPENAI_API_KEY = ""
    for name in TASK_PROVIDER_VARS:
        setattr(cfg, name, "")
    cfg.ENABLE_DISCORD_BOT = False
    cfg.DISCORD_TOKEN = ""
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""
    cfg.ENABLE_TELEGRAM_BOT = False
    cfg.TELEGRAM_BOT_TOKEN = ""
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""
    cfg.BACKEND_HOST = "127.0.0.1"
    cfg.BACKEND_ALLOWED_NETWORKS = DEFAULT_ALLOWED_NETWORKS
    cfg.BACKEND_PORT = 8000
    cfg.ENVIRONMENT = "development"
    cfg.ALLOW_INSECURE_CORS = False
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.FRONTEND_URL = "http://localhost:3000"
    return cfg


def _library(monkeypatch, present: bool) -> None:
    """Pin the library's presence so these cases never depend on whether the
    machine running them happens to have it installed."""
    import core.config_schema as cs

    monkeypatch.setattr(cs, "_telegram_library_present", lambda: present)


# ── the door flag ─────────────────────────────────────────────────────────
def test_the_knobs_exist_and_default_to_off_and_blank():
    """An existing install that has never heard of Telegram must be
    byte-identical: the flag defaults FALSE, unlike Discord's."""
    # The DECLARED defaults, not the values this checkout's env happens to hold
    # (the owner turned his door on in .env on 2026-09-17; the test read it).
    from core.config_schema import CoreConfigSchema
    assert CoreConfigSchema.model_fields["ENABLE_TELEGRAM_BOT"].default is False
    assert CoreConfigSchema.model_fields["TELEGRAM_BOT_TOKEN"].default == ""


def test_both_knobs_are_declared_schema_fields():
    from core.config_schema import CoreConfigSchema

    assert "ENABLE_TELEGRAM_BOT" in CoreConfigSchema.model_fields
    assert "TELEGRAM_BOT_TOKEN" in CoreConfigSchema.model_fields


def test_the_token_says_where_to_set_it():
    from core.config_schema import where_for

    assert ".env.local" in where_for("TELEGRAM_BOT_TOKEN")


def test_the_door_off_says_nothing_at_all(monkeypatch):
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()

    assert cfg.validate() is True
    assert "TELEGRAM" not in cfg.punch_list.render().upper()
    assert cfg.effective_telegram_enabled is False


def test_the_door_on_with_no_token_is_soft_and_names_the_open_doors(monkeypatch):
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = ""

    assert cfg.validate() is True                 # never a blocker

    text = cfg.punch_list.render()
    assert "TELEGRAM_BOT_TOKEN" in text, text
    assert "closed for this boot" in text, text
    assert "http://127.0.0.1:8000" in text, text
    assert "http://localhost:3000" in text, text
    assert "1. TELEGRAM_BOT_TOKEN" not in text, text
    assert cfg.effective_telegram_enabled is False


def test_the_door_on_with_a_token_says_nothing(monkeypatch):
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TOKEN

    assert cfg.validate() is True
    assert "TELEGRAM_BOT_TOKEN" not in cfg.punch_list.render()
    assert cfg.effective_telegram_enabled is True


def test_a_token_with_the_flag_off_is_still_off(monkeypatch):
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = False
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TOKEN

    assert cfg.validate() is True
    assert cfg.effective_telegram_enabled is False


def test_a_missing_library_closes_the_door_and_says_how_to_fix_it(monkeypatch):
    _library(monkeypatch, False)
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TOKEN

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "python-telegram-bot is not installed" in text, text
    assert "requirements.txt" in text, text
    assert cfg.effective_telegram_enabled is False


def test_the_discord_door_is_untouched_beside_it(monkeypatch):
    """The regression guard: Discord's own line still fires on its own terms
    and the two doors never speak for each other."""
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = ""
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TOKEN

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "DISCORD_TOKEN" in text, text
    assert "TELEGRAM_BOT_TOKEN" not in text, text
    assert cfg.effective_discord_enabled is False
    assert cfg.effective_telegram_enabled is True


# ── the owner id, re-gated onto the door ──────────────────────────────────
def test_the_door_open_with_no_owner_id_says_everyone_is_a_guest(monkeypatch):
    """Task 1 could not say this: there was no door to be open. Now there is,
    and the Discord rule applies verbatim — only when the bot is effectively
    ON, because a line about an id for a door that never opens is noise."""
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TOKEN
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in text, text
    assert "guest" in text, text
    assert "1. LOCAL_OWNER_TELEGRAM_USER_ID" not in text, text


def test_a_blank_owner_id_with_the_door_shut_still_says_nothing(monkeypatch):
    _library(monkeypatch, True)
    cfg = _fresh_clone_config()
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""

    assert cfg.validate() is True
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" not in cfg.punch_list.render()


# ── the channel map ───────────────────────────────────────────────────────
def test_the_channel_map_follows_boots_verdict_for_telegram_too():
    from core.launcher_runtime import build_channel_enabled_map

    off = SimpleNamespace(effective_telegram_enabled=False,
                          ENABLE_TELEGRAM_BOT=True,
                          TELEGRAM_BOT_TOKEN=FICTIONAL_TOKEN)
    assert build_channel_enabled_map(config_obj=off)["telegram_bot"] is False

    on = SimpleNamespace(effective_telegram_enabled=True,
                         ENABLE_TELEGRAM_BOT=True,
                         TELEGRAM_BOT_TOKEN=FICTIONAL_TOKEN)
    assert build_channel_enabled_map(config_obj=on)["telegram_bot"] is True


def test_the_channel_map_never_opens_telegram_on_a_blank_token():
    """Belt and braces, like Discord's: an object read before validate() ran
    must not open a door with no key in it."""
    from core.launcher_runtime import build_channel_enabled_map

    unvalidated = SimpleNamespace(effective_telegram_enabled=True,
                                  ENABLE_TELEGRAM_BOT=True,
                                  TELEGRAM_BOT_TOKEN="")
    assert build_channel_enabled_map(config_obj=unvalidated)["telegram_bot"] is False

    stand_in = SimpleNamespace(ENABLE_TELEGRAM_BOT=True, TELEGRAM_BOT_TOKEN="")
    assert build_channel_enabled_map(config_obj=stand_in)["telegram_bot"] is False


def test_the_discord_entry_of_the_channel_map_is_unchanged():
    from core.launcher_runtime import build_channel_enabled_map

    on = SimpleNamespace(effective_discord_enabled=True, ENABLE_DISCORD_BOT=True,
                         DISCORD_TOKEN="x" * 40)
    assert build_channel_enabled_map(config_obj=on)["discord_bot"] is True


# ── the plugin catalog ────────────────────────────────────────────────────
def test_the_catalog_carries_the_telegram_channel():
    from core.plugin_runtime import plugin_runtime

    items = plugin_runtime.prepare_startup_catalog(force=True)
    entry = [i for i in items if i.get("plugin_id") == "builtin.channel.telegram_bot"]

    assert entry, [i.get("plugin_id") for i in items]
    assert entry[0]["families"] == ["channel_integration"]
    assert entry[0]["criticality"] == "default"
    assert entry[0]["reload_mode"] == "restart_required"


def test_the_telegram_bundle_starts_the_telegram_client():
    from core.plugin_runtime import plugin_runtime

    bundle = plugin_runtime._load_contribution_bundle(
        {"plugin_id": "builtin.channel.telegram_bot", "source_kind": "builtin"})
    contributions = bundle.channel_integrations

    assert [c.channel_id for c in contributions] == ["telegram_bot"]
    assert contributions[0].start.__name__ == "start_telegram_client"


# ── the health readout ────────────────────────────────────────────────────
def test_the_channels_readout_reports_telegram_ready(monkeypatch):
    import core.launcher_support as ls

    monkeypatch.setattr(ls.config, "ENABLE_TELEGRAM_BOT", True, raising=False)
    monkeypatch.setattr(ls.config, "TELEGRAM_BOT_TOKEN", FICTIONAL_TOKEN, raising=False)
    monkeypatch.setattr(ls.config, "effective_telegram_enabled", True, raising=False)

    telegram = ls.build_channels_readout()["telegram"]

    assert telegram["enabled"] is True
    assert telegram["configured"] is True
    assert telegram["ready"] is True


def test_the_channels_readout_is_not_ready_without_a_token(monkeypatch):
    import core.launcher_support as ls

    monkeypatch.setattr(ls.config, "ENABLE_TELEGRAM_BOT", True, raising=False)
    monkeypatch.setattr(ls.config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(ls.config, "effective_telegram_enabled", False, raising=False)

    telegram = ls.build_channels_readout()["telegram"]

    assert telegram["enabled"] is True
    assert telegram["configured"] is False
    assert telegram["ready"] is False
