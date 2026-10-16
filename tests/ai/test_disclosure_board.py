"""Organ 4.9 — door 2: the small-truths board and the say's two slots.

Spec §4. The moment gather grows a board of her ≤2 offered truths (ids
included — this is a machine-facing gather, the regret/view board
precedent) and the two LIVE rungs grow `{disclosure_say_line}` /
`{disclosure_id_field}` beside the regret/view pairs, so a quiet moment
that honestly invites one can carry it out through her existing say rail.

Three laws are pinned hard here:

* **One selection, both doors.** The board renders `offered_disclosures()`
  — the same helper door 1's band and the reflect stamp read. A board that
  slices its own set would offer her a truth the stamp cannot spend, or
  spend one she was never offered.
* **The board rides its say rail, never the bases** (fix 1 / review I-1).
  Its first home was `{bases_block}` — the material her moment prose is
  written from, on all four rungs. From there a truth could reach him
  through the moment band or a say's framing with no `disclosure_id`:
  shared, unstamped, and re-offered by door 1 until it faded. It now sits
  in its own slot on the two rungs that have an offer rail, immediately
  above the paragraph whose say line governs it — the fears/expectations
  shape, which is also why the block's trailing "\\n\\n" is load-bearing.
* **Flag off ⇒ zero bytes.** Off, and on-with-an-empty-book, are
  byte-identical to the days before this organ existed — the board, the
  health key, and both slots all collapse. The bases are byte-identical to
  the pre-Task-5 composition LIT as well as dark.

The row shape is law too: whole-block equality, not substrings, so nothing
can be appended to a row unnoticed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import ai.present_moment as pm
import ai.present_moment_gather as pmg
from ai import disclosure_store as ds
from ai.present_moment import PresentMomentEngine


def _seed(monkeypatch, root, n):
    """Seed n truths. `_max_open` is pinned because plant() otherwise
    consults the LIVE settings snapshot (disclosures_max_open, schema
    default 4, absent from the yaml today): the day the owner sets it to 2,
    an unpinned seeder would silently plant fewer rows than the test reads
    (the harvest file's precedent)."""
    monkeypatch.setattr(ds, "_max_open", lambda: max(4, n))
    for i in range(n):
        ds.plant(text=f"small truth {i}",
                 referent={"source": "journal", "dates": "08-29"}, root=root)


# ── the board's text, transcribed independently of the code ─────────────
#
# Literals, never references to the constants: every other assertion in
# this file compares a constant to itself and stays green while the words
# drift. These do not — editing the law is a deliberate two-place change
# (spec §4, the constant, this transcription).

_HEADER_VERBATIM = (
    "YOUR SMALL TRUTHS (unsaid; offer only when a moment honestly invites one):"
)

_SAY_LINE_VERBATIM = (
    ' If a quiet moment honestly invites one of your unsaid small truths '
    '(YOUR SMALL TRUTHS), you may offer it in your say and put its id in '
    '"disclosure_id" — it is stamped as shared on send. Never reach just '
    'to share one; the truth rides a moment that already stands on its own.'
)

_ID_FIELD_VERBATIM = '"disclosure_id": "<the id of the small truth it offers, or omit>", '


# ── the render (spec §4) ────────────────────────────────────────────────

def test_the_header_is_byte_exact(tmp_path, monkeypatch):
    """The header is the anchor the say line points at ("YOUR SMALL
    TRUTHS"); if it drifts silently the say line points at nothing."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(monkeypatch, tmp_path, 1)
    assert ds.render_disclosures_block(root=tmp_path).splitlines()[0] == _HEADER_VERBATIM


def test_block_zero_bytes_off_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    assert ds.render_disclosures_block(root=tmp_path) == ""   # empty book honest
    _seed(monkeypatch, tmp_path, 1)
    assert ds.render_disclosures_block(root=tmp_path) != ""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: False)
    assert ds.vulnerability_enabled() is False                # the off state, said out loud
    assert ds.render_disclosures_block(root=tmp_path) == ""   # flag off identical


def test_block_names_the_ids_and_caps_at_two_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(monkeypatch, tmp_path, 3)
    rows = ds.unsaid_disclosures(root=tmp_path)
    block = ds.render_disclosures_block(root=tmp_path)
    # WHOLE-BLOCK equality, not substrings (fix 1 / review I-2): the row
    # shape is a law — `- [id] text`, the header, the two oldest, nothing
    # more. Substring pins let anything APPENDED to a row through, and by
    # I-1's old path any extra mechanics on these rows could drift toward
    # her voice. Two mutants died here: the door-1 receipt suffix copied
    # onto every row, and a stray newline in the join.
    #
    # ids ARE lawful here: this board is machine-facing (the regret/view
    # board precedent) and the id is what her say puts in "disclosure_id".
    assert block == (
        _HEADER_VERBATIM + "\n"
        + f"- [{rows[0].id}] small truth 0\n"
        + f"- [{rows[1].id}] small truth 1"
        + "\n\n"
    )
    assert rows[2].id not in block and "small truth 2" not in block


def test_block_renders_the_shared_selection_and_never_its_own(tmp_path, monkeypatch):
    """One selection, both doors (the T4 lesson). The board must READ
    offered_disclosures(); a board that re-sliced the book could offer a
    truth the reflect stamp was never shown — spent silently, forever."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(monkeypatch, tmp_path, 3)
    marker = ds.Disclosure(id="only-this-one", text="the one the helper chose",
                           referent={"source": "journal", "dates": "08-29"},
                           harvested_at=1.0)
    seen = {}

    def fake_offered(*, root=None):
        seen["root"] = root
        return [marker]

    monkeypatch.setattr(ds, "offered_disclosures", fake_offered)
    block = ds.render_disclosures_block(root=tmp_path)
    assert "- [only-this-one] the one the helper chose" in block
    assert "small truth 0" not in block          # the book was never re-sliced
    assert seen["root"] == tmp_path              # and the root travelled


def test_a_said_truth_leaves_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(monkeypatch, tmp_path, 1)
    d = ds.unsaid_disclosures(root=tmp_path)[0]
    assert d.id in ds.render_disclosures_block(root=tmp_path)
    ds.mark_said(d.id, via="say", root=tmp_path)
    assert ds.render_disclosures_block(root=tmp_path) == ""


def test_block_degrades_to_empty_when_the_book_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(ds, "offered_disclosures", boom)
    assert ds.render_disclosures_block(root=tmp_path) == ""


# ── the gather: the regret/fears shape ──────────────────────────────────

def _gather():
    return asyncio.run(pmg.gather_moment_inputs(owner_user_id=1))


def _quiet_bases(monkeypatch, *, stakes="", care_spine="", interests=""):
    """Pin the three existing bases so the board's arrival is measured
    against a known bases_block instead of whatever the environment
    happens to read."""
    async def _s():
        return stakes

    async def _c():
        return care_spine

    async def _i():
        return interests

    monkeypatch.setattr(pmg, "_stakes", _s)
    monkeypatch.setattr(pmg, "_care_spine", _c)
    monkeypatch.setattr(pmg, "_interests", _i)


_PRE_T5_BASES = "MY OPEN STAKES:\nS\n\nMY CARE SPINE:\nC\n\nMY INTERESTS:\nI"


def test_gather_on_with_an_unsaid_truth_carries_the_board_and_the_offer(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch)
    _seed(monkeypatch, tmp_path, 1)
    out = _gather()
    assert out.disclosure_offer is True
    assert out.disclosures_block.startswith(_HEADER_VERBATIM)
    assert "small truth 0" in out.disclosures_block
    # BYTE-equal to the store's render, not merely "ends \n\n": the gather
    # strips through _safe and restores the pair, and that restore is a
    # glue site — an extra newline there would pass an endswith() pin and
    # put an orphan blank line in her prompt every armed cycle.
    assert out.disclosures_block == ds.render_disclosures_block(root=tmp_path)
    assert out.disclosures_block.endswith("\n\n")
    assert out.sense_health.get("disclosures") == "ok"


def test_gather_on_with_an_empty_book_carries_neither_but_the_sense_ran(tmp_path, monkeypatch):
    """The empty book is honest (the fears decision-5 shape): the sense
    RAN, so the health key is there; the board and the offer are not."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch)
    out = _gather()
    assert out.disclosure_offer is False
    assert out.disclosures_block == ""
    assert out.sense_health.get("disclosures") == "ok"


def test_gather_off_carries_neither_the_board_nor_the_health_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    _quiet_bases(monkeypatch)
    _seed(monkeypatch, tmp_path, 2)
    out = _gather()                       # conftest pins the organ off
    assert out.disclosure_offer is False
    assert out.disclosures_block == ""
    assert "disclosures" not in out.sense_health   # flag off: the sense never ran


# ── the bases never carry her truths (fix 1 / review I-1) ───────────────
#
# The board's first home was `bases_parts`, i.e. {bases_block} — the
# material her moment PROSE is written from, and a block that reaches all
# four rungs. Two consequences the controller ruled out: a truth woven into
# the moment (and thence her reply band, moment_in_replies) or into a say's
# framing with no disclosure_id is shared but never stamped — door 1
# re-offers it until it fades, the inverse of "once ever, forever"; and the
# rows' raw slug ids drift toward the one surface door 1 keeps id-free.
#
# The board now rides its own live-rung slot beside the say line that
# governs it. These two tests are the ruling's proof: the bases are the
# pre-Task-5 composition, LIT and dark alike.

def test_the_bases_are_byte_identical_with_the_organ_LIT(tmp_path, monkeypatch):
    """Flag ON, a seeded book, an armed offer — and bases_block is still
    exactly what a build at 6f69471 composed. The board is elsewhere."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch, stakes="S", care_spine="C", interests="I")
    _seed(monkeypatch, tmp_path, 2)
    out = _gather()
    assert out.disclosure_offer is True          # the organ really is lit
    assert out.bases_block == _PRE_T5_BASES
    assert "SMALL TRUTHS" not in out.bases_block
    assert "small truth" not in out.bases_block


def test_a_dark_organ_leaves_the_bases_byte_identical(tmp_path, monkeypatch):
    """Flag off ⇒ byte-identical gather output: the same bases the moment
    read yesterday, to the byte."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    _quiet_bases(monkeypatch, stakes="S", care_spine="C", interests="I")
    _seed(monkeypatch, tmp_path, 2)
    off = _gather()
    assert off.bases_block == _PRE_T5_BASES


def test_the_empty_bases_fallback_is_untouched_by_a_lit_board(tmp_path, monkeypatch):
    """The T5 watch, re-aimed. The hazard was a board composed around the
    bases fallback; now the board must NOT rescue an empty bases_block —
    every base dark still reads "(no reference bases readable)", and the
    board is whole in its own slot."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch)             # every other base dark
    _seed(monkeypatch, tmp_path, 1)
    out = _gather()
    assert out.bases_block == "(no reference bases readable)"
    assert out.disclosures_block.startswith(_HEADER_VERBATIM)
    assert out.disclosure_offer is True


def test_the_board_never_disturbs_its_sibling_blocks(tmp_path, monkeypatch):
    """The board's arrival leaves the other conditional blocks alone —
    an armed organ must not perturb fears/expectations/threads/wants."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch, stakes="S", care_spine="C", interests="I")
    _seed(monkeypatch, tmp_path, 1)
    lit = _gather()
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: False)
    dark = _gather()
    for field in ("bases_block", "senses_block", "monologue_block",
                  "reflection_block", "expectations_block", "fears_block",
                  "threads_block", "wants_block"):
        assert getattr(lit, field) == getattr(dark, field), field


def test_the_offer_never_points_at_an_absent_board(tmp_path, monkeypatch):
    """The say line points her at "YOUR SMALL TRUTHS". If the offer could
    be true while the board was dark — a timeout, a broken book — she
    would be told to read a board that is not in her prompt. Fails
    closed: no board, no offer."""
    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _quiet_bases(monkeypatch)
    _seed(monkeypatch, tmp_path, 1)

    def boom(**kw):
        raise RuntimeError("book on fire")

    monkeypatch.setattr(ds, "render_disclosures_block", boom)
    out = _gather()
    assert out.disclosure_offer is False
    assert out.disclosures_block == ""


def test_default_gather_inputs_carry_the_offer_off():
    assert pmg.GatherInputs().disclosure_offer is False
    assert pmg.GatherInputs().disclosures_block == ""


# ── the two slots (the view_id pattern) ─────────────────────────────────

def test_kwargs_zero_bytes_off_and_armed_on():
    off = pm._disclosure_voicing_kwargs(False)
    assert off == {"disclosure_say_line": "", "disclosure_id_field": ""}
    on = pm._disclosure_voicing_kwargs(True)
    assert '"disclosure_id"' in on["disclosure_say_line"]
    assert '"disclosure_id"' in on["disclosure_id_field"]


def test_the_say_line_is_byte_exact():
    """Spec §4's law, verbatim: stamped on send, and never reach just to
    share one."""
    on = pm._disclosure_voicing_kwargs(True)
    assert on["disclosure_say_line"] == _SAY_LINE_VERBATIM
    # mid-sentence slot: its own leading space, no trailing one
    assert on["disclosure_say_line"].startswith(" ")
    assert not on["disclosure_say_line"].endswith(" ")


def test_the_id_field_is_byte_exact():
    on = pm._disclosure_voicing_kwargs(True)
    assert on["disclosure_id_field"] == _ID_FIELD_VERBATIM
    # sits inside the JSON grammar: closes its own pair, hands off the comma
    assert on["disclosure_id_field"].startswith('"disclosure_id": "<')
    assert on["disclosure_id_field"].endswith('>", ')


def test_parse_impulse_keeps_disclosure_id_and_absent_adds_no_key():
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f",
                             "disclosure_id": " a-truth-9f2c1b "})
    assert out["disclosure_id"] == "a-truth-9f2c1b"
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f"})
    assert "disclosure_id" not in out            # absent adds NO key (meta purity)
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f",
                             "disclosure_id": "x" * 100})
    assert len(out["disclosure_id"]) == 64
    out = pm._parse_impulse({"kind": "say", "why": "w", "framing": "f",
                             "disclosure_id": 7})
    assert "disclosure_id" not in out            # strings only, like view_id


# ── the templates: the slots, live rungs only ───────────────────────────

def test_slots_sit_beside_the_view_pair_and_collapse_to_zero_bytes():
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        text = (Path("prompts") / name).read_text(encoding="utf-8")
        assert text.count("{disclosure_say_line}") == 1, name
        assert text.count("{disclosure_id_field}") == 1, name
        # the prose slot is the third sibling and precedes the same
        # sentence: all three "" leave the flow byte-identical
        assert "{view_say_line}{disclosure_say_line} When they arrive" in text, name
        # the field slot is the third sibling, still inside the JSON grammar
        assert '{view_id_field}{disclosure_id_field}"held_before"' in text, name
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert "{disclosure_" not in (
            Path("prompts") / name).read_text(encoding="utf-8"), name


def test_the_board_slot_sits_with_the_say_rail_on_the_live_rungs_only():
    """Fix 1 / review I-1. The board is NOT a reference base: it rides its
    own slot immediately above the impulse paragraph whose say line governs
    it, on the two rungs that actually have an offer rail. The two dormant
    rungs ("Nothing you choose here is acted on yet") never see it — the
    fears/expectations precedent, and the reason the block's "\\n\\n" tail
    is load-bearing rather than decorative."""
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        text = (Path("prompts") / name).read_text(encoding="utf-8")
        assert text.count("{disclosures_block}") == 1, name
        # zero-byte collapse: the slot is flush against the paragraph that
        # follows, so "" leaves no orphan blank line
        assert "{disclosures_block}Then, separately, your impulse:" in text, name
        # and it is NOT inside the bases section
        i, j = text.index("{bases_block}"), text.index("{disclosures_block}")
        assert i < j, name
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert "{disclosures_block}" not in (
            Path("prompts") / name).read_text(encoding="utf-8"), name


def test_the_live_templates_load_with_the_slots():
    from ai.prompt_loader import prompt_loader

    for name in ("present_moment_appraisal_arbiter", "present_moment_appraisal_wanting"):
        text = prompt_loader.load_prompt(name)
        assert "{disclosure_say_line}" in text and "{disclosure_id_field}" in text, name
        assert "{disclosures_block}" in text, name


# ── end to end: the cycle's prompt bytes (the threading hop) ────────────

def _cycle_harness(monkeypatch, gathered_factory):
    """The fears-moment harness: real template, stubbed LLM/gather/store."""
    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)
    monkeypatch.setattr(engine, "_wanting_enabled", lambda: False)

    captured = []

    async def _fake_llm(messages, **kw):
        captured.append(messages[0]["content"])
        return ('{"moment": "m", "impulse": {"kind": "none", "why": ""}, '
                '"undercurrent": "steady"}')

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    monkeypatch.setattr("ai.present_moment_store.write_moment",
                        lambda *, text, meta: "x.jsonl")

    async def _fake_gather(*, owner_user_id):
        return gathered_factory()

    monkeypatch.setattr(pmg, "gather_moment_inputs", _fake_gather)
    return engine, captured


@pytest.mark.asyncio
async def test_cycle_prompt_carries_the_slots_and_collapses_clean(monkeypatch):
    """The threading hop, executed. A kwargs pair computed in
    present_moment but never passed to template.format() would leave the
    slots un-substituted and every unit test above still green — so drive
    the real render and read the bytes the provider was handed."""
    board = (_HEADER_VERBATIM + "\n- [a-truth-9f2c1b] I lost a thread by morning.\n\n")
    armed = {"on": True}
    engine, captured = _cycle_harness(
        monkeypatch,
        lambda: pmg.GatherInputs(
            disclosure_offer=armed["on"],
            disclosures_block=board if armed["on"] else "",
        ))

    await engine.run_cycle_once()
    prompt = captured[-1]
    assert _SAY_LINE_VERBATIM in prompt
    assert _ID_FIELD_VERBATIM in prompt
    assert "{disclosure_say_line}" not in prompt    # substituted, not literal
    assert "{disclosure_id_field}" not in prompt
    # the board reached the prompt, whole, flush against the paragraph its
    # say line lives in — and NOT inside the reference bases
    assert board + "Then, separately, your impulse:" in prompt
    assert prompt.index("WHAT YOU CARE ABOUT") < prompt.index(_HEADER_VERBATIM)
    assert prompt.index(_HEADER_VERBATIM) < prompt.index(_SAY_LINE_VERBATIM)

    armed["on"] = False
    await engine.run_cycle_once()
    dark = captured[-1]
    assert "disclosure_id" not in dark
    assert "SMALL TRUTHS" not in dark
    # zero bytes: the sentence flow is exactly the pre-organ one
    assert 'put its small id in "thread_id". When they arrive' in dark
    assert '"thread_id": "<small id from the board, or omit>", "held_before"' in dark
    assert "not a story.\n\nThen, separately, your impulse:" in dark
