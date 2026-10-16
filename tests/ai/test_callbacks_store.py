"""Organ 4.12 (provenance half) — her book of callbacks (spec §1).

No receipt, no entry — at plant AND at fold, value-deep. Dedup lives in the
store (three writers). Nothing is ever deleted; the only transition is
open → retired, by his word. No caps, no fades (the quota half is dropped)."""
from __future__ import annotations

import json
import logging
import time
import unicodedata
from pathlib import Path

import pytest

import ai.callbacks_store as cs
from core.settings_schema import CompanionConfig

RECEIPT = {"who": "him", "when": "2026-03-14T02:11:09+00:00",
           "line": "just toss it in the oubliette with the other steppers",
           "source": "discord_1.jsonl:412"}


def _plant(root, bit="the oubliette", **over):
    """The receipt line is BUILT from the bit: since the C1 fix round `plant`
    refuses a receipt whose line does not contain the bit, so a helper that
    pinned one fixed line could only ever plant one bit."""
    receipt = dict(RECEIPT)
    receipt["line"] = f"just toss it in the pile with {bit} and the other steppers"
    kw = dict(bit=bit, kind="nickname", coined_by="him", receipt=receipt, root=root)
    kw.update(over)
    return cs.plant(**kw)


# ── the schema ──────────────────────────────────────────────────────────

def test_schema_defaults_off_with_a_sane_knob():
    c = CompanionConfig()
    assert c.idiolect is False
    assert c.callbacks_band_max == 12
    with pytest.raises(Exception):
        CompanionConfig(callbacks_band_max=0)


def test_the_knob_is_wired_to_its_schema_name(monkeypatch):
    comp = CompanionConfig(idiolect=True, callbacks_band_max=20)
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: comp)
    assert cs._band_max() == 20


# ── the gate: fails closed, and can turn on ─────────────────────────────

def test_gate_fails_closed_and_is_not_stuck_closed(monkeypatch):
    """Routed through the import-time capture: the suite pins
    `idiolect_enabled` OFF for every test, so asserting on the module
    attribute would assert on the fixture."""
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert cs.idiolect_enabled_impl() is False

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr("core.turn.companion_flags.companion_settings", boom)
    assert cs.idiolect_enabled_impl() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(idiolect=True))
    assert cs.idiolect_enabled_impl() is True


# ── normalize: the one comparison every rail shares ─────────────────────

def test_normalize_folds_case_punctuation_and_whitespace():
    assert cs.normalize("  The  OUBLIETTE, again! ") == "the oubliette again"
    assert cs.normalize("") == ""
    # ...and it takes out PUNCTUATION only. A rail that stripped everything
    # non-Latin folded "café" to "caf" and "🍍" to "" — and an empty key
    # short-circuits find_open_by_bit, so those bits could never dedup and
    # task 3's phrase search could never find them (review I1).
    assert cs.normalize("Café!") == "café"
    assert cs.normalize("🍍") == "🍍"
    assert cs.normalize("  日本語。 ") == "日本語"
    assert cs.normalize("pine-apple") == "pine apple"


# ── plant: the receipts rail ────────────────────────────────────────────

def test_plant_writes_a_receipted_entry(tmp_path: Path):
    c = _plant(tmp_path)
    assert c is not None and c.bit == "the oubliette" and c.kind == "nickname"
    assert c.coined_by == "him" and c.receipt["who"] == "him"
    assert c.receipt["line"].startswith("just toss it")
    assert c.retired_at is None
    rows = [json.loads(l) for l in (tmp_path / "callbacks.jsonl").read_text().splitlines()]
    assert rows[0]["op"] == "plant" and rows[0]["receipt"]["when"] == RECEIPT["when"]


def test_plant_refuses_a_blank_bit_or_an_unknown_kind_or_coiner(tmp_path: Path):
    assert _plant(tmp_path, bit="   ") is None
    assert _plant(tmp_path, kind="joke") is None
    assert _plant(tmp_path, coined_by="them") is None
    assert not (tmp_path / "callbacks.jsonl").exists()


@pytest.mark.parametrize("missing", ["who", "when", "line"])
def test_plant_refuses_a_half_receipt(tmp_path: Path, missing):
    r = dict(RECEIPT); r[missing] = "   "
    assert _plant(tmp_path, receipt=r) is None
    r = dict(RECEIPT); del r[missing]
    assert _plant(tmp_path, receipt=r) is None
    assert not (tmp_path / "callbacks.jsonl").exists()


def test_plant_refuses_a_receipt_whose_who_or_when_is_not_real(tmp_path: Path):
    r = dict(RECEIPT); r["who"] = "someone"
    assert _plant(tmp_path, receipt=r) is None
    r = dict(RECEIPT); r["when"] = "last march"
    assert _plant(tmp_path, receipt=r) is None
    r = dict(RECEIPT); r["when"] = "2026-03-14T02:11:09Z"      # Z is fine
    assert _plant(tmp_path, receipt=r) is not None


def test_plant_refuses_a_duplicate_of_an_open_bit_normalized(tmp_path: Path):
    assert _plant(tmp_path, bit="The Oubliette") is not None
    assert _plant(tmp_path, bit="the oubliette!") is None
    assert len(cs.open_callbacks(root=tmp_path)) == 1


def test_dedup_reaches_a_bit_that_is_not_latin(tmp_path: Path):
    """The point of I1: the second plant must be REFUSED, not filed twice."""
    assert _plant(tmp_path, bit="🍍") is not None
    assert _plant(tmp_path, bit="🍍") is None
    assert _plant(tmp_path, bit="café") is not None
    assert _plant(tmp_path, bit="CAFÉ.") is None
    assert [c.bit for c in cs.open_callbacks(root=tmp_path)] == ["🍍", "café"]


def test_one_word_typed_two_ways_is_one_bit(tmp_path: Path):
    """NFC is the FIRST step of normalize: an e with a combining acute and a
    precomposed e-acute are different bytes and the same word. Without the
    fold her book files them as two bits, and task 3's phrase search — which
    reads this same rail — could never find the other one."""
    nfc, nfd = "caf\u00e9", "cafe\u0301"
    assert nfc != nfd                       # genuinely different on the wire
    assert cs.normalize(nfd) == cs.normalize(nfc) == nfc
    assert _plant(tmp_path, bit=nfc) is not None
    assert _plant(tmp_path, bit=nfd) is None
    assert len(cs.open_callbacks(root=tmp_path)) == 1


def test_normalize_drops_invisible_characters_but_keeps_the_zwj():
    """Format characters (category Cf) are noise she never typed on purpose:
    a stray ZWSP, BOM or LRM must not be able to split one bit into two
    rows. They read as a SEPARATOR — the same call as punctuation.

    The ZWJ (U+200D) is the exception: it is STRUCTURE inside an emoji
    cluster, and dropping it shatters one family into three people."""
    assert cs.normalize("the\u200boubliette") == "the oubliette"
    assert cs.normalize("\ufeffthe oubliette\u200e") == "the oubliette"
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert cs.normalize(family) == family


def test_an_invisible_character_cannot_split_one_bit_in_two(tmp_path: Path):
    assert _plant(tmp_path, bit="the oubliette") is not None
    assert _plant(tmp_path, bit="the\u200boubliette") is None
    assert len(cs.open_callbacks(root=tmp_path)) == 1


def test_the_cap_falls_after_the_fold(tmp_path: Path):
    """_clean composes BEFORE it collapses and cuts. A decomposed bit is
    longer in codepoints, so a cap applied first truncates it at a
    different word — and files it as a second bit."""
    lone = _plant(tmp_path, bit="cafe\u0301")
    assert lone is not None and lone.bit == "caf\u00e9"       # stored composed
    assert unicodedata.is_normalized("NFC", lone.bit)
    nfc, nfd = "caf\u00e9 " * 16, "cafe\u0301 " * 16          # 79 vs 95 raw chars
    assert _plant(tmp_path, bit=nfc) is not None
    assert _plant(tmp_path, bit=nfd) is None
    assert len(cs.open_callbacks(root=tmp_path)) == 2


def test_plant_refuses_a_bit_with_no_word_in_it(tmp_path: Path):
    """Punctuation-only has nothing to dedup ON — its normalized form is the
    empty key every other bit's lookup short-circuits on."""
    assert _plant(tmp_path, bit="!!!") is None
    assert _plant(tmp_path, bit="— …") is None
    assert not (tmp_path / "callbacks.jsonl").exists()


def test_plant_refuses_a_line_that_does_not_contain_the_bit(tmp_path: Path, caplog):
    """C1, the universal rail: a receipt IS the line the bit was born in, so
    the bit has to be IN it. Task 2 put this at reflect's door; task 3 opened
    a second door without it, and the lookup's 240-char cap could cut the bit
    out of its own proof. It belongs at the store, where all three writers
    pass — "three writers, one rail"."""
    r = dict(RECEIPT); r["line"] = "we walked the whole thing again"
    with caplog.at_level(logging.INFO, logger="ai.callbacks_store"):
        assert cs.plant(bit="the oubliette", kind="bit", coined_by="him",
                        receipt=r, root=tmp_path) is None
    assert "the line does not contain the bit" in caplog.text
    assert not (tmp_path / "callbacks.jsonl").exists()


def test_the_bit_in_line_rail_is_normalized_like_every_other_comparison(tmp_path: Path):
    r = dict(RECEIPT); r["line"] = "ok — toss it in The OUBLIETTE, then"
    assert cs.plant(bit="the oubliette", kind="bit", coined_by="him",
                    receipt=r, root=tmp_path) is not None


def test_a_retired_bit_may_be_born_again(tmp_path: Path):
    c = _plant(tmp_path)
    assert cs.retire(c.id, reason="I never called it that", root=tmp_path)
    again = _plant(tmp_path)
    assert again is not None and again.id != c.id


def test_plant_caps_the_bit_and_the_line(tmp_path: Path):
    # The line is y's too: the capped bit must survive inside the capped
    # line or the C1 rail refuses the plant before either cap is observable.
    r = dict(RECEIPT); r["line"] = "y" * 1000
    c = _plant(tmp_path, bit="y" * 500, receipt=r)
    assert len(c.bit) == cs.BIT_CAP and len(c.receipt["line"]) == cs.LINE_CAP


def test_no_cap_on_the_book(tmp_path: Path):
    for i in range(30):
        assert _plant(tmp_path, bit=f"bit number {i}") is not None
    assert len(cs.open_callbacks(root=tmp_path)) == 30


# ── retire: his word, never a deletion ─────────────────────────────────

def test_retire_marks_and_keeps(tmp_path: Path):
    c = _plant(tmp_path)
    assert cs.retire(c.id, reason="that's not ours", root=tmp_path) is True
    assert cs.open_callbacks(root=tmp_path) == []
    kept = cs.find_callback(c.id, root=tmp_path)
    assert kept is not None and kept.retired_at is not None
    assert kept.retired_reason == "that's not ours"
    assert [x.id for x in cs.all_callbacks(root=tmp_path)] == [c.id]


def test_retire_refuses_unknown_and_twice(tmp_path: Path):
    c = _plant(tmp_path)
    assert cs.retire("nope-000000", reason="x", root=tmp_path) is False
    assert cs.retire(c.id, reason="x", root=tmp_path) is True
    assert cs.retire(c.id, reason="again", root=tmp_path) is False
    assert cs.find_callback(c.id, root=tmp_path).retired_reason == "x"


def test_retire_refuses_a_blank_reason(tmp_path: Path):
    c = _plant(tmp_path)
    assert cs.retire(c.id, reason="   ", root=tmp_path) is False


# ── readers ─────────────────────────────────────────────────────────────

def test_open_is_oldest_first_and_recent_is_newest_first(tmp_path: Path, monkeypatch):
    t = [1000.0]

    def clock():
        t[0] += 10.0
        return t[0]

    monkeypatch.setattr(cs.time, "time", clock)
    for b in ("one", "two", "three"):
        _plant(tmp_path, bit=b)
    assert [c.bit for c in cs.open_callbacks(root=tmp_path)] == ["one", "two", "three"]
    assert [c.bit for c in cs.recent_callbacks(limit=2, root=tmp_path)] == ["three", "two"]


def test_find_open_by_bit_is_normalized(tmp_path: Path):
    c = _plant(tmp_path)
    assert cs.find_open_by_bit("THE oubliette.", root=tmp_path).id == c.id
    assert cs.find_open_by_bit("the boneyard", root=tmp_path) is None


# ── the fold: survives bad rows, and the receipts law holds there too ───

def test_fold_survives_malformed_rows(tmp_path: Path):
    good = _plant(tmp_path, bit="row a")
    p = tmp_path / "callbacks.jsonl"
    bad = [
        '"a bare string"',
        '[1, 2, 3]',
        json.dumps({"op": "plant", "bit": "no id", "kind": "bit", "coined_by": "him",
                    "receipt": RECEIPT, "at": 1.0}),
        json.dumps({"op": "plant", "id": "no-bit-1", "bit": "  ", "kind": "bit",
                    "coined_by": "him", "receipt": RECEIPT, "at": 1.0}),
        json.dumps({"op": "plant", "id": "bad-at-1", "bit": "x", "kind": "bit",
                    "coined_by": "him", "receipt": RECEIPT, "at": "soon"}),
        json.dumps({"op": "plant", "id": "str-ref-1", "bit": "x", "kind": "bit",
                    "coined_by": "him", "receipt": "journal", "at": 1.0}),
        json.dumps({"op": "plant", "id": "half-1", "bit": "x", "kind": "bit",
                    "coined_by": "him", "receipt": {**RECEIPT, "line": ""}, "at": 1.0}),
        json.dumps({"op": "plant", "id": "when-1", "bit": "x", "kind": "bit",
                    "coined_by": "him", "receipt": {**RECEIPT, "when": "march"}, "at": 1.0}),
        json.dumps({"op": "plant", "id": "kind-1", "bit": "x", "kind": "pun",
                    "coined_by": "him", "receipt": RECEIPT, "at": 1.0}),
        json.dumps({"op": "plant", "id": "coiner-1", "bit": "x", "kind": "bit",
                    "coined_by": "them", "receipt": RECEIPT, "at": 1.0}),
        # The C1 rail is value-deep at BOTH ends: a row whose receipt line
        # does not carry its bit is malformed, and the fold is the other
        # half of the promise plant already keeps.
        json.dumps({"op": "plant", "id": "bit-not-in-line-1",
                    "bit": "the pineapple protocol", "kind": "bit",
                    "coined_by": "him", "receipt": RECEIPT, "at": 1.0}),
        json.dumps({"op": "retire", "id": "ghost-1", "reason": "x", "at": 2.0}),
    ]
    with p.open("a", encoding="utf-8") as fh:
        for row in bad:
            fh.write(row + "\n")
    good_b = _plant(tmp_path, bit="row b")
    assert [c.id for c in cs.open_callbacks(root=tmp_path)] == [good.id, good_b.id]


def test_fold_of_a_missing_book_is_empty(tmp_path: Path):
    assert cs.load_callbacks(root=tmp_path) == {}
    assert cs.open_callbacks(root=tmp_path) == []


# ── the env seam ────────────────────────────────────────────────────────

def test_callbacks_root_env_redirects_every_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "elsewhere"))
    c = cs.plant(bit="the oubliette", kind="bit", coined_by="both", receipt=dict(RECEIPT))
    assert c is not None
    assert (tmp_path / "elsewhere" / "callbacks.jsonl").exists()
    assert cs.open_callbacks()[0].id == c.id
