from __future__ import annotations

from dataclasses import dataclass

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/system_prompt.py", "agent_system_prompt_under_test")


@dataclass
class FakeCtx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = "skills here"
    runtime_text: str = "runtime info"
    memory_text: str = "memory snapshot"
    facts_text: str = "user facts"
    # Step 31 (spec §4): "" = a text turn — no section, byte-identical.
    spoken_register_text: str = ""


def test_build_system_prompt_includes_all_sections():
    mod = _load()
    ctx = FakeCtx()
    out = mod.build_system_prompt(ctx, cadence_directive="cadence here")
    assert "I'm PaLLi." in out
    assert "skills here" in out
    assert "runtime info" in out
    assert "memory snapshot" in out
    assert "user facts" in out
    assert "cadence here" in out


def test_cadence_directive_is_last_for_cache_friendliness():
    mod = _load()
    ctx = FakeCtx()
    out = mod.build_system_prompt(ctx, cadence_directive="LAST_MARKER")
    assert out.rfind("LAST_MARKER") > out.rfind("user facts")


def test_empty_sections_skipped():
    mod = _load()
    ctx = FakeCtx(skills_text="", facts_text="")
    out = mod.build_system_prompt(ctx, cadence_directive="dir")
    assert "I'm PaLLi." in out
    assert "dir" in out
    assert "\n\n\n\n" not in out


def test_no_cadence_directive_emits_no_directive_section():
    mod = _load()
    ctx = FakeCtx()
    out = mod.build_system_prompt(ctx, cadence_directive="")
    assert "I'm PaLLi." in out


def test_texting_voice_block_present_and_cache_stable():
    """2026-07-11 brevity work: the prompt must carry a default-brevity /
    texting-register rule (there was NONE — the model's helpful-assistant
    default filled the silence with essays and markdown headers). It lives
    in the stable prefix (before the volatile per-turn sections) so it
    anchors every turn and stays cache-friendly."""
    mod = _load()
    ctx = FakeCtx()
    out = mod.build_system_prompt(ctx, cadence_directive="dir")
    # Batch 7 (4.3): the block speaks as her ("## How I talk").
    assert "## How I talk" in out
    assert "Brief is my default" in out
    assert "only when they ask for depth" in out
    # no markdown-essay formatting unless asked
    assert "headers" in out and "bullet" in out
    # stable-prefix placement: before the skills section
    assert out.find("## How I talk") < out.find("skills here")


def test_spoken_register_section_present_iff_text():
    """Step 31 (spec §4): a spoken turn's prompt says so, in its own short
    section; a text turn's prompt is byte-identical to a build without the
    band (absent field ≡ empty field ≡ no section)."""
    mod = _load()
    on = mod.build_system_prompt(
        FakeCtx(spoken_register_text="SPOKEN, NOT WRITTEN: said aloud."),
        "dir",
    )
    off = mod.build_system_prompt(FakeCtx(), "dir")
    assert "SPOKEN, NOT WRITTEN" in on
    assert "## This one is spoken aloud" in on
    assert "SPOKEN, NOT WRITTEN" not in off

    class _BareCtx:
        """The five original fields and nothing else — a pre-band ctx."""
        identity_core_text = "I'm PaLLi."
        skills_text = "skills here"
        runtime_text = "runtime info"
        memory_text = "memory snapshot"
        facts_text = "user facts"

    assert off == mod.build_system_prompt(_BareCtx(), "dir")


# --- the forge block (step 34, spec §2): flag-gated, byte-identical off -----

def test_forge_block_absent_when_flag_off(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: False)
    out = build_system_prompt(FakeCtx(), cadence_directive="")
    assert "forge" not in out.lower()


def test_forge_block_present_when_flag_on(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: True)
    out = build_system_prompt(FakeCtx(), cadence_directive="")
    assert out.count("## My forge") == 1
    # the teaching: she says plainly she can't yet; she claims a capability
    # only once she has it; building takes minutes and she says so; she
    # checks her belt first.
    assert "can't yet" in out
    assert "I claim a capability only once I have it" in out
    assert "minute" in out.lower()


def test_forge_block_off_is_byte_identical(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.system_prompt import build_system_prompt

    monkeypatch.setattr(bt, "_forged_tools_enabled", lambda: False)
    off = build_system_prompt(FakeCtx(), cadence_directive="dir")

    def _boom():
        raise AssertionError("gate must not raise through the block")

    monkeypatch.setattr(bt, "_forged_tools_enabled", _boom)
    off_broken_gate = build_system_prompt(FakeCtx(), cadence_directive="dir")
    assert off == off_broken_gate


# ── Batch 2, task 3 (cut the cost, 2026-09-27): stable prefix / volatile tail ─
# The prompt splits at runtime_text: everything before it (identity minus the
# memory doc, the constitutional blocks, voice, skills, then the memory doc)
# is the STABLE prefix that travels as `system`; from the clock on it is the
# VOLATILE tail that rides in the one trailing system message. Same sections,
# same text — a different order and a different message.

@dataclass
class SplitCtx(FakeCtx):
    runtime_text: str = "## Current Time\n10:00"
    owner_memory_text: str = "### Memory\nBACKGROUND REFERENCE.\n\n## Durable Context\n- likes tea"


def _split_ctx(clock: str = "10:00") -> SplitCtx:
    return SplitCtx(runtime_text=f"## Current Time\n{clock}")


def test_parts_join_to_the_whole():
    mod = _load()
    ctx = _split_ctx()
    stable, volatile = mod.build_system_prompt_parts(ctx, cadence_directive="[short]")
    assert mod.build_system_prompt(ctx, cadence_directive="[short]") == "\n\n".join(p for p in (stable, volatile) if p)


def test_stable_has_no_clock_and_volatile_has_it():
    mod = _load()
    stable, volatile = mod.build_system_prompt_parts(_split_ctx(), cadence_directive="[short]")
    assert "## Current Time" not in stable and "## Current Time" in volatile
    assert "[short]" in volatile and "[short]" not in stable


def test_stable_is_identical_across_cadence_and_clock():
    mod = _load()
    a, _ = mod.build_system_prompt_parts(_split_ctx(clock="10:00"), cadence_directive="[short]")
    b, _ = mod.build_system_prompt_parts(_split_ctx(clock="10:05"), cadence_directive="[medium]")
    assert a == b


def test_memory_doc_sits_after_every_static_block():
    mod = _load()
    stable, _ = mod.build_system_prompt_parts(_split_ctx(), cadence_directive="")
    # "## How I talk" is the texting-voice block — the last constitutional
    # block, present in this suite (test_texting_voice_block_present_and_
    # cache_stable pins it) — and "skills here" the skills catalog, the last
    # entry before the memory doc.
    assert stable.index("## Durable Context") > stable.index("## How I talk")
    assert stable.index("## Durable Context") > stable.index("skills here")
    assert stable.endswith("- likes tea")


def test_volatile_tail_keeps_todays_order_and_the_directive_last():
    mod = _load()
    _, volatile = mod.build_system_prompt_parts(_split_ctx(), cadence_directive="LAST_MARKER")
    assert volatile.index("## Current Time") < volatile.index("memory snapshot") < volatile.index("user facts")
    assert volatile.endswith("LAST_MARKER")
    assert "## What I've actually done" in volatile  # the grounding law is always present


def test_split_is_the_old_prompt_with_only_the_memory_doc_moved():
    """The whole prompt (parts joined) differs from a build with the memory
    doc riding inside identity_core_text — where it lived before — only by
    that doc's position: the paragraph multisets are equal."""
    mod = _load()
    memory = "### Memory\nBACKGROUND REFERENCE.\n\n## Durable Context\n- likes tea"
    before = FakeCtx(identity_core_text="I'm PaLLi.\n\n" + memory, runtime_text="## Current Time\n10:00")
    after = SplitCtx(identity_core_text="I'm PaLLi.", owner_memory_text=memory)
    old = mod.build_system_prompt(before, cadence_directive="[short]")
    new = mod.build_system_prompt(after, cadence_directive="[short]")
    assert old != new
    assert sorted(old.split("\n\n")) == sorted(new.split("\n\n"))


def test_ctx_without_owner_memory_text_still_builds():
    mod = _load()
    stable, volatile = mod.build_system_prompt_parts(FakeCtx(), cadence_directive="dir")
    assert "I'm PaLLi." in stable and "runtime info" in volatile and "### Memory" not in stable
