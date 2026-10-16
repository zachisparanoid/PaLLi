"""Organ 4.12 · Shared Idiolect — flag plumbing (spec §6).

Tasks 1-5 pinned each SURFACE dark. This file pins the FLAG: default OFF,
the suite pins every gate OFF, each gate fails closed WITHOUT being stuck
closed (two-sided, through import-time captures), the knob is really
wired, and the owner's live yaml is the one place the organ is armed."""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
import yaml

import ai.callbacks_store as cs
import core.agent.builtin_tools as bt
import core.turn.reflect as reflect
from core.settings_schema import CompanionConfig

_REAL_FILING_ON = reflect._callback_filing_on          # captured at collection


def _raises():
    return lambda: (_ for _ in ()).throw(RuntimeError("settings not booted"))


@pytest.fixture
def real_store_gate(monkeypatch):
    monkeypatch.setattr(cs, "idiolect_enabled", cs.idiolect_enabled_impl)


def test_schema_defaults_off_with_a_sane_knob():
    c = CompanionConfig()
    assert c.idiolect is False and c.callbacks_band_max == 12


def test_the_knob_is_wired(monkeypatch):
    comp = CompanionConfig(idiolect=True, callbacks_band_max=21)
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: comp)
    assert cs._band_max() == 21


def test_every_idiolect_gate_reads_off_in_a_bare_process():
    assert cs.idiolect_enabled() is False
    assert reflect._callback_filing_on() is False
    assert reflect._callback_filing_enabled() is False
    assert bt._idiolect_enabled() is False


def test_the_filing_alias_delegates_in_both_directions(monkeypatch):
    monkeypatch.setattr(reflect, "_callback_filing_on", lambda: True)
    assert reflect._callback_filing_enabled() is True
    monkeypatch.setattr(reflect, "_callback_filing_on", lambda: False)
    assert reflect._callback_filing_enabled() is False


def test_the_reflect_gate_is_pinned_in_its_own_right(monkeypatch):
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: True)
    assert reflect._callback_filing_on() is False


def test_the_pins_hold_when_the_owner_flips_the_yaml(monkeypatch):
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(idiolect=True))
    assert cs.idiolect_enabled() is False
    assert reflect._callback_filing_on() is False
    assert bt._idiolect_enabled() is False


def test_the_store_gate_reads_both_ways(real_store_gate, monkeypatch):
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert cs.idiolect_enabled_impl() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(idiolect=True))
    assert cs.idiolect_enabled_impl() is True


def test_the_reflect_gate_fails_closed_and_is_not_stuck_closed(real_store_gate, monkeypatch):
    for broken in (lambda: None, _raises(), lambda: SimpleNamespace()):
        monkeypatch.setattr("core.turn.companion_flags.companion_settings", broken)
        assert _REAL_FILING_ON() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(idiolect=True))
    monkeypatch.setattr(cs, "idiolect_enabled", _raises())
    assert _REAL_FILING_ON() is False
    monkeypatch.setattr(cs, "idiolect_enabled", cs.idiolect_enabled_impl)
    assert _REAL_FILING_ON() is True


def test_the_registry_gate_fails_closed_and_is_not_stuck_closed(monkeypatch):
    from core.settings_boot import settings_service

    monkeypatch.setattr(settings_service, "snapshot", lambda: None)
    assert bt._idiolect_enabled_impl() is False
    monkeypatch.setattr(settings_service, "snapshot", _raises())
    assert bt._idiolect_enabled_impl() is False
    monkeypatch.setattr(settings_service, "snapshot", lambda: SimpleNamespace(companion=None))
    assert bt._idiolect_enabled_impl() is False
    monkeypatch.setattr(settings_service, "snapshot",
                        lambda: SimpleNamespace(companion=SimpleNamespace(idiolect=True)))
    assert bt._idiolect_enabled_impl() is True


def test_live_yaml_carries_the_organ():
    data = yaml.safe_load(pathlib.Path("data/palli.yaml").read_text())
    comp = data.get("companion") or {}
    assert comp.get("idiolect") is True
    assert comp.get("callbacks_band_max") == 12
