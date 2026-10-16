"""Organ 4.13 — flag plumbing: schema defaults, off-pins, yaml presence.

Off is byte-identical everywhere: the code default is OFF, the suite pins it
OFF for every test that does not ask for it, and the live yaml is the owner's
single enablement (activation = his restart — the regret precedent).
"""
from core.settings_schema import CompanionConfig


def test_schema_defaults_off_with_sane_knobs():
    c = CompanionConfig()
    assert c.opinions is False
    assert c.views_max_active == 5 and c.views_dormant_cap == 10


def test_conftest_pins_opinions_off():
    # The autouse fixture pins these targets; a bare test process must see OFF
    # even though the live yaml (lazy-loaded) ships companion.opinions TRUE.
    from ai import stake_appraiser, stake_ledger, stake_review
    assert stake_ledger.opinions_enabled() is False
    assert stake_appraiser._opinions_enabled() is False
    assert stake_review._opinions_enabled() is False


def test_live_yaml_carries_the_organ():
    import pathlib

    import yaml
    data = yaml.safe_load(pathlib.Path("data/palli.yaml").read_text())
    comp = data.get("companion") or {}
    assert comp.get("opinions") is True
    assert comp.get("views_max_active") == 5
    assert comp.get("views_dormant_cap") == 10
