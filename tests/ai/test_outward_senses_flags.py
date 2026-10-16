"""Step 23 · Outward senses — the flags and dials (Task 1).

Spec: docs/superpowers/specs/2026-08-24-outward-senses-design.md
§0.1 empty denylist BY HIS CALL · §0.3 nas cap 0 = unlimited (07c law) ·
§0.6 sweeps a few times a day, the listener has its own sub-switch.
"""
import pathlib

import yaml

from core.settings_schema import CompanionConfig


def test_outward_senses_knobs_default_off_and_sane():
    f = CompanionConfig.model_fields
    assert f["outward_senses"].default is False
    assert f["lan_presence"].default is False
    assert f["lan_listener"].default is True               # sub-switch under lan_presence
    assert f["curiosity_nas_daily_cap"].default == 0      # 0 = unlimited (07c law)
    assert f["nas_deny"].default_factory() == []
    assert f["lan_sweep_interval_minutes"].default == 480  # three a day (§0.6)
    assert f["lan_absence_minutes"].default == 15
    assert f["lan_devices"].default_factory() == {}


def test_knob_rails_hold():
    import pytest

    with pytest.raises(Exception):
        CompanionConfig(lan_sweep_interval_minutes=5)      # floor 30 — no 5-minute clock (§0.6)
    with pytest.raises(Exception):
        CompanionConfig(lan_absence_minutes=0)
    with pytest.raises(Exception):
        CompanionConfig(curiosity_nas_daily_cap=-1)
    ok = CompanionConfig(lan_sweep_interval_minutes=30, curiosity_nas_daily_cap=0,
                         nas_deny=["private"], lan_devices={"aa:bb": "his phone"})
    assert ok.nas_deny == ["private"] and ok.lan_devices == {"aa:bb": "his phone"}


def test_the_shipped_yaml_keeps_the_step_off_until_there_is_hardware():
    """Step 28 task 5: this read the OWNER's data/palli.yaml and required all three
    flags True — his flips, on his machine, with his camera, his NAS and his LAN. The
    shipped config (data/palli.example.yaml) has them off, because each needs hardware
    a fresh install cannot assume (task 3's HARDWARE_BOUND_FLAGS), so the old assertion
    failed in every clone. Pinned here as the shipped truth — plus the one value that is
    the same in both files and is a real decision, not a default."""
    comp = (yaml.safe_load(
        pathlib.Path("data/palli.example.yaml").read_text()) or {}).get("companion") or {}
    assert comp.get("outward_senses") is False             # needs a camera and a screen
    assert comp.get("curiosity_nas_dives") is False        # needs a NAS (PALLI_NAS_ROOT)
    assert comp.get("lan_presence") is False               # needs a LAN to sweep
    assert comp.get("nas_deny") == []                      # empty BY HIS CALL — do not "helpfully" populate
