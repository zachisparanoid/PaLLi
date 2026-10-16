"""The boot punch list learns the Telegram owner id (telegram door, task 1).

Week 2 task 3 gave ``LOCAL_OWNER_DISCORD_USER_ID`` a soft line when its
value is set but unusable: the key LOOKS configured, local_mode drops it
with a log nobody reads, and every stranger — and the owner — is a guest.
The Telegram id has exactly the same failure mode, so it gets the same
line.

Blank said NOTHING in task 1: there was no Telegram door for it to matter
at. Task 2 opened the door and RE-GATED this check onto it, mirroring the
Discord rule exactly — only when the bot is effectively ON (flag AND token).
These cases therefore open the door before asking, and the blank-id line
itself is proved in tests/core/test_telegram_door_config.py beside the flag
that governs it.

In-process, the `_fresh_clone_config` shape of tests/core/test_fresh_clone_boot.py:
`Config()` reads env into CLASS attributes at import, so instance
attributes are the only seam a test can reach.
"""
from __future__ import annotations

import pytest

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
    # Task 2: the door the owner-id check is gated on. Open, with a
    # fictional token, so these cases are about the ID and nothing else.
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = "123456:ABC-fictional"
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""
    cfg.BACKEND_HOST = "127.0.0.1"
    cfg.BACKEND_ALLOWED_NETWORKS = DEFAULT_ALLOWED_NETWORKS
    cfg.BACKEND_PORT = 8000
    cfg.ENVIRONMENT = "development"
    return cfg


def test_the_knob_exists_on_the_config_and_defaults_to_blank():
    assert hasattr(Config, "LOCAL_OWNER_TELEGRAM_USER_ID")


def test_the_knob_is_a_declared_schema_field():
    """Undeclared keys cannot carry a punch line — the soft mechanism reads
    the schema's own fields."""
    from core.config_schema import CoreConfigSchema

    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in CoreConfigSchema.model_fields


def test_the_key_says_where_to_set_it():
    from core.config_schema import where_for

    where = where_for("LOCAL_OWNER_TELEGRAM_USER_ID")
    assert ".env.local" in where, where


def test_a_telegram_id_that_is_not_an_id_says_so_and_says_the_value():
    cfg = _fresh_clone_config()
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "your-id-here"

    assert cfg.validate() is True                      # never a blocker

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in text, text
    assert "your-id-here" in text, text
    assert "guest" in text, text
    assert "Telegram" in text, text
    # A warning, never a numbered blocker.
    assert "1. LOCAL_OWNER_TELEGRAM_USER_ID" not in text, text


def test_a_numeric_telegram_id_says_nothing():
    cfg = _fresh_clone_config()
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "424242424"

    assert cfg.validate() is True
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" not in cfg.punch_list.render()


def test_a_blank_telegram_id_says_nothing_while_the_door_is_shut():
    """The re-gating (task 2): with no door open there is nothing to warn
    about, exactly as the Discord rule holds for its own id."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_TELEGRAM_BOT = False
    cfg.TELEGRAM_BOT_TOKEN = ""
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""

    assert cfg.validate() is True
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" not in cfg.punch_list.render()


def test_the_discord_line_is_unchanged_beside_it():
    """The regression guard: the Discord warning still fires on its own
    terms, and the Telegram key never speaks for it."""
    cfg = _fresh_clone_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = "x" * 40
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "424242424"

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_DISCORD_USER_ID" in text, text
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" not in text, text


@pytest.mark.parametrize("value", ["424242424"])
def test_a_usable_value_never_warns(value):
    cfg = _fresh_clone_config()
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = value

    assert cfg.validate() is True
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" not in cfg.punch_list.render()


@pytest.mark.parametrize("value", ["", "   "])
def test_an_absent_value_is_the_guest_line_now_the_door_exists(value):
    """The two cases task 1 pinned as SILENT (review round 1, minor 9). The
    re-gating did not delete them, it changed what they mean: with the door
    open, an id nobody set — including one that is only spaces, because
    `.strip()` makes it blank — means everyone on Telegram is a guest, and
    that is said out loud."""
    cfg = _fresh_clone_config()                # this file opens the door
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = value

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in text, text
    assert "guest" in text, text
    assert "1. LOCAL_OWNER_TELEGRAM_USER_ID" not in text, text
