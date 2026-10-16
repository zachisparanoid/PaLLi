"""Tests for core.cli_presets — startup CLI flag parser."""
from __future__ import annotations

import os
import sys
from typing import Dict
from unittest.mock import patch

import pytest

# IMPORTANT: cli_presets reads from `core.config.config` at apply time,
# NOT at import time. Tests can import freely.
from core.cli_presets import (
    MORE_CHATTY,
    MORE_PERCEPTION,
    parse_and_apply,
)


@pytest.fixture
def fake_config():
    """Stand-in for the config singleton with all preset attrs preset to baseline.

    cli_presets._apply does `from core.config import config` at call time,
    which resolves `core.config.config`. Patching that module attribute
    redirects the lookup to our fake.
    """
    class _FakeCfg:
        DESKTOP_ACTIVITY_POLL_SECONDS = 30.0
        DESKTOP_ACTIVITY_WINDOW_SECONDS = 1800.0
        DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS = 600.0
        DESKTOP_ACTIVITY_MAX_RECENT_FILES = 8
        PROACTIVE_CONFIDENCE_THRESHOLD = 0.50
        MAX_DAILY_PROACTIVE_MESSAGES = 8
        PROACTIVE_ABSOLUTE_MIN_GAP_MINUTES = 60
        MAX_PROACTIVE_FREQUENCY_HOURS = 2.0
        MIN_CONVERSATION_GAP_HOURS = 0.5
        PROACTIVE_CHECK_INTERVAL_MINUTES = 10
        PROACTIVE_RANDOMNESS_LOW_CHANCE = 0.30
        PROACTIVE_RANDOMNESS_MED_CHANCE = 0.45
        PROACTIVE_RANDOMNESS_HIGH_CHANCE = 0.50

    cfg = _FakeCfg()
    import core.config
    with patch.object(core.config, "config", cfg):
        yield cfg


def test_no_flags_applies_no_overrides(fake_config):
    flags = parse_and_apply([])
    assert all(v is False for v in flags.values())
    assert fake_config.DESKTOP_ACTIVITY_POLL_SECONDS == 30.0
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == 0.50


def test_more_perception_applies_only_perception_overrides(fake_config):
    flags = parse_and_apply(["--more-perception"])
    assert flags["more_perception"] is True
    assert flags["more_chatty"] is False
    assert fake_config.DESKTOP_ACTIVITY_POLL_SECONDS == MORE_PERCEPTION["DESKTOP_ACTIVITY_POLL_SECONDS"]
    assert fake_config.DESKTOP_ACTIVITY_WINDOW_SECONDS == MORE_PERCEPTION["DESKTOP_ACTIVITY_WINDOW_SECONDS"]
    # Chatty values untouched
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == 0.50


def test_more_chatty_applies_only_chatty_overrides(fake_config):
    flags = parse_and_apply(["--more-chatty"])
    assert flags["more_chatty"] is True
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == MORE_CHATTY["PROACTIVE_CONFIDENCE_THRESHOLD"]
    assert fake_config.MAX_DAILY_PROACTIVE_MESSAGES == MORE_CHATTY["MAX_DAILY_PROACTIVE_MESSAGES"]
    # Perception values untouched
    assert fake_config.DESKTOP_ACTIVITY_POLL_SECONDS == 30.0


def test_more_everything_applies_both_presets(fake_config):
    flags = parse_and_apply(["--more-everything"])
    assert flags["more_perception"] is True
    assert flags["more_chatty"] is True
    assert flags["more_everything"] is True
    assert fake_config.DESKTOP_ACTIVITY_POLL_SECONDS == MORE_PERCEPTION["DESKTOP_ACTIVITY_POLL_SECONDS"]
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == MORE_CHATTY["PROACTIVE_CONFIDENCE_THRESHOLD"]


def test_max_alias_works(fake_config):
    flags = parse_and_apply(["--max"])
    assert flags["more_everything"] is True


def test_combining_individual_flags_equivalent_to_more_everything(fake_config):
    parse_and_apply(["--more-perception", "--more-chatty"])
    assert fake_config.DESKTOP_ACTIVITY_POLL_SECONDS == MORE_PERCEPTION["DESKTOP_ACTIVITY_POLL_SECONDS"]
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == MORE_CHATTY["PROACTIVE_CONFIDENCE_THRESHOLD"]


def test_unknown_flags_ignored(fake_config):
    """Other layers may consume their own argv; cli_presets must not error."""
    flags = parse_and_apply(["--unknown-future-thing", "--more-chatty", "--also-not-mine"])
    assert flags["more_chatty"] is True
    assert flags["more_perception"] is False


def test_underscore_variant_accepted(fake_config):
    """Both kebab-case and underscore forms work."""
    flags = parse_and_apply(["--more_perception"])
    assert flags["more_perception"] is True


def test_overrides_supersede_existing_config(fake_config):
    """Even if dotenv already populated config, the CLI flag must win."""
    fake_config.PROACTIVE_CONFIDENCE_THRESHOLD = 0.99
    parse_and_apply(["--more-chatty"])
    assert fake_config.PROACTIVE_CONFIDENCE_THRESHOLD == MORE_CHATTY["PROACTIVE_CONFIDENCE_THRESHOLD"]


def test_os_environ_also_updated(fake_config, monkeypatch):
    """Some downstream readers go through os.environ; ensure both surfaces are written."""
    for k in MORE_CHATTY:
        monkeypatch.delenv(k, raising=False)
    parse_and_apply(["--more-chatty"])
    for k, v in MORE_CHATTY.items():
        assert os.environ[k] == str(v), f"{k} not in os.environ"
