"""OPEN_THREADS_* tuning knob defaults — now sourced from data/palli.yaml.

T17 (2026-05-12) deleted the ENABLE_OPEN_THREADS_AGENDA flag — the
agenda is unconditional.

E.5 Wave 4 step 3 (2026-05-13) migrated the OPEN_THREADS_* tuning
knobs from ``core.config.config.OPEN_THREADS_*`` (env-var reads) to
``settings_service.snapshot().features.open_threads_*`` (palli.yaml).
The tests below now pin the YAML-driven snapshot values that
the retired ``surface_decision`` lane consumed (step 19 deleted it;
the knobs stay for config compat).
"""
from pathlib import Path

from core.settings_service import SettingsService


def _fresh_features():
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    svc = SettingsService(yaml_path=repo_yaml)
    svc.load()
    return svc.snapshot().features


def test_open_threads_surface_interval_minutes_default_30():
    features = _fresh_features()
    assert features.open_threads_surface_interval_minutes == 30


def test_open_threads_activity_window_hours_default_4():
    features = _fresh_features()
    assert features.open_threads_surface_activity_window_hours == 4.0


def test_open_threads_quiet_hours_defaults():
    """Schema defaults for the quiet-hours knobs.

    Asserts FeaturesConfig's own defaults, not the live yaml — the
    owner tuned data/palli.yaml quiet hours to 0/23:59 (nocturnal),
    so the yaml values are no longer the shipped defaults.
    """
    from core.settings_schema import FeaturesConfig

    features = FeaturesConfig()
    assert features.open_threads_surface_quiet_hours_start == 7
    assert features.open_threads_surface_quiet_hours_end_hour == 22
    assert features.open_threads_surface_quiet_hours_end_minute == 30


def test_open_threads_post_surface_backoff_multiplier_default_4():
    features = _fresh_features()
    assert features.open_threads_post_surface_backoff_multiplier == 4
