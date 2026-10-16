"""The authorship trace reaches the REPLY prompt (2026-08-16).

The three-doors and claw conversations: her hands build overnight behind
the null-delivery wall, so "what've you been up to?" began with live
archaeology and "the wall hid it from me". The trace (already in her
monologue and moment gathers since 08-14) now rides the reply prompt as
a read-only band — present exactly once with the plain-first framing,
byte-identical to a build without it when dark, placed beside the moment.
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
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""


@dataclass
class _CtxNoField:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""


TRACE = ("YOUR OWN HANDS, RECENTLY (...):\n"
         "- 2026-08-14 4:14 AM session: claw/ — a small machine with "
         "something inside (committed 91aa314)")


def test_section_absent_when_empty():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "## Your own recent work" not in out


def test_section_present_once_with_plain_first_framing():
    out = build_system_prompt(_Ctx(own_work_trace_text=TRACE), cadence_directive="")
    assert out.count("## Your own recent work") == 1
    assert out.count("claw/") == 1
    assert "say what it is and what it does first" in out


def test_byte_identical_when_dark():
    with_field = build_system_prompt(_Ctx(), cadence_directive="")
    without_field = build_system_prompt(_CtxNoField(), cadence_directive="")
    assert with_field == without_field


def test_placed_before_the_moment_and_situational():
    out = build_system_prompt(
        _Ctx(own_work_trace_text=TRACE, present_moment_text="MOMENT_MARKER",
             situational_text="SIT_MARKER"),
        cadence_directive="")
    assert out.index("## Your own recent work") < out.index("MOMENT_MARKER") < out.index("SIT_MARKER")


def test_plain_first_line_lives_in_the_voice_block():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "plain first, meaning second" in out
