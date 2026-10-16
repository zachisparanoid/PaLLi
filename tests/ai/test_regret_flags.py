"""Step 25 · Regret — flags and knobs (spec §0, §6)."""
from core.settings_schema import CompanionConfig


def test_regret_flag_defaults_off_with_dials():
    fields = CompanionConfig.model_fields
    assert fields["regret"].default is False
    assert fields["regret_max_open"].default == 2
    assert fields["regret_half_life_days"].default == 14


def test_live_yaml_flips_regret_on():
    import yaml, pathlib
    data = yaml.safe_load(pathlib.Path("data/palli.yaml").read_text())
    comp = data.get("companion") or {}
    assert comp.get("regret") is True
    assert comp.get("regret_max_open", 2) == 2
