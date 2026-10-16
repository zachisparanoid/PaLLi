"""Settings schema and palli.yaml carry the OPEN_THREADS_* knobs consistently.

T17 (2026-05-12) deleted both ``features.enable_open_threads_agenda`` and
``features.companion_agenda`` from the schema + yaml — the agenda is now
unconditional. The remaining test below pins the surviving tuning knobs.
"""
from pathlib import Path

import yaml

from core.settings_schema import FeaturesConfig


def test_features_schema_has_no_open_threads_or_companion_agenda_flags():
    """T17: ENABLE_OPEN_THREADS_AGENDA + companion_agenda fields are deleted."""
    fields = set(FeaturesConfig.model_fields.keys())
    assert "enable_open_threads_agenda" not in fields
    assert "companion_agenda" not in fields


def test_palli_yaml_open_threads_tuning_knobs_match_config_defaults():
    """The live yaml carries all surviving OPEN_THREADS_* tuning knobs.

    Asserts presence and type only — the values themselves are
    owner-tuned (quiet hours moved to 0/23:59 for a nocturnal owner)
    and must not be pinned against schema defaults.
    """
    yaml_path = Path("data/palli.yaml")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    features = data.get("features", {})
    assert isinstance(features.get("open_threads_surface_interval_minutes"), int)
    assert isinstance(
        features.get("open_threads_surface_activity_window_hours"), (int, float)
    )
    assert isinstance(features.get("open_threads_surface_quiet_hours_start"), int)
    assert isinstance(features.get("open_threads_surface_quiet_hours_end_hour"), int)
    assert isinstance(features.get("open_threads_surface_quiet_hours_end_minute"), int)
    assert isinstance(
        features.get("open_threads_post_surface_backoff_multiplier"), int
    )
