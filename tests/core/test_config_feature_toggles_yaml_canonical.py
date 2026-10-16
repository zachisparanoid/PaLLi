"""Step 3 — Feature toggle migration to palli.yaml canonical.

Phase E.5 Wave 4 step 3 (2026-05-13). Migrates the feature-tier toggles
(OPEN_THREADS_* tuning knobs, ENABLE_WEB_SEARCH, ENABLE_PROACTIVE_CONVERSATIONS,
HOME_ASSISTANT_ENABLED) from ``os.getenv`` reads on ``core.config.Config``
to ``settings_service.snapshot().features.*``.

Pinning:

  * Consumers of OPEN_THREADS_* (the retired surface_decision lane) now
    read the YAML values, not the env defaults.
  * Consumers of HOME_ASSISTANT_ENABLED in ``core/home_presence.py`` now
    read the YAML value.
  * ``config_schema.validate_config()`` continues to enforce the
    cross-field coherence rules (``ENABLE_WEB_SEARCH`` + ``EXA_API_KEY``;
    ``HOME_ASSISTANT_ENABLED`` + URL/TOKEN/entities) by reading the
    ambient YAML snapshot when the Config object no longer carries the
    field. That side keeps the launcher-validator behavior intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings_boot import settings_service
from core.settings_service import SettingsService


def _fresh_snapshot():
    """Force a load of the SHIPPED config, uncontaminated by sibling tests.

    Step 28 task 5: this used to read data/palli.yaml — the OWNER's live file. The public
    tree does not carry it (it is his), and a fresh clone has none until its first boot
    seeds one, so seven tests across this file and its voice twin failed in an assembled
    tree for that reason alone. It now reads data/palli.example.yaml, the file that IS
    shipped: still a real on-disk yaml, still loaded through a fresh SettingsService,
    still carrying values that differ from the schema defaults — so the pin is as
    load-bearing as it was, and it now says the same thing in both trees.

    Unconditional, not "the live file if present": `palli_ctl onboard` seeds
    data/palli.yaml BY COPYING the example (task 4 fix 2), so in a clone that path exists
    and holds the example's values. Keying off existence would flip these assertions to
    the owner's values against a file that is not his.
    """
    example = Path(__file__).resolve().parents[2] / "data" / "palli.example.yaml"
    svc = SettingsService(yaml_path=example)
    svc.load()
    return svc.snapshot()


def test_yaml_open_threads_knobs_reach_features_snapshot(tmp_path):
    """Knob values written in yaml reach the features snapshot.

    Uses a hermetic yaml with deliberately non-default values — the
    live data/palli.yaml knobs are owner-tuned (e.g. quiet hours moved
    to 0/23:59 for a nocturnal owner) and must not be pinned here.
    """
    from tests.core.conftest import write_hermetic_settings_yaml

    target = write_hermetic_settings_yaml(
        tmp_path / "palli.yaml",
        features={
            "open_threads_surface_interval_minutes": 45,
            "open_threads_surface_activity_window_hours": 6.0,
            "open_threads_surface_quiet_hours_start": 8,
            "open_threads_surface_quiet_hours_end_hour": 21,
            "open_threads_surface_quiet_hours_end_minute": 15,
            "open_threads_post_surface_backoff_multiplier": 3,
        },
    )
    svc = SettingsService(yaml_path=target)
    svc.load()
    f = svc.snapshot().features
    assert f.open_threads_surface_interval_minutes == 45
    assert f.open_threads_surface_activity_window_hours == 6.0
    assert f.open_threads_surface_quiet_hours_start == 8
    assert f.open_threads_surface_quiet_hours_end_hour == 21
    assert f.open_threads_surface_quiet_hours_end_minute == 15
    assert f.open_threads_post_surface_backoff_multiplier == 3


def test_yaml_feature_toggles_reach_features_snapshot():
    """The shipped values, off a fresh load. web_search is False in the example on
    purpose (task 4 fix 1): on with a blank EXA_API_KEY a fresh clone fails config
    validation and never boots. The schema default is True, so a False here can only
    have come from the yaml this load actually read."""
    f = _fresh_snapshot().features
    assert f.proactive_conversations is True
    assert f.web_search is False
    assert f.home_assistant is False


def test_settings_boot_snapshot_returns_yaml_feature_values():
    """The module-level settings_service singleton returns the same.

    Unlike _fresh_snapshot() above, this one reads what tests/conftest.py seeded from
    data/palli.example.yaml — the SHIPPED config, not the owner's. web_search is False
    there on purpose (step 28 task 4 fix 1): on with a blank EXA_API_KEY, a fresh clone
    fails config validation and never boots. That makes this line a STRONGER pin than it
    was, not a weaker one — the schema default is True (core/settings_schema.py:179), so
    a False here can only have come from the yaml the singleton actually read."""
    f = settings_service.snapshot().features
    assert f.proactive_conversations is True
    assert f.web_search is False
    assert f.home_assistant is False
    assert f.open_threads_surface_interval_minutes == 30
