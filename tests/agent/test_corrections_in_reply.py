"""Batch 5, task 7 (memory that picks, 2026-09-28; rulings R20, R21): the
owner's standing corrections reach her REPLIES. Until now
``render_corrections_block`` had night-job callers only — the reflection,
the day-gist, the story pages — so a belief they corrected in conversation
outranked nothing at the moment she answered them. The adapter renders the
block as its own band (``corrections_text``) at budget 1500 (fix round 1, ruling R54; 700 rendered one of 19 live) with
``sync=False`` (the reply path must never append to their file) and
``footer=True`` (ruling R61: one closing line when the budget cut lines),
and the system prompt joins it to the STABLE prefix right after the memory band.
A guest turn gets none, before the file is touched; a raising store gives
""; two renders of one store are the same bytes.

The autouse guard in tests/conftest.py points CORRECTIONS_DIR at tmp, so
every correction planted here is disposable.
"""
from __future__ import annotations

import pytest

from ai import corrections_store as cs
from core.agent.context_adapter import (
    AgentLoopContext,
    _render_corrections,
    build_agent_loop_context,
)
from core.agent.system_prompt import build_system_prompt_parts
from core.identity.models import CompiledIdentity, IdentityDocument
from core.turn.types import Turn, TurnContext, TurnMetadata

_MEMORY_FILE = (
    "# Memory\n\n## Durable Context\n- likes tea\n\n## Active Threads\n- [09-27] the boat\n\n"
    "## Important Decisions\n- no essays\n\n## Useful References\n- the NAS\n"
)


def _turn(owner_id: int = 1) -> Turn:
    return Turn(surface="discord_text", owner_id=owner_id, content="hi", metadata=TurnMetadata())


def _compiled() -> CompiledIdentity:
    def doc(key: str, text: str) -> IdentityDocument:
        return IdentityDocument(doc_key=key, path=key, frontmatter={}, content=text)
    return CompiledIdentity(
        core={"core_identity": doc("core_identity", "# Core\nI am PaLLi.")},
        owner={"memory": doc("memory", _MEMORY_FILE)},
    )


def _ctx(turn: Turn) -> TurnContext:
    return TurnContext(turn=turn, compiled_identity=_compiled())


def _plant(belief: str, truth: str) -> None:
    row = cs.record_correction(belief=belief, truth=truth, source="hers", evidence="their 09-28 message")
    assert row is not None


def test_one_standing_correction_reaches_the_stable_prefix_right_after_the_memory_band():
    _plant("the three doors were never built", "she built all three doors on 08-12")
    turn = _turn()
    out = build_agent_loop_context(turn, _ctx(turn))
    assert out.corrections_text.startswith(cs.BLOCK_LABEL)
    assert 'NOT "the three doors were never built" — TRUE: she built all three doors on 08-12' in out.corrections_text

    stable, volatile = build_system_prompt_parts(out, cadence_directive="[short]")
    assert cs.BLOCK_LABEL in stable and cs.BLOCK_LABEL not in volatile
    # right after the memory band: the memory core comes before it, and it
    # ends the stable prefix
    assert stable.index("### Memory") < stable.index(cs.BLOCK_LABEL)
    assert stable.index("- likes tea") < stable.index(cs.BLOCK_LABEL)
    assert stable.endswith(out.corrections_text)


def test_the_band_is_rendered_at_budget_1500_without_syncing_his_file(monkeypatch):
    _plant("the bike is in the shed", "the bike is in the hallway")
    seen = {}
    real = cs.render_corrections_block

    def recording(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    def never_sync(**kwargs):
        raise AssertionError("the reply path must never sync their file")

    monkeypatch.setattr(cs, "render_corrections_block", recording)
    monkeypatch.setattr(cs, "sync_his_file", never_sync)
    text = _render_corrections(_ctx(_turn()), is_owner=True)
    assert seen == {"budget": 1500, "sync": False, "footer": True}
    assert "the bike is in the hallway" in text


def test_a_guest_turn_gets_no_corrections_and_never_reaches_the_store(monkeypatch):
    _plant("the owner is called Sam", "the owner is called Pat")
    turn = _turn(owner_id=999)
    assert build_agent_loop_context(turn, _ctx(turn), is_owner=False).corrections_text == ""

    def boom(**kwargs):
        raise AssertionError("a guest render must return before the store is read")

    monkeypatch.setattr(cs, "render_corrections_block", boom)
    assert _render_corrections(_ctx(turn), is_owner=False) == ""


def test_a_raising_store_renders_nothing(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("events file unreadable")

    monkeypatch.setattr(cs, "render_corrections_block", boom)
    turn = _turn()
    assert _render_corrections(_ctx(turn), is_owner=True) == ""
    out = build_agent_loop_context(turn, _ctx(turn))
    assert out.corrections_text == ""
    stable, _ = build_system_prompt_parts(out, cadence_directive="")
    assert cs.BLOCK_LABEL not in stable


def test_nothing_standing_renders_nothing():
    assert cs.standing() == []
    turn = _turn()
    out = build_agent_loop_context(turn, _ctx(turn))
    assert out.corrections_text == ""
    stable, _ = build_system_prompt_parts(out, cadence_directive="")
    assert cs.BLOCK_LABEL not in stable
    assert stable.endswith(out.owner_memory_text)  # the memory band still ends the prefix


def test_two_renders_of_one_store_are_identical_bytes():
    """The band sits in the cache-stable prefix, so it must not carry the
    clock: the day in each line is the CORRECTION's own date (its ``at``),
    never today's."""
    _plant("first wrong belief", "first truth")
    _plant("second wrong belief", "second truth")
    turn = _turn()
    a = _render_corrections(_ctx(turn), is_owner=True)
    b = _render_corrections(_ctx(turn), is_owner=True)
    assert a == b
    assert a == cs.render_corrections_block(budget=1500, sync=False, footer=True).strip()
    assert "first truth" in a and "second truth" in a


def _snapshot_with(**memory):
    from types import SimpleNamespace
    return lambda: SimpleNamespace(memory=SimpleNamespace(**memory))


def test_the_band_honours_the_corrections_budget_setting(monkeypatch):
    """Fix round 1 (ruling R56): ``memory.corrections_budget_chars`` is the
    band's budget — the owner turns it without a build. A budget with room
    for only the newest line drops the older one; the default is 1500."""
    import core.settings_boot as settings_boot

    _plant("older wrong belief about the boat", "the boat is on the trailer at home")
    _plant("newer wrong belief about the cat", "the cat is called Miso")
    whole = cs.render_corrections_block(budget=100_000, sync=False)
    lines = [ln for ln in whole.split("\n") if ln.startswith("- since ")]
    assert len(lines) == 2 and "Miso" in lines[0] and "trailer" in lines[1]   # newest first

    turn = _turn()
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=len(lines[0]) + 3))
    text = _render_corrections(_ctx(turn), is_owner=True)
    assert "Miso" in text and "trailer" not in text
    assert text.startswith(cs.BLOCK_LABEL)

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=100_000))
    text = _render_corrections(_ctx(turn), is_owner=True)
    assert "Miso" in text and "trailer" in text

    # the whole adapter path reads the same setting
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=len(lines[0]) + 3))
    out = build_agent_loop_context(turn, _ctx(turn))
    assert "Miso" in out.corrections_text and "trailer" not in out.corrections_text


def test_a_zero_budget_turns_the_band_off_without_reading_the_store(monkeypatch):
    import core.settings_boot as settings_boot

    _plant("some wrong belief", "the truth")
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=0))
    turn = _turn()
    assert _render_corrections(_ctx(turn), is_owner=True) == ""
    assert build_agent_loop_context(turn, _ctx(turn)).corrections_text == ""

    def boom(**kwargs):
        raise AssertionError("a zero budget must return before the store is read")

    monkeypatch.setattr(cs, "render_corrections_block", boom)
    assert _render_corrections(_ctx(turn), is_owner=True) == ""


def test_unbootable_settings_fall_back_to_the_1500_default(monkeypatch):
    import core.settings_boot as settings_boot

    _plant("a wrong belief", "a truth")
    seen = {}
    real = cs.render_corrections_block

    def recording(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", boom)
    monkeypatch.setattr(cs, "render_corrections_block", recording)
    assert "a truth" in _render_corrections(_ctx(_turn()), is_owner=True)
    assert seen == {"budget": 1500, "sync": False, "footer": True}


def test_the_band_carries_a_footer_when_the_budget_cuts_corrections(monkeypatch):
    """Ruling R61 (fix wave): the reply's band alone says how many standing
    corrections did not fit — one closing line, "(N more corrections
    stand.)" — through ``footer=True``; the same store rendered with the
    default (every night gather) carries no such line, so nothing they
    read changes; a set that fits whole has no footer either way."""
    import core.settings_boot as settings_boot

    _plant("older wrong belief about the bike", "the bike is in the hallway")
    _plant("newer wrong belief about the cat", "the cat is called Miso")
    whole = cs.render_corrections_block(budget=100_000, sync=False)
    lines = [ln for ln in whole.split("\n") if ln.startswith("- since ")]
    assert len(lines) == 2
    room_for_one = len(lines[0]) + 3

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=room_for_one))
    text = _render_corrections(_ctx(_turn()), is_owner=True)
    assert text.endswith("\n(1 more corrections stand.)")
    assert "Miso" in text and "hallway" not in text
    assert text.count("more corrections stand") == 1
    # the night gathers' render of the same cut set: no footer, byte for byte the old block
    night = cs.render_corrections_block(budget=room_for_one, sync=False)
    assert "more corrections stand" not in night and text == night + "\n(1 more corrections stand.)"

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", _snapshot_with(corrections_budget_chars=100_000))
    assert "more corrections stand" not in _render_corrections(_ctx(_turn()), is_owner=True)


def test_hand_built_contexts_default_empty():
    turn = _turn()
    ctx = AgentLoopContext(
        turn=turn, ctx=_ctx(turn), identity_core_text="", skills_text="", runtime_text="",
        memory_text="", facts_text="", user_text="", recent_history_text="",
    )
    assert ctx.corrections_text == ""
