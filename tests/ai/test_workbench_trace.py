"""The authorship trace (2026-08-14): "this was you."

The three-doors inversion (2026-08-12/13): her workbench built the
surprise trilogy, the null-delivery wall hid the authorship, her
monologue concluded HE built it, and a wrong-tree self-verify turned that
into a false confession. These tests pin the deterministic other half of
the wall: recent logbook entries rendered as authorship facts, newest
kept under the cap, quiet when there's nothing recent, and reaching both
gathers (the moment's own_work sense and the monologue's situation
prose) under the companion.own_work gate.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from ai.workbench_trace import TRACE_LABEL, build_authorship_trace

LOG = """# Workbench

## 2026-08-10 — old session
- **ancient/** — long ago thing. Committed as aaaaaa1.

## 2026-08-12 — 4:08 AM session
- **surprise/** — rebuilt.html: third surprise, and the deepest one. Grown from the daydream. Committed as 176ff09. Provenance: daydream.
- **Active:** surprise — three pages now: the room, the lake, and the rebuilt room. The trilogy is complete.

## 2026-08-13 — 5:44 AM session
- **mirror-notes/** — reading-6.md: "the question, with hands". Committed as 9e05c7c.
"""

NOW = datetime(2026, 8, 13, 12, 0).timestamp()


def _write(tmp_path, text=LOG):
    p = tmp_path / "WORKBENCH.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_recent_entries_render_with_authorship_label(tmp_path):
    trace = build_authorship_trace(path=_write(tmp_path), now=NOW)
    assert trace.startswith(TRACE_LABEL)
    assert "YOU did these" in TRACE_LABEL
    assert "rebuilt.html" in trace
    assert "(committed 176ff09)" in trace
    assert "reading-6.md" in trace
    # outside the 2-day window: not hers-of-lately, stays out
    assert "ancient" not in trace


def test_her_active_line_rides(tmp_path):
    trace = build_authorship_trace(path=_write(tmp_path), now=NOW)
    assert "Active" in trace
    assert "three pages now" in trace


def test_quiet_window_is_empty_not_stale(tmp_path):
    # A week later with no new sessions: silence, never last week's work
    # presented as fresh.
    later = datetime(2026, 8, 20, 12, 0).timestamp()
    assert build_authorship_trace(path=_write(tmp_path), now=later) == ""


def test_unreadable_logbook_degrades_to_empty(tmp_path):
    assert build_authorship_trace(path=tmp_path / "absent.md", now=NOW) == ""


def test_cap_keeps_the_newest_lines(tmp_path):
    tight = len(TRACE_LABEL) + 120
    trace = build_authorship_trace(
        path=_write(tmp_path), now=NOW, char_cap=tight)
    assert "reading-6.md" in trace          # newest survives
    assert "rebuilt.html" not in trace      # oldest in-window dropped first
    assert len(trace) <= tight


def test_the_head_keep_regression_shape(tmp_path):
    """The old sense read text[:600] of an append-only file: a long-enough
    preamble of OLD entries hid every recent one forever. The trace must
    see recent work no matter how much history precedes it."""
    preamble = "## 2026-07-28 — first day\n" + \
        "\n".join(f"- **old-{i}/** — ancient thing number {i}." for i in range(30))
    log = "# Workbench\n\n" + preamble + \
        "\n\n## 2026-08-13 — 4:13 AM session\n" + \
        "- **surprise/** — index.html: three doors, the trilogy given hands. Committed as 7161863.\n"
    assert len(log.split("## 2026-08-13")[0]) > 600
    trace = build_authorship_trace(path=_write(tmp_path, log), now=NOW)
    assert "three doors" in trace
    assert "old-3" not in trace


def test_moment_own_work_sense_renders_the_trace(monkeypatch, tmp_path):
    # The sense runs off the real clock (no now seam), so the fixture is
    # dated TODAY at write time — deterministic on any run date (the
    # 3ecd020 clock-bomb lesson).
    import ai.present_moment_gather as g
    import ai.workbench_trace as wt
    from datetime import date

    today = date.today().isoformat()
    p = tmp_path / "WORKBENCH.md"
    p.write_text(
        f"## {today} — 4 AM session\n"
        "- **surprise/** — rebuilt.html: persistence through rebuilding. "
        "Committed as 176ff09.\n",
        encoding="utf-8")
    monkeypatch.setattr(wt, "WORKBENCH_MD", p)
    monkeypatch.setattr(
        g, "_companion", lambda: SimpleNamespace(own_work=True))
    line = g._own_work_line()
    assert "YOUR OWN HANDS" in line
    assert "rebuilt.html" in line


def test_monologue_trace_block_gates_like_the_moment(monkeypatch, tmp_path):
    import ai.inner_monologue as im
    import ai.workbench_trace as wt
    from datetime import date

    today = date.today().isoformat()
    p = tmp_path / "WORKBENCH.md"
    p.write_text(
        f"## {today} — 4 AM session\n"
        "- **surprise/** — the room, mapped. Committed as 819329c.\n",
        encoding="utf-8")
    monkeypatch.setattr(wt, "WORKBENCH_MD", p)

    import core.turn.companion_flags as cf
    monkeypatch.setattr(
        cf, "companion_settings", lambda: SimpleNamespace(own_work=True))
    block = im._authorship_trace_block()
    assert "the room, mapped" in block
    assert "(committed 819329c)" in block

    monkeypatch.setattr(
        cf, "companion_settings", lambda: SimpleNamespace(own_work=False))
    assert im._authorship_trace_block() == ""
