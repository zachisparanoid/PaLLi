"""scripts/callbacks_peek.py — his audit surface on the book (spec §5):
read-only, receipts IN FULL (who, when, the line, the source), third
person, works flag-off and says so, exits 0 on a missing book."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import ai.callbacks_store as cs

# The line CARRIES the bit on purpose: since the C1 rail (task 3 fix 2) the
# store refuses — at plant AND at fold — a receipt whose line does not
# contain its own bit, so a fixture line that merely looks plausible would
# file nothing and every assertion below would pass on an empty book.
R = {"who": "him", "when": "2026-03-14T02:11:09+00:00",
     "line": "toss it in the oubliette with the other steppers", "source": "discord_42.jsonl:412"}


def _peek():
    spec = importlib.util.spec_from_file_location("callbacks_peek", Path("scripts/callbacks_peek.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(tmp_path, monkeypatch, capsys, *, flag=False) -> str:
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: flag)
    monkeypatch.setattr(sys, "argv", ["callbacks_peek.py"])
    assert _peek().main() == 0
    return capsys.readouterr().out


def test_missing_book_is_honest_and_touches_nothing(tmp_path, monkeypatch, capsys):
    out = _run(tmp_path, monkeypatch, capsys)
    assert "holds nothing" in out and "no book on disk" in out
    assert "companion.idiolect isn't armed" in out
    assert not (tmp_path / "callbacks.jsonl").exists()


def test_open_shows_the_receipt_in_full_third_person(tmp_path, monkeypatch, capsys):
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him", receipt=R, root=tmp_path)
    out = _run(tmp_path, monkeypatch, capsys, flag=True)
    assert "OPEN" in out and c.id in out and "the oubliette" in out and "(nickname, the owner's)" in out
    assert "they said it, 2026-03-14T02:11:09+00:00" in out
    assert '"toss it in the oubliette with the other steppers"' in out
    assert "discord_42.jsonl:412" in out
    assert "isn't armed" not in out


def test_retired_shows_his_words_and_is_never_deleted(tmp_path, monkeypatch, capsys):
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him", receipt=R, root=tmp_path)
    cs.retire(c.id, reason="I never said that", root=tmp_path)
    out = _run(tmp_path, monkeypatch, capsys, flag=True)
    assert "RETIRED" in out and c.id in out and "retired" in out
    assert '"I never said that"' in out
    assert "0 open, 1 retired" in out


def test_dark_with_a_book_says_so(tmp_path, monkeypatch, capsys):
    cs.plant(bit="the oubliette", kind="nickname", coined_by="him", receipt=R, root=tmp_path)
    out = _run(tmp_path, monkeypatch, capsys, flag=False)
    assert "the book below is still theirs" in out and "the oubliette" in out


def test_one_absurd_timestamp_never_takes_down_his_audit_tool(tmp_path, monkeypatch, capsys):
    """The twin's law (tests/scripts/test_disclosures_peek.py), and this
    organ's own at one level down (`_born_day`, task 4 fix 1): a nonsense
    stamp costs a DATE, never the read. This is the surface he reaches for
    WHEN the book looks wrong, so a traceback here would hide exactly what
    he came to see — and a half-printed book is the worse failure, because
    it reads as a short book."""
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him", receipt=R, root=tmp_path)
    # A clock jump / corrupt append on a RETIRE row: JSON-valid, numerically
    # absurd, and kept by the fold (inf and 1e30 are both numeric).
    with (tmp_path / "callbacks.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": 1e30, "op": "retire", "id": "%s", "reason": "I never said that"}\n' % c.id)
    out = _run(tmp_path, monkeypatch, capsys, flag=True)
    assert "retired sometime" in out                 # honest, not a crash
    assert '"I never said that"' in out
    assert "toss it in the oubliette with the other steppers" in out   # reached the receipt


def test_the_peek_imports_readers_only():
    """House law, proven two ways — the text and the namespace. A peek
    calls no writer and nothing that ensures or repairs state."""
    src = Path("scripts/callbacks_peek.py").read_text(encoding="utf-8")
    assert "plant" not in src.replace("planted", "") and "retire(" not in src
    for forbidden in ("_append_event", "mkdir", "open("):
        assert forbidden not in src
    mod = _peek()
    for writer in ("plant", "retire", "_append_event"):
        assert not hasattr(mod, writer)
