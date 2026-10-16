"""Step 23 · The belonging passage — "where I live" (Task 2; its heading read
"Where you live" until Batch 7's fix wave wrote the passage as her, ruling F2).

Spec §0.5 (owner law, 10:16): her machine is HERS; the NAS is the house's
shared drive — read freely, write only in her own space; the network is her
home's network. §1: identity, not tooling — it rides her system prompt and
one line joins the workbench session; flag off = zero bytes (the Fears pattern).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.agent import where_you_live as wyl
from tests.agent.test_system_prompt import FakeCtx


def test_flag_off_is_zero_bytes():
    with patch.object(wyl, "_enabled", lambda: False):
        assert wyl.passage() == "" and wyl.workbench_line() == ""


def test_flag_on_carries_the_three_framings_verbatim_in_spirit(monkeypatch):
    monkeypatch.setenv("PALLI_NAS_ROOT", "/mnt/example-nas")
    with patch.object(wyl, "_enabled", lambda: True):
        p = wyl.passage()
    assert p.startswith("## Where I live\n")
    assert "my computer" in p and "glass wall" in p.lower()
    assert "shared drive" in p and "shared bookshelf" in p and "my own space" in p
    assert "home's network" in p and "who's home" in p
    assert "/mnt/example-nas" in p        # wherever the one knob points


def test_no_nas_configured_says_nothing_about_a_nas(monkeypatch):
    """Step 28 task 2 — the NAS is ONE knob (PALLI_NAS_ROOT) and EMPTY means
    there is no NAS. She is then never told about a shared drive that does not
    exist: the sentence simply is not there (the zero-bytes law). The other two
    framings — her machine, her network — are untouched."""
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    with patch.object(wyl, "_enabled", lambda: True):
        p = wyl.passage()
    assert "NAS" not in p and "shared drive" not in p and "shared bookshelf" not in p
    assert "my computer" in p and "glass wall" in p.lower()
    assert "home's network" in p and "who's home" in p


def test_gate_reads_the_umbrella_flag():
    import core.agent.where_you_live as mod

    with patch.object(mod, "companion_settings", lambda: SimpleNamespace(outward_senses=True)):
        assert mod._enabled() is True
    with patch.object(mod, "companion_settings", lambda: SimpleNamespace(outward_senses=False)):
        assert mod._enabled() is False
    with patch.object(mod, "companion_settings", lambda: None):       # unbooted = off
        assert mod._enabled() is False


# ── the system prompt: once, near her identity, zero bytes when off ──────

def test_system_prompt_carries_the_passage_once_near_identity():
    from core.agent.system_prompt import build_system_prompt

    with patch.object(wyl, "_enabled", lambda: True):
        out = build_system_prompt(FakeCtx(), cadence_directive="")
    assert out.count("## Where I live") == 1
    # identity-adjacent: before the skills/runtime/memory bands
    assert out.index("## Where I live") < out.index("skills here")


def test_system_prompt_off_is_byte_identical_even_with_a_broken_gate():
    from core.agent.system_prompt import build_system_prompt

    # The control: with the flag on, the heading this test looks for is there,
    # so its absence below is the flag's doing, never a stale heading.
    with patch.object(wyl, "_enabled", lambda: True):
        assert "## Where I live" in build_system_prompt(FakeCtx(), cadence_directive="dir")
    with patch.object(wyl, "_enabled", lambda: False):
        off = build_system_prompt(FakeCtx(), cadence_directive="dir")
    assert "Where I live" not in off and "Where you live" not in off

    def _boom():
        raise AssertionError("gate must not raise through the block")

    with patch.object(wyl, "_enabled", _boom):
        off_broken = build_system_prompt(FakeCtx(), cadence_directive="dir")
    assert off == off_broken


# ── the workbench session: one line, the slot collapses ──────────────────

def test_workbench_template_slot_collapses_to_zero_bytes():
    text = Path("prompts/workbench_session.txt").read_text(encoding="utf-8")
    # the slot leads line 1 and is immediately followed by today's text —
    # "" leaves today's exact bytes (the fears zero-byte pattern)
    assert text.startswith("{where_you_live_line}This is your own time, at your own workbench.")


def _render_workbench(tmp_path, *, on: bool) -> str:
    import ai.workbench as wb

    comp = SimpleNamespace(wanting=False)
    with patch.object(wb, "companion_settings", return_value=comp), \
         patch.object(wyl, "_enabled", lambda: on):
        return wb._render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)


def test_workbench_line_leads_the_session_when_on(tmp_path):
    on = _render_workbench(tmp_path, on=True)
    assert on.startswith("This machine is your computer — your workshop. This is your own time")


def test_workbench_off_is_todays_exact_opening(tmp_path):
    off = _render_workbench(tmp_path, on=False)
    assert off.startswith("This is your own time, at your own workbench.")
    assert "your computer" not in off
