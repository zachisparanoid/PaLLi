"""Narrative Self injection bands (Wave 1b) — render, byte-purity, seeded
docs → bands integration.

Harness lineage: _Ctx duck-type from tests/turn/test_night_cycle_surfacing.py;
adapter-chain purity (p1 == p2) from tests/agent/test_stake_injection.py:58-66.
Every hop is pinned — the render chain's known scar is fields computed but
never flattened (the caring-memory 2026-06-13 blank-fields bug).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.agent.context_adapter import build_agent_loop_context
from core.agent.system_prompt import build_system_prompt
from core.turn.types import Turn, TurnContext, TurnMetadata


@dataclass
class _Ctx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    narrative_care_spine_text: str = ""
    narrative_my_story_text: str = ""
    narrative_story_of_us_text: str = ""


def _turn(content: str = "hey") -> Turn:
    return Turn(
        surface="discord_text",
        owner_id="1",
        content=content,
        # the owner's private chat: in a group the story of us stays out
        # (the presence slice, 10-14; tests/agent/test_presence_slice.py)
        metadata=TurnMetadata(origin="user", channel_id="123", is_dm=True),
    )


def _ctx(**overrides) -> TurnContext:
    base = dict(turn=_turn())
    base.update(overrides)
    return TurnContext(**base)


def test_turncontext_accepts_narrative_fields_with_defaults():
    ctx = _ctx()
    assert ctx.narrative_care_spine == ""
    assert ctx.narrative_my_story == ""
    assert ctx.narrative_story_of_us == ""
    ctx2 = _ctx(narrative_my_story="I held the week.")
    assert "held the week" in ctx2.narrative_my_story


def test_adapter_flattens_all_three_fields():
    """The scar test: a TurnContext field the adapter doesn't flatten reaches
    nobody. Pin every hop explicitly."""
    loop_ctx = build_agent_loop_context(
        _turn(),
        _ctx(
            narrative_care_spine="I care about the workshop.",
            narrative_my_story="I held the week.",
            narrative_story_of_us="We are early.",
        ),
    )
    assert loop_ctx.narrative_care_spine_text == "I care about the workshop."
    assert loop_ctx.narrative_my_story_text == "I held the week."
    assert loop_ctx.narrative_story_of_us_text == "We are early."


def test_bands_absent_when_empty_byte_identical():
    p1 = build_system_prompt(_Ctx(), cadence_directive="")
    p2 = build_system_prompt(
        _Ctx(narrative_care_spine_text="", narrative_my_story_text="", narrative_story_of_us_text=""),
        cadence_directive="",
    )
    assert p1 == p2
    assert "What I care about (my own words)" not in p1
    assert "My story (as I last wrote it)" not in p1
    assert "The story of us (as I last wrote it)" not in p1


def test_adapter_chain_byte_purity():
    """Full-chain purity (the stake test's idiom): TurnContext without the
    fields vs with empty strings ⇒ identical prompt bytes."""
    loop_no_field = build_agent_loop_context(_turn(), _ctx())
    loop_empty = build_agent_loop_context(
        _turn(),
        _ctx(narrative_care_spine="", narrative_my_story="", narrative_story_of_us=""),
    )
    p1 = build_system_prompt(loop_no_field, cadence_directive="")
    p2 = build_system_prompt(loop_empty, cadence_directive="")
    assert "What I care about (my own words)" not in p1
    assert p1 == p2


def test_bands_present_when_set_once_each_in_order():
    out = build_system_prompt(
        _Ctx(
            narrative_care_spine_text="I care about the workshop.",
            narrative_my_story_text="I held the week.",
            narrative_story_of_us_text="We are early.",
        ),
        cadence_directive="",
    )
    assert out.count("## What I care about (my own words)") == 1
    assert out.count("## My story (as I last wrote it)") == 1
    assert out.count("## The story of us (as I last wrote it)") == 1
    assert "I care about the workshop." in out
    assert "I held the week." in out
    assert "We are early." in out
    # order: spine → my story → story of us, after the night-cycle band slot
    spine_i = out.index("## What I care about (my own words)")
    story_i = out.index("## My story (as I last wrote it)")
    us_i = out.index("## The story of us (as I last wrote it)")
    assert spine_i < story_i < us_i
    # framing law: hers to speak from, never to recite (spec §4.3)
    assert "recite" in out
    # ambient pointer: story names the what; introspect holds the receipts (spec §4.4)
    assert "introspect" in out


def test_band_renderer_clips_defensively():
    out = build_system_prompt(
        _Ctx(
            narrative_care_spine_text="x" * 5000,
            narrative_my_story_text="y" * 5000,
            narrative_story_of_us_text="z" * 5000,
        ),
        cadence_directive="",
    )
    assert "x" * 701 not in out
    assert "y" * 2401 not in out
    assert "z" * 2401 not in out


def test_recall_read_is_flag_gated(monkeypatch, tmp_path):
    """Flag OFF ⇒ recall never reads the docs; TurnContext fields stay ''.
    (The registration-style purity test — the band test above pins bytes.)"""
    import ai.narrative_self as ns

    calls = {"n": 0}

    def spy_read(doc, **kw):
        calls["n"] += 1
        return "SHOULD NOT APPEAR"

    monkeypatch.setattr(ns, "read_document", spy_read)
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: False)
    from core.turn.recall import _read_narrative_documents

    spine, story, us = _read_narrative_documents()
    assert (spine, story, us) == ("", "", "")
    assert calls["n"] == 0

    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
    monkeypatch.setattr(ns, "first_authorship_done", lambda repo_root=None: True)
    spine, story, us = _read_narrative_documents()
    assert spine == "SHOULD NOT APPEAR" and calls["n"] == 3


def test_recall_stays_dark_before_first_authorship(monkeypatch):
    """Review #7 (fix wave C): flag ON but first authorship not applied ⇒
    zero document reads and empty bands — the on-disk placeholder can never
    inject framed as her own words."""
    import ai.narrative_self as ns

    calls = {"n": 0}

    def spy_read(doc, **kw):
        calls["n"] += 1
        return "MUST NOT APPEAR"

    monkeypatch.setattr(ns, "read_document", spy_read)
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
    monkeypatch.setattr(ns, "first_authorship_done", lambda repo_root=None: False)
    from core.turn.recall import _read_narrative_documents

    assert _read_narrative_documents() == ("", "", "")
    assert calls["n"] == 0


def test_committed_docs_reach_the_bands(monkeypatch, tmp_path):
    """§7 sandbox integration, unit half: a gated commit lands → recall's
    read returns the authored text (the probe pack covers the full turn)."""
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(
        revision=ns.Revision(doc="MY_STORY.md", new_text="I held the week.", reasoning="r"),
        repo_root=root,
    )
    monkeypatch.setattr(ns, "identity_palli_root", lambda: root)
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
    # real first_authorship_done() runs here: the commit above IS the receipt
    # (keep the process-wide positive cache out of this assertion's way)
    monkeypatch.setattr(ns, "_FIRST_AUTHORSHIP_SEEN", False, raising=False)
    from core.turn.recall import _read_narrative_documents

    spine, story, us = _read_narrative_documents()
    assert story == "I held the week."
    assert us == ""
