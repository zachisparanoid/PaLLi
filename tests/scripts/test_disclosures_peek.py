"""scripts/disclosures_peek.py — the owner's audit surface on her book of
small truths (organ 4.9, spec §6/§8).

Read-only by construction: the peek folds the append-only jsonl and calls
no writer, so running it on a missing book must leave the disk exactly as
it found it and still exit 0 (the empty book is honest). Receipts render
here in full — this is the surface "receipt drift" (§7.3) is checked on."""
from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path

import ai.disclosure_store as ds

_SOURCE = "night reflections"
_DATES = "08-26..08-28"
FAR_FUTURE = time.time() + 400 * 86400   # past any sane half-life knob


def _peek():
    spec = importlib.util.spec_from_file_location(
        "disclosures_peek", Path("scripts/disclosures_peek.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant(root, text, *, source=_SOURCE, dates=_DATES):
    d = ds.plant(text=text, referent={"source": source, "dates": dates}, root=root)
    assert d is not None
    return d


def _book(root):
    """One book with all three states — planted through the real writers so
    the peek is pinned against the store's actual event grammar."""
    said = _plant(root, "a restart is walking into a room mid-sentence")
    assert ds.mark_said(said.id, via="reply", root=root)
    faded = _plant(root, "I kept re-reading the line about the workshop")
    assert ds.fade_stale(now=FAR_FUTURE, root=root) == 1
    unsaid = _plant(root, "the quiet after he logs off has a texture",
                    source="monologue", dates="08-29, 08-30")
    return said, faded, unsaid


def _run(tmp_path, monkeypatch, capsys, *, all_=False, flag=False) -> str:
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: flag)
    monkeypatch.setattr(sys, "argv",
                        ["disclosures_peek.py"] + (["--all"] if all_ else []))
    assert _peek().main() == 0
    return capsys.readouterr().out


def _section(out: str, header: str) -> str:
    rest = out.split(header)[1]
    return rest.split("\n\n")[0]


def test_an_unsaid_truth_shows_its_receipt_and_how_long_she_has_held_it(
        tmp_path, monkeypatch, capsys):
    _, _, unsaid = _book(tmp_path)
    out = _run(tmp_path, monkeypatch, capsys)
    block = _section(out, "UNSAID")
    assert unsaid.id in block
    assert "the quiet after he logs off has a texture" in block
    assert "— from her monologue, 08-29, 08-30" in block
    held = next(l for l in block.splitlines() if unsaid.id in l)
    assert re.search(r"held \d+[mhd]$", held.strip())


def test_the_age_reads_in_days_once_a_truth_is_old():
    mod = _peek()
    assert mod._age(time.time() - 3 * 86400) == "3d"
    assert mod._age(time.time() - 5 * 3600) == "5h"


def test_a_shared_truth_carries_the_day_and_the_door(tmp_path, monkeypatch, capsys):
    said, _, unsaid = _book(tmp_path)
    out = _run(tmp_path, monkeypatch, capsys)
    day = time.strftime("%m-%d")
    shared = _section(out, "SHARED")
    assert f"[shared {day} via reply]" in shared
    assert "a restart is walking into a room mid-sentence" in shared
    assert f"— from her {_SOURCE}, {_DATES}" in shared
    # Said once, forever: it left the offer sections and never comes back.
    assert said.id not in _section(out, "UNSAID")
    assert unsaid.id not in shared


def test_the_counts_line_names_the_book_and_its_three_states(
        tmp_path, monkeypatch, capsys):
    _book(tmp_path)
    out = _run(tmp_path, monkeypatch, capsys)
    assert str(tmp_path / "disclosures.jsonl") in out
    assert "1 unsaid, 1 shared, 1 faded" in out


def test_faded_truths_wait_for_all(tmp_path, monkeypatch, capsys):
    _, faded, _ = _book(tmp_path)
    quiet = _run(tmp_path, monkeypatch, capsys)
    assert "I kept re-reading the line about the workshop" not in quiet
    assert "+1 faded" in quiet and "--all" in quiet

    full = _run(tmp_path, monkeypatch, capsys, all_=True)
    block = _section(full, "FADED")
    assert faded.id in block
    assert "I kept re-reading the line about the workshop" in block
    assert f"— from her {_SOURCE}, {_DATES}" in block
    assert "last week's truth isn't tonight's" in block   # her fade note
    assert time.strftime("%m-%d", time.localtime(FAR_FUTURE)) in block


def test_the_empty_book_is_honest_and_exits_zero(tmp_path, monkeypatch, capsys):
    out = _run(tmp_path, monkeypatch, capsys)
    assert "holds nothing" in out
    assert "honest" in out
    assert "UNSAID" not in out
    # Dark AND empty is the expected pre-restart state, not a defect — and
    # the line can't promise a book below when there is none.
    assert "the organ is off" in out and "harvest" in out
    assert "below" not in out

    armed = _run(tmp_path, monkeypatch, capsys, flag=True)
    assert "holds nothing" in armed and "the organ is off" not in armed


def test_the_peek_says_when_the_organ_is_dark_but_still_reads_the_book(
        tmp_path, monkeypatch, capsys):
    """His audit tool, not her organ: it reads the book whether or not
    companion.vulnerability is armed — and says which."""
    _book(tmp_path)
    dark = _run(tmp_path, monkeypatch, capsys, flag=False)
    assert "the organ is off" in dark
    assert "the quiet after he logs off has a texture" in dark   # still read

    armed = _run(tmp_path, monkeypatch, capsys, flag=True)
    assert "the organ is off" not in armed
    assert "the quiet after he logs off has a texture" in armed


def test_the_peek_never_writes_and_never_creates_the_book(
        tmp_path, monkeypatch, capsys):
    """House law: a peek calls no writer and no reader that ensures or
    repairs state. Proven three ways — the disk, the namespace, the text."""
    root = tmp_path / "absent"
    monkeypatch.setenv("DISCLOSURES_ROOT", str(root))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(sys, "argv", ["disclosures_peek.py", "--all"])
    mod = _peek()
    assert mod.main() == 0
    capsys.readouterr()
    assert not root.exists()                      # no mkdir, no touched book

    _book(tmp_path)
    path = tmp_path / "disclosures.jsonl"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    _run(tmp_path, monkeypatch, capsys, all_=True, flag=True)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before

    for writer in ("plant", "mark_said", "fade_stale", "_append_event"):
        assert not hasattr(mod, writer)
    text = Path("scripts/disclosures_peek.py").read_text(encoding="utf-8")
    for forbidden in ("plant(", "mark_said", "fade_stale", "_append_event",
                      "mkdir", "open("):
        assert forbidden not in text


def test_one_absurd_timestamp_never_takes_down_his_audit_tool(
        tmp_path, monkeypatch, capsys):
    """The store's fold survives its own bad rows so a corrupted book can
    never impersonate an honest empty one. The peek is the surface he
    reaches for WHEN something is wrong — a traceback there hides exactly
    the book he came to read, so a nonsense date degrades to a word."""
    _book(tmp_path)
    wild = _plant(tmp_path, "a truth stamped out of time")
    # A clock jump / corrupt append: JSON-valid, numerically absurd, and on
    # a STAMP row — the fold keeps it, so said_at is what has to survive.
    with (tmp_path / "disclosures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": 1e30, "op": "say", "id": "%s", "via": "reply"}\n' % wild.id)
    out = _run(tmp_path, monkeypatch, capsys, all_=True)
    assert "a truth stamped out of time" in out
    assert "the quiet after he logs off has a texture" in out   # the rest survives
    assert "[shared sometime via reply]" in out                 # honest, not a crash


def test_an_infinite_clock_never_leaves_him_a_half_read_book(
        tmp_path, monkeypatch, capsys):
    """Review I-2: `json.loads` accepts the `Infinity` literal and the fold
    KEEPS the row (inf is numeric). _day was guarded; _dur was not, so the
    counts line and the whole UNSAID section printed and THEN it died
    inside SHARED — a book that looks half-read, which is the one thing
    this module's docstring promises not to do."""
    _book(tmp_path)
    wild = _plant(tmp_path, "a truth stamped out of time")
    with (tmp_path / "disclosures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": Infinity, "op": "say", "id": "%s", "via": "say"}\n'
                 % wild.id)
    out = _run(tmp_path, monkeypatch, capsys, all_=True)
    assert "a truth stamped out of time" in out                   # reached
    assert "I kept re-reading the line about the workshop" in out      # and past it
    assert "[shared sometime via say]" in out
    assert "held an unreadable span before she said it" in out


def test_a_stamp_that_precedes_its_harvest_says_so(tmp_path, monkeypatch, capsys):
    """Review M-1: max(0.0, …) turned a NEGATIVE span — a say stamped
    before the harvest it stamps — into a plausible "held 1m". The loud
    direction (an enormous day count) announces corruption; the silent one
    hides it, which is the opposite trade on an audit surface."""
    wild = _plant(tmp_path, "a truth said before it was found")
    with (tmp_path / "disclosures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": %r, "op": "say", "id": "%s", "via": "reply"}\n'
                 % (wild.harvested_at - 5 * 86400, wild.id))
    out = _run(tmp_path, monkeypatch, capsys)
    assert "held an unreadable span before she said it" in out
    assert "held 1m before she said it" not in out


def test_the_duration_helper_is_guarded_like_the_date_helper():
    mod = _peek()
    assert mod._dur(3 * 86400) == "3d" and mod._dur(5 * 3600) == "5h"
    for bad in (float("inf"), float("-inf"), float("nan"), -1.0, -1e30):
        assert mod._dur(bad) == "an unreadable span"
