"""Organ 4.13 · Opinions (spec §5) — the views board.

The board is offered only when the organ is ON and an open, UNMUTED view is
actually on the page: a muted view was "dropped" at his word and is held
quietly, so it never reaches the board (§0.1). Two slots ride the two live
rungs and both collapse to zero bytes otherwise, so a flag-off gather AND a
no-live-view gather stay byte-identical — the regret shape, exactly.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from ai import present_moment, present_moment_gather
from ai.stake_ledger import Stake


def _view(muted=None):
    return Stake(id=7, kind="view", stake="s", status="open", resolution="",
                 horizon_at=None, source_channel_id="", last_surfaced_at=None,
                 created_at=1.0, updated_at=1.0, resolved_at=None,
                 voiced_at=None, evidence="e", muted_at=muted)


# ── the probe: a muted view never reaches the board ─────────────────────

@pytest.mark.asyncio
async def test_probe_ignores_muted_views(monkeypatch):
    async def rows(**kw):
        return [_view(muted=123.0)]
    monkeypatch.setattr("ai.stake_ledger.open_stakes", rows)
    assert await present_moment_gather._view_voicing_open() == ""

    async def rows2(**kw):
        return [_view()]
    monkeypatch.setattr("ai.stake_ledger.open_stakes", rows2)
    assert await present_moment_gather._view_voicing_open() == "open"


@pytest.mark.asyncio
async def test_probe_ignores_other_kinds(monkeypatch):
    other = Stake(id=2, kind="hope", stake="the survey lands", status="open",
                  resolution="", horizon_at=None, source_channel_id="",
                  last_surfaced_at=None, created_at=1.0, updated_at=1.0,
                  resolved_at=None)

    async def rows(**kw):
        return [other]
    monkeypatch.setattr("ai.stake_ledger.open_stakes", rows)
    assert await present_moment_gather._view_voicing_open() == ""


# ── the two slots: zero bytes off, the contract on ──────────────────────

def test_kwargs_zero_bytes_off_and_armed_on():
    off = present_moment._view_voicing_kwargs(False)
    assert off == {"view_say_line": "", "view_id_field": ""}
    on = present_moment._view_voicing_kwargs(True)
    assert "view_id" in on["view_id_field"] and "view <id>" in on["view_say_line"]


def test_say_line_carries_the_restraint_law():
    on = present_moment._view_voicing_kwargs(True)
    # mid-sentence slot: its own leading space, no trailing one
    assert on["view_say_line"].startswith(" ") and not on["view_say_line"].endswith(" ")
    assert '"view_id"' in on["view_say_line"]
    # the law: the view rides a reach that already stands on its own
    assert "Never reach just to voice a view" in on["view_say_line"]
    assert on["view_id_field"].startswith('"view_id": "<') and on["view_id_field"].endswith('>", ')


# ── the say rides view_id through the parse ─────────────────────────────

def test_parse_impulse_keeps_view_id_and_absent_adds_no_key():
    out = present_moment._parse_impulse(
        {"kind": "say", "why": "w", "framing": "f", "view_id": " 7 "})
    assert out["view_id"] == "7"
    out = present_moment._parse_impulse({"kind": "say", "why": "w", "framing": "f"})
    assert "view_id" not in out                      # absent adds NO key (meta purity)
    out = present_moment._parse_impulse(
        {"kind": "say", "why": "w", "framing": "f", "view_id": "x" * 100})
    assert len(out["view_id"]) == 64
    out = present_moment._parse_impulse(
        {"kind": "say", "why": "w", "framing": "f", "view_id": 7})
    assert "view_id" not in out                      # strings only, like regret_id


# ── the templates carry the slots, beside the regret pair ───────────────

def test_templates_carry_the_slots():
    for name in ("present_moment_appraisal_arbiter", "present_moment_appraisal_wanting"):
        from ai.prompt_loader import prompt_loader
        text = prompt_loader.load_prompt(name)
        assert "{view_say_line}" in text and "{view_id_field}" in text


def test_slots_sit_beside_the_regret_pair_and_collapse_to_zero_bytes():
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        text = (Path("prompts") / name).read_text(encoding="utf-8")
        assert text.count("{view_say_line}") == 1, name
        assert text.count("{view_id_field}") == 1, name
        # the prose slot follows the regret slot and precedes the same
        # sentence: all "" leave the flow byte-identical (organ 4.9's small
        # truths added a third sibling after this one)
        assert "{regret_say_line}{view_say_line}{disclosure_say_line} When they arrive" in text, name
        # the field slot follows the regret field, still inside the JSON grammar
        assert '{regret_id_field}{view_id_field}{disclosure_id_field}"held_before"' in text, name
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert "{view_" not in (Path("prompts") / name).read_text(encoding="utf-8"), name


# ── the gather offer: on AND a live view, health key only when it ran ───

def _fake_open_stakes(rows):
    async def fake(*, limit=8, brain_client=None):
        return list(rows)
    return fake


def _gather():
    return asyncio.run(present_moment_gather.gather_moment_inputs(owner_user_id=1))


def test_gather_offers_view_voicing_only_when_on_and_a_live_view_is_open():
    live, muted = _view(), _view(muted=123.0)
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.opinions_enabled", return_value=True), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([live])):
        out = _gather()
    assert out.view_voicing is True
    assert out.sense_health.get("view_voicing") == "ok"
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.opinions_enabled", return_value=True), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([muted])):
        out = _gather()
    assert out.view_voicing is False                 # held quietly: never on the board
    assert out.sense_health.get("view_voicing") == "ok"
    with patch("ai.stake_ledger.ledger_enabled", return_value=True), \
         patch("ai.stake_ledger.opinions_enabled", return_value=False), \
         patch("ai.stake_ledger.open_stakes", _fake_open_stakes([live])):
        out = _gather()
    assert out.view_voicing is False
    assert "view_voicing" not in out.sense_health     # flag off: the sense never ran


def test_default_gather_inputs_carry_the_flag_off():
    assert present_moment_gather.GatherInputs().view_voicing is False
