"""Grounded Vulnerability (organ 4.9) — the book. Store laws: receipts
required, decline-at-cap, said-once-offering, honest fade, nothing deleted."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai import disclosure_store as ds
from core.settings_schema import CompanionConfig


def _plant(root, text="I lost a thread I'd been carrying — just gone.",
           source="monologue", dates="08-26..08-28"):
    return ds.plant(text=text, referent={"source": source, "dates": dates}, root=root)


def test_no_receipt_no_entry(tmp_path: Path):
    assert ds.plant(text="an unfounded feeling", referent={"source": "", "dates": ""},
                    root=tmp_path) is None
    assert ds.plant(text="", referent={"source": "journal", "dates": "08-29"},
                    root=tmp_path) is None
    assert not (tmp_path / "disclosures.jsonl").exists()


def test_half_a_receipt_is_no_receipt(tmp_path: Path):
    """The central law refuses EITHER half missing — a source with no dates
    is the realistic harvest failure, and whitespace is not a receipt."""
    assert ds.plant(text="a truth", referent={"source": "journal", "dates": ""},
                    root=tmp_path) is None
    assert ds.plant(text="a truth", referent={"source": "", "dates": "08-29"},
                    root=tmp_path) is None
    assert ds.plant(text="a truth", referent={"source": "journal", "dates": "   "},
                    root=tmp_path) is None
    assert ds.plant(text="a truth", referent={"source": "  ", "dates": "08-29"},
                    root=tmp_path) is None
    assert ds.plant(text="   ", referent={"source": "journal", "dates": "08-29"},
                    root=tmp_path) is None
    assert not (tmp_path / "disclosures.jsonl").exists()


def test_schema_defaults_off_with_sane_knobs():
    """The knobs the readers getattr() for must exist on the model — a
    rename or a deletion degrades silently to _DEFAULT_* forever."""
    c = CompanionConfig()
    assert c.vulnerability is False
    assert c.disclosures_max_open == 4 and c.disclosure_half_life_days == 14
    # ge=1: 0 would be a second off-switch wearing a budget's clothes.
    with pytest.raises(ValidationError):
        CompanionConfig(disclosures_max_open=0)
    with pytest.raises(ValidationError):
        CompanionConfig(disclosure_half_life_days=0)


def test_plant_and_fold_carry_the_receipt(tmp_path: Path):
    d = _plant(tmp_path)
    assert d is not None and d.referent == {"source": "monologue", "dates": "08-26..08-28"}
    loaded = ds.recent_disclosures(root=tmp_path)
    assert loaded and loaded[0].referent["source"] == "monologue"
    assert loaded[0].said_at is None and loaded[0].faded_at is None


def test_cap_declines_never_queues(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ds, "_max_open", lambda: 2)
    assert _plant(tmp_path, text="one") and _plant(tmp_path, text="two")
    assert _plant(tmp_path, text="three") is None
    assert len(ds.unsaid_disclosures(root=tmp_path)) == 2


def test_said_once_offering(tmp_path: Path):
    d = _plant(tmp_path)
    assert ds.mark_said(d.id, via="reply", root=tmp_path) is True
    assert ds.mark_said(d.id, via="say", root=tmp_path) is False  # said stays said
    assert ds.unsaid_disclosures(root=tmp_path) == []
    kept = ds.recent_disclosures(root=tmp_path)[0]
    assert kept.said_via == "reply" and kept.said_at is not None


def test_half_life_fades_unsaid_only(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ds, "_half_life_days", lambda: 2)   # NON-default on purpose
    old = _plant(tmp_path, text="an old one")
    said = _plant(tmp_path, text="a said one")
    ds.mark_said(said.id, via="reply", root=tmp_path)
    future = time.time() + 3 * 86400
    assert ds.fade_stale(now=future, root=tmp_path) == 1
    rows = {d.id: d for d in ds.recent_disclosures(root=tmp_path)}
    assert rows[old.id].faded_at is not None and "faded unsaid" in rows[old.id].fade_note
    assert rows[said.id].faded_at is None            # said is history, not stale
    assert ds.mark_said(old.id, via="reply", root=tmp_path) is False  # faded ≠ offerable


def test_half_life_knob_is_load_bearing_both_ways(tmp_path: Path, monkeypatch):
    """The owner's knob sets the window, not a hardcoded 14: inside it a
    candidate survives, past it the same candidate fades, and widening the
    knob keeps it."""
    monkeypatch.setattr(ds, "_half_life_days", lambda: 2)
    d = _plant(tmp_path, text="one small true thing")
    t0 = d.harvested_at
    assert ds.fade_stale(now=t0 + 1 * 86400, root=tmp_path) == 0   # inside the window
    monkeypatch.setattr(ds, "_half_life_days", lambda: 30)
    assert ds.fade_stale(now=t0 + 3 * 86400, root=tmp_path) == 0   # the knob widens it
    monkeypatch.setattr(ds, "_half_life_days", lambda: 2)
    assert ds.fade_stale(now=t0 + 3 * 86400, root=tmp_path) == 1   # past it, it fades
    assert ds.unsaid_disclosures(root=tmp_path) == []


def test_gate_fails_closed(monkeypatch):
    """Routed through vulnerability_enabled_impl, not the module attribute:
    the suite-wide _vulnerability_off_by_default guard (tests/conftest.py)
    replaces ai.disclosure_store.vulnerability_enabled with `lambda: False`
    for EVERY test, so asserting on the attribute proves the fixture works
    and nothing about the gate. Flipping the gate to fail OPEN
    (`comp is None or ...`) left the whole suite green until this alias —
    the passions/opinions pattern, learned the same way."""
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert ds.vulnerability_enabled_impl() is False
    assert ds._max_open() == 4 and ds._half_life_days() == 14

    # ...and unbooted settings are not the only closed door: a raising
    # snapshot and a settings object with no such knob are both OFF.
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: (_ for _ in ()).throw(RuntimeError("not booted")))
    assert ds.vulnerability_enabled_impl() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace())
    assert ds.vulnerability_enabled_impl() is False


def test_fold_survives_malformed_rows(tmp_path: Path):
    """Corruption must never impersonate an honest empty book: a bad row is
    skipped and the good rows on BOTH sides of it survive the fold — the
    store owns this, not six future callers."""
    (tmp_path / "disclosures.jsonl").write_text(
        '{"at": 1000.0, "op": "plant", "id": "row-a", "text": "truth A",'
        ' "referent": {"source": "monologue", "dates": "08-26"}}\n'
        '"a bare string row"\n'
        '[1, 2, 3]\n'
        '{"at": 1001.0, "op": "plant", "text": "a row with no id",'
        ' "referent": {"source": "journal", "dates": "08-27"}}\n'
        '{"at": "nope", "op": "plant", "id": "row-c", "text": "a row with a bad at",'
        ' "referent": {"source": "journal", "dates": "08-27"}}\n'
        '{"at": 1002.0, "op": "plant", "id": "row-d", "text": "   ",'
        ' "referent": {"source": "journal", "dates": "08-27"}}\n'
        # No receipt, no entry — the law holds at the fold, not just at plant.
        '{"at": 1002.1, "op": "plant", "id": "row-e", "text": "truth E",'
        ' "referent": "journal"}\n'
        '{"at": 1002.2, "op": "plant", "id": "row-f", "text": "truth F",'
        ' "referent": {}}\n'
        '{"at": 1002.3, "op": "plant", "id": "row-g", "text": "truth G"}\n'
        # A HALF receipt is no receipt — the same law plant() enforces.
        '{"at": 1002.4, "op": "plant", "id": "row-h", "text": "truth H",'
        ' "referent": {"source": "journal", "dates": ""}}\n'
        '{"at": 1002.5, "op": "plant", "id": "row-i", "text": "truth I",'
        ' "referent": {"source": "", "dates": "08-27"}}\n'
        '{"at": 1002.6, "op": "plant", "id": "row-j", "text": "truth J",'
        ' "referent": {"source": "journal", "dates": "   "}}\n'
        '{"at": 1003.0, "op": "plant", "id": "row-b", "text": "truth B",'
        ' "referent": {"source": "journal", "dates": "08-28"}}\n',
        encoding="utf-8",
    )
    rows = ds.unsaid_disclosures(root=tmp_path)
    assert [d.id for d in rows] == ["row-a", "row-b"]
    assert [d.text for d in rows] == ["truth A", "truth B"]


def test_fold_survives_a_bad_line(tmp_path: Path):
    _plant(tmp_path)
    with (tmp_path / "disclosures.jsonl").open("a") as fh:
        fh.write("not json\n")
    assert len(ds.recent_disclosures(root=tmp_path)) == 1
