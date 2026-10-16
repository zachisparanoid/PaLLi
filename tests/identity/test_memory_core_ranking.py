"""Batch 5, task 3 (memory that picks, 2026-09-28): the memory core picks
what matters.

Every reply prompt used to carry the whole of MEMORY.md (~72k characters).
It now carries a CORE under ``memory.core_budget_chars``. Ruling R37 (fix
round 1) says how:

- Important Decisions whole, first, never cut.
- Every other bullet scores ``max(last_referenced_at, stamp, and — for an
  unstamped line — the render day's local midnight)``: an unstamped line is
  never older than today, and a reference can never lower it. Ties fall to
  the reference count (descending), then the section (Durable Context,
  Active Threads, Useful References, then the rest), then file order.
- The budget the decisions leave is SHARED by section — Durable Context
  60 %, Active Threads 30 %, Useful References 10 %. Pass one: each section
  fills its own share in rank order, stopping at the first bullet that does
  not fit (R36). Pass two: the leftover goes round, Durable first, the same
  way. So on day one the core carries Durable facts AND threads AND
  references, and a run of fresh threads can never push every fact out.
- What made the core renders under its own header in Batch 2's section
  order (R8), bullets in file order as the exact lines from the file, then
  the footer that names how many lines are looked up instead (R7b).

The render takes ``now`` so these tests are deterministic (Law 5).
"""
from __future__ import annotations

import time

from core.identity.compiler import (
    MemoryCore,
    plan_memory_core,
    render_memory_core,
    render_memory_doc,
)
from core.identity.constants import IDENTITY_DOC_PREAMBLES
from core.identity.models import CompiledIdentity
from core.memory_bullets import item_id_for


def _day(stamp: str, hour: int = 0) -> float:
    """A local-time instant on a calendar day, read the way a stamp is."""
    return time.mktime(time.strptime(stamp, "%Y-%m-%d")) + hour * 3600


NOW = _day("2026-09-28", 15)  # three in the afternoon, local, on the render day


# 10-05: one sentence, no count (see the footer test below)
FOOTER = "(Any memory line not shown here is looked up when a moment calls for it.)"


def _body(core: MemoryCore) -> str:
    """The core after ``### Memory\\n<preamble>``."""
    return core.text.split("\n\n", 1)[1]


# ── the plan's test, verbatim ────────────────────────────────────────────────


def test_recently_used_lines_beat_old_unused_ones(memory_bundle_factory):
    doc = "## Durable Context\n- [2026-01-01] old but used\n- [2026-09-20] new never used\n- [2026-01-02] old unused\n\n## Important Decisions\n- decided\n"
    refs = {item_id_for("- [2026-01-01] old but used"): {"last_referenced_at": 1_790_000_000.0, "count": 3}}
    out = render_memory_core(memory_bundle_factory(doc), budget_chars=120, refs=refs)
    assert "old but used" in out and "decided" in out
    assert "old unused" not in out


# ── the score and its ties (R37.1, R37.2) ───────────────────────────────────


def test_the_cut_stops_at_the_first_line_that_does_not_fit(memory_bundle_factory):
    """R36: strict rank order. A short line further down the ranking is not
    pulled ahead of a long one that did not fit."""
    doc = (
        "## Active Threads\n"
        "- [2026-09-27] yesterday, short\n"
        "- [2026-09-26] two days ago, long " + "z" * 60 + "\n"
        "- [2026-09-25] three days ago, short\n"
    )
    shown = "## Active Threads\n- [2026-09-27] yesterday, short"
    # room for the short third line but not the long second one
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=len(shown) + 40, refs={}, now=NOW)
    assert "yesterday, short" in core.text
    assert "two days ago" not in core.text
    assert "three days ago" not in core.text
    assert core.omitted == 2


def test_an_unstamped_line_reads_as_born_today_and_a_reference_today_beats_it(memory_bundle_factory):
    """R37.1: an unstamped line scores the render day's midnight, a stamped
    one its stamp, and a reference lifts either to the later time. Within one
    section, so the shares play no part."""
    doc = "## Durable Context\n- [2026-09-27] noted yesterday\n- they take their tea black\n"
    bundle = memory_bundle_factory(doc)
    one_line = len("## Durable Context\n- [2026-09-27] noted yesterday")  # room for either line alone
    core = plan_memory_core(bundle, budget_chars=one_line, refs={}, now=NOW)
    assert "tea black" in core.text and "noted yesterday" not in core.text
    used_today = {item_id_for("noted yesterday"): {"last_referenced_at": NOW - 3600, "count": 1}}
    core = plan_memory_core(bundle, budget_chars=one_line, refs=used_today, now=NOW)
    assert "noted yesterday" in core.text and "tea black" not in core.text


def test_an_unstamped_line_referenced_yesterday_outranks_one_never_referenced(memory_bundle_factory):
    """R37.1 + R37.2 — the review's second finding: a reference can never
    lower an unstamped line below today's midnight. The two tie on score and
    the count decides, whatever the file order."""
    doc = "## Durable Context\n- never referenced\n- referenced twice\n"
    bundle = memory_bundle_factory(doc)
    refs = {item_id_for("referenced twice"): {"last_referenced_at": _day("2026-09-27", 9), "count": 2}}
    one_line = len("## Durable Context\n- referenced twice")  # the two lines are the same length
    core = plan_memory_core(bundle, budget_chars=one_line, refs=refs, now=NOW)
    assert "referenced twice" in core.text and "never referenced" not in core.text
    # without the reference the file's order decides
    core = plan_memory_core(bundle, budget_chars=one_line, refs={}, now=NOW)
    assert "never referenced" in core.text and "referenced twice" not in core.text


def test_ties_fall_to_the_count_then_file_order(memory_bundle_factory):
    doc = "## Durable Context\n- a\n- b\n- c\n"
    bundle = memory_bundle_factory(doc)
    same_time = NOW - 3600
    refs = {
        item_id_for("b"): {"last_referenced_at": same_time, "count": 1},
        item_id_for("c"): {"last_referenced_at": same_time, "count": 5},
    }
    two = len("## Durable Context\n- b\n- c")
    core = plan_memory_core(bundle, budget_chars=two, refs=refs, now=NOW)
    assert _body(core) == f"## Durable Context\n- b\n- c\n\n{FOOTER}"  # both referenced lines beat a; file order shown
    one = len("## Durable Context\n- c")
    core = plan_memory_core(bundle, budget_chars=one, refs=refs, now=NOW)
    assert _body(core) == f"## Durable Context\n- c\n\n{FOOTER}"  # the higher count wins the one place
    core = plan_memory_core(bundle, budget_chars=one, refs={}, now=NOW)
    assert _body(core) == f"## Durable Context\n- a\n\n{FOOTER}"  # no references: file order


def test_the_tie_order_and_the_shares_are_the_ruled_constants():
    from core.identity.compiler import _MEMORY_RANK_TIE_ORDER, _MEMORY_SECTION_SHARES

    assert _MEMORY_RANK_TIE_ORDER == ("## Durable Context", "## Active Threads", "## Useful References")
    assert _MEMORY_SECTION_SHARES == {
        "## Durable Context": 0.60,
        "## Active Threads": 0.30,
        "## Useful References": 0.10,
    }


def test_decisions_render_whole_even_past_the_budget(memory_bundle_factory):
    doc = "## Durable Context\n- a durable fact\n\n## Important Decisions\n- decided A\n- decided B\n- decided C\n"
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=10, refs={}, now=NOW)
    for line in ("- decided A", "- decided B", "- decided C"):
        assert line in core.text
    assert "a durable fact" not in core.text and "## Durable Context" not in core.text
    assert core.omitted == 1


# ── the shares (R37.3) ──────────────────────────────────────────────────────


def test_each_section_fills_its_own_share_then_the_leftover_goes_round_durable_first(memory_bundle_factory):
    """Budget 60, no decisions: Durable's share is 36 → d1..d3 (33); Active's
    18 fits no thread (t1 under its header costs 24); the leftover 27 goes to
    Durable first → d4 (5), and then t1 (24) no longer fits."""
    doc = "## Active Threads\n- t1\n- t2\n\n## Durable Context\n- d1\n- d2\n- d3\n- d4\n"
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=60, refs={}, now=NOW)
    assert _body(core) == f"## Durable Context\n- d1\n- d2\n- d3\n- d4\n\n{FOOTER}"


def test_a_thread_referenced_today_does_not_push_every_durable_line_out(memory_bundle_factory):
    """(c) The review's finding the other way round: threads that outrank
    every Durable line — stamped today AND referenced this afternoon — still
    leave Durable Context its share, and the bigger one."""
    threads = "\n".join(f"- [2026-09-28] thread {i}" for i in range(10))
    durable = "\n".join(f"- durable fact {i}" for i in range(10))
    doc = f"## Durable Context\n{durable}\n\n## Active Threads\n{threads}\n"
    refs = {item_id_for(f"thread {i}"): {"last_referenced_at": NOW - 3600, "count": 1} for i in range(10)}
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=200, refs=refs, now=NOW)
    lines = core.text.splitlines()
    shown_durable = [line for line in lines if line.startswith("- durable fact")]
    shown_threads = [line for line in lines if line.startswith("- [2026-09-28] thread")]
    assert shown_durable and shown_threads
    assert len(shown_durable) > len(shown_threads)
    assert core.omitted == 20 - len(shown_durable) - len(shown_threads) > 0


def test_a_live_shaped_doc_shows_every_section(memory_bundle_factory):
    """(a) The review's measurement as a fixture: 200 unstamped Durable
    lines, 40 unstamped References, 100 stamped Threads, 10 Decisions,
    budget 6000 — at least one bullet from each of the three shared
    sections, the decisions whole, the frame outside the budget."""
    durable = "\n".join(f"- durable fact {i} " + "d" * 40 for i in range(200))
    references = "\n".join(f"- reference {i} " + "r" * 40 for i in range(40))
    threads = "\n".join(f"- [2026-09-{i % 27 + 1:02d}] thread {i} " + "t" * 40 for i in range(100))
    decisions = "\n".join(f"- decided {i}" for i in range(10))
    doc = (
        f"## Durable Context\n{durable}\n\n## Active Threads\n{threads}\n\n"
        f"## Important Decisions\n{decisions}\n\n## Useful References\n{references}\n"
    )
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=6000, refs={}, now=NOW)
    lines = core.text.splitlines()
    counts = {
        "decisions": sum(line.startswith("- decided") for line in lines),
        "durable": sum(line.startswith("- durable fact") for line in lines),
        "threads": sum(line.startswith("- [2026-09-") for line in lines),
        "references": sum(line.startswith("- reference") for line in lines),
    }
    assert counts["decisions"] == 10
    assert counts["durable"] >= 1 and counts["threads"] >= 1 and counts["references"] >= 1, counts
    assert counts["durable"] > counts["threads"] > counts["references"], counts
    assert core.omitted == 350 - sum(counts.values())
    assert len(core.text) <= 6000 + 600  # the label, preamble and footer sit outside the budget


# ── rendering ───────────────────────────────────────────────────────────────


def test_sections_render_least_changing_first_and_bullets_in_file_order(memory_bundle_factory):
    """R8: Batch 2's section order (test_compiler_memory_order pins it for the
    whole doc); inside a section the file's order, whatever the ranking."""
    doc = (
        "## Zeta\n- z\n\n## Active Threads\n- [2026-09-01] older thread\n- [2026-09-27] newer thread\n\n"
        "## Useful References\n- d\n\n## Important Decisions\n- c\n\n## Durable Context\n- a\n"
    )
    text = render_memory_core(memory_bundle_factory(doc), budget_chars=10_000, refs={}, now=NOW)
    body = text.split("\n\n", 1)[1]  # after "### Memory\n<preamble>"
    assert body == (
        "## Important Decisions\n- c\n\n## Useful References\n- d\n\n## Durable Context\n- a\n\n"
        "## Active Threads\n- [2026-09-01] older thread\n- [2026-09-27] newer thread\n\n## Zeta\n- z\n\n"
        + FOOTER
    )


def test_a_section_header_renders_only_when_one_of_its_bullets_made_the_core(memory_bundle_factory):
    doc = (
        "## Durable Context\n- [2026-09-28] fresh\n\n## Useful References\n- [2026-01-01] stale ref\n\n"
        "## Active Threads\n- [2026-01-02] stale thread\n"
    )
    shown = "## Durable Context\n- [2026-09-28] fresh"
    text = render_memory_core(memory_bundle_factory(doc), budget_chars=len(shown), refs={}, now=NOW)
    assert "## Durable Context" in text
    assert "## Useful References" not in text and "## Active Threads" not in text
    assert "stale" not in text


def test_the_footer_always_renders_and_never_carries_the_count(memory_bundle_factory):
    """10-05 (the owner: "make the reflection cacheable"): the footer used to
    say how many lines were held back — "(534 more lines …)" — so every new
    memory changed it, and DeepSeek's prefix cache broke at that line on her
    replies and her reflection alike (measured: 8% of a 258k-character
    reflection cached, the count the only difference before "This turn").
    One fixed sentence now; the count stays on ``MemoryCore.omitted``."""
    doc = "## Durable Context\n- one\n- two\n- three\n"
    everything = plan_memory_core(memory_bundle_factory(doc), budget_chars=10_000, refs={}, now=NOW)
    assert everything.omitted == 0 and everything.text.endswith(FOOTER)
    tight = len("## Durable Context\n- one\n- two")
    some = plan_memory_core(memory_bundle_factory(doc), budget_chars=tight, refs={}, now=NOW)
    more = plan_memory_core(memory_bundle_factory(doc + "- four\n"), budget_chars=tight, refs={}, now=NOW)
    assert (some.omitted, more.omitted) == (1, 2)
    assert "- three" not in some.text and "- four" not in more.text
    assert some.text == more.text  # one more line held back changes nothing she is sent
    assert not any(ch.isdigit() for ch in FOOTER)


def test_label_preamble_pre_heading_part_and_footer_sit_outside_the_budget(memory_bundle_factory):
    """R7b: only section headers, the blank lines between sections and the
    bullet lines count; the frame around them is free."""
    doc = "# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n## Durable Context\n- a\n- b\n"
    inside = "## Durable Context\n- a\n- b"
    bundle = memory_bundle_factory(doc, favorite_tea="oolong")
    core = plan_memory_core(bundle, budget_chars=len(inside), refs={}, now=NOW)
    preamble = IDENTITY_DOC_PREAMBLES["memory"].rstrip()
    assert core.text == (
        f"### Memory\n{preamble}\n\n"
        "- favorite_tea: oolong\n\n# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n"
        f"{inside}\n\n{FOOTER}"
    )
    assert core.omitted == 0
    # one character less and the last line no longer fits
    tighter = plan_memory_core(bundle, budget_chars=len(inside) - 1, refs={}, now=NOW)
    assert "- b" not in tighter.text and tighter.omitted == 1


def test_an_ample_budget_shows_the_whole_doc_the_way_render_memory_doc_does(memory_bundle_factory):
    doc = (
        "# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n"
        "## Durable Context\n- a\n\n## Active Threads\n- [2026-09-27] b\n\n"
        "## Important Decisions\n- c\n\n## Useful References\n- d\n"
    )
    bundle = memory_bundle_factory(doc, favorite_tea="oolong")
    assert render_memory_core(bundle, budget_chars=10_000, refs={}, now=NOW) == (
        render_memory_doc(bundle) + f"\n\n{FOOTER}"
    )


def test_non_bullet_lines_inside_a_section_are_not_rendered(memory_bundle_factory):
    doc = "## Durable Context\nsome prose the file should not have\n\n- a\n### a sub-heading\n- b\n"
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=10_000, refs={}, now=NOW)
    assert _body(core) == f"## Durable Context\n- a\n- b\n\n{FOOTER}"
    assert core.omitted == 0


def test_absent_or_empty_doc_renders_empty_like_the_whole_doc(memory_bundle_factory):
    assert render_memory_core(CompiledIdentity(owner={}), budget_chars=100, refs={}, now=NOW) == ""
    assert plan_memory_core(memory_bundle_factory(""), budget_chars=100, refs={}, now=NOW) == MemoryCore(
        text="", ids=frozenset(), omitted=0,
    )
    assert render_memory_core(memory_bundle_factory("   \n"), budget_chars=100, refs={}, now=NOW) == ""


# ── the plan (R3) and determinism (Law 5) ───────────────────────────────────


def test_the_core_names_the_ids_of_the_bullets_it_shows(memory_bundle_factory):
    doc = "## Durable Context\n- [2026-09-28] shown\n- [2026-01-01] hidden\n\n## Important Decisions\n- decided\n"
    bundle = memory_bundle_factory(doc)
    budget = len("## Important Decisions\n- decided\n\n## Durable Context\n- [2026-09-28] shown")
    core = plan_memory_core(bundle, budget_chars=budget, refs={}, now=NOW)
    assert core.ids == frozenset({item_id_for("- decided"), item_id_for("shown")})
    assert core.omitted == 1
    assert render_memory_core(bundle, budget_chars=budget, refs={}, now=NOW) == core.text


def test_same_inputs_same_bytes_and_the_hour_does_not_leak(memory_bundle_factory):
    doc = (
        "## Durable Context\n- unstamped fact\n- [2026-09-20] stamped fact\n\n"
        "## Active Threads\n- [2026-09-27] a thread\n- [2026-09-01] an old thread\n\n"
        "## Important Decisions\n- decided\n\n## Useful References\n- a reference\n"
    )
    bundle = memory_bundle_factory(doc)
    refs = {item_id_for("an old thread"): {"last_referenced_at": _day("2026-09-26", 9), "count": 2}}
    budget = 130
    first = plan_memory_core(bundle, budget_chars=budget, refs=refs, now=NOW)
    second = plan_memory_core(bundle, budget_chars=budget, refs=refs, now=NOW)
    assert first == second
    assert 0 < first.omitted < 6
    # the same day at one in the morning or eleven at night renders the same bytes
    early = plan_memory_core(bundle, budget_chars=budget, refs=refs, now=_day("2026-09-28", 1))
    late = plan_memory_core(bundle, budget_chars=budget, refs=refs, now=_day("2026-09-28", 23))
    assert early.text == late.text == first.text


def test_refs_of_unknown_shape_are_ignored_not_fatal(memory_bundle_factory):
    from types import MappingProxyType

    doc = "## Durable Context\n- [2026-01-01] a\n- [2026-01-02] b\n"
    odd_refs = {item_id_for("a"): {"count": "three"}, item_id_for("b"): "garbage", "mcb_nobody": None}
    budget = len("## Durable Context\n- [2026-01-02] b")
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=budget, refs=odd_refs, now=NOW)
    assert "- [2026-01-02] b" in core.text and "- [2026-01-01] a" not in core.text  # stamps alone decide
    # any mapping serves as refs — memory_refs.snapshot hands out a read-only view (M3)
    view = MappingProxyType({item_id_for("a"): {"last_referenced_at": NOW - 60, "count": 1}})
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=budget, refs=view, now=NOW)
    assert "- [2026-01-01] a" in core.text and "- [2026-01-02] b" not in core.text


def test_a_headingless_doc_is_one_unnamed_section_under_the_budget(memory_bundle_factory):
    """Ruling R62 (fix wave): with no ``## `` heading anywhere, the whole body
    used to land in the pre-heading part — outside the budget, rendered
    whole. Now it is one unnamed section: ranked and shared like Durable
    Context, no header rendered, non-bullet lines dropped, and the footer
    counts what did not fit."""
    lines = [f"- [2026-0{1 + i % 9}-{1 + i % 28:02d}] line number {i} of a memory with no headings" for i in range(200)]
    doc = "some prose the file should not have\n" + "\n".join(lines) + "\n"
    core = plan_memory_core(memory_bundle_factory(doc), budget_chars=500, refs={}, now=NOW)
    body = _body(core)
    shown = [ln for ln in body.split("\n") if ln.startswith("- ")]
    assert 0 < len(shown) < 200
    assert core.omitted == 200 - len(shown)
    assert body.endswith(FOOTER)
    rendered = body[: -len(FOOTER)].rstrip("\n")
    assert rendered == "\n".join(shown)              # no header line, the bullets alone
    assert len(rendered) <= 500                        # under the budget
    assert "## " not in body and "some prose" not in body
    assert shown == sorted(shown, key=lines.index)     # file order among the shown
    assert all("[2026-09-" in ln for ln in shown)      # the newest stamps won the room
    assert core.ids == frozenset(item_id_for(ln) for ln in shown)
    # an ample budget shows every bullet and still no header
    whole = plan_memory_core(memory_bundle_factory(doc), budget_chars=100_000, refs={}, now=NOW)
    assert whole.omitted == 0 and _body(whole).count("\n- ") == 199 and "## " not in _body(whole)
