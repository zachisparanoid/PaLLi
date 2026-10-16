"""Her conversational self-knowledge of the workbench (spec §5): a small
flag-gated teaching block — factual, pressure-free — so she KNOWS the
capability without being pushed to use or mention it. Absent ⇒ prompt
byte-identical (the pen's law).

Without it she would meet her own workbench as a stranger and could not
answer "what can you do these days?" truthfully in conversation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace


@dataclass
class _Ctx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""


def test_block_absent_when_flag_off(monkeypatch):
    import core.turn.companion_flags as flags
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(flags, "companion_settings",
                        lambda: SimpleNamespace(own_work=False))
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "workbench" not in out.lower()


def test_block_present_and_pressure_free_when_on(monkeypatch):
    import core.turn.companion_flags as flags
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(flags, "companion_settings",
                        lambda: SimpleNamespace(own_work=True))
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert out.count("## My workbench") == 1
    assert "Nothing is owed" in out and "nobody is waiting" in out


def test_block_no_longer_says_quiet_hours_and_knows_about_the_message_lane(monkeypatch):
    """2026-08-19: her time is not rationed to his downtime (item 5) and a
    SHARE reaches him as a message (item 3) — the block she reads about
    herself must say the true thing."""
    import core.turn.companion_flags as flags
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(flags, "companion_settings",
                        lambda: SimpleNamespace(own_work=True))
    out = build_system_prompt(_Ctx(), cadence_directive="")
    assert "in quiet hours" not in out
    assert "whenever something pulls" in out
    assert "i remember what i did there" in out.lower()
