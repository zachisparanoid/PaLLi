"""scripts/stakes_peek.py — read-only, zero live-path risk; step 25 adds the
voiced mark (`[said to them MM-DD]`) on a voiced regret and must keep reading
a ledger born before the column existed.

Organ 4.13 · Opinions adds the view half: her evidence on the row, the
last-said / held-quietly tells, and the waiting (dormant) views underneath —
this peek is the first tell the record promises him after the restart, so
what it prints has to be the truth about a view."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

_CREATE_OLD = """
CREATE TABLE stakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stake TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', resolution TEXT NOT NULL DEFAULT '',
    horizon_at REAL, source_channel_id TEXT NOT NULL DEFAULT '', last_surfaced_at REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, resolved_at REAL{extra}
)
"""
VOICED_AT = time.time() - 36 * 3600


def _peek():
    spec = importlib.util.spec_from_file_location("stakes_peek", Path("scripts/stakes_peek.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(tmp_path, *, with_column: bool) -> Path:
    path = tmp_path / "peek.sqlite3"
    con = sqlite3.connect(path)
    con.execute(_CREATE_OLD.format(extra=", voiced_at REAL" if with_column else ""))
    now = time.time()
    con.execute("INSERT INTO stakes (kind, stake, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("hope", "the survey lands", "open", now - 2 * 86400, now - 86400))
    con.execute("INSERT INTO stakes (kind, stake, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("regret", "I left the question unasked", "open", now - 2 * 86400, now - 86400))
    if with_column:
        con.execute("INSERT INTO stakes (kind, stake, status, created_at, updated_at, voiced_at) VALUES (?,?,?,?,?,?)",
                    ("regret", "I buried him in settings", "open", now - 3 * 86400, now - 86400, VOICED_AT))
    con.commit()
    con.close()
    return path


def _run(tmp_path, monkeypatch, capsys, *, with_column: bool) -> str:
    monkeypatch.setenv("DATABASE_PATH", str(_db(tmp_path, with_column=with_column)))
    monkeypatch.setattr(sys, "argv", ["stakes_peek.py"])
    assert _peek().main() == 0
    return capsys.readouterr().out


def test_peek_marks_a_voiced_regret_with_the_day_it_was_said(tmp_path, monkeypatch, capsys):
    out = _run(tmp_path, monkeypatch, capsys, with_column=True)
    day = time.strftime("%m-%d", time.localtime(VOICED_AT))
    lines = out.splitlines()
    voiced = next(l for l in lines if "(regret, held 3d)" in l)
    assert voiced.endswith(f"[said to them {day}]")
    unvoiced = next(l for l in lines if "(regret, held 2d)" in l)
    assert "[said to them" not in unvoiced
    hope = next(l for l in lines if "(hope, held 2d)" in l)
    assert hope.strip() == "[1] (hope, held 2d)"           # untouched
    assert out.count("[said to them") == 1


def test_peek_reads_a_ledger_born_before_the_column(tmp_path, monkeypatch, capsys):
    out = _run(tmp_path, monkeypatch, capsys, with_column=False)
    assert "(regret, held 2d)" in out and "[said to them" not in out
    assert "(2 open)" in out
    # The view half has to survive the same ledger: no evidence column, no
    # muted_at column, and no dormant rows — nothing extra may be printed.
    assert "evidence:" not in out
    assert "WAITING" not in out


# ── Organ 4.13 · Opinions — the view half of the peek ────────────────────

_CREATE_VIEWS = """
CREATE TABLE stakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stake TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', resolution TEXT NOT NULL DEFAULT '',
    horizon_at REAL, source_channel_id TEXT NOT NULL DEFAULT '', last_surfaced_at REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, resolved_at REAL,
    voiced_at REAL, evidence TEXT, muted_at REAL
)
"""
MUTED_AT = time.time() - 12 * 3600
LONG_EVIDENCE = "the 1978 service manuals against the forum thread, " * 12  # 588 chars
SETTLED_AT = time.time() - 6 * 3600
SETTLED_STAKE = "the 90s decks were built better"
SETTLED_RESOLUTION = "his 1996 revision sheet — it was the tape path, not the decade"


def _views_db(tmp_path, *, with_dormant: bool = True, with_settled: bool = False) -> Path:
    # A distinct file per build: one test peeks at two ledgers in a row.
    name = f"views-{'dormant' if with_dormant else 'quiet'}{'-settled' if with_settled else ''}"
    path = tmp_path / f"{name}.sqlite3"
    con = sqlite3.connect(path)
    con.execute(_CREATE_VIEWS)
    now = time.time()

    def row(kind, stake, *, days, status="open", evidence=None, voiced=None, muted=None,
            resolved=None, resolution=""):
        con.execute(
            "INSERT INTO stakes (kind, stake, status, created_at, updated_at, evidence, "
            "voiced_at, muted_at, resolved_at, resolution) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, stake, status, now - days * 86400, now - 86400, evidence, voiced, muted,
             resolved, resolution),
        )

    row("hope", "the survey lands", days=2)                                  # [1] control
    row("view", "I side with the 1978 manuals over the forums", days=2,
        evidence="his 07-14 thread, the 08-02 dive")                          # [2] plain
    row("view", "the flat-four wants the colder plug", days=3,
        evidence="two teardowns, 08-11 and 08-19", voiced=VOICED_AT)          # [3] said
    row("view", "rebuilds beat replacements on this bench", days=4,
        evidence="the 08-05 argument he won", voiced=VOICED_AT, muted=MUTED_AT)  # [4] muted
    row("view", "a long one", days=5, evidence=LONG_EVIDENCE)                 # [5] capped
    row("regret", "I left the question unasked", days=6,
        evidence="a stray key that must never render")                        # [6] kind-guard
    if with_dormant:
        row("view", "the forums are right about the 90s decks", days=8,
            status="dormant", evidence="one thread, 08-28")                    # [7] waiting longest
        row("view", "torque beats horsepower for what he does", days=7,
            status="dormant", evidence="his own 08-30 line")                   # [8] waiting
    if with_settled:
        row("view", SETTLED_STAKE, days=9, status="settled",                   # [9] landed
            evidence="his 08-12 teardown", resolved=SETTLED_AT,
            resolution=SETTLED_RESOLUTION)
    con.commit()
    con.close()
    return path


def _run_views(tmp_path, monkeypatch, capsys, *, with_dormant: bool = True,
               with_settled: bool = False) -> str:
    monkeypatch.setenv(
        "DATABASE_PATH",
        str(_views_db(tmp_path, with_dormant=with_dormant, with_settled=with_settled)),
    )
    monkeypatch.setattr(sys, "argv", ["stakes_peek.py"])
    assert _peek().main() == 0
    return capsys.readouterr().out


def _line_after(out: str, needle: str) -> str:
    lines = out.splitlines()
    return lines[next(i for i, l in enumerate(lines) if needle in l) + 1]


def test_peek_shows_a_views_evidence_capped_at_240(tmp_path, monkeypatch, capsys):
    out = _run_views(tmp_path, monkeypatch, capsys)
    assert _line_after(out, "(view, held 2d)").strip() == (
        "I side with the 1978 manuals over the forums — evidence: his 07-14 thread, the 08-02 dive"
    )
    capped = _line_after(out, "(view, held 5d)")
    assert capped.strip() == f"a long one — evidence: {LONG_EVIDENCE[:240]}"
    assert LONG_EVIDENCE not in out                       # the full string never renders
    # Evidence is view-only at the store; the peek is kind-guarded too.
    assert "a stray key that must never render" not in out
    assert _line_after(out, "(regret, held 6d)").strip() == "I left the question unasked"
    # And a non-view row's own line is byte-untouched.
    hope = next(l for l in out.splitlines() if "(hope, held 2d)" in l)
    assert hope.strip() == "[1] (hope, held 2d)"


def test_peek_tells_a_said_view_from_one_he_asked_her_to_drop(tmp_path, monkeypatch, capsys):
    out = _run_views(tmp_path, monkeypatch, capsys)
    said_day = time.strftime("%m-%d", time.localtime(VOICED_AT))
    muted_day = time.strftime("%m-%d", time.localtime(MUTED_AT))
    lines = out.splitlines()

    said = next(l for l in lines if "(view, held 3d)" in l)
    assert said.endswith(f" [said {said_day}]")
    # Muted wins over said — he asked her to leave it, and that outranks the tell.
    muted = next(l for l in lines if "(view, held 4d)" in l)
    assert muted.endswith(f" [held quietly {muted_day}]")
    assert "[said" not in muted
    # An unvoiced, unmuted view carries no marker at all.
    assert next(l for l in lines if "(view, held 2d)" in l).strip() == "[2] (view, held 2d)"
    # A view is LAST-said, not said-once — the regret wording never leaks onto one.
    assert "[said to them" not in out
    assert out.count("[said ") == 1 and out.count("[held quietly ") == 1


def test_peek_lists_the_waiting_views_only_when_some_are_waiting(tmp_path, monkeypatch, capsys):
    out = _run_views(tmp_path, monkeypatch, capsys)
    assert "(6 open)" in out                              # dormant rows are not open
    waiting = out.split("WAITING (dormant views):")[1]
    assert "[7] the forums are right about the 90s decks" in waiting
    assert "[8] torque beats horsepower for what he does" in waiting
    # Oldest first — the longest wait has most earned a slot (dormant_views()).
    assert waiting.index("[7]") < waiting.index("[8]")

    quiet = _run_views(tmp_path, monkeypatch, capsys, with_dormant=False)
    assert "WAITING" not in quiet and "(6 open)" in quiet


def test_peek_shows_a_settled_view_in_how_things_landed(tmp_path, monkeypatch, capsys):
    """Spec §6: `settled` is a view's ONLY terminal — resolve/fade are
    kind-guarded away, and views never die of age. So if the closed query
    doesn't count it, a settled view matches no section of the peek and the
    first settle reads as a DELETION on his audit surface. Nothing in this
    ledger is ever deleted; it lands, with what it landed into."""
    out = _run_views(tmp_path, monkeypatch, capsys, with_settled=True)
    landed = out.split("How things landed")[1]
    header = next(l for l in landed.splitlines() if "[9]" in l).strip()
    assert header.startswith("[9] settled ") and header.endswith("(view)")
    # settle stamps resolved_at, so the existing date rendering already works.
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(SETTLED_AT)) in header
    assert f"was: {SETTLED_STAKE}" in landed
    assert f"now: {SETTLED_RESOLUTION}" in landed          # what it settled into
    # It left the open slate on its way here — settled is not open, not waiting.
    assert "(6 open)" in out
    assert SETTLED_STAKE not in out.split("How things landed")[0]
