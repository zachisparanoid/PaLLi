"""Night Cycle surfacing: the latest daily-meaning reflection appears in the
reply's system prompt only when it exists — byte-identical (section absent)
when empty. (2026-07-11, Task 6.)"""
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
    night_cycle_reflection_text: str = ""


def test_section_absent_when_empty():
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "Last night I reflected" not in out


def test_section_present_when_set():
    ctx = _Ctx(night_cycle_reflection_text="Quiet day; the reminder work landed.")
    out = build_system_prompt(ctx, cadence_directive="")
    assert out.count("## Last night I reflected") == 1
    assert "reminder work landed" in out
