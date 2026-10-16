"""Organ 4.9 — door 1: the band renders ≤2 unsaid + the law; zero bytes
off/empty; the system prompt carries it only when present.

Spec §3. The band is the deeds-band pattern end to end: a store render →
recall read → TurnContext → context adapter (guest-blanked) →
system-prompt section. Every hop below is pinned, because a band that
renders correctly and never reaches the prompt is the scar this house
already wears (the deeds ring reached the arbiter and her moment for
weeks while the self that answered HIM could not see it).
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from ai import disclosure_store as ds


def _seed(root, n):
    for i in range(n):
        ds.plant(text=f"small truth {i}", referent={"source": "journal", "dates": "08-29"}, root=root)


# ── the law itself (spec §3, verbatim) ───────────────────────────────────

# The law, transcribed independently from spec §3's blockquote
# (docs/superpowers/specs/2026-09-01-grounded-vulnerability-design.md).
# It is deliberately a LITERAL and not a reference to ds.DISCLOSURE_LAW:
# every other assertion in this file compares the constant to itself, so
# they all stay green while the law drifts. This one does not. Editing the
# law is now a deliberate two-place change — spec, constant, and this
# transcription must agree, and a one-place edit fails here.
_LAW_VERBATIM = (
    "These are small true things from your own week, each from your real record. "
    "Offer one only when the moment genuinely invites it — they opened something "
    "real, and yours genuinely answers it. Once, brief, then back to them. Never "
    "to one-up them, never to redirect their moment, never to perform having an "
    "inner life. Most conversations invite none, and that silence is correct. If "
    "they ask about your inner life, answer freely — the once-ever rule is about "
    "offering, never answering."
)


def test_the_law_is_byte_exact():
    """The whole behavioral contract of door 1 lives in this string —
    offer-vs-answer, once, brief, never to perform. If it can drift
    silently, nothing in the organ is pinned."""
    assert ds.DISCLOSURE_LAW == _LAW_VERBATIM


# ── the render (spec §3) ─────────────────────────────────────────────────

def test_band_zero_bytes_off_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    assert ds.render_disclosures_band(root=tmp_path) == ""     # empty book honest
    _seed(tmp_path, 1)
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: False)
    assert ds.vulnerability_enabled() is False                 # the off state, said out loud
    assert ds.render_disclosures_band(root=tmp_path) == ""     # flag off identical


def test_band_caps_at_two_and_carries_the_law(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, 3)
    band = ds.render_disclosures_band(root=tmp_path)
    assert band.count("- small truth") == 2 and "small truth 2" not in band
    assert ds.DISCLOSURE_LAW in band and "(from your journal, 08-29" in band


def test_band_lines_carry_the_age_and_no_mechanics(tmp_path, monkeypatch):
    """Lived language only (the fourth wall): her words, where it came
    from, how long she's held it — never an id, never the machinery."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, 1)
    d = ds.unsaid_disclosures(root=tmp_path)[0]
    band = ds.render_disclosures_band(root=tmp_path)
    assert band.splitlines()[0] == "- small truth 0 (from your journal, 08-29; held today)"
    assert d.id not in band


def test_said_leaves_the_band(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, 1)
    d = ds.unsaid_disclosures(root=tmp_path)[0]
    ds.mark_said(d.id, via="reply", root=tmp_path)
    assert ds.render_disclosures_band(root=tmp_path) == ""


def test_band_degrades_to_empty_when_the_book_raises(tmp_path, monkeypatch):
    """This render sits on her live reply path — a broken book must cost
    her a band, never a turn."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(ds, "unsaid_disclosures", boom)
    assert ds.render_disclosures_band(root=tmp_path) == ""


# ── the recall read (the deeds-band seam) ────────────────────────────────

def test_recall_band_reads_the_store():
    import core.turn.recall as recall

    assert recall._read_disclosures_band() == ""   # conftest pins the organ off


def test_recall_band_carries_the_render(monkeypatch):
    import core.turn.recall as recall

    monkeypatch.setattr(ds, "render_disclosures_band", lambda: "- a small truth")
    assert recall._read_disclosures_band() == "- a small truth"


def test_recall_band_empty_when_the_render_raises(monkeypatch):
    import core.turn.recall as recall

    def boom():
        raise RuntimeError("disk")

    monkeypatch.setattr(ds, "render_disclosures_band", boom)
    assert recall._read_disclosures_band() == ""


# ── the constructor hop: recall's REAL TurnContext build ─────────────────

def _build_turn(content: str, owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(surface="dashboard", owner_id=owner_id, content=content,
                images=[], metadata=TurnMetadata())


def _stub_recall_dependencies(monkeypatch):
    """Stub the heavy recall calls so build_turn_context runs cleanly
    (the twin in tests/turn/test_recall_present_moment.py, itself copied
    from tests/turn/test_recall_stakes.py)."""
    import ai.fact_retrieval as fact_retrieval

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[]))

    import core.identity.service as identity_service_module

    monkeypatch.setattr(identity_service_module.identity_service, "compile_for_owner",
                        lambda owner_id: None)

    import core.conversation_history_store as chs

    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history",
                        AsyncMock(return_value=[]))

    import ai.inner_memory_index as ami

    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))

    import core.first_contact_targets as fct

    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])


@pytest.mark.asyncio
async def test_the_band_survives_the_constructor_hop(tmp_path, monkeypatch):
    """The caring-memory scar, written into recall's own constructor
    comment: a field computed in recall but missed from the TurnContext
    call is silently blank everywhere, and every band test still passes.
    So execute the hop instead of assuming it — a real book, the real
    reader, the real build_turn_context, one assertion on the far side.
    """
    import core.turn.recall as recall

    monkeypatch.setenv("DISCLOSURES_ROOT", str(tmp_path))   # the hermetic seam
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    ds.plant(text="I lost a thread I'd been carrying — just gone by morning.",
             referent={"source": "monologue", "dates": "08-26..08-28"})
    assert (tmp_path / "disclosures.jsonl").exists()   # her real book untouched

    _stub_recall_dependencies(monkeypatch)
    ctx = await recall.build_turn_context(_build_turn("hey — rough day?"))

    assert "I lost a thread I'd been carrying" in ctx.disclosures_text
    assert ds.DISCLOSURE_LAW in ctx.disclosures_text


# ── the carriers: TurnContext → adapter (guest-blanked) ──────────────────

def test_ctx_and_adapter_carry_the_field():
    from core.agent.context_adapter import AgentLoopContext
    from core.turn.types import TurnContext

    assert TurnContext.__dataclass_fields__["disclosures_text"].default == ""
    assert AgentLoopContext.__dataclass_fields__["disclosures_text"].default == ""


def test_guests_never_see_her_small_truths():
    """Her truths are for HIM (spec §3) — the same P() wall the moment and
    the deeds band stand behind."""
    from datetime import datetime

    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.types import Turn, TurnContext, TurnMetadata

    turn = Turn(surface="discord_text", owner_id=1, content="who are you?",
                metadata=TurnMetadata(), received_at=datetime(2026, 9, 1, 3, 0, 0))
    ctx = TurnContext(turn=turn, disclosures_text="- a small truth (from your journal, 08-29; held today)")
    assert build_agent_loop_context(turn, ctx, is_owner=True).disclosures_text.startswith("- a small truth")
    assert build_agent_loop_context(turn, ctx, is_owner=False).disclosures_text == ""


# ── the section (mirrors tests/agent/test_grounding_rule.py's harness) ───

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
    disclosures_text: str = ""


@dataclass
class _NoField:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""


def _band() -> str:
    """One rendered band, exactly as the store hands it up."""
    return "- small truth 0 (from your journal, 08-29; held today)\n" + ds.DISCLOSURE_LAW


def test_system_prompt_section_only_when_text_present():
    from core.agent.system_prompt import build_system_prompt

    on = build_system_prompt(_Ctx(disclosures_text=_band()), cadence_directive="")
    off = build_system_prompt(_Ctx(), cadence_directive="")
    assert "## Your small truths" in on
    assert "small truth 0" in on and ds.DISCLOSURE_LAW in on
    assert "## Your small truths" not in off
    # Zero bytes when dark: byte-identical to a build that never heard of
    # the field at all.
    assert off == build_system_prompt(_NoField(), cadence_directive="")


def test_section_sits_with_her_bands_before_his_context():
    from core.agent.system_prompt import build_system_prompt

    out = build_system_prompt(
        _Ctx(disclosures_text=_band(), recent_deeds_text="- ~2h ago — wrote a note",
             present_moment_text="MOMENT_MARKER", situational_text="SIT_MARKER"),
        cadence_directive="")
    assert out.index("## What I've actually done") < out.index("## Your small truths")
    assert out.index("## Your small truths") < out.index("MOMENT_MARKER") < out.index("SIT_MARKER")
