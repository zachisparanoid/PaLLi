"""Present Moment Slice 2: the reply prompt carries her latest moment as a
volatile band ONLY when recall produced one — byte-identical (section
absent) when dark. Placement: before situational_text (her present beside
his context). Spec: docs/superpowers/specs/
2026-07-27-present-moment-slice-2-design.md §2/§4."""
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
    present_moment_text: str = ""
    situational_text: str = ""


@dataclass
class _CtxNoField:
    """A build without Slice 2 — the byte-identity reference shape."""

    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    situational_text: str = ""


BAND = "Your last moment, written 19 minutes ago:\nThe quiet feels like concentration."


def test_section_absent_when_empty():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "Your present moment" not in out


def test_section_present_exactly_once_with_framing_law():
    out = build_system_prompt(_Ctx(present_moment_text=BAND), cadence_directive="")
    assert out.count("## Your present moment") == 1
    assert "the room you answer from" in out
    assert "Never narrate it back" in out
    assert "The quiet feels like concentration." in out


def test_byte_identical_when_dark():
    """The standing law: flag off (empty field) ⇒ the prompt is byte-equal
    to a build that never had Slice 2."""
    with_field_empty = build_system_prompt(_Ctx(), cadence_directive="cadence")
    without_field = build_system_prompt(_CtxNoField(), cadence_directive="cadence")
    assert with_field_empty == without_field


def test_placed_before_situational_text():
    out = build_system_prompt(
        _Ctx(present_moment_text=BAND, situational_text="SIT_MARKER"),
        cadence_directive="",
    )
    assert out.index("## Your present moment") < out.index("SIT_MARKER")
