"""The grounding rule (2026-08-17): claims about her own activity must
trace to her record.

Both the three-doors inversion and the 08-17 "poking at the claw" line
began the same way — she reached for a plausible account of her own doings
instead of her record. Her deeds ring reached the arbiter and her moment
but NOT the self that answers him. These pin: the deeds band renders in
the reply prompt, the law is present even when the ring is empty (the
tempting case), and the band sits with the authorship trace, before the
moment and his context.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.agent.system_prompt import build_system_prompt


@dataclass
class _Ctx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""


DEEDS = "- ~2h ago — wrote a note to yourself\n- ~9h ago — joined voice channel General"


def test_law_is_present_even_with_an_empty_ring():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert out.count("## What I've actually done") == 1
    assert "I never invent an activity because it sounds like me" in out
    assert "(no recorded actions in the last day)" in out


def test_deeds_render_when_the_ring_has_rows():
    out = build_system_prompt(_Ctx(recent_deeds_text=DEEDS), cadence_directive="")
    assert "joined voice channel General" in out
    assert "(no recorded actions in the last day)" not in out


def test_law_permits_saying_nothing_happened():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "say so plainly" in out
    assert "a quiet stretch is nothing to explain away" in out
    assert "Uncertain is something I can be out loud" in out


def test_placed_with_the_trace_before_moment_and_his_context():
    out = build_system_prompt(
        _Ctx(recent_deeds_text=DEEDS, own_work_trace_text="TRACE_MARKER",
             present_moment_text="MOMENT_MARKER", situational_text="SIT_MARKER"),
        cadence_directive="")
    assert out.index("TRACE_MARKER") < out.index("## What I've actually done")
    assert out.index("## What I've actually done") < out.index("MOMENT_MARKER") < out.index("SIT_MARKER")


def test_section_is_ungated_truth_machinery():
    # Unlike the trace (companion.own_work) this band has no flag: a build
    # without it is NOT a supported configuration.
    a = build_system_prompt(_Ctx(), cadence_directive="")

    @dataclass
    class _NoField:
        identity_core_text: str = "I'm PaLLi."
        skills_text: str = ""
        runtime_text: str = ""
        memory_text: str = ""
        facts_text: str = ""
        situational_text: str = ""

    b = build_system_prompt(_NoField(), cadence_directive="")
    assert "## What I've actually done" in a and "## What I've actually done" in b
